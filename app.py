# --- PAGE CONFIG ---
import pandas as pd
import streamlit as st
import os
import csv
import time

st.set_page_config(page_title="Bin Helper", layout="wide")

# --- SESSION STATE ---
if "active_view" not in st.session_state:
    st.session_state.active_view = None
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

# --- AUTO REFRESH ---
if st.session_state.auto_refresh:
    st.rerun()

# --- GITHUB FILE URLS ---
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

# --- LOAD DATA ---
@st.cache_data
def load_data(inventory_url, master_url):
    inventory_df = pd.read_excel(inventory_url, engine="openpyxl")
    master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    return inventory_df, master_df

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
    st.stop()

# --- DATA PREP ---
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}
filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# --- BUSINESS RULES ---
def exclude_damage_missing(df):
    return df[
        ~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"]) &
        ~df["LocationName"].astype(str).str.upper().str.startswith("IB")
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        df["LocationName"].astype(str).str[0].str.isdigit()
    ]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        (~df["LocationName"].astype(str).str.endswith("01") |
         df["LocationName"].astype(str).str.startswith("111")) &
        df["LocationName"].astype(str).str.isnumeric() &
        df["Qty"].between(6, 15)
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN") and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper() == "MISSING"]

# --- BULK DISCREPANCY LOGIC ---
def analyze_bulk_locations_grouped(df):
    df = exclude_damage_missing(df)
    results = []
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                results.append({
                    "LocationName": slot,
                    "TotalPallets": count,
                    "MaxAllowed": max_pallets,
                    "Issue": f"Exceeds max allowed: {count} > {max_pallets}"
                })
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations_grouped(filtered_inventory_df)

# --- DISCREPANCY LOGIC ---
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []
    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
        results.append({**row.to_dict(), "Issue": issue})
    full_df = df[
        (~df["LocationName"].astype(str).str.endswith("01") |
         df["LocationName"].astype(str).str.startswith("111")) &
        df["LocationName"].astype(str).str.isnumeric()
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "Partial pallet needs to be moved to Partial Loc." if row["Qty"] <= 5 else "Qty out of range for full pallet bin"
        results.append({**row.to_dict(), "Issue": issue})
    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# --- LOGGING FUNCTION ---
def log_resolved_discrepancy_with_note(row, note):
    log_file = "resolved_discrepancies.csv"
    row_with_note = row.copy()
    row_with_note["Note"] = note
    file_exists = os.path.isfile(log_file)
    with open(log_file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=row_with_note.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_with_note)
    st.session_state.resolved_items.add(row.get("LocationName", "") + str(row.get("PalletId", "")))

# --- FILTER FUNCTION ---
def apply_filters(df):
    for key, value in st.session_state.filters.items():
        if value and key in df.columns:
            df = df[df[key].astype(str).str.contains(value, case=False, na=False)]
    return df

# --- KPI CARDS ---
st.markdown("<h1 style='text-align: center; color: #2E86C1;'>📊 Bin-Helper Dashboard</h1>", unsafe_allow_html=True)
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Rack Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df), "icon": "📦"}
]
cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']}\n{item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# --- FILTERS ---
st.sidebar.markdown("### 🔍 Filter Options")
st.session_state.filters["LocationName"] = st.sidebar.text_input("Location", value=st.session_state.filters["LocationName"])
st.session_state.filters["PalletId"] = st.sidebar.text_input("Pallet ID", value=st.session_state.filters["PalletId"])
st.session_state.filters["WarehouseSku"] = st.sidebar.text_input("Warehouse SKU", value=st.session_state.filters["WarehouseSku"])
st.session_state.filters["CustomerLotReference"] = st.sidebar.text_input("LOT", value=st.session_state.filters["CustomerLotReference"])

# --- HISTORY LOG ---
st.sidebar.markdown("### ✅ History Log")
log_file = "resolved_discrepancies.csv"
if os.path.exists(log_file):
    history_df = pd.read_csv(log_file)
    st.sidebar.dataframe(history_df.reset_index(drop=True), use_container_width=True, hide_index=True)
