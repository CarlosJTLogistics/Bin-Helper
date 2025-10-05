import os
import pandas as pd
import streamlit as st
from datetime import datetime

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Empty Bins"

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"

# ---------------- LOAD DATA ----------------
if not os.path.exists(inventory_file_path) or not os.path.exists(master_file_path):
    st.error("Please upload both ON_HAND_INVENTORY.xlsx and Empty Bin Formula.xlsx to proceed.")
    st.stop()

inventory_df = pd.read_excel(inventory_file_path, engine="openpyxl")
master_df = pd.read_excel(master_file_path, sheet_name="Master Locations", engine="openpyxl")

# ---------------- DATA PREP ----------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

# Filter out DAMAGE and IB locations
inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

# ---------------- BUSINESS RULES ----------------
occupied_locations = set(inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

def exclude_damage_missing(df):
    return df[~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        df["LocationName"].astype(str).str[0].str.isdigit()
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN") and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(inventory_df)
partial_bins_df = get_partial_bins(inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)

# ---------------- BULK DISCREPANCY LOGIC ----------------
def analyze_bulk_locations(df):
    results = []
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                details = df[df["LocationName"] == slot]
                for _, drow in details.iterrows():
                    results.append({
                        "Location": slot,
                        "Issue": f"Too many pallets ({count} > {max_pallets})",
                        "Qty": drow.get("Qty", ""),
                        "WarehouseSku": drow.get("WarehouseSku", ""),
                        "PalletId": drow.get("PalletId", ""),
                        "CustomerLotReference": drow.get("CustomerLotReference", ""),
                        "Notes": ""
                    })
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations(inventory_df)

# ---------------- KPI CARDS ----------------
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df), "icon": "⚠️"}
]

cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# ---------------- UI ----------------
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")
search_location = st.text_input("🔍 Filter by Location")

if st.session_state.active_view == "Bulk Discrepancies":
    filtered_bulk_df = bulk_df.copy()
    if search_location:
        filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].str.contains(search_location, case=False, na=False)]

    if filtered_bulk_df.empty:
        st.warning("✅ No discrepancies found.")
    else:
        grouped_by_location = filtered_bulk_df.groupby("Location")
        for location, loc_group in grouped_by_location:
            with st.expander(f"📍 Location: {location} ({len(loc_group)} rows)"):
                grouped_by_issue = loc_group.groupby("Issue")
                for issue, issue_group in grouped_by_issue:
                    with st.expander(f"⚠️ Issue: {issue} ({len(issue_group)} rows)"):
                        st.table(issue_group.drop(columns=["Location", "Issue"]))

elif st.session_state.active_view == "Empty Bins":
    st.table(empty_bins_view_df)

elif st.session_state.active_view == "Full Pallet Bins":
    st.table(full_pallet_bins_df)

elif st.session_state.active_view == "Empty Partial Bins":
    st.table(empty_partial_bins_df)

elif st.session_state.active_view == "Partial Bins":
    st.table(partial_bins_df)
