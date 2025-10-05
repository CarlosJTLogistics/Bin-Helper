import os
import pandas as pd
import streamlit as st
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

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

# ---------------- DISCREPANCY LOGIC ----------------
def find_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["LocationName", "Qty", "Issue", "Notes"])
    local = df.copy()
    local["LocationName"] = local["LocationName"].astype(str)
    issues_by_loc = {}

    duplicates = local.groupby("LocationName").size()
    for loc, n in duplicates[duplicates > 1].items():
        if not loc[0].isdigit():
            continue
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
        qty_sum = int(local.loc[local["LocationName"] == loc, "Qty"].sum())
        for issue in sorted(set(issues)):
            rows.append({"LocationName": loc, "Qty": qty_sum, "Issue": issue, "Notes": ""})
    df_out = pd.DataFrame(rows)

    if not correction_df.empty:
        corrected_pairs = set(zip(correction_df["LocationName"], correction_df["Issue"]))
        df_out = df_out[~df_out.apply(lambda x: (x["LocationName"], x["Issue"]) in corrected_pairs, axis=1)]

    return df_out

def analyze_bulk_locations(df):
    results = []
    discrepancies = 0
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            issue = ""
            if count > max_pallets:
                issue = f"Too many pallets ({count} > {max_pallets})"
                discrepancies += 1
                results.append({
                    "Location": slot,
                    "Current Pallets": count,
                    "Max Allowed": max_pallets,
                    "Issue": issue,
                    "Notes": ""
                })
    df_out = pd.DataFrame(results)
    if not correction_df.empty:
        corrected_pairs = set(zip(correction_df["LocationName"], correction_df["Issue"]))
        df_out = df_out[~df_out.apply(lambda x: (x["Location"], x["Issue"]) in corrected_pairs, axis=1)]
    return df_out, discrepancies

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

# ---------------- AGGRID TABS ----------------
if st.session_state.active_view == "Discrepancies":
    filtered_df = discrepancy_df.copy()
    if search_location:
        filtered_df = filtered_df[filtered_df["LocationName"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    grid_options = gb.build()

    grid_response = AgGrid(
        filtered_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        theme="dark",
        key="discrepancy_grid"
    )

    selected_rows = grid_response["selected_rows"]
    if selected_rows and st.button("✔ Apply Selected Discrepancy Corrections"):
        for row in selected_rows:
            details = filtered_inventory_df[filtered_inventory_df["LocationName"] == row["LocationName"]]
            for _, drow in details.iterrows():
                log_correction(row["LocationName"], row["Issue"], drow.get("WarehouseSku",""), drow.get("PalletId",""),
                               drow.get("CustomerLotReference",""), drow.get("Qty",""), row.get("Notes",""))
        st.success(f"✅ {len(selected_rows)} corrections logged.")

elif st.session_state.active_view == "Bulk Discrepancies":
    filtered_bulk_df = bulk_df[bulk_df["Issue"] != ""].copy()
    if search_location:
        filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].str.contains(search_location, case=False, na=False)]

    gb = GridOptionsBuilder.from_dataframe(filtered_bulk_df)
    gb.configure_selection("multiple", use_checkbox=True)
    gb.configure_column("Notes", editable=True)
    grid_options = gb.build()

    grid_response = AgGrid(
        filtered_bulk_df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        theme="dark",
        key="bulk_grid"
    )

    selected_rows = grid_response["selected_rows"]
    if selected_rows and st.button("✔ Apply Selected Bulk Corrections"):
        for row in selected_rows:
            details = filtered_inventory_df[filtered_inventory_df["LocationName"] == row["Location"]]
            for _, drow in details.iterrows():
                log_correction(row["Location"], row["Issue"], drow.get("WarehouseSku",""), drow.get("PalletId",""),
                               drow.get("CustomerLotReference",""), drow.get("Qty",""), row.get("Notes",""))
        st.success(f"✅ {len(selected_rows)} bulk corrections logged.")