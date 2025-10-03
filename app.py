import os
import pandas as pd
import streamlit as st
import requests
from io import BytesIO

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# -------------------- SIDEBAR --------------------
st.sidebar.title("📦 Bin Helper")
st.sidebar.markdown("### 📁 Upload Required Files")

uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"])
uploaded_master = st.sidebar.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"])

# Default file paths
DEFAULT_INVENTORY_PATH = "ON_HAND_INVENTORY.xlsx"
DEFAULT_MASTER_PATH = "Empty Bin Formula.xlsx"

# Save uploaded files permanently
if uploaded_inventory:
    with open(DEFAULT_INVENTORY_PATH, "wb") as f:
        f.write(uploaded_inventory.getbuffer())
    st.sidebar.success(f"✅ Inventory file saved as default: {DEFAULT_INVENTORY_PATH}")

if uploaded_master:
    with open(DEFAULT_MASTER_PATH, "wb") as f:
        f.write(uploaded_master.getbuffer())
    st.sidebar.success(f"✅ Master file saved as default: {DEFAULT_MASTER_PATH}")

# GitHub fallback URL
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"

# -------------------- LOAD INVENTORY FILE --------------------
try:
    if os.path.exists(DEFAULT_INVENTORY_PATH):
        st.sidebar.info(f"📂 Using local file: {DEFAULT_INVENTORY_PATH}")
        inventory_dict = pd.read_excel(DEFAULT_INVENTORY_PATH, sheet_name=None, engine="openpyxl")
    else:
        st.sidebar.info("🌐 Using GitHub fallback file")
        response = requests.get(inventory_url)
        response.raise_for_status()
        inventory_dict = pd.read_excel(BytesIO(response.content), sheet_name=None, engine="openpyxl")
except Exception as e:
    st.error(f"❌ Failed to load ON_HAND_INVENTORY.xlsx: {e}")
    st.stop()

inventory_df = list(inventory_dict.values())[0]

# -------------------- LOAD MASTER LOCATIONS --------------------
try:
    if os.path.exists(DEFAULT_MASTER_PATH):
        st.sidebar.info(f"📂 Using local file: {DEFAULT_MASTER_PATH}")
        master_locations_df = pd.read_excel(DEFAULT_MASTER_PATH, sheet_name="Master Locations", engine="openpyxl")
    else:
        st.error("❌ Empty Bin Formula.xlsx not found. Please upload it.")
        st.stop()
except Exception as e:
    st.error(f"❌ Failed to load Empty Bin Formula.xlsx: {e}")
    st.stop()

# -------------------- DATA PREP --------------------
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}
slot_ranges = {"A": 59, "B": 64, "C": 64, "D": 64, "E": 64, "F": 64, "G": 64, "H": 64, "I": 64}
future_bulk_zones = ["A", "B", "I"]

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
        & ~df["LocationName"].astype(str).str[0].isin(bulk_rules.keys())
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN") and str(loc)[0] not in bulk_rules.keys()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_damage(df):
    mask = df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
    return df[mask]

def get_missing(df):
    mask = df["LocationName"].astype(str).str.upper().eq("MISSING")
    return df[mask]

def find_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["LocationName", "Qty", "Issue"])
    local = df.copy()
    local["LocationName"] = local["LocationName"].astype(str)
    issues_by_loc = {}
    duplicates = local.groupby("LocationName").size()
    for loc, n in duplicates[duplicates > 1].items():
        loc_u = str(loc).upper()
        if loc_u not in ["DAMAGE", "IBDAMAGE", "MISSING"] and (str(loc)[0] not in bulk_rules.keys()):
            issues_by_loc.setdefault(loc, []).append(f"Multiple pallets in same location ({n} pallets)")
    for _, row in local.iterrows():
        loc = str(row["LocationName"])
        qty = row["Qty"]
        loc_u = loc.upper()
        if (
            loc.endswith("01")
            and not loc.startswith("111")
            and not loc_u.startswith("TUN")
            and (loc[0] not in bulk_rules.keys())
        ):
            if qty > 5:
                issues_by_loc.setdefault(loc, []).append("Partial bin exceeds max capacity (Qty > 5)")
        if loc.isnumeric() and ((not loc.endswith("01")) or loc.startswith("111")):
            if qty < 6 or qty > 15:
                issues_by_loc.setdefault(loc, []).append("Full pallet bin outside expected range (6-15)")
        if loc and (loc[0] in future_bulk_zones) and qty > 0:
            issues_by_loc.setdefault(loc, []).append("Inventory found in future bulk location")
    rows = []
    for loc, issues in issues_by_loc.items():
        issues = sorted(set(issues))
        qty_sum = int(local.loc[local["LocationName"] == loc, "Qty"].sum())
        for issue in issues:
            rows.append({"LocationName": loc, "Qty": qty_sum, "Issue": issue})
    return pd.DataFrame(rows, columns=["LocationName", "Qty", "Issue"])

