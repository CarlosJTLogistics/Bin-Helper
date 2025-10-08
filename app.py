import pandas as pd
import streamlit as st
import os
import time

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = None
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

# ---------------- AUTO REFRESH ----------------
if st.session_state.auto_refresh:
    st.rerun()

# ---------------- GITHUB FILE URLS ----------------
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

# ---------------- LOAD DATA ----------------
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

occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# ---------------- BUSINESS RULES ----------------
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
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
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

empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper() == "MISSING"]

# ---------------- BULK DISCREPANCY LOGIC ----------------
def analyze_bulk_locations_grouped(df):
    df = exclude_damage_missing(df)
    results = []
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                results.append({
                    "LocationName": slot,
                    "TotalPallets": count,
                    "MaxAllowed": max_pallets,
                    "Issue": f"Exceeds max allowed: {count} > {max_pallets}"
                })
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations_grouped(filtered_inventory_df)

# ---------------- DISCREPANCY LOGIC ----------------
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []

    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
        results.append(row.to_dict() | {"Issue": issue})

    full_df = df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric())
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "Qty out of range for full pallet bin"
        results.append(row.to_dict() | {"Issue": issue})

    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ---------------- FILTER FUNCTION ----------------
def apply_filters(df):
    for key, value in st.session_state.filters.items():
        if value and key in df.columns:
            df = df[df[key].astype(str).str.contains(value, case=False, na=False)]
    return df

# ---------------- CUSTOM STYLES ----------------
st.markdown("""
<style>
.kpi-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
  margin-top: 2rem;
}
.kpi-card {
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  border-radius: 15px;
  padding: 1.5rem;
  text-align: center;
  color: white;
  font-size: 1rem;
  font-weight: bold;
  transition: transform 0.3s ease, box-shadow 0.3s ease;
  box-shadow: 0 0 10px rgba(0,0,0,0.3);
  cursor: pointer;
}
.kpi-card:hover {
  transform: scale(1.05);
  box-shadow: 0 0 20px #FFD700;
}
.kpi-icon {
  font-size: 2rem;
  margin-bottom: 0.5rem;
}
.kpi-value {
  font-size: 2rem;
  font-weight: bold;
  margin-bottom: 0.5rem;
}
@keyframes fadeIn {
  from {opacity: 0;}
  to {opacity: 1;}
}
.welcome-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  animation: fadeIn 2s ease-in;
  margin-top: 3rem;
}
.welcome-text {
  font-size: 2.5rem;
  font-weight: bold;
  background: linear-gradient(90deg, #FFD700, #FF8C00);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  text-align: center;
  margin-top: 1rem;
}
</style>
""", unsafe_allow_html=True)

# ---------------- HOME SCREEN ----------------
if st.session_state.active_view is None:
    st.markdown("<div class='welcome-container'>", unsafe_allow_html=True)
    st.markdown("<div class='welcome-text'>👋 Welcome to Bin Helper</div>", unsafe_allow_html=True)
    st.image("image.png", use_column_width="auto")  # Replace with your uploaded image path
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------- KPI CARDS ----------------
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Rack Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df), "icon": "📦"}
]

st.markdown("<div class='kpi-container'>", unsafe_allow_html=True)
for item in kpi_data:
    st.markdown(f"<div class='kpi-card'>", unsafe_allow_html=True)
    st.markdown(f"<div class='kpi-icon'>{item['icon']}</div>", unsafe_allow_html=True)
    placeholder = st.empty()
    for i in range(0, item['value'] + 1, max(1, item['value'] // 20 or 1)):
        placeholder.markdown(f"<div class='kpi-value'>{i}</div>", unsafe_allow_html=True)
        time.sleep(0.02)
    placeholder.markdown(f"<div class='kpi-value'>{item['value']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div>{item['title']}</div>", unsafe_allow_html=True)
    if st.button(f"View {item['title']}", key=f"btn_{item['title']}"):
        st.session_state.active_view = item['title']
    st.markdown("</div>", unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

# ---------------- FILTERS ----------------
st.sidebar.markdown("### 🔍 Filter Options")
st.session_state.filters["LocationName"] = st.sidebar.text_input("Location", value=st.session_state.filters["LocationName"])
st.session_state.filters["PalletId"] = st.sidebar.text_input("Pallet ID", value=st.session_state.filters["PalletId"])
st.session_state.filters["WarehouseSku"] = st.sidebar.text_input("Warehouse SKU", value=st.session_state.filters["WarehouseSku"])
st.session_state.filters["CustomerLotReference"] = st.sidebar.text_input("LOT", value=st.session_state.filters["CustomerLotReference"])

# ---------------- HISTORY LOG ----------------
st.sidebar.markdown("### ✅ History Log")
log_file = "resolved_discrepancies.csv"
if os.path.exists(log_file):
    history_df = pd.read_csv(log_file)
    st.sidebar.dataframe(history_df.reset_index(drop=True), use_container_width=True, hide_index=True)
else:
    st.sidebar.info("No resolved discrepancies logged yet.")

# ---------------- DISPLAY VIEWS ----------------
view_map = {
    "Rack Discrepancies": discrepancy_df,
    "Bulk Discrepancies": bulk_df,
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df
}

if st.session_state.active_view:
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    active_df = apply_filters(raw_df)
    st.subheader(f"{st.session_state.active_view}")
    st.dataframe(active_df.reset_index(drop=True), use_container_width=True, hide_index=True)