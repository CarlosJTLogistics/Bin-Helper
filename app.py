import pandas as pd
import streamlit as st
import os
import csv
from datetime import datetime
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()

# ---------------- FILE PATHS ----------------
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"
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

# ---------------- VIEW MAP ----------------
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

# ---------------- CARD VIEW FUNCTION ----------------
def show_discrepancy_cards(df, discrepancy_type):
    grouped_df = df.groupby("LocationName")
    for location, group in grouped_df:
        issue_summary = ", ".join(group["Issue"].unique()) if "Issue" in group.columns else ""
        pallet_count = len(group)
        status = "✅ Fixed" if all(
            (location + str(row.get("PalletId", idx))) in st.session_state.resolved_items
            for idx, row in group.iterrows()
        ) else "❌ Unresolved"

        st.markdown(f"""
        <div style="border:1px solid #ccc; border-radius:10px; padding:15px; margin-bottom:10px;">
            <h4>{location}</h4>
            <p><strong>Issue:</strong> {issue_summary}</p>
            <p><strong>Pallets:</strong> {pallet_count}</p>
            <p><strong>Status:</strong> <span style="color:{'green' if '✅' in status else 'red'};">{status}</span></p>
        </div>
        """, unsafe_allow_html=True)

        for idx, row in group.iterrows():
            row_id = location + str(row.get("PalletId", idx))
            if row_id in st.session_state.resolved_items:
                continue
            display_cols = [c for c in ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId", "Qty"] if c in row.index]
            st.write(row[display_cols])
            note_key = f"note_{discrepancy_type}_{location}_{idx}"
            note = st.text_input(f"Note for {row.get('PalletId', 'this item')}", key=note_key)
            if st.button(f"✅ Mark {row.get('PalletId', 'Item')} Fixed", key=f"{discrepancy_type}_fix_{location}_{idx}"):
                log_resolved_discrepancy_with_note(row.to_dict(), note)
                st.success(f"{row.get('PalletId', 'Item')} logged as fixed!")
                st.experimental_rerun()

# ---------------- WELCOME ANIMATION ----------------
def load_lottie_url(url):
    r = requests.get(url)
    if r.status_code == 200:
        return r.json()
    return None

lottie_animation = load_lottie_url("https://assets10.lottiefiles.com/packages/lf20_49rdyysj.json")
if lottie_animation:
    st_lottie(lottie_animation, speed=1, loop=True, height=150)

# ---------------- DASHBOARD VIEW ----------------
def show_dashboard():
    st.markdown("<h2 style='text-align:center;'>📊 Bin Helper Dashboard</h2>", unsafe_allow_html=True)

    # KPI Cards
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
            st.metric(label=item["title"], value=item["value"])

    st.markdown("---")
    # Charts
    col1, col2 = st.columns([2, 1])
    with col2:
        total_locations = len(master_locations)
        occupied_count = len(occupied_locations)
        empty_count = total_locations - occupied_count
        usage_percent = round((occupied_count / total_locations) * 100, 2)
        st.subheader("📍 Location Usage")
        fig_usage = px.pie(names=["Occupied", "Empty"], values=[occupied_count, empty_count], hole=0.6,
                           color_discrete_sequence=["#2E86C1", "#AED6F1"])
        fig_usage.update_traces(textinfo="percent+label")
        st.plotly_chart(fig_usage, use_container_width=True)
        st.caption(f"Total Locations: {total_locations} | Usage: {usage_percent}%")
    with col1:
        st.subheader("📦 Inventory Movement")
        movement_data = {
            "Full Pallet Bins": len(full_pallet_bins_df),
            "Partial Bins": len(partial_bins_df),
            "Damages": len(damages_df),
            "Missing": len(missing_df)
        }
        fig_movement = px.bar(x=list(movement_data.keys()), y=list(movement_data.values()), color=list(movement_data.keys()),
                              title="Inventory Distribution", text=list(movement_data.values()))
        fig_movement.update_traces(textposition="outside")
        st.plotly_chart(fig_movement, use_container_width=True)

    # Bulk Zone Utilization
    st.subheader("📦 Bulk Zone Utilization")
    bulk_utilization = []
    for zone in bulk_rules.keys():
        zone_df = filtered_inventory_df[filtered_inventory_df["LocationName"].astype(str).str.startswith(zone)]
        pallet_count = len(zone_df)
        total_qty = zone_df["Qty"].sum()
        bulk_utilization.append({"Zone": zone, "Pallet Count": pallet_count, "Qty": total_qty})
    bulk_df_chart = pd.DataFrame(bulk_utilization)
    fig_bulk = px.bar(bulk_df_chart, x="Zone", y=["Pallet Count", "Qty"], barmode="group",
                      title="Bulk Zone Utilization: Pallet Count vs Qty")
    st.plotly_chart(fig_bulk, use_container_width=True)

# ---------------- NAVIGATION ----------------
nav_options = ["Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins", "Partial Bins",
               "Damages", "Missing", "Rack Discrepancies", "Bulk Discrepancies"]
selected_nav = st.radio("🔍 Navigate:", nav_options, horizontal=True)
st.markdown("---")

# ---------------- MAIN VIEW ----------------
if selected_nav == "Dashboard":
    show_dashboard()
elif selected_nav in ["Rack Discrepancies", "Bulk Discrepancies"]:
    st.subheader(f"{selected_nav}")
    raw_df = view_map.get(selected_nav, pd.DataFrame())
    active_df = apply_filters(raw_df)
    discrepancy_type = "rack" if selected_nav == "Rack Discrepancies" else "bulk"
    show_discrepancy_cards(active_df, discrepancy_type)
else:
    required_cols = ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId"]
    active_df = apply_filters(view_map.get(selected_nav, pd.DataFrame()))
    available_cols = [col for col in required_cols if col in active_df.columns]
    st.dataframe(active_df[available_cols].reset_index(drop=True), use_container_width=True, hide_index=True)