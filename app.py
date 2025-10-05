import os
import pandas as pd
import streamlit as st
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Discrepancies"

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"
log_file = "correction_log.csv"

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
        or loc_str.isdigit()
        or loc_str[0] in bulk_rules.keys()
    )

# Filter inventory for valid locations and exclude DAMAGE/IB
filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
filtered_inventory_df = filtered_inventory_df[
    ~filtered_inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"]) &
    ~filtered_inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

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
bulk_df = bulk_df[
    ~bulk_df["Location"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"]) &
    ~bulk_df["Location"].astype(str).str.upper().str.startswith("IB")
]

discrepancy_df = find_discrepancies(filtered_inventory_df)
discrepancy_df = discrepancy_df[
    ~discrepancy_df["Location"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"]) &
    ~discrepancy_df["Location"].astype(str).str.upper().str.startswith("IB")
]

# ---------------- UI ----------------
st.markdown("## 📦 Bin Helper Dashboard")
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")

search_location = st.text_input("🔍 Filter by Location")

if st.session_state.active_view == "Discrepancies":
    filtered_df = discrepancy_df.copy()
    if search_location:
        filtered_df = filtered_df[filtered_df["Location"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True, cellStyle={"white-space": "normal"})
    gb.configure_column("Location", rowGroup=True, hide=True)
    gb.configure_grid_options(enableRowGroup=True)
    gb.configure_default_column(resizable=True, wrapText=True, autoHeight=True)
    if not filtered_df.empty:
        gb.configure_side_bar()
    grid_options = gb.build()

    AgGrid(
        filtered_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        allow_unsafe_jscode=True,
        theme="material",
        key="discrepancy_grid",
        height=600,
        fit_columns_on_grid_load=True,
        use_container_width=True
    )

elif st.session_state.active_view == "Bulk Discrepancies":
    filtered_bulk_df = bulk_df.copy()
    if search_location:
        filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_bulk_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True, cellStyle={"white-space": "normal"})
    gb.configure_column("Location", rowGroup=True, hide=True)
    gb.configure_grid_options(enableRowGroup=True)
    gb.configure_default_column(resizable=True, wrapText=True, autoHeight=True)
    if not filtered_bulk_df.empty:
        gb.configure_side_bar()
    grid_options = gb.build()

    AgGrid(
        filtered_bulk_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        allow_unsafe_jscode=True,
        theme="material",
        key="bulk_grid",
        height=600,
        fit_columns_on_grid_load=True,
        use_container_width=True
    )