def analyze_bulk_locations(df):
    results = []
    empty_locations = 0
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
        all_slots = [f"{letter}{str(i).zfill(3)}" for i in range(1, slot_ranges[letter])]
        for slot in all_slots:
            if slot not in slot_counts:
                empty_locations += 1
                results.append({
                    "Location": slot,
                    "Current Pallets": 0,
                    "Max Allowed": max_pallets,
                    "Issue": ""
                })
    return pd.DataFrame(results), empty_locations, discrepancies

bulk_df, bulk_empty_locations, bulk_discrepancies = analyze_bulk_locations(bulk_inventory_df)
bulk_df["Issue"] = bulk_df["Issue"].fillna("").astype(str).str.strip()  # ✅ Normalize Issue column

bulk_locations_count = bulk_inventory_df[
    bulk_inventory_df["LocationName"].astype(str).str[0].isin(bulk_rules.keys()) &
    (bulk_inventory_df["Qty"] > 0)
]["LocationName"].nunique()
bulk_total_qty = int(bulk_inventory_df[
    bulk_inventory_df["LocationName"].astype(str).str[0].isin(bulk_rules.keys()) &
    (bulk_inventory_df["Qty"] > 0)
]["Qty"].sum())

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]
discrepancy_df = find_discrepancies(filtered_inventory_df)

# -------------------- UI --------------------
st.markdown("## 📦 Bin Helper Dashboard")

# KPI cards
kpi_cols = st.columns(4)
kpi_cols[0].metric("Empty Bins", len(empty_bins_view_df))
kpi_cols[1].metric("Full Pallet Bins", len(full_pallet_bins_df))
kpi_cols[2].metric("Empty Partial Bins", len(empty_partial_bins_df))
kpi_cols[3].metric("Partial Bins", len(partial_bins_df))

kpi_cols2 = st.columns(4)
kpi_cols2[0].metric("Damaged Qty", int(damage_df["Qty"].sum()))
kpi_cols2[1].metric("Missing Qty", int(missing_df["Qty"].sum()))
kpi_cols2[2].metric("Discrepancies", len(discrepancy_df))
kpi_cols2[3].metric("Bulk Discrepancies", bulk_discrepancies)

# Tabs
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "Empty Bins", "Full Pallet Bins", "Empty Partial Bins", "Partial Bins",
    "Damages", "Missing", "Discrepancies", "Bulk Locations", "Bulk Discrepancies"
])

with tab1:
    st.subheader("📦 Empty Bins")
    st.dataframe(empty_bins_view_df)

with tab2:
    st.subheader("🟩 Full Pallet Bins")
    st.dataframe(full_pallet_bins_df)

with tab3:
    st.subheader("🟨 Empty Partial Bins")
    st.dataframe(empty_partial_bins_df)

with tab4:
    st.subheader("🟥 Partial Bins")
    st.dataframe(partial_bins_df)

with tab5:
    st.subheader("🛠️ Damaged Inventory")
    st.dataframe(damage_df)

with tab6:
    st.subheader("❓ Missing Inventory")
    st.dataframe(missing_df)

with tab7:
    st.subheader("⚠️ Discrepancies")
    st.dataframe(discrepancy_df)

with tab8:
    st.subheader("📦 Bulk Locations")
    st.dataframe(bulk_df)

# ✅ FIXED TAB 9 WITH DEBUG
with tab9:
    st.subheader("⚠️ Bulk Discrepancies")
    q = st.text_input("Search bulk slot", "", placeholder="Type a bulk slot (e.g., A012, I032)")

    bulk_disc_view = bulk_df[bulk_df["Issue"] != ""]

    # Debug info
    st.caption(f"Debug: Total rows in bulk_df = {len(bulk_df)}, Rows with issues = {len(bulk_disc_view)}")

    if q:
        bulk_disc_view = bulk_disc_view[
            bulk_disc_view["Location"].astype(str).str.contains(q, case=False, na=False, regex=False)
        ]

    if bulk_disc_view.empty:
        st.warning("✅ No bulk discrepancies found for the current filter.")
    else:
        st.dataframe(bulk_disc_view, use_container_width=True)