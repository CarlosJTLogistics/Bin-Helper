import pandas as pd
import streamlit as st
import os
import csv
from datetime import datetime
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# --- PAGE CONFIG ---
st.set_page_config(page_title="Bin Helper", layout="wide")

# --- SESSION STATE ---
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()

# --- FILE PATHS ---
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"
resolved_file = "resolved_discrepancies.csv"

# --- LOAD DATA ---
inventory_df = pd.read_excel(inventory_file, engine="openpyxl")
master_df = pd.read_excel(master_file, sheet_name="Master Locations", engine="openpyxl")

# --- DATA PREP ---
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

# Exclude OB and IB locations globally
inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith(("OB", "IB"))
]

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}
filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"])
]
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# --- BUSINESS RULES ---
def exclude_damage_missing(df):
    return df[~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])]

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

# --- BULK DISCREPANCY LOGIC ---
def analyze_bulk_locations_grouped(df):
    df = exclude_damage_missing(df)
    results = []
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                slot_df = letter_df[letter_df["LocationName"] == slot]
                for _, row in slot_df.iterrows():
                    results.append(row.to_dict() | {"Issue": f"Exceeds max allowed: {count} > {max_pallets}"})
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations_grouped(filtered_inventory_df)

# --- DISCREPANCY LOGIC ---
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []
    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
        results.append(row.to_dict() | {"Issue": issue})
    full_df = df[
        ((~df["LocationName"].astype(str).str.endswith("01")) |
         (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric())
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "Partial Pallet needs to be moved to Partial Location"
        results.append(row.to_dict() | {"Issue": issue})
    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# --- BULK LOCATIONS & EMPTY SLOTS ---
bulk_locations = []
empty_bulk_locations = []
location_counts = filtered_inventory_df.groupby("LocationName").size().reset_index(name="PalletCount")
for _, row in location_counts.iterrows():
    location = row["LocationName"]
    count = row["PalletCount"]
    zone = str(location)[0].upper()
    if zone in bulk_rules:
        max_allowed = bulk_rules[zone]
        empty_slots = max_allowed - count
        bulk_locations.append({
            "LocationName": location,
            "Zone": zone,
            "PalletCount": count,
            "MaxAllowed": max_allowed,
            "EmptySlots": max(0, empty_slots)
        })
        if empty_slots > 0:
            empty_bulk_locations.append({
                "LocationName": location,
                "Zone": zone,
                "EmptySlots": empty_slots
            })
bulk_locations_df = pd.DataFrame(bulk_locations)
empty_bulk_locations_df = pd.DataFrame(empty_bulk_locations)

# --- LOGGING ---
def log_resolved_discrepancy_with_note(row, note, selected_lot, discrepancy_type):
    file_exists = os.path.isfile(resolved_file)
    with open(resolved_file, mode="a", newline="") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Timestamp", "LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty", "Note", "SelectedLOT", "DiscrepancyType"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            row.get("LocationName", ""),
            row.get("PalletId", ""),
            row.get("WarehouseSku", ""),
            row.get("CustomerLotReference", ""),
            row.get("Qty", ""),
            note,
            selected_lot,
            discrepancy_type
        ])

# --- NAVIGATION ---
nav_options = ["Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins", "Partial Bins",
               "Damages", "Missing", "Rack Discrepancies", "Bulk Discrepancies", "Bulk Locations", "Empty Bulk Locations"]
selected_nav = st.radio("🔍 Navigate:", nav_options, horizontal=True)
st.markdown("---")

# --- Rack Discrepancies Tab ---
elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    display_cols = ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"]
    available_cols = [col for col in display_cols if col in discrepancy_df.columns]
    if not discrepancy_df.empty:
        st.dataframe(discrepancy_df[available_cols].reset_index(drop=True), use_container_width=True)
        csv_data = discrepancy_df[available_cols].to_csv(index=False).encode("utf-8")
        st.download_button(label="Download Rack Discrepancies CSV", data=csv_data,
                           file_name="rack_discrepancies.csv", mime="text/csv")
    else:
        st.info("No rack discrepancies found.")

# --- Bulk Discrepancies Tab ---
elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")
    display_cols = ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"]
    if not bulk_df.empty:
        for location, group in bulk_df.groupby("LocationName"):
            with st.expander(f"📍 {location}"):
                for _, row in group.iterrows():
                    st.write(f"**Location:** {row.get('LocationName', 'N/A')}")
                    st.write(f"**Pallet ID:** {row.get('PalletId', 'N/A')}")
                    st.write(f"**WarehouseSku:** {row.get('WarehouseSku', 'N/A')}")
                    st.write(f"**Customer LOT:** {row.get('CustomerLotReference', 'N/A')}")
                    st.write(f"**Qty:** {row.get('Qty', 'N/A')}")
                    st.markdown("---")
        csv_data = bulk_df[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(label="Download Bulk Discrepancies CSV", data=csv_data,
                           file_name="bulk_discrepancies.csv", mime="text/csv")
        # LOT Fix Section
        st.subheader("✅ Fix Discrepancy by LOT")
        lot_list = bulk_df["CustomerLotReference"].dropna().unique().tolist()
        if lot_list:
            selected_lot = st.selectbox("Select LOT to fix:", lot_list, key="bulk_global_lot_select")
            note = st.text_input(f"Add note for LOT {selected_lot}:", key="bulk_global_note")
            if st.button("Fix Selected LOT", key="bulk_global_fix"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"] == selected_lot]
                for _, row in rows_to_fix.iterrows():
                    log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot, "Bulk")
                st.success(f"All discrepancies for LOT {selected_lot} marked as fixed ({len(rows_to_fix)} pallets).")
        else:
            st.info("No LOT options available for bulk discrepancies.")
    else:
        st.info("No bulk discrepancies found.")