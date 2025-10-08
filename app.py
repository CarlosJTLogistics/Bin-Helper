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

# --- LOTTIE ANIMATION ---
def load_lottie_url(url):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_url = "https://assets2.lottiefiles.com/packages/lf20_4kx2q32n.json"
lottie_json = load_lottie_url(lottie_url)

# --- Styling Function for Discrepancy Tables ---
def style_discrepancy_table(df):
    return df.style.set_table_styles([
        {'selector': 'thead th', 'props': [('font-weight', 'bold'), ('background-color', '#f0f0f0'), ('color', '#333')]},
        {'selector': 'tbody tr:nth-child(even)', 'props': [('background-color', '#f9f9f9')]},
        {'selector': 'tbody tr:hover', 'props': [('background-color', '#ffe6e6')]},
        {'selector': 'td', 'props': [('font-family', 'Arial'), ('font-size', '14px')]}
    ]).applymap(
        lambda val: 'background-color: #FF4C4C; color: white;' if isinstance(val, str) else '',
        subset=["Issue"]
    )

# --- NAVIGATION ---
nav_options = ["Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins", "Partial Bins",
               "Damages", "Missing", "Rack Discrepancies", "Bulk Discrepancies", "Bulk Locations", "Empty Bulk Locations"]
selected_nav = st.radio("🔍 Navigate:", nav_options, horizontal=True)
st.markdown("---")

# --- MAIN VIEW ---
if selected_nav == "Dashboard":
    st.markdown("<h2 style='text-align:center;'>📊 Bin Helper Dashboard</h2>", unsafe_allow_html=True)
    st_lottie(lottie_json, height=150, key="warehouse_anim")
    # KPI Cards
    kpi_data = [
        {"title": "Empty Bins", "value": len(empty_bins_view_df)},
        {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df)},
        {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df)},
        {"title": "Partial Bins", "value": len(partial_bins_df)},
        {"title": "Damages", "value": len(damages_df)},
        {"title": "Missing", "value": len(missing_df)},
        {"title": "Rack Discrepancies", "value": len(discrepancy_df)},
        {"title": "Bulk Discrepancies", "value": len(bulk_df)},
        {"title": "Empty Bulk Locations", "value": len(empty_bulk_locations_df)}
    ]
    cols = st.columns(len(kpi_data))
    for i, item in enumerate(kpi_data):
        with cols[i]:
            st.metric(label=item["title"], value=item["value"])
    # Charts
    chart_df = filtered_inventory_df.copy()
    location_usage = chart_df["LocationName"].value_counts().nlargest(10).reset_index()
    location_usage.columns = ["LocationName", "Count"]
    fig1 = px.pie(location_usage, names="LocationName", values="Count", title="Top 10 Location Usage")
    st.plotly_chart(fig1, use_container_width=True)
    inventory_movement = chart_df.groupby("WarehouseSku")["Qty"].sum().nlargest(10).reset_index()
    fig2 = px.bar(inventory_movement, x="WarehouseSku", y="Qty", title="Top 10 Inventory Movement", color="WarehouseSku")
    fig2.update_xaxes(type="category")
    st.plotly_chart(fig2, use_container_width=True)
    zone_summary = bulk_locations_df.groupby("Zone").agg({
        "PalletCount": "sum",
        "EmptySlots": "sum"
    }).reset_index()
    fig_bulk = px.bar(zone_summary, x="Zone", y=["PalletCount", "EmptySlots"], barmode="group",
                      title="Bulk Zone Utilization (Pallets vs Empty Slots)",
                      labels={"value": "Count", "variable": "Metric"})
    fig_bulk.update_xaxes(type="category")
    st.plotly_chart(fig_bulk, use_container_width=True)

