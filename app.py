import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime
import requests
from io import BytesIO

# Optional: Lottie animation
try:
    from streamlit_lottie import st_lottie

    def load_lottiefile(filepath: str):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            return None

    lottie_box = load_lottiefile("box_animation.json")
except ImportError:
    st_lottie = None
    lottie_box = None

# Page configuration
st.set_page_config(page_title="Bin Helper", layout="wide")

# ======================
# Sidebar
# ======================
st.sidebar.title("📦 Bin Helper")

# File uploaders
st.sidebar.markdown("### 📁 Upload Required Files")
uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"])
uploaded_balances = st.sidebar.file_uploader("Upload INVENTORY_BALANCES.xlsx", type=["xlsx"])
uploaded_master = st.sidebar.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"])

# GitHub fallback URLs
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
balances_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/INVENTORY_BALANCES.xlsx"

# ======================
# Load ON_HAND_INVENTORY.xlsx
# ======================
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

# ======================
# Load INVENTORY_BALANCES.xlsx
# ======================
try:
    if uploaded_balances:
        balances_df = pd.read_excel(uploaded_balances, engine="openpyxl")
    else:
        response = requests.get(balances_url)
        response.raise_for_status()
        balances_df = pd.read_excel(BytesIO(response.content), engine="openpyxl")
except Exception as e:
    st.error(f"❌ Failed to load INVENTORY_BALANCES.xlsx: {e}")
    st.stop()

# ======================
# Load Master Locations
# ======================
sample_file_path = "Empty Bin Formula.xlsx"
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

# ======================
# Business Rules (same as before)
# ======================
def is_valid_location(loc):
    if pd.isna(loc):
        return False
    loc_str = str(loc).upper()
    return (loc_str.startswith("TUN") or loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"] or loc_str.isdigit())

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_locations_df.iloc[1:, 0].dropna().astype(str).unique())

# Empty bins: in master but not occupied
empty_bins = sorted(master_locations - occupied_locations)
empty_bins_view_df = pd.DataFrame({"LocationName": empty_bins})

def exclude_damage_missing(df):
    return df[~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[df["PalletCount"] == 1]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN")
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [loc for loc in master_locs if loc.endswith("01") and not loc.startswith("111") and not loc.upper().startswith("TUN")]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_damage(df):
    mask = df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
    return df[mask]

def get_missing(df):
    mask = df["LocationName"].astype(str).str.upper().eq("MISSING")
    return df[mask]

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]

damage_qty = int(damage_df["Qty"].sum()) if not damage_df.empty else 0
missing_qty = int(missing_df["Qty"].sum()) if not missing_df.empty else 0

# ======================
# KPI Cards and UI (same as before)
# ======================
st.markdown("## 📦 Bin Helper")
st.write("✅ ON_HAND_INVENTORY and INVENTORY_BALANCES loaded successfully!")

# Show previews for debugging
with st.expander("Preview Data"):
    st.subheader("ON_HAND_INVENTORY.xlsx")
    st.dataframe(inventory_df.head())
    st.subheader("INVENTORY_BALANCES.xlsx")
    st.dataframe(balances_df.head())