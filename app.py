import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime
import requests
from io import BytesIO

# Page configuration
st.set_page_config(page_title="Bin Helper", layout="wide")

# Sidebar
st.sidebar.title("📦 Bin Helper")
st.sidebar.markdown("### 📁 Upload Required Files")
uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"])
uploaded_master = st.sidebar.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"])

# GitHub fallback URL
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
sample_file_path = "Empty Bin Formula.xlsx"

# Load ON_HAND_INVENTORY.xlsx
try:
    if uploaded_inventory:
        inventory_dict = pd.read_excel(uploaded_inventory, sheet_name=None, engine="openpyxl")
    else:
        response = requests.get(inventory_url)
        response.raise_for_status()
        inventory_dict = pd.read_excel(BytesIO(response.content), sheet_name=None, engine="openpyxl")
except Exception as e:
    st.error(f"❌ Failed to load ON_HAND_INVENTORY.xlsx: {e}")
    st.stop()

inventory_df = list(inventory_dict.values())[0]

# Load Master Locations
try:
    if uploaded_master:
        master_locations_df = pd.read_excel(uploaded_master, sheet_name="Master Locations", engine="openpyxl")
    else:
        master_locations_df = pd.read_excel(sample_file_path, sheet_name="Master Locations", engine="openpyxl")
except Exception as e:
    st.error(f"❌ Failed to load Empty Bin Formula.xlsx: {e}")
    st.stop()

# Normalize numeric types
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)

# Bulk zone definitions
bulk_rules = {
    "A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4
}
slot_ranges = {
    "A": 59, "B": 64, "C": 64, "D": 64, "E": 64, "F": 64, "G": 64, "H": 64, "I": 64
}
future_bulk_zones = ["A", "B", "I"]

# Business Rules
def is_valid_location(loc):
    if pd.isna(loc):
        return False
    loc_str = str(loc).upper()
    return (
        loc_str.startswith("TUN") or
        loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"] or
        loc_str.isdigit() or
        loc_str[0] in bulk_rules.keys()
    )

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_locations_df.iloc[1:, 0].dropna().astype(str).unique())

# Remove damage locations from bulk analysis
bulk_inventory_df = filtered_inventory_df[
    ~filtered_inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
]

# Empty Bin logic
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
        (
            (~df["LocationName"].astype(str).str.endswith("01")) |
            (df["LocationName"].astype(str).str.startswith("111"))
        ) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        ~df["LocationName"].astype(str).str[0].isin(bulk_rules.keys())
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not loc.upper().startswith("TUN") and loc[0] not in bulk_rules.keys()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_damage(df):
    mask = df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
    return df[mask]

def get_missing(df):
    mask = df["LocationName"].astype(str).str.upper().eq("MISSING")
    return df[mask]

# Discrepancy logic
def find_discrepancies(df):
    discrepancies = []
    for _, row in df.iterrows():
        loc = str(row["LocationName"])
        qty = row["Qty"]
        # Partial bin rule
        if (
            loc.endswith("01") and
            not loc.startswith("111") and
            not loc.upper().startswith("TUN") and
            loc[0] not in bulk_rules.keys()
        ):
            if qty > 5:
                discrepancies.append({
                    "LocationName": loc,
                    "Qty": qty,
                    "Issue": "Partial bin exceeds max capacity (Qty > 5)"
                })
        # Full pallet bin rule
        if (loc.isnumeric() and ((not loc.endswith("01")) or loc.startswith("111"))):
            if qty < 6 or qty > 15:
                discrepancies.append({
                    "LocationName": loc,
                    "Qty": qty,
                    "Issue": "Full pallet bin outside expected range (6-15)"
                })
        # Future bulk zones should be empty
        if loc[0] in future_bulk_zones and qty > 0:
            discrepancies.append({
                "LocationName": loc,
                "Qty": qty,
                "Issue": "Inventory found in future bulk location"
            })
    # Multi-pallet rule (ignore bulk zones)
    duplicates = df.groupby("LocationName").size()
    multi_pallet_locs = duplicates[duplicates > 1].index.tolist()
    for loc in multi_pallet_locs:
        if (
            loc.upper() not in ["DAMAGE", "IBDAMAGE", "MISSING"] and
            loc[0] not in bulk_rules.keys()
        ):
            discrepancies.append({
                "LocationName": loc,
                "Qty": None,
                "Issue": f"Multiple pallets in same location ({duplicates[loc]} pallets)"
            })
    return pd.DataFrame(discrepancies)

# Bulk Location Logic
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

