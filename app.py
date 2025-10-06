import streamlit as st
import pandas as pd
import numpy as np

# Page configuration
st.set_page_config(page_title="Bin Helper Dashboard", layout="wide", initial_sidebar_state="expanded")

# Sidebar refresh controls
st.sidebar.title("🔄 Refresh Controls")
auto_refresh = st.sidebar.checkbox("Auto-refresh every 5 minutes", value=False)
if st.sidebar.button("Manual Refresh"):
    st.experimental_rerun()

# Load inventory data from ON_HAND_INVENTORY.xlsx
inventory_df = pd.read_excel("ON_HAND_INVENTORY.xlsx", engine="openpyxl")
inventory_df["LocationName"] = inventory_df["LocationName"].astype(str)
inventory_df["Qty"] = pd.to_numeric(inventory_df["Qty"], errors="coerce").fillna(0)
inventory_df["PalletId"] = inventory_df["PalletId"].astype(str)
filtered_inventory_df = inventory_df[~inventory_df["LocationName"].str.startswith("ZZ")]

# Load master location data from Empty Bin Formula.xlsx
master_df = pd.read_excel("Empty Bin Formula.xlsx", sheet_name="Master Locations", engine="openpyxl")
master_df["LocationName"] = master_df["LocationName"].astype(str)

# Define bulk zone rules
bulk_rules = {zone: 8 for zone in "ABCDEFGHI"}

# Analyze bulk locations
def analyze_bulk_rows(df):
    bulk_rows = df[df["LocationName"].str.match(r"^[A-I]\d{2}$")].copy()
    bulk_rows["Zone"] = bulk_rows["LocationName"].str[0]
    bulk_rows["PalletCount"] = bulk_rows.groupby("LocationName")["PalletId"].transform("count")
    bulk_rows = bulk_rows.drop_duplicates(subset=["LocationName"])
    bulk_rows["MaxAllowed"] = bulk_rows["Zone"].map(bulk_rules)
    bulk_rows["Discrepancy"] = bulk_rows["PalletCount"] > bulk_rows["MaxAllowed"]
    bulk_rows["DiscrepancyReason"] = np.where(
        bulk_rows["PalletCount"] > bulk_rows["MaxAllowed"],
        "Too many pallets",
        ""
    )
    return bulk_rows

bulk_locations_df = analyze_bulk_rows(filtered_inventory_df)
bulk_discrepancies_df = bulk_locations_df[bulk_locations_df["Discrepancy"] == True]

# Identify empty bulk locations
all_bulk_locations = [f"{zone}{str(i).zfill(2)}" for zone in bulk_rules.keys() for i in range(1, 21)]
occupied_bulk_locations = bulk_locations_df["LocationName"].unique().tolist()
empty_bulk_locations = [loc for loc in all_bulk_locations if loc not in occupied_bulk_locations]
empty_bulk_locations_df = pd.DataFrame({"LocationName": empty_bulk_locations})

# Identify full pallet bins
full_pallet_bins_df = filtered_inventory_df[
    filtered_inventory_df["LocationName"].str.startswith("111") &
    ~filtered_inventory_df["LocationName"].str.endswith("01") &
    (filtered_inventory_df["Qty"] >= 6) & (filtered_inventory_df["Qty"] <= 15)
]

# Identify partial bins
partial_bins_df = filtered_inventory_df[
    filtered_inventory_df["LocationName"].str.endswith("01") &
    ~filtered_inventory_df["LocationName"].str.startswith("111") &
    ~filtered_inventory_df["LocationName"].str.contains("TUN") &
    filtered_inventory_df["LocationName"].str[0].str.isdigit()
]

# Identify damages and missing
damage_df = filtered_inventory_df[filtered_inventory_df["LocationName"].str.contains("DAMAGE")]
ibdamage_df = filtered_inventory_df[filtered_inventory_df["LocationName"].str.contains("IBDAMAGE")]
missing_df = filtered_inventory_df[filtered_inventory_df["LocationName"].str.contains("MISSING")]

# Identify empty bins
master_locations = master_df["LocationName"].dropna().astype(str).unique().tolist()
occupied_locations = filtered_inventory_df["LocationName"].dropna().astype(str).unique().tolist()
empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})

# Identify empty partial bins
master_partial_candidates = master_df[
    master_df["LocationName"].str.endswith("01") &
    ~master_df["LocationName"].str.startswith("111") &
    ~master_df["LocationName"].str.contains("TUN") &
    master_df["LocationName"].str[0].str.isdigit()
]["LocationName"].dropna().astype(str).unique().tolist()

occupied_partial_locations = partial_bins_df["LocationName"].dropna().astype(str).unique().tolist()
empty_partial_bins_df = pd.DataFrame({"LocationName": [loc for loc in master_partial_candidates if loc not in occupied_partial_locations]})

# Dashboard header
st.title("📦 Bin Helper Dashboard")

# KPI cards
col1, col2, col3, col4 = st.columns(4)
col1.metric("🟦 Full Pallet Bins", len(full_pallet_bins_df))
col2.metric("🟨 Partial Bins", len(partial_bins_df))
col3.metric("📭 Empty Bins", len(empty_bins_view_df))
col4.metric("⚠️ Missing", len(missing_df))

# Sidebar navigation
view = st.sidebar.radio("📊 Select View", [
    "Dashboard",
    "Empty Bins",
    "Full Pallet Bins",
    "Empty Partial Bins",
    "Partial Bins",
    "Damages",
    "Missing",
    "Bulk Locations",
    "Bulk Discrepancies",
    "Empty Bulk Locations"
])

# View rendering
if view == "Dashboard":
    st.subheader("📊 Inventory Overview")
    st.dataframe(filtered_inventory_df)

elif view == "Empty Bins":
    st.subheader("📭 Empty Bins")
    st.dataframe(empty_bins_view_df)

elif view == "Full Pallet Bins":
    st.subheader("🟦 Full Pallet Bins")
    st.dataframe(full_pallet_bins_df)

elif view == "Empty Partial Bins":
    st.subheader("📭 Empty Partial Bins")
    st.dataframe(empty_partial_bins_df)

elif view == "Partial Bins":
    st.subheader("🟨 Partial Bins")
    st.dataframe(partial_bins_df)

elif view == "Damages":
    st.subheader("🟥 Damaged Bins")
    st.dataframe(pd.concat([damage_df, ibdamage_df]))

elif view == "Missing":
    st.subheader("⚠️ Missing Bins")
    st.dataframe(missing_df)

elif view == "Bulk Locations":
    st.subheader("📦 Bulk Locations")
    st.dataframe(bulk_locations_df)

elif view == "Bulk Discrepancies":
    st.subheader("🚨 Bulk Discrepancies")
    st.dataframe(bulk_discrepancies_df)

elif view == "Empty Bulk Locations":
    st.subheader("📭 Empty Bulk Locations")
