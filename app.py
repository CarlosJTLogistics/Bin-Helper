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

# Search Filters
st.sidebar.markdown("### 🔍 Search Filter")
search_location = st.sidebar.text_input("Location Name")

# File Uploads
st.sidebar.markdown("### 📂 Upload Files")

# Inventory File
if os.path.exists(inventory_file_path):
    st.sidebar.markdown(f"✅ **Active Inventory File:** `{os.path.basename(inventory_file_path)}`")
    st.sidebar.download_button("⬇️ Download Inventory File", open(inventory_file_path, "rb"), file_name="ON_HAND_INVENTORY.xlsx")
else:
    uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"], key="inv_file")
    if uploaded_inventory:
        with open(inventory_file_path, "wb") as f:
            f.write(uploaded_inventory.getbuffer())
        st.sidebar.success("✅ Inventory file uploaded and saved.")

# Master File
if os.path.exists(master_file_path):
    st.sidebar.markdown(f"✅ **Active Master File:** `{os.path.basename(master_file_path)}`")
    st.sidebar.download_button("⬇️ Download Master File", open(master_file_path, "rb"), file_name="Empty Bin Formula.xlsx")
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
    st.sidebar.download_button(
        label="⬇️ Download Correction Log",
        data=correction_df.to_csv(index=False),
        file_name="correction_log.csv",
        mime="text/csv"
    )
else:
    st.sidebar.info("No correction log found yet.")

# ---------------- LOAD DATA ----------------
if os.path.exists(inventory_file_path):
    inventory_df = pd.read_excel(inventory_file_path, engine="openpyxl")
else:
    st.error("Please upload ON_HAND_INVENTORY.xlsx to proceed.")
    st.stop()

if os.path.exists(master_file_path):
    master_locations_df = pd.read_excel(master_file_path, sheet_name="Master Locations", engine="openpyxl")
else:
    st.error("Please upload Empty Bin Formula.xlsx to proceed.")
    st.stop()

# ---------------- DATA PREP ----------------
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def is_valid_location(loc):
    if pd.isna(loc):
        return False
    loc_str = str(loc).upper()
    return (
        loc_str.startswith("TUN")
        or loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"]
        or loc_str.isdigit()
        or loc_str[0] in bulk_rules.keys()
    )

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_locations_df.iloc[1:, 0].dropna().astype(str).unique())

bulk_inventory_df = filtered_inventory_df[
    ~filtered_inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
]

empty_bins = [
    loc for loc in master_locations
    if loc not in occupied_locations
    and not loc.endswith("01")
    and "STAGE" not in loc.upper()
    and loc.upper() not in ["DAMAGE", "IBDAMAGE", "MISSING"]
]
empty_bins_view_df = pd.DataFrame({"LocationName": empty_bins})

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

# ---------------- DISCREPANCY LOGIC ----------------
def find_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        loc = str(row["LocationName"])
        qty = row["Qty"]
        if loc.endswith("01") and qty > 5:
            rows.append({
                "Location": loc,
                "Issue": "Partial bin exceeds max capacity",
                "Qty": qty,
                "WarehouseSku": row.get("WarehouseSku", ""),
                "PalletId": row.get("PalletId", ""),
                "CustomerLotReference": row.get("CustomerLotReference", ""),
                "Notes": ""
            })
    return pd.DataFrame(rows)

def analyze_bulk_locations(df):
    rows = []
    for _, row in df.iterrows():
        loc = str(row["LocationName"])
        rows.append({
            "Location": loc,
            "Issue": "Too many pallets",
            "Current Pallets": row.get("PalletCount", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", ""),
            "Qty": row.get("Qty", ""),
            "Notes": ""
        })
    return pd.DataFrame(rows), len(rows)

bulk_df, bulk_discrepancies = analyze_bulk_locations(bulk_inventory_df)
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
    {"title": "Full Pallet Bins", "value": f"QTY {len(get_full_pallet_bins(filtered_inventory_df))}", "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": f"QTY {len(get_empty_partial_bins(master_locations, occupied_locations))}", "icon": "🟨"},
    {"title": "Partial Bins", "value": f"QTY {len(get_partial_bins(filtered_inventory_df))}", "icon": "🟥"},
    {"title": "Damages", "value": f"QTY {int(get_damage(filtered_inventory_df)['Qty'].sum())}", "icon": "🛠️"},
    {"title": "Missing", "value": f"QTY {int(get_missing(filtered_inventory_df)['Qty'].sum())}", "icon": "❓"},
    {"title": "Discrepancies", "value": f"QTY {len(discrepancy_df)}", "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": f"QTY {bulk_discrepancies}", "icon": "📦"}
]
cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# Display Selected View
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")

# ---------------- TAB CONTENT ----------------
if st.session_state.active_view == "Discrepancies":
    gb = GridOptionsBuilder.from_dataframe(discrepancy_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    gb.configure_column("Location", rowGroup=True, hide=True)
    gb.configure_side_bar()
    grid_options = gb.build()

    grid_response = AgGrid(
        discrepancy_df,
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
    gb = GridOptionsBuilder.from_dataframe(bulk_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    gb.configure_column("Location", rowGroup=True, hide=True)
    gb.configure_side_bar()
    grid_options = gb.build()

    grid_response = AgGrid(
        bulk_df,
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