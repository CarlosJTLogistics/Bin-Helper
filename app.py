import os
import pandas as pd
import streamlit as st
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- CUSTOM CSS FOR DARK THEME ----------------
st.markdown("""
    <style>
    .ag-theme-material {
        background-color: #1e1e1e !important;
        color: #ffffff !important;
    }
    .ag-theme-material .ag-header-cell-label {
        color: #ffffff !important;
    }
    .ag-theme-material .ag-cell {
        color: #ffffff !important;
    }
    </style>
""", unsafe_allow_html=True)

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Empty Bins"

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"
log_file = "correction_log.csv"

# ---------------- SIDEBAR ----------------
st.sidebar.title("📦 Bin Helper")
search_location = st.sidebar.text_input("🔍 Filter by Location")

# File Uploads
st.sidebar.markdown("### 📂 Upload Files")
if os.path.exists(inventory_file_path):
    st.sidebar.markdown(f"✅ **Active Inventory File:** `{os.path.basename(inventory_file_path)}`")
else:
    uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"], key="inv_file")
    if uploaded_inventory:
        with open(inventory_file_path, "wb") as f:
            f.write(uploaded_inventory.getbuffer())
        st.sidebar.success("✅ Inventory file uploaded and saved.")

if os.path.exists(master_file_path):
    st.sidebar.markdown(f"✅ **Active Master File:** `{os.path.basename(master_file_path)}`")
else:
    uploaded_master = st.sidebar.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"], key="master_file")
    if uploaded_master:
        with open(master_file_path, "wb") as f:
            f.write(uploaded_master.getbuffer())
        st.sidebar.success("✅ Master file uploaded and saved.")

# Correction Log
st.sidebar.markdown("### 📋 Correction Log")
correction_df = pd.DataFrame()
if os.path.exists(log_file):
    correction_df = pd.read_csv(log_file)
    st.sidebar.dataframe(correction_df, use_container_width=True)
    st.sidebar.download_button("⬇️ Download Correction Log", correction_df.to_csv(index=False), "correction_log.csv", "text/csv")
else:
    st.sidebar.info("No correction log found yet.")

# ---------------- LOAD DATA ----------------
if not os.path.exists(inventory_file_path) or not os.path.exists(master_file_path):
    st.error("Please upload both ON_HAND_INVENTORY.xlsx and Empty Bin Formula.xlsx to proceed.")
    st.stop()

inventory_df = pd.read_excel(inventory_file_path, engine="openpyxl")
master_df = pd.read_excel(master_file_path, sheet_name="Master Locations", engine="openpyxl")

# ---------------- DATA PREP ----------------
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def is_valid_location(loc):
    if pd.isna(loc): return False
    loc_str = str(loc).upper()
    return (
        loc_str.startswith("TUN")
        or loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"]
        or loc_str.isdigit()
        or loc_str[0] in bulk_rules.keys()
    )

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# ---------------- BUSINESS RULES ----------------
def exclude_damage_missing(df):
    return df[~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111")))
        & (df["LocationName"].astype(str).str.isnumeric())
        & (df["Qty"].between(6, 15))
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01")
        & ~df["LocationName"].astype(str).str.startswith("111")
        & ~df["LocationName"].astype(str).str.upper().str.startswith("TUN")
        & df["LocationName"].astype(str).str[0].str.isdigit()
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01")
        and not loc.startswith("111")
        and not str(loc).upper().startswith("TUN")
        and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_damage(df):
    mask = df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
    return df[mask]

def get_missing(df):
    mask = df["LocationName"].astype(str).str.upper().eq("MISSING")
    return df[mask]

# Compute KPI DataFrames
empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)
missing_df = get_missing(filtered_inventory_df)

# ---------------- DISCREPANCY LOGIC ----------------
def find_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    local = df.copy()
    local["LocationName"] = local["LocationName"].astype(str)
    issues_by_loc = {}

    duplicates = local.groupby("LocationName").size()
    for loc, n in duplicates[duplicates > 1].items():
        if not loc[0].isdigit(): continue
        issues_by_loc.setdefault(loc, []).append(f"Multiple pallets in same location ({n} pallets)")

    for _, row in local.iterrows():
        loc = str(row["LocationName"])
        qty = row["Qty"]
        if loc.endswith("01") and qty > 5 and not loc.startswith("111") and not loc.upper().startswith("TUN") and loc[0].isdigit():
            issues_by_loc.setdefault(loc, []).append("Partial bin exceeds max capacity (Qty > 5)")
        if loc.isnumeric() and not loc.endswith("01") and (qty < 6 or qty > 15):
            issues_by_loc.setdefault(loc, []).append("Partial pallet needs to be moved to partial location")

    rows = []
    for loc, issues in issues_by_loc.items():
        details = local[local["LocationName"] == loc]
        for issue in sorted(set(issues)):
            for _, drow in details.iterrows():
                rows.append({
                    "Location": loc,
                    "Issue": issue,
                    "Qty": drow.get("Qty", ""),
                    "WarehouseSku": drow.get("WarehouseSku", ""),
                    "PalletId": drow.get("PalletId", ""),
                    "CustomerLotReference": drow.get("CustomerLotReference", ""),
                    "Notes": ""
                })
    return pd.DataFrame(rows)

