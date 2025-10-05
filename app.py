import os
import pandas as pd
import streamlit as st
from datetime import datetime

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Empty Bins"
if "selected_discrepancy" not in st.session_state:
    st.session_state.selected_discrepancy = None

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"
history_log_path = "discrepancy_history.csv"

# ---------------- LOAD DATA ----------------
if not os.path.exists(inventory_file_path) or not os.path.exists(master_file_path):
    st.error("Please upload both ON_HAND_INVENTORY.xlsx and Empty Bin Formula.xlsx to proceed.")
    st.stop()

inventory_df = pd.read_excel(inventory_file_path, engine="openpyxl")
master_df = pd.read_excel(master_file_path, sheet_name="Master Locations", engine="openpyxl")

# ---------------- DATA PREP ----------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

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
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
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

# ---------------- DISCREPANCY LOGIC ----------------
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []

    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "PalletCount": row.get("PalletCount", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", ""),
            "Issue": issue
        })

    full_df = df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric())
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "Qty out of range for full pallet bin"
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "PalletCount": row.get("PalletCount", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", ""),
            "Issue": issue
        })

    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ---------------- KPI CARDS ----------------
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
]

# ---------------- SIDEBAR: HISTORY ----------------
with st.sidebar:
    st.title("📋 Discrepancy History")
    if os.path.exists(history_log_path):
        history_df = pd.read_csv(history_log_path)
        search = st.text_input("Search History")
        if search:
            history_df = history_df[history_df.apply(lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1)]
        st.dataframe(history_df)
    else:
        st.info("No discrepancy history found.")

# ---------------- MAIN UI ----------------
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")
search_location = st.text_input("🔍 Filter by Location")

def display_table(df):
    if "LocationName" in df.columns and search_location:
        df = df[df["LocationName"].str.contains(search_location, case=False, na=False)]
    st.dataframe(df, use_container_width=True)
    return df

if st.session_state.active_view == "Discrepancies":
    st.markdown("### ⚠️ Discrepancies")
    display_df = discrepancy_df.copy()
    selected_row = st.selectbox("Select a discrepancy to fix", display_df["LocationName"].unique())
    selected_issue = display_df[display_df["LocationName"] == selected_row].iloc[0] if not display_df[display_df["LocationName"] == selected_row].empty else None

    if selected_issue is not None:
        with st.sidebar:
            st.markdown("### 🛠️ Fix Discrepancy")
            st.write(f"**Location:** {selected_issue['LocationName']}")
            st.write(f"**Issue:** {selected_issue['Issue']}")
            fix_note = st.text_area("📝 Add a note about the fix")
            if st.button("✅ Submit Fix"):
                new_entry = {
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "LocationName": selected_issue["LocationName"],
                    "Issue": selected_issue["Issue"],
                    "Note": fix_note
                }
                if os.path.exists(history_log_path):
                    history_df = pd.read_csv(history_log_path)
                    history_df = pd.concat([history_df, pd.DataFrame([new_entry])], ignore_index=True)
                else:
                    history_df = pd.DataFrame([new_entry])
                history_df.to_csv(history_log_path, index=False)
                st.success("Fix submitted and logged.")
else:
    if st.session_state.active_view == "Empty Bins":
        display_table(empty_bins_view_df)
    elif st.session_state.active_view == "Full Pallet Bins":
        display_table(full_pallet_bins_df)
    elif st.session_state.active_view == "Empty Partial Bins":
        display_table(empty_partial_bins_df)
    elif st.session_state.active_view == "Partial Bins":
        display_table(partial_bins_df)
    elif st.session_state.active_view == "Damages":
        display_table(damages_df)
    elif st.session_state.active_view == "Missing":
        display_table(missing_df)

# ---------------- KPI NAVIGATION ----------------
cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']