else:
    st.sidebar.info("No resolved discrepancies logged yet.")

# --- UNDO FIX PANEL ---
st.sidebar.markdown("### 🔄 Undo Fix")
if os.path.exists(log_file):
    try:
        undo_df = pd.read_csv(log_file)
        if not undo_df.empty and "LocationName" in undo_df.columns and "PalletId" in undo_df.columns:
            undo_df["Key"] = undo_df["LocationName"].astype(str) + undo_df["PalletId"].astype(str)
            selected_key = st.sidebar.selectbox("Select a resolved item to undo", undo_df["Key"])
            if st.sidebar.button("Undo Fix"):
                updated_df = undo_df[undo_df["Key"] != selected_key].drop(columns=["Key"])
                updated_df.to_csv(log_file, index=False)
                st.sidebar.success("✅ Fix undone. Item will reappear in the dashboard.")
                time.sleep(1)
                st.stop()
        else:
            st.sidebar.info("No resolved items to undo.")
    except Exception as e:
        st.sidebar.error(f"Error loading resolved discrepancies: {e}")
else:
    st.sidebar.info("No resolved discrepancies logged yet.")

# --- DISPLAY VIEWS ---
view_map = {
    "Rack Discrepancies": discrepancy_df,
    "Bulk Discrepancies": bulk_df,
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df
}
if st.session_state.active_view:
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    active_df = apply_filters(raw_df)
    st.subheader(f"{st.session_state.active_view}")
    if st.session_state.active_view == "Bulk Discrepancies":
        grouped_df = analyze_bulk_locations_grouped(filtered_inventory_df)
        for idx, row in grouped_df.iterrows():
            location = row["LocationName"]
            with st.expander(f"⚠️ {row['LocationName']} — {row['Issue']}"):
                st.write(f"**Max Allowed:** {row['MaxAllowed']}\n**Total Pallets:** {row['TotalPallets']}")
                details = filtered_inventory_df[filtered_inventory_df["LocationName"] == location]
                for i, drow in details.iterrows():
                    row_id = drow.get("LocationName", "") + str(drow.get("PalletId", ""))
                    if row_id in st.session_state.resolved_items:
                        continue
                    st.write(drow[["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId", "Qty"]])
                    note_key = f"note_bulk_{idx}_{i}"
                    note = st.text_input(f"Note for Pallet {drow['PalletId']}", key=note_key)
                    if st.button(f"✅ Mark Pallet {drow['PalletId']} Fixed", key=f"bulk_fix_{idx}_{i}"):
                        log_resolved_discrepancy_with_note(drow.to_dict(), note)
                        st.success(f"Pallet {drow['PalletId']} logged as fixed!")
                        time.sleep(1)
                        st.stop()
    elif st.session_state.active_view == "Rack Discrepancies":
        for idx, row in active_df.iterrows():
            row_id = row.get("LocationName", "") + str(row.get("PalletId", ""))
            if row_id in st.session_state.resolved_items:
                continue
            with st.expander(f"⚠️ {row['LocationName']} — {row['Issue']}"):
                st.write(f"**Qty:** {row.get('Qty', 'N/A')}")
                st.write(row[["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId", "Qty", "Issue"]])
                note_key = f"note_rack_{idx}"
                note = st.text_input(f"Note for Pallet {row['PalletId']}", key=note_key)
                if st.button(f"✅ Mark Pallet {row['PalletId']} Fixed", key=f"rack_fix_{idx}"):
                    log_resolved_discrepancy_with_note(row.to_dict(), note)
                    st.success(f"Pallet {row['PalletId']} logged as fixed!")
                    time.sleep(1)
                    st.stop()
    else:
        required_cols = ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId", "Qty"]
        available_cols = [col for col in required_cols if col in active_df.columns]
        st.dataframe(active_df[available_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
else:
    st.info("👆 Select a KPI card above to view details.")