def analyze_bulk_locations(df):
    results = []
    discrepancies = 0
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                issue = f"Too many pallets ({count} > {max_pallets})"
                discrepancies += 1
                details = df[df["LocationName"] == slot]
                for _, drow in details.iterrows():
                    results.append({
                        "Location": slot,
                        "Issue": issue,
                        "Qty": drow.get("Qty", ""),
                        "WarehouseSku": drow.get("WarehouseSku", ""),
                        "PalletId": drow.get("PalletId", ""),
                        "CustomerLotReference": drow.get("CustomerLotReference", ""),
                        "Notes": ""
                    })
    return pd.DataFrame(results), discrepancies

bulk_df, bulk_discrepancies = analyze_bulk_locations(filtered_inventory_df)
discrepancy_df = find_discrepancies(filtered_inventory_df)

# ---------------- LOGGING FUNCTION ----------------
def log_correction(location, issue, sku, pallet_id, lot, qty, notes):
    log_entry = {
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "LocationName": location,
        "Issue": issue,
        "Correction": "Marked Corrected",
        "WarehouseSku": sku,
        "PalletId": pallet_id,
        "CustomerLotReference": lot,
        "Qty": qty,
        "Notes": notes
    }
    log_df = pd.DataFrame([log_entry])
    if os.path.exists(log_file):
        log_df.to_csv(log_file, mode="a", header=False, index=False)
    else:
        log_df.to_csv(log_file, index=False)

# ---------------- UI ----------------
st.markdown("## 📦 Bin Helper Dashboard")

# KPI Cards
kpi_data = [
    {"title": "Empty Bins", "value": f"QTY {len(empty_bins_view_df)}", "icon": "📦"},
    {"title": "Full Pallet Bins", "value": f"QTY {len(full_pallet_bins_df)}", "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": f"QTY {len(empty_partial_bins_df)}", "icon": "🟨"},
    {"title": "Partial Bins", "value": f"QTY {len(partial_bins_df)}", "icon": "🟥"},
    {"title": "Damages", "value": f"QTY {int(damage_df['Qty'].sum())}", "icon": "🛠️"},
    {"title": "Missing", "value": f"QTY {int(missing_df['Qty'].sum())}", "icon": "❓"},
    {"title": "Discrepancies", "value": f"QTY {len(discrepancy_df)}", "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": f"QTY {bulk_discrepancies}", "icon": "📦"}
]
cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]

if st.session_state.active_view == "Empty Bins":
    st.dataframe(empty_bins_view_df)
elif st.session_state.active_view == "Full Pallet Bins":
    st.dataframe(full_pallet_bins_df[columns_to_show])
elif st.session_state.active_view == "Empty Partial Bins":
    st.dataframe(empty_partial_bins_df)
elif st.session_state.active_view == "Partial Bins":
    st.dataframe(partial_bins_df[columns_to_show])
elif st.session_state.active_view == "Damages":
    st.dataframe(damage_df[columns_to_show])
elif st.session_state.active_view == "Missing":
    st.dataframe(missing_df[columns_to_show])
elif st.session_state.active_view == "Discrepancies":
    filtered_df = discrepancy_df.copy()
    if search_location:
        filtered_df = filtered_df[filtered_df["Location"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    gb.configure_column("Location", rowGroup=True, hide=False)
    gb.configure_column("Issue", rowGroup=True, hide=False)
    gb.configure_side_bar()
    grid_options = gb.build()

    grid_response = AgGrid(
        filtered_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        allow_unsafe_jscode=True,
        theme="material",
        key="discrepancy_grid"
    )

    selected_rows = grid_response.get("selected_rows", [])
    if selected_rows is not None and len(selected_rows) > 0 and st.button("✔ Apply Selected Discrepancy Corrections"):
        for row in selected_rows:
            log_correction(row["Location"], row["Issue"], row["WarehouseSku"], row["PalletId"],
                           row["CustomerLotReference"], row["Qty"], row["Notes"])
        st.success(f"✅ {len(selected_rows)} corrections logged.")

elif st.session_state.active_view == "Bulk Discrepancies":
    filtered_bulk_df = bulk_df.copy()
    if search_location:
        filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_bulk_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    gb.configure_column("Location", rowGroup=True, hide=False)
    gb.configure_column("Issue", rowGroup=True, hide=False)
    gb.configure_side_bar()
    grid_options = gb.build()

    grid_response = AgGrid(
        filtered_bulk_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        allow_unsafe_jscode=True,
        theme="material",
        key="bulk_grid"
    )

    selected_rows = grid_response.get("selected_rows", [])
    if selected_rows is not None and len(selected_rows) > 0 and st.button("✔ Apply Selected Bulk Corrections"):
        for row in selected_rows:
            log_correction(row["Location"], row["Issue"], row["WarehouseSku"], row["PalletId"],
                           row["CustomerLotReference"], row["Qty"], row["Notes"])
        st.success(f"✅ {len(selected_rows)} bulk corrections logged.")