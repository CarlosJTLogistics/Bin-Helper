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

# --- FILE PATHS ---
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"

# Create logs folder next to app.py
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(logs_dir, exist_ok=True)
resolved_file = os.path.join(logs_dir, "resolved_discrepancies.csv")

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

# --- LOGGING ---
def log_resolved_discrepancy_with_note(row, note, selected_lot):
    file_exists = os.path.isfile(resolved_file)
    with open(resolved_file, mode="a", newline="") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Timestamp", "LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty", "Note", "SelectedLOT"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            row.get("LocationName", ""),
            row.get("PalletId", ""),
            row.get("WarehouseSku", ""),
            row.get("CustomerLotReference", ""),
            row.get("Qty", ""),
            note,
            selected_lot
        ])

# --- INITIALIZE DISCREPANCY DATAFRAMES ---
discrepancy_df = analyze_discrepancies(filtered_inventory_df)
bulk_df = analyze_bulk_locations_grouped(filtered_inventory_df)

# --- REMOVE RESOLVED LOTS ---
if os.path.isfile(resolved_file):
    resolved_df = pd.read_csv(resolved_file)
    resolved_lots = resolved_df["SelectedLOT"].dropna().unique().tolist()
    discrepancy_df = discrepancy_df[~discrepancy_df["CustomerLotReference"].isin(resolved_lots)]
    bulk_df = bulk_df[~bulk_df["CustomerLotReference"].isin(resolved_lots)]

# --- LOTTIE ANIMATION ---
def load_lottie_url(url):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_url = "https://assets2.lottiefiles.com/packages/lf20_4kx2q32n.json"
lottie_json = load_lottie_url(lottie_url)

# --- NAVIGATION ---
nav_options = ["Dashboard", "Rack Discrepancies", "Bulk Discrepancies", "Resolved Discrepancies"]
selected_nav = st.radio("Navigate:", nav_options)

# --- Dashboard ---
if selected_nav == "Dashboard":
    st.title("📊 Bin Helper Dashboard")
    st_lottie(lottie_json, height=150, key="warehouse_anim")

    kpi_data = [
        {"title": "Rack Discrepancies", "value": discrepancy_df["LocationName"].nunique()},
        {"title": "Bulk Discrepancies", "value": bulk_df["LocationName"].nunique()},
    ]
    cols = st.columns(len(kpi_data))
    for i, item in enumerate(kpi_data):
        with cols[i]:
            st.metric(label=item["title"], value=item["value"])

# --- Rack Discrepancies ---
elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    for location, group in discrepancy_df.groupby("LocationName"):
        with st.expander(f"📍 {str(location)}"):
            st.dataframe(group)
            lot_list = group["CustomerLotReference"].dropna().unique().tolist()
            if lot_list:
                selected_lot = st.selectbox("Select LOT to fix:", lot_list, key=f"rack_lot_{location}")
                note = st.text_input("Add note:", key=f"rack_note_{location}")
                if st.button("Mark LOT as Fixed", key=f"rack_fix_{location}"):
                    rows_to_fix = group[group["CustomerLotReference"] == selected_lot]
                    for _, row in rows_to_fix.iterrows():
                        log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot)
                    st.success(f"LOT {selected_lot} marked as fixed.")
                    st.rerun()

# --- Bulk Discrepancies ---
elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")
    for location, group in bulk_df.groupby("LocationName"):
        with st.expander(f"📍 {str(location)}"):
            st.dataframe(group)
            lot_list = group["CustomerLotReference"].dropna().unique().tolist()
            if lot_list:
                selected_lot = st.selectbox("Select LOT to fix:", lot_list, key=f"bulk_lot_{location}")
                note = st.text_input("Add note:", key=f"bulk_note_{location}")
                if st.button("Mark LOT as Fixed", key=f"bulk_fix_{location}"):
                    rows_to_fix = group[group["CustomerLotReference"] == selected_lot]
                    for _, row in rows_to_fix.iterrows():
                        log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot)
                    st.success(f"LOT {selected_lot} marked as fixed.")
                    st.rerun()

# --- Resolved Discrepancies ---
elif selected_nav == "Resolved Discrepancies":
    st.subheader("✅ Resolved Discrepancies Log")
    if os.path.isfile(resolved_file):
        resolved_df = pd.read_csv(resolved_file)
        st.dataframe(resolved_df)
        for lot in resolved_df["SelectedLOT"].dropna().unique():
            if st.button(f"Undo {lot}"):
                updated_df = resolved_df[resolved_df["SelectedLOT"] != lot]
                updated_df.to_csv(resolved_file, index=False)
                st.success(f"LOT {lot} has been restored to discrepancies.")
                st.rerun()
    else:
        st.info("No resolved discrepancies have been logged yet.")