# Bulk metrics
bulk_locations_count = bulk_inventory_df[
    bulk_inventory_df["LocationName"].astype(str).str[0].isin(bulk_rules.keys()) &
    (bulk_inventory_df["Qty"] > 0)
]["LocationName"].nunique()

bulk_total_qty = int(bulk_inventory_df[
    bulk_inventory_df["LocationName"].astype(str).str[0].isin(bulk_rules.keys()) &
    (bulk_inventory_df["Qty"] > 0)
]["Qty"].sum())

# Prepare other data
columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]
discrepancy_df = find_discrepancies(filtered_inventory_df)

# Filters
st.sidebar.markdown("### 🔎 Filters")
sku_list = ["All"] + sorted(filtered_inventory_df["WarehouseSku"].dropna().astype(str).unique().tolist())
lot_list = ["All"] + sorted(filtered_inventory_df["CustomerLotReference"].dropna().astype(str).unique().tolist())
pallet_list = ["All"] + sorted(filtered_inventory_df["PalletId"].dropna().astype(str).unique().tolist())
location_list = ["All"] + sorted(filtered_inventory_df["LocationName"].dropna().astype(str).unique().tolist())

sku_filter = st.sidebar.selectbox("SKU", sku_list)
lot_filter = st.sidebar.selectbox("LOT Number", lot_list)
pallet_filter = st.sidebar.selectbox("Pallet ID", pallet_list)
location_filter = st.sidebar.selectbox("Location", location_list)

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df
    if sku_filter != "All":
        out = out[out["WarehouseSku"].astype(str) == sku_filter]
    if lot_filter != "All":
        out = out[out["CustomerLotReference"].astype(str) == lot_filter]
    if pallet_filter != "All":
        out = out[out["PalletId"].astype(str) == pallet_filter]
    if location_filter != "All":
        out = out[out["LocationName"].astype(str) == location_filter]
    return out

# Navigation state
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = "Empty Bins"

# KPI card function with unique keys
def kpi_card(title: str, value: int, tab_name: str, icon: str = "", key: str = None):
    label = f"{(icon + ' ') if icon else ''}{title}\n{value:,}"
    unique_key = key or f"kpi_{tab_name}_{title.replace(' ', '_')}"
    if st.button(label, key=unique_key, use_container_width=True, help=f"Open {tab_name}"):
        st.session_state.selected_tab = tab_name

# KPI Area
st.markdown("## 📦 Bin Helper")
c1, c2, c3 = st.columns(3)
with c1:
    kpi_card("Empty Bins", len(empty_bins_view_df), "Empty Bins", icon="📦")
with c2:
    kpi_card("Full Pallet Bins", len(full_pallet_bins_df), "Full Pallet Bins", icon="🟩")
with c3:
    kpi_card("Empty Partial Bins", len(empty_partial_bins_df), "Empty Partial Bins", icon="🟨")

c4, c5, c6, c7 = st.columns(4)
with c4:
    kpi_card("Partial Bins", len(partial_bins_df), "Partial Bins", icon="🟥")
with c5:
    kpi_card("Damages (QTY)", int(damage_df["Qty"].sum()), "Damages", icon="🛠️")
with c6:
    kpi_card("Missing (QTY)", int(missing_df["Qty"].sum()), "Missing", icon="❓")
with c7:
    kpi_card("Discrepancies", len(discrepancy_df), "Discrepancies", icon="⚠️")

c8, c9, c10 = st.columns(3)
with c8:
    kpi_card("Empty Bulk Locations", bulk_empty_locations, "Bulk Locations", icon="📦")
with c9:
    kpi_card("Bulk Discrepancies", bulk_discrepancies, "Bulk Locations", icon="⚠️")
with c10:
    kpi_card("Bulk Locations", bulk_locations_count, "Bulk Locations", icon="🏗️")

# Tab content
st.markdown(f"### 🔍 Viewing: {st.session_state.selected_tab}")
tab = st.session_state.selected_tab

if tab == "Bulk Locations":
    st.subheader("📦 Bulk Locations Analysis")
    st.metric(label="Empty Bulk Locations", value=f"{bulk_empty_locations:,}")
    st.metric(label="Bulk Discrepancies", value=f"{bulk_discrepancies:,}")
    st.metric(label="Bulk Locations with Inventory", value=f"{bulk_locations_count:,}")
    st.metric(label="Total QTY in Bulk Zones", value=f"{bulk_total_qty:,}")
    st.dataframe(bulk_df[bulk_df["Issue"].astype(str).str.strip() != ""])
