import streamlit as st
import pandas as pd
import os
import csv
import requests
from streamlit_lottie import st_lottie  # Install with: pip install streamlit-lottie

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide", initial_sidebar_state="expanded")

# ---------------- APP VERSION ----------------
APP_VERSION = "v1.3.1"

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Dashboard"
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "refresh_triggered" not in st.session_state:
    st.session_state.refresh_triggered = False

# ---------------- AUTO REFRESH ----------------
if st.session_state.auto_refresh or st.session_state.refresh_triggered:
    st.session_state.refresh_triggered = False
    st.rerun()

# ---------------- SIDEBAR CONTROLS ----------------
st.sidebar.markdown("### 🔄 Auto Refresh")
if st.sidebar.button("🔁 Refresh Now"):
    st.session_state.refresh_triggered = True
if st.sidebar.checkbox("Enable Auto Refresh", value=st.session_state.auto_refresh):
    st.session_state.auto_refresh = True
else:
    st.session_state.auto_refresh = False

# ---------------- LOTTIE LOADER ----------------
def load_lottie(url):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_icon = load_lottie("https://assets10.lottiefiles.com/packages/lf20_jcikwtux.json")

# ---------------- LOAD DATA ----------------
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

@st.cache_data
def load_data(inventory_url, master_url):
    inventory_df = pd.read_excel(inventory_url, engine="openpyxl")
    master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    return inventory_df, master_df

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
    st.stop()

# ---------------- DATA PREP ----------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

# ---------------- BULK ROW LOGIC ----------------
def analyze_bulk_rows(df):
    df = df.copy()
    df["Zone"] = df["LocationName"].astype(str).str[0]
    bulk_locations = []
    bulk_discrepancies = []
    empty_bulk_locations = []

    for zone, max_pallets in bulk_rules.items():
        zone_df = df[df["Zone"] == zone]
        row_counts = zone_df.groupby("LocationName")["PalletId"].count()
        for location, count in row_counts.items():
            bulk_locations.append({
                "LocationName": location,
                "Zone": zone,
                "Pallets": count,
                "MaxAllowed": max_pallets
            })
            if count > max_pallets:
                bulk_discrepancies.append({
                    "LocationName": location,
                    "Zone": zone,
                    "Pallets": count,
                    "MaxAllowed": max_pallets,
                    "Issue": f"Too many pallets in {location} (Max: {max_pallets})"
                })
            elif count < max_pallets:
                empty_bulk_locations.append({
                    "LocationName": location,
                    "Zone": zone,
                    "Pallets": count,
                    "MaxAllowed": max_pallets,
                    "Issue": f"{location} has empty pallet slots (Max: {max_pallets})"
                })
    return pd.DataFrame(bulk_locations), pd.DataFrame(bulk_discrepancies), pd.DataFrame(empty_bulk_locations)

bulk_locations_df, bulk_discrepancies_df, empty_bulk_locations_df = analyze_bulk_rows(filtered_inventory_df)

# ---------------- OTHER BUSINESS LOGIC ----------------
def exclude_damage_missing(df):
    return df[
        ~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"]) &
        ~df["LocationName"].astype(str).str.upper().str.startswith("IB")
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        df["LocationName"].astype(str).str[0].str.isdigit()
    ]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        ((~df["LocationName"].astype(str).str.endswith("01")) |
         (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN") and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

master_locations = master_df["LocationName"].dropna().astype(str).unique().tolist()
occupied_locations = filtered_inventory_df["LocationName"].dropna().astype(str).unique().tolist()
empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper() == "MISSING"]

# ---------------- SIDEBAR MENU ----------------
menu = st.sidebar.radio("📂 Dashboard Menu", [
    "Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
    "Partial Bins", "Damages", "Missing", "Rack Discrepancies",
    "Bulk Locations", "Bulk Discrepancies", "Empty Bulk Locations"
])
st.session_state.active_view = menu

# ---------------- DASHBOARD VIEW ----------------
if st.session_state.active_view == "Dashboard":
    st_lottie(lottie_icon, height=150)
    st.markdown(f"<h1 style='text-align: center; color: #2E86C1;'>📊 Bin-Helper Dashboard <span style='font-size:18px; color:gray;'>({APP_VERSION})</span></h1>", unsafe_allow_html=True)

    total_bins_occupied = len(full_pallet_bins_df) + len(partial_bins_df)
    total_empty_bins = len(empty_bins_view_df) + len(empty_partial_bins_df)
    total_discrepancies = len(bulk_discrepancies_df)

    kpi_data = [
        {"title": "Total Bins Occupied", "value": total_bins_occupied, "icon": "📦"},
        {"title": "Total Empty Bins", "value": total_empty_bins, "icon": "🗑️"},
        {"title": "Bulk Locations", "value": len(bulk_locations_df), "icon": "📍"},
        {"title": "Empty Bulk Locations", "value": len(empty_bulk_locations_df), "icon": "📭"},
        {"title": "Bulk Discrepancies", "value": len(bulk_discrepancies_df), "icon": "⚠️"}
    ]
    cols = st.columns(len(kpi_data))
    for i, item in enumerate(kpi_data):
        with cols[i]:
            st.markdown(f"""
                <div style="background-color:#1f77b4; padding:20px; border-radius:10px; text-align:center; color:white;">
                    <h3>{item['icon']} {item['title']}</h3>
                    <h2>{item['value']}</h2>
                </div>
            """, unsafe_allow_html=True)

# ---------------- DISPLAY VIEWS ----------------
view_map = {
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df,
    "Bulk Locations": bulk_locations_df,
    "Bulk Discrepancies": bulk_discrepancies_df,
    "Empty Bulk Locations": empty_bulk_locations_df
}

if st.session_state.active_view != "Dashboard":
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    st.subheader(f"{st.session_state.active_view}")