# --- Rack Discrepancies ---
elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    for location, group in discrepancy_df.groupby("LocationName"):
        with st.expander(f"📍 {location}"):
            display_df = group[["PalletId", "Qty", "CustomerLotReference", "Issue"]].copy()
            styled_df = style_discrepancy_table(display_df)
            st.dataframe(styled_df, use_container_width=True)

            # Download CSV
            csv_data = display_df.to_csv(index=False).encode("utf-8")
            st.download_button(label=f"Download CSV for {location}", data=csv_data,
                               file_name=f"rack_discrepancies_{location}.csv", mime="text/csv")

            # LOT Fix Controls
            lot_list = group["CustomerLotReference"].dropna().unique().tolist()
            if lot_list:
                selected_lot = st.selectbox(f"Select LOT to fix for {location}:", lot_list, key=f"lot_select_{location}")
                note = st.text_input(f"Add note for LOT {selected_lot}:", key=f"note_{location}")
                if st.button(f"Mark LOT {selected_lot} as Fixed", key=f"fix_{location}"):
                    rows_to_fix = group[group["CustomerLotReference"] == selected_lot]
                    for _, row in rows_to_fix.iterrows():
                        log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot)
                    st.success(f"LOT {selected_lot} marked as fixed ({len(rows_to_fix)} pallets).")
            else:
                st.info("No LOT options available for this discrepancy.")

# --- Bulk Discrepancies ---
elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")
    for location, group in bulk_df.groupby("LocationName"):
        with st.expander(f"📍 {location}"):
            display_df = group[["PalletId", "Qty", "CustomerLotReference", "Issue"]].copy()
            styled_df = style_discrepancy_table(display_df)
            st.dataframe(styled_df, use_container_width=True)

            # Download CSV
            csv_data = display_df.to_csv(index=False).encode("utf-8")
            st.download_button(label=f"Download CSV for {location}", data=csv_data,
                               file_name=f"bulk_discrepancies_{location}.csv", mime="text/csv")

            # LOT Fix Controls
            lot_list = group["CustomerLotReference"].dropna().unique().tolist()
            if lot_list:
                selected_lot = st.selectbox(f"Select LOT to fix for {location}:", lot_list, key=f"bulk_lot_select_{location}")
                note = st.text_input(f"Add note for LOT {selected_lot}:", key=f"bulk_note_{location}")
                if st.button(f"Mark LOT {selected_lot} as Fixed", key=f"bulk_fix_{location}"):
                    rows_to_fix = group[group["CustomerLotReference"] == selected_lot]
                    for _, row in rows_to_fix.iterrows():
                        log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot)
                    st.success(f"LOT {selected_lot} marked as fixed ({len(rows_to_fix)} pallets).")
            else:
                st.info("No LOT options available for this discrepancy.")

# --- Bulk Locations ---
elif selected_nav == "Bulk Locations":
    st.subheader("📦 Bulk Locations")
    for _, row in bulk_locations_df.iterrows():
        with st.expander(f"📍 {row['LocationName']} | Zone: {row['Zone']} | Pallets: {row['PalletCount']} | Empty Slots: {row['EmptySlots']}"):
            location_pallets = filtered_inventory_df[filtered_inventory_df["LocationName"] == row["LocationName"]]
            if not location_pallets.empty:
                st.dataframe(location_pallets[["PalletId", "WarehouseSku", "CustomerLotReference", "Qty"]],
                             use_container_width=True)
            else:
                st.info("No pallets found for this location.")

# --- Empty Bulk Locations ---
elif selected_nav == "Empty Bulk Locations":
    st.subheader("📦 Empty Bulk Locations")
    st.dataframe(empty_bulk_locations_df, use_container_width=True)

else:
    view_map = {
        "Empty Bins": empty_bins_view_df,
        "Full Pallet Bins": full_pallet_bins_df,
        "Empty Partial Bins": empty_partial_bins_df,
        "Partial Bins": partial_bins_df,
        "Damages": damages_df,
        "Missing": missing_df
    }
    active_df = view_map.get(selected_nav, pd.DataFrame())
    required_cols = ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId"]
    available_cols = [col for col in required_cols if col in active_df.columns]
