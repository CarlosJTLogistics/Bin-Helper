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

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"
log_file = "correction_log.csv"

# ---------------- SIDEBAR ----------------
st.sidebar.title("📦 Bin Helper")
search_location = st.sidebar.text_input("🔍 Filter by Location")

# Correction Log
correction_df = pd.DataFrame()
if os.path.exists(log_file):
    correction_df = pd.read_csv(log_file)
    st.sidebar.markdown("### 📋 Correction Log")
    st.sidebar.dataframe(correction_df, use_container_width=True)
    st.sidebar.download_button("⬇️ Download Correction Log", correction_df.to_csv(index=False), "correction_log.csv", "text/csv")

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
    df_out = pd.DataFrame(rows)

    if not correction_df.empty:
        corrected_pairs = set(zip(correction_df["LocationName"], correction_df["Issue"]))
        df_out = df_out[~df_out.apply(lambda x: (x["Location"], x["Issue"]) in corrected_pairs, axis=1)]

    return df_out

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
    df_out = pd.DataFrame(results)
    if not correction_df.empty:
        corrected_pairs = set(zip(correction_df["LocationName"], correction_df["Issue"]))
        df_out = df_out[~df_out.apply(lambda x: (x["Location"], x["Issue"]) in corrected_pairs, axis=1)]
    return df_out, discrepancies

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

tabs = ["Discrepancies", "Bulk Discrepancies"]
selected_tab = st.selectbox("Select View", tabs)

if selected_tab == "Discrepancies":
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

elif selected_tab == "Bulk Discrepancies":
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