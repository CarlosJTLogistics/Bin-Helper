import pandas as pd
import streamlit as st
import os
import csv
from datetime import datetime
import plotly.express as px

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Dashboard"
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()

# ---------------- FILE PATHS ----------------
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"
trend_file = "trend_history.csv"
resolved_file = "resolved_discrepancies.csv"

# ---------------- LOAD DATA ----------------
inventory_df = pd.read_excel(inventory_file, engine="openpyxl")
master_df = pd.read_excel(master_file, sheet_name="Master Locations", engine="openpyxl")

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

# ✅ FIX: Exclude partial bins from Empty Bins
empty_bins_view_df = pd.DataFrame({
    "LocationName": [
        loc for loc in master_locations
        if loc not in occupied_locations and not loc.endswith("01")
    ]
})

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

# ---------------- LOGGING FUNCTION ----------------
def log_resolved_discrepancy_with_note(row, note):
    row_with_note = row.copy()
    row_with_note["Note"] = note
    file_exists = os.path.isfile(resolved_file)
    with open(resolved_file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=row_with_note.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_with_note)
    st.session_state.resolved_items.add(row.get("LocationName", "") + str(row.get("PalletId", "")))

# ---------------- FILTER FUNCTION ----------------
def apply_filters(df):
    for key, value in st.session_state.filters.items():
        if value and key in df.columns:
            df = df[df[key].astype(str).str.contains(value, case=False, na=False)]
    return df

# ---------------- TREND TRACKING ----------------
def log_trend_data():
    today = datetime.today().strftime("%Y-%m-%d")
    trend_row = {
        "Date": today,
        "EmptyBins": len(empty_bins_view_df),
        "FullPalletBins": len(full_pallet_bins_df),
        "PartialBins": len(partial_bins_df),
        "EmptyPartialBins": len(empty_partial_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
        "RackDiscrepancies": len(discrepancy_df),
        "BulkDiscrepancies": len(bulk_df)
    }

    if os.path.exists(trend_file):
        existing = pd.read_csv(trend_file)
        if today in existing["Date"].values:
            return
    with open(trend_file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=trend_row.keys())
        if os.stat(trend_file).st_size == 0:
            writer.writeheader()
        writer.writerow(trend_row)

log_trend_data()

# ---------------- DASHBOARD VIEW ----------------
def show_dashboard():
    st.markdown("## 📈 Trends Overview")
    if os.path.exists(trend_file):
        trend_df = pd.read_csv(trend_file)
        fig = px.line(trend_df, x="Date", y=["EmptyBins", "FullPalletBins", "PartialBins", "EmptyPartialBins", "RackDiscrepancies", "BulkDiscrepancies"],
                      markers=True, title="Bin Trends Over Time")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No trend data available yet.")

# ---------------- KPI CARDS ----------------
st.markdown("<h1 style='text-align: center; color: #2E86C1;'>📊 Bin-Helper Dashboard</h1>", unsafe_allow_html=True)

kpi_data = [
    {"title": "Dashboard", "value": "", "icon": "🏠"},
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
        if st.button(f"{item['icon']} {item['title']} {'| ' + str(item['value']) if item['value'] != '' else ''}", key=item['title']):
            st.session_state.active_view = item['title']

# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.markdown("### 🔍 Filter Options")
    st.session_state.filters["LocationName"] = st.text_input("Location", value=st.session_state.filters["LocationName"])
    st.session_state.filters["PalletId"] = st.text_input("Pallet ID", value=st.session_state.filters["PalletId"])
    st.session_state.filters["WarehouseSku"] = st.text_input("Warehouse SKU", value=st.session_state.filters["WarehouseSku"])
    st.session_state.filters["CustomerLotReference"] = st.text_input("LOT", value=st.session_state.filters["CustomerLotReference"])

    st.markdown("### ✅ History Log")
    if os.path.exists(resolved_file):
        history_df = pd.read_csv(resolved_file)
        st.dataframe(history_df.reset_index(drop=True), use_container_width=True, hide_index=True)
    else:
        st.info("No resolved discrepancies logged yet.")

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

if st.session_state.active_view == "Dashboard":
    show_dashboard()
else:
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    active_df = apply_filters(raw_df)

    st.subheader(f"{st.session_state.active_view}")
    if st.session_state.active_view == "Bulk Discrepancies":
        for idx, row in active_df.iterrows():
            location = row["LocationName"]
            with st.expander(f"{location} | {row['Issue']}"):
                details = filtered_inventory_df[filtered_inventory_df["LocationName"] == location]
                for i, drow in details.iterrows():
                    row_id = drow.get("LocationName", "") + str(drow.get("PalletId", ""))
                    if row_id in st.session_state.resolved_items:
                        continue
                    st.write(drow[["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId"]])
                    note_key = f"note_bulk_{idx}_{i}"
                    note = st.text_input(f"Note for Pallet {drow['PalletId']}", key=note_key)
                    if st.button(f"✅ Mark Pallet {drow['PalletId']} Fixed", key=f"bulk_fix_{idx}_{i}"):
                        log_resolved_discrepancy_with_note(drow.to_dict(), note)
                        st.success(f"Pallet {drow['PalletId']} logged as fixed!")
                        st.experimental_rerun()
    elif st.session_state.active_view == "Rack Discrepancies":
        for idx, row in active_df.iterrows():
            row_id = row.get("LocationName", "") + str(row.get("PalletId", ""))
            if row_id in st.session_state.resolved_items:
                continue
            st.write(row[["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId"]])
            note_key = f"note_rack_{idx}"
            note = st.text_input(f"Note for Pallet {row['PalletId']}", key=note_key)
            if st.button(f"✅ Mark Pallet {row['PalletId']} Fixed", key=f"rack_fix_{idx}"):
                log_resolved_discrepancy_with_note(row.to_dict(), note)
                st.success(f"Pallet {row['PalletId']} logged as fixed!")
                st.experimental_rerun()
    else:
        required_cols = ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId"]
        available_cols = [col for col in required_cols if col in active_df.columns]
        st.dataframe(active_df[available_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
