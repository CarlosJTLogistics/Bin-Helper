import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime

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
# Inline CSS Themes with Hover Animations
# ======================
def inject_kpi_theme(theme: str):
    css = """
    <style>
    .kpi-surface .stButton>button {
        width: 100% !important;
        text-align: left !important;
        padding: 16px 18px !important;
        border-radius: 14px !important;
        white-space: pre-line !important;
        font-size: 1.9rem !important;
        font-weight: 800 !important;
        line-height: 1.15 !important;
        transition: all 0.3s ease-in-out !important;
        transform: scale(1) !important;
    }
    .kpi-surface .stButton>button::first-line {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        color: #6b7280 !important;
    }
    .kpi-surface .stButton>button:hover {
        transform: scale(1.03) translateY(-2px) !important;
        box-shadow: 0 14px 28px rgba(0,0,0,.12) !important;
    }
    .main .block-container { padding-top: 1rem; }
    .kpi-surface .stButton { margin-bottom: 0.5rem; }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

# ======================
# Sidebar
# ======================
st.sidebar.title("📦 Bin Helper")

# Theme selector
theme_options = ["Metallic Silver (Blue Outline)", "Neutral Light", "Dark Slate", "Legacy"]
theme_choice = st.sidebar.radio("Theme", theme_options)
inject_kpi_theme(theme_choice)

# Refresh button
if st.sidebar.button("🔄 Refresh Now"):
    st.query_params["refresh"] = str(int(datetime.now().timestamp()))
    st.rerun()

last_refresh = st.sidebar.empty()

# File uploaders
st.sidebar.markdown("### 📁 Upload Required Files")
uploaded_inventory = st.sidebar.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"])
uploaded_master = st.sidebar.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"])

# Add download link for sample Empty Bin Formula.xlsx
sample_file_path = "Empty Bin Formula.xlsx"
if os.path.exists(sample_file_path):
    with open(sample_file_path, "rb") as f:
        st.sidebar.download_button("📥 Download Sample Empty Bin Formula.xlsx", f, file_name="Empty Bin Formula.xlsx")

if not uploaded_inventory:
    st.warning("Please upload ON_HAND_INVENTORY.xlsx to proceed.")
    st.stop()

# ======================
# Read uploaded files
# ======================
inventory_dict = pd.read_excel(uploaded_inventory, sheet_name=None, engine="openpyxl")
inventory_df = list(inventory_dict.values())[0]

if uploaded_master:
    master_locations_df = pd.read_excel(uploaded_master, sheet_name="Master Locations", engine="openpyxl")
else:
    master_locations_df = pd.read_excel(sample_file_path, sheet_name="Master Locations", engine="openpyxl")

# Normalize numeric types
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)

# ======================
# Business Rules
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
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not loc.upper().startswith("TUN")
    ]
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
# Filters
# ======================
st.sidebar.markdown("### 🔎 Filters")
sku_list = ["All"] + sorted(filtered_inventory_df["WarehouseSku"].dropna().astype(str).unique().tolist())
lot_list = ["All"] + sorted(filtered_inventory_df["CustomerLotReference"].dropna().astype(str).unique().tolist())
pallet_list = ["All"] + sorted(filtered_inventory_df["PalletId"].dropna().astype(str).unique().tolist())

sku_filter = st.sidebar.selectbox("SKU", sku_list)
lot_filter = st.sidebar.selectbox("LOT Number", lot_list)
pallet_filter = st.sidebar.selectbox("Pallet ID", pallet_list)

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df
    if sku_filter != "All":
        out = out[out["WarehouseSku"].astype(str) == sku_filter]
    if lot_filter != "All":
        out = out[out["CustomerLotReference"].astype(str) == lot_filter]
    if pallet_filter != "All":
        out = out[out["PalletId"].astype(str) == pallet_filter]
    return out

# ======================
# Navigation state
# ======================
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = "Empty Bins"

def kpi_card(title: str, value: int, tab_name: str, icon: str = "", key: str | None = None):
    label = f"{(icon + ' ') if icon else ''}{title}\n{value:,}"
    if st.button(label, key=key or f"kpi_{tab_name}", use_container_width=True, help=f"Open {tab_name}"):
        st.session_state.selected_tab = tab_name

# ======================
# KPI Area
# ======================
st.markdown("## 📦 Bin Helper")

st.markdown('<div class="kpi-surface">', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
with c1:
    kpi_card("Empty Bins", len(empty_bins_view_df), "Empty Bins", icon="📦")
with c2:
    kpi_card("Full Pallet Bins", len(full_pallet_bins_df), "Full Pallet Bins", icon="🟩")
with c3:
    kpi_card("Empty Partial Bins", len(empty_partial_bins_df), "Empty Partial Bins", icon="🟨")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="kpi-surface">', unsafe_allow_html=True)
c4, c5, c6 = st.columns(3)
with c4:
    kpi_card("Partial Bins", len(partial_bins_df), "Partial Bins", icon="🟥")
with c5:
    kpi_card("Damages (QTY)", damage_qty, "Damages", icon="🛠️")
with c6:
    kpi_card("Missing (QTY)", missing_qty, "Missing", icon="❓")
st.markdown("</div>", unsafe_allow_html=True)

# ======================
# Central View
# ======================
tab = st.session_state.selected_tab
st.markdown(f"### 🔍 Viewing: {tab}")

if tab == "Empty Bins":
    st.subheader("📦 Empty Bins")
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(empty_bins_view_df)

elif tab == "Full Pallet Bins":
    st.subheader("🟩 Full Pallet Bins")
    df = apply_filters(full_pallet_bins_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(df)

elif tab == "Empty Partial Bins":
    st.subheader("🟨 Empty Partial Bins")
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(empty_partial_bins_df)

elif tab == "Partial Bins":
    st.subheader("🟥 Partial Bins")
    df = apply_filters(partial_bins_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(df)

elif tab == "Damages":
    st.subheader("🛠️ Damages (DAMAGE & IBDAMAGE)")
    df = apply_filters(damage_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.metric(label="Total Damaged Qty", value=f"{damage_qty:,}")
    st.dataframe(df)

elif tab == "Missing":
    st.subheader("❓ Missing")
    df = apply_filters(missing_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.metric(label="Total Missing Qty", value=f"{missing_qty:,}")
    st.dataframe(df)

# ======================
# Footer
# ======================
