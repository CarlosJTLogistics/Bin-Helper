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

# Business Rules
def is_valid_location(loc):
    if pd.isna(loc):
        return False
    loc_str = str(loc).upper()
    return (loc_str.startswith("TUN") or loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"] or loc_str.isdigit())

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_locations_df.iloc[1:, 0].dropna().astype(str).unique())

# ✅ Updated Empty Bin logic (TUN included)
empty_bins = [
    loc for loc in master_locations
    if loc not in occupied_locations
    and not loc.endswith("01")  # exclude partial bins
    and "STAGE" not in loc.upper()  # exclude staging
    and loc.upper() not in ["DAMAGE", "IBDAMAGE", "MISSING"]  # exclude damage/missing
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

# ✅ Discrepancy logic
def find_discrepancies(df):
    discrepancies = []
    
    # Partial bins rule
    for _, row in df.iterrows():
        loc = str(row["LocationName"])
        qty = row["Qty"]
        
        if loc.endswith("01") and not loc.startswith("111") and not loc.upper().startswith("TUN"):
            if qty > 5:
                discrepancies.append({
                    "LocationName": loc,
                    "Qty": qty,
                    "Issue": "Partial bin exceeds max capacity (Qty > 5)"
                })
        
        if (loc.isnumeric() and ((not loc.endswith("01")) or loc.startswith("111"))):
            if qty < 6 or qty > 15:
                discrepancies.append({
                    "LocationName": loc,
                    "Qty": qty,
                    "Issue": "Full pallet bin outside expected range (6-15)"
                })
    
    # Multi-pallet rule (excluding DAMAGE, IBDAMAGE, MISSING)
    duplicates = df.groupby("LocationName").size()
    multi_pallet_locs = duplicates[duplicates > 1].index.tolist()
    for loc in multi_pallet_locs:
        if loc.upper() not in ["DAMAGE", "IBDAMAGE", "MISSING"]:
            discrepancies.append({
                "LocationName": loc,
                "Qty": None,
                "Issue": f"Multiple pallets in same location ({duplicates[loc]} pallets)"
            })
    
    return pd.DataFrame(discrepancies)

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]
discrepancy_df = find_discrepancies(filtered_inventory_df)

damage_qty = int(damage_df["Qty"].sum()) if not damage_df.empty else 0
missing_qty = int(missing_df["Qty"].sum()) if not missing_df.empty else 0

# Filters
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

# Navigation state
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = "Empty Bins"

def kpi_card(title: str, value: int, tab_name: str, icon: str = "", key: str = None):
    label = f"{(icon + ' ') if icon else ''}{title}\n{value:,}"
    if st.button(label, key=key or f"kpi_{tab_name}", use_container_width=True, help=f"Open {tab_name}"):
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
    kpi_card("Damages (QTY)", damage_qty, "Damages", icon="🛠️")
with c6:
    kpi_card("Missing (QTY)", missing_qty, "Missing", icon="❓")
with c7:
    kpi_card("Discrepancies", len(discrepancy_df), "Discrepancies", icon="⚠️")

# Tab content
st.markdown(f"### 🔍 Viewing: {st.session_state.selected_tab}")
tab = st.session_state.selected_tab

if tab == "Empty Bins":
    st.subheader("📦 Empty Bins")
    st.dataframe(empty_bins_view_df)
elif tab == "Full Pallet Bins":
    st.subheader("🟩 Full Pallet Bins")
    df = apply_filters(full_pallet_bins_df)
    st.dataframe(df)
elif tab == "Empty Partial Bins":
    st.subheader("🟨 Empty Partial Bins")
    st.dataframe(empty_partial_bins_df)
elif tab == "Partial Bins":
    st.subheader("🟥 Partial Bins")
    df = apply_filters(partial_bins_df)
    st.dataframe(df)
elif tab == "Damages":
    st.subheader("🛠️ Damages (DAMAGE & IBDAMAGE)")
    df = apply_filters(damage_df)
    st.metric(label="Total Damaged Qty", value=f"{damage_qty:,}")
    st.dataframe(df)
elif tab == "Missing":
    st.subheader("❓ Missing")
    df = apply_filters(missing_df)
    st.metric(label="Total Missing Qty", value=f"{missing_qty:,}")
    st.dataframe(df)
elif tab == "Discrepancies":
    st.subheader("⚠️ Discrepancies")
    if discrepancy_df.empty:
        st.success("No discrepancies found!")
    else:
        st.metric(label="Total Discrepancies", value=f"{len(discrepancy_df):,}")
        st.dataframe(discrepancy_df)
        # Export to Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            discrepancy_df.to_excel(writer, index=False, sheet_name='Discrepancies')
        st.download_button("📤 Export Discrepancies to Excel", data=output.getvalue(), file_name="discrepancies.xlsx")

# Footer
st.sidebar.markdown(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")