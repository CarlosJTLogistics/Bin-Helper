import os
import pandas as pd
import streamlit as st
from datetime import datetime

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Empty Bins"

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"

# ---------------- SIDEBAR ----------------
st.sidebar.title("📦 Bin Helper")

# Search Filters
st.sidebar.markdown("### 🔍 Search Filter")
search_location = st.sidebar.text_input("Location Name")
search_pallet = st.sidebar.text_input("Pallet ID")
search_lot = st.sidebar.text_input("Customer Lot Reference")
search_sku = st.sidebar.text_input("Warehouse SKU")

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
log_file = "correction_log.csv"
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
    if df is None or df.empty:
        return pd.DataFrame(columns=["LocationName", "Qty", "Issue"])
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
            rows.append({"LocationName": loc, "Qty": qty_sum, "Issue": issue})
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
                "Issue": issue
            })
    df_out = pd.DataFrame(results)
    if not correction_df.empty:
        corrected_pairs = set(zip(correction_df["LocationName"], correction_df["Issue"]))
        df_out = df_out[~df_out.apply(lambda x: (x["Location"], x["Issue"]) in corrected_pairs, axis=1)]
    return df_out, discrepancies

bulk_df, bulk_discrepancies = analyze_bulk_locations(bulk_inventory_df)
bulk_df["Issue"] = bulk_df["Issue"].fillna("").astype(str).str.strip()

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]
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
    if os.path.exists("correction_log.csv"):
        log_df.to_csv("correction_log.csv", mode="a", header=False, index=False)
    else:
        log_df.to_csv("correction_log.csv", index=False)

# ---------------- UI ----------------
st.markdown("## 📦 Bin Helper Dashboard")

# KPI Cards
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": int(damage_df["Qty"].sum()), "icon": "🛠️"},
    {"title": "Missing", "value": int(missing_df["Qty"].sum()), "icon": "❓"},
    {"title": "Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": bulk_discrepancies, "icon": "📦"}
]

cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']}\\n{item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# Display Selected View
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")

if st.session_state.active_view == "Empty Bins":
    st.dataframe(empty_bins_view_df)
elif st.session_state.active_view == "Full Pallet Bins":
    st.dataframe(full_pallet_bins_df)
elif st.session_state.active_view == "Empty Partial Bins":
    st.dataframe(empty_partial_bins_df)
elif st.session_state.active_view == "Partial Bins":
    st.dataframe(partial_bins_df)
elif st.session_state.active_view == "Damages":
    st.dataframe(damage_df)
elif st.session_state.active_view == "Missing":
    st.dataframe(missing_df)
elif st.session_state.active_view == "Discrepancies":
    st.markdown("#### Drill-down Details")
    filtered_df = discrepancy_df.copy()
    if search_location:
        filtered_df = filtered_df[filtered_df["LocationName"].str.contains(search_location, case=False, na=False)]

    for loc in filtered_df["LocationName"].unique():
        loc_issues = filtered_df[filtered_df["LocationName"] == loc]
        with st.expander(f"📍 Location: {loc} — {len(loc_issues)} issue(s)"):
            details = filtered_inventory_df[filtered_inventory_df["LocationName"] == loc]
            st.write("**Inventory Details:**")
            for _, drow in details.iterrows():
                st.write(f"• SKU: `{drow.get('WarehouseSku','')}` | Pallet ID: `{drow.get('PalletId','')}` | "
                         f"Lot: `{drow.get('CustomerLotReference','')}` | Qty: `{drow.get('Qty','')}`")

            selected_issues = []
            st.markdown("**Issue** | **Qty** | **Notes** | **Select**")
            for idx, row in loc_issues.iterrows():
                issue = row["Issue"]
                qty = row["Qty"]
                col1, col2, col3, col4 = st.columns([3, 1, 3, 1])
                with col1:
                    st.write(issue)
                with col2:
                    st.write(qty)
                with col3:
                    notes = st.text_input("", key=f"note_{loc}_{idx}")
                with col4:
                    if st.checkbox("", key=f"chk_{loc}_{idx}"):
                        selected_issues.append((issue, notes))

            if selected_issues and st.button("✔ Apply Selected Corrections", key=f"apply_{loc}"):
                for issue, notes in selected_issues:
                    for _, drow in details.iterrows():
                        log_correction(loc, issue, drow.get("WarehouseSku",""), drow.get("PalletId",""),
                                       drow.get("CustomerLotReference",""), drow.get("Qty",""), notes)
                st.success(f"✅ {len(selected_issues)} corrections applied for {loc}")
elif st.session_state.active_view == "Bulk Discrepancies":
    st.markdown("#### Drill-down Details")
    filtered_bulk_df = bulk_df[bulk_df["Issue"] != ""].copy()
    if search_location:
        filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].str.contains(search_location, case=False, na=False)]

    for loc in filtered_bulk_df["Location"].unique():
        loc_issues = filtered_bulk_df[filtered_bulk_df["Location"] == loc]
        with st.expander(f"📍 Bulk Location: {loc} — {len(loc_issues)} issue(s)"):
            details = filtered_inventory_df[filtered_inventory_df["LocationName"] == loc]
            st.write("**Inventory Details:**")
            for _, drow in details.iterrows():
                st.write(f"• SKU: `{drow.get('WarehouseSku','')}` | Pallet ID: `{drow.get('PalletId','')}` | "
                         f"Lot: `{drow.get('CustomerLotReference','')}` | Qty: `{drow.get('Qty','')}`")

            selected_bulk_issues = []
            st.markdown("**Issue** | **Qty** | **Notes** | **Select**")
            for idx, row in loc_issues.iterrows():
                issue = row["Issue"]
                qty = row["Current Pallets"]
                col1, col2, col3, col4 = st.columns([3, 1, 3, 1])
                with col1:
                    st.write(issue)
                with col2:
                    st.write(qty)
                with col3:
                    notes = st.text_input("", key=f"bulk_note_{loc}_{idx}")
                with col4:
                    if st.checkbox("", key=f"bulk_chk_{loc}_{idx}"):
                        selected_bulk_issues.append((issue, notes))

            if selected_bulk_issues and st.button("✔ Apply Selected Bulk Corrections", key=f"bulk_apply_{loc}"):
                for issue, notes in selected_bulk_issues:
                    for _, drow in details.iterrows():
                        log_correction(loc, issue, drow.get("WarehouseSku",""), drow.get("PalletId",""),
                                       drow.get("CustomerLotReference",""), drow.get("Qty",""), notes)
                st.success(f"✅ {len(selected_bulk_issues)} bulk corrections applied for {loc}")