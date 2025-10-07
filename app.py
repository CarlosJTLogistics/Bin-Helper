# --- PAGE CONFIG ---
import pandas as pd
import streamlit as st
import os
import csv
import time
import plotly.express as px

st.set_page_config(page_title="Bin Helper", layout="wide")

# --- SESSION STATE ---
if "active_view" not in st.session_state:
    st.session_state.active_view = None
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

# --- AUTO REFRESH ---
if st.session_state.auto_refresh:
    st.rerun()

# --- GITHUB FILE URLS ---
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

# --- LOAD DATA ---
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

# --- DATA PREP ---
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# --- BUSINESS RULES ---
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
        (~df["LocationName"].astype(str).str.endswith("01") |
         df["LocationName"].astype(str).str.startswith("111")) &
        df["LocationName"].astype(str).str.isnumeric() &
        df["Qty"].between(6, 15)
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

# --- BULK DISCREPANCY LOGIC ---
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

# --- DISCREPANCY LOGIC ---
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []
    # Partial bin errors
    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
        results.append({**row.to_dict(), "Issue": issue})
    # Full bin errors
    full_df = df[
        (~df["LocationName"].astype(str).str.endswith("01") |
         df["LocationName"].astype(str).str.startswith("111")) &
        df["LocationName"].astype(str).str.isnumeric()
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "Partial pallet needs to be moved to Partial Loc." if row["Qty"] <= 5 else "Qty out of range for full pallet bin"
        results.append({**row.to_dict(), "Issue": issue})
    # New rule: multiple pallets in rack location
    rack_df = df[
        df["LocationName"].astype(str).str.isnumeric() |
        df["LocationName"].astype(str).str.startswith("111")
    ]
    rack_errors = rack_df[rack_df["PalletCount"] >= 2]
    for _, row in rack_errors.iterrows():
        results.append({**row.to_dict(), "Issue": "Multiple pallets in rack location"})
    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# --- LOGGING FUNCTION ---
log_file = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\resolved_discrepancies.csv"

def log_resolved_discrepancy_with_note(row, note):
    row_with_note = row.copy()
    row_with_note["Note"] = note
    file_exists = os.path.isfile(log_file)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=row_with_note.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_with_note)
    st.session_state.resolved_items.add(row.get("LocationName", "") + str(row.get("PalletId", "")))

# --- HOME SCREEN ---
st.markdown("<h1 style='text-align: center; color: #2E86C1;'>📊 Bin-Helper Dashboard</h1>", unsafe_allow_html=True)

# --- KPI CARDS ---
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
cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']}\n{item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# --- SUMMARY SECTION ---
st.markdown("## 📋 Summary Insights")
total_bins = len(master_locations)
occupied_bins = len(occupied_locations)
empty_bins = total_bins - occupied_bins
empty_ratio = empty_bins / total_bins if total_bins else 0

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Bin Locations", total_bins)
with col2:
    st.metric("Occupied Bins", occupied_bins)
with col3:
    st.metric("Empty Bins", empty_bins)

st.progress(empty_ratio)

st.markdown("### ⚠️ Top Discrepancy Types")
top_discrepancies = discrepancy_df["Issue"].value_counts().head(3)
for issue, count in top_discrepancies.items():
    st.write(f"- {issue}: {count}")

# --- HEATMAP SECTION ---
st.markdown("## 🗺️ Warehouse Zone Heatmap")
zone_counts = {}
for zone in bulk_rules.keys():
    zone_df = filtered_inventory_df[filtered_inventory_df["LocationName"].astype(str).str.startswith(zone)]
    zone_counts[zone] = len(zone_df)

heatmap_df = pd.DataFrame(list(zone_counts.items()), columns=["Zone", "Pallets"])
fig = px.imshow([heatmap_df["Pallets"].tolist()],
                x=heatmap_df["Zone"].tolist(),
                color_continuous_scale="Blues",
                labels={"x": "Zone", "color": "Pallets"})
fig.update_layout(title="Pallet Distribution by Zone", height=300)
st.plotly_chart(fig, use_container_width=True)