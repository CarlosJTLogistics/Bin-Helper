# -*- coding: utf-8 -*-
import os
import csv
import re
from datetime import datetime

import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# --- PAGE CONFIG ---
st.set_page_config(page_title="Bin Helper", layout="wide")

# --- SESSION STATE ---
if "filters" not in st.session_state:
    st.session_state.filters = {
        "LocationName": "",
        "PalletId": "",
        "WarehouseSku": "",
        "CustomerLotReference": ""
    }
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()

# --- UTIL: safer rerun wrapper ---
def _rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()

# --- ALWAYS-ON BANNER (Animated) ---
def _load_lottie(url: str):
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def show_banner():
    """Animated banner kept at the top across all tabs."""
    # Curated set of warehouse/logistics-themed animations (forklift, scanning, logistics)
    candidate_urls = [
        # Forklift / warehouse motion
        "https://assets10.lottiefiles.com/packages/lf20_9kmmv9.json",
        # Barcode scanning / boxes
        "https://assets2.lottiefiles.com/packages/lf20_1pxqjqps.json",
        # Logistics / delivery concept
        "https://assets9.lottiefiles.com/packages/lf20_wnqlfojb.json",
        # Fallback to your previous one
        "https://assets10.lottiefiles.com/packages/lf20_j1adxtyb.json",
    ]

    with st.container():
        col_a, col_b = st.columns([1, 3])
        with col_a:
            data = None
            for u in candidate_urls:
                data = _load_lottie(u)
                if data:
                    break
            if data:
                st_lottie(data, height=140, key="banner_lottie", speed=1.0, loop=True)
            else:
                st.info("Banner animation unavailable")
        with col_b:
            st.markdown(
                """
                <div style="padding:6px 0 0 6px">
                  <h2 style="margin:0;">Bin Helper</h2>
                  <p style="margin:4px 0 0;">
                    Fast, visual lookups for <b>Empty</b>, <b>Partial</b>, <b>Full</b>, <b>Damages</b>, and <b>Missing</b> — all by your warehouse rules.
                  </p>
                </div>
                """,
                unsafe_allow_html=True
            )

# ---- Render the persistent banner first (visible on every tab) ----
show_banner()

# --- FILE PATHS ---
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"
resolved_file = "resolved_discrepancies.csv"

# --- LOAD DATA ---
inventory_df = pd.read_excel(inventory_file, engine="openpyxl")
# Preserve original behavior: load specific sheet "Master Locations"
master_df = pd.read_excel(master_file, sheet_name="Master Locations", engine="openpyxl")

# --- DATA PREP ---
# Coerce numeric types
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

# Normalize LocationName
inventory_df["LocationName"] = inventory_df["LocationName"].astype(str)

# Exclude OB and IB locations globally
inventory_df = inventory_df[~inventory_df["LocationName"].str.upper().str.startswith(("OB", "IB"))].copy()

# Bulk rule caps (unchanged)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

# --- MASTER LOCATIONS (Robust extraction; same intent as before, safer for headers) ---
def extract_master_locations(df: pd.DataFrame) -> set:
    """
    Return a set of master locations by picking a 'Location' column if present,
    else first column; keep only numeric slots or TUN-prefixed codes.
    """
    for c in df.columns:
        if 'location' in str(c).lower():
            s = df[c].dropna().astype(str).str.strip()
            s = s[s.str.match(r'^(TUN\w+|\d+)$')]
            if not s.empty:
                return set(s.unique().tolist())
    # Fallback: first column
    s = df.iloc[:, 0].dropna().astype(str).str.strip()
    s = s[s.str.match(r'^(TUN\w+|\d+)$')]
    return set(s.unique().tolist())

# Build master location set
master_locations = extract_master_locations(master_df)

# --- HELPERS / BUSINESS RULES (PRESERVED) ---
def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["LocationName"].str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])].copy()

filtered_inventory_df = exclude_damage_missing(inventory_df)
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())

def get_partial_bins(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    s = df2["LocationName"].astype(str)
    mask = (
        s.str.endswith("01") &
        ~s.str.startswith("111") &
        ~s.str.upper().str.startswith("TUN") &
        s.str[0].str.isdigit()
    )
    return df2.loc[mask].copy()

def get_full_pallet_bins(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    s = df2["LocationName"].astype(str)
    mask = ((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric() & df2["Qty"].between(6, 15)
    return df2.loc[mask].copy()

def get_empty_partial_bins(master_locs: set, occupied_locs: set) -> pd.DataFrame:
    series = pd.Series(list(master_locs), dtype=str)
    mask = (
        series.str.endswith("01") &
        ~series.str.startswith("111") &
        ~series.str.upper().str.startswith("TUN") &
        series.str[0].str.isdigit()
    )
    partial_candidates = set(series[mask])
    empty_partial = sorted(partial_candidates - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

# --- BUILD VIEWS (PRESERVED) ---
empty_bins_view_df = pd.DataFrame({
    "LocationName": sorted([loc for loc in master_locations if (loc not in occupied_locations and not str(loc).endswith("01"))])
})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)

damages_df = inventory_df[inventory_df["LocationName"].str.upper().isin(["DAMAGE", "IBDAMAGE"])].copy()
missing_df = inventory_df[inventory_df["LocationName"].str.upper() == "MISSING"].copy()

# --- BULK DISCREPANCY LOGIC (PRESERVED) ---
def analyze_bulk_locations_grouped(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    results = []
    # Consider only letter-zones present in bulk_rules
    letter_mask = df2["LocationName"].str[0].str.upper().isin(bulk_rules.keys())
    df2 = df2[letter_mask]
    if df2.empty:
        return pd.DataFrame()
    slot_counts = df2.groupby("LocationName").size()
    for slot, count in slot_counts.items():
        zone = str(slot)[0].upper()
        max_pallets = bulk_rules.get(zone)
        if max_pallets is not None and count > max_pallets:
            slot_df = df2[df2["LocationName"] == slot]
            for _, row in slot_df.iterrows():
                rec = row.to_dict()
                rec["Issue"] = f"Exceeds max allowed: {count} > {max_pallets}"
                results.append(rec)
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations_grouped(filtered_inventory_df)

# --- RACK DISCREPANCY LOGIC (PRESERVED) ---
def analyze_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    results = []

    # Partial errors: Qty > 5 OR PalletCount > 1
    p_df = get_partial_bins(df2)
    if not p_df.empty:
        pe = p_df[(p_df["Qty"] > 5) | (p_df["PalletCount"] > 1)]
        for _, row in pe.iterrows():
            issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
            rec = row.to_dict()
            rec["Issue"] = issue
            results.append(rec)

    # Full rack errors: locations considered 'full rack' but Qty not in 6..15
    s = df2["LocationName"].astype(str)
    full_mask = ((~s.str.endswith("01")) | s.str.startswith("111")) & s.str.isnumeric()
    f_df = df2.loc[full_mask]
    if not f_df.empty:
        fe = f_df[~f_df["Qty"].between(6, 15)]
        for _, row in fe.iterrows():
            rec = row.to_dict()
            rec["Issue"] = "Partial Pallet needs to be moved to Partial Location"
            results.append(rec)

    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# --- BULK LOCATIONS & EMPTY SLOTS (PRESERVED) ---
bulk_locations = []
empty_bulk_locations = []
location_counts = filtered_inventory_df.groupby("LocationName").size().reset_index(name="PalletCount")

for _, row in location_counts.iterrows():
    location = str(row["LocationName"])
    count = int(row["PalletCount"])
    zone = location[0].upper()
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

# --- LOGGING (PRESERVED) ---
def log_resolved_discrepancy_with_note(row, note, selected_lot, discrepancy_type):
    file_exists = os.path.isfile(resolved_file)
    with open(resolved_file, mode="a", newline="") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow([
                "Timestamp", "LocationName", "PalletId", "WarehouseSku",
                "CustomerLotReference", "Qty", "Note", "SelectedLOT", "DiscrepancyType"
            ])
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

# --- NAVIGATION (safe default + pending_nav pattern) ---
nav_options = [
    "Dashboard",
    "Empty Bins",
    "Full Pallet Bins",
    "Empty Partial Bins",
    "Partial Bins",
    "Damages",
    "Missing",
    "Rack Discrepancies",
    "Bulk Discrepancies",
    "Bulk Locations",
    "Empty Bulk Locations",
    "Self-Test"  # internal guard page; safe, read-only
]

# If a button requested navigation, consume it BEFORE creating the radio
_default_nav = st.session_state.get("nav", "Dashboard")
if "pending_nav" in st.session_state:
    _default_nav = st.session_state.pop("pending_nav", _default_nav)

# Create the radio with an index derived from default
try:
    _default_index = nav_options.index(_default_nav) if _default_nav in nav_options else 0
except ValueError:
    _default_index = 0

selected_nav = st.radio("🔍 Navigate:", nav_options, index=_default_index, horizontal=True, key="nav")
st.markdown("---")

# --- DASHBOARD VIEW ---
if selected_nav == "Dashboard":
    st.subheader("📊 Bin Helper Dashboard")

    # KPI cards with "View" buttons that jump to tabs (safe pending_nav + rerun)
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Empty Bins", len(empty_bins_view_df))
        if st.button("View", key="btn_empty"):
            st.session_state["pending_nav"] = "Empty Bins"
            _rerun()
    with col2:
        st.metric("Empty Partial Bins", len(empty_partial_bins_df))
        if st.button("View", key="btn_empty_partial"):
            st.session_state["pending_nav"] = "Empty Partial Bins"
            _rerun()
    with col3:
        st.metric("Partial Bins", len(partial_bins_df))
        if st.button("View", key="btn_partial"):
            st.session_state["pending_nav"] = "Partial Bins"
            _rerun()
    with col4:
        st.metric("Full Pallet Bins", len(full_pallet_bins_df))
        if st.button("View", key="btn_full"):
            st.session_state["pending_nav"] = "Full Pallet Bins"
            _rerun()
    with col5:
        st.metric("Damages", len(damages_df))
        if st.button("View", key="btn_damage"):
            st.session_state["pending_nav"] = "Damages"
            _rerun()
    with col6:
        st.metric("Missing", len(missing_df))
        if st.button("View", key="btn_missing"):
            st.session_state["pending_nav"] = "Missing"
            _rerun()

    # KPI bar chart (unchanged)
    kpi_data = {
        "Category": ["Empty Bins", "Empty Partial Bins", "Partial Bins", "Full Pallet Bins", "Damages", "Missing"],
        "Count": [
            len(empty_bins_view_df),
            len(empty_partial_bins_df),
            len(partial_bins_df),
            len(full_pallet_bins_df),
            len(damages_df),
            len(missing_df)
        ]
    }
    kpi_df = pd.DataFrame(kpi_data)
    fig = px.bar(kpi_df, x="Category", y="Count", title="Bin Status Distribution", text="Count")
    st.plotly_chart(fig, use_container_width=True)

# --- TAB VIEWS (PRESERVED) ---
elif selected_nav == "Empty Bins":
    st.subheader("Empty Bins")
    st.dataframe(empty_bins_view_df, use_container_width=True)

elif selected_nav == "Empty Partial Bins":
    st.subheader("Empty Partial Bins")
    st.dataframe(empty_partial_bins_df, use_container_width=True)

elif selected_nav == "Partial Bins":
    st.subheader("Partial Bins")
    st.dataframe(partial_bins_df, use_container_width=True)

elif selected_nav == "Full Pallet Bins":
    st.subheader("Full Pallet Bins")
    st.dataframe(full_pallet_bins_df, use_container_width=True)

elif selected_nav == "Damages":
    st.subheader("Damaged Pallets")
    st.dataframe(damages_df, use_container_width=True)

elif selected_nav == "Missing":
    st.subheader("Missing Pallets")
    st.dataframe(missing_df, use_container_width=True)

elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    if not discrepancy_df.empty:
        st.dataframe(discrepancy_df, use_container_width=True)
        csv_data = discrepancy_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Rack Discrepancies CSV", csv_data, "rack_discrepancies.csv", "text/csv")
    else:
        st.info("No rack discrepancies found.")

elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")
    if not bulk_df.empty:
        # Show only SKU, LOT, Pallet ID (display); keep full CSV for download
        display_cols = [c for c in ["WarehouseSku", "CustomerLotReference", "PalletId"] if c in bulk_df.columns]
        if display_cols:
            st.dataframe(bulk_df[display_cols], use_container_width=True)
        else:
            st.dataframe(bulk_df, use_container_width=True)

        csv_data = bulk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Bulk Discrepancies CSV", csv_data, "bulk_discrepancies.csv", "text/csv")

        st.subheader("✅ Fix Discrepancy by LOT")
        lot_list = bulk_df["CustomerLotReference"].dropna().unique().tolist()
        if lot_list:
            selected_lot = st.selectbox("Select LOT to fix:", lot_list)
            note = st.text_input(f"Add note for LOT {selected_lot}:")
            if st.button("Fix Selected LOT"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"] == selected_lot]
                for _, row in rows_to_fix.iterrows():
                    log_resolved_discrepancy_with_note(row.to_dict(), note, selected_lot, "Bulk")
                st.success(f"All discrepancies for LOT {selected_lot} marked as fixed ({len(rows_to_fix)} pallets).")
        else:
            st.info("No LOT options available for bulk discrepancies.")
    else:
        st.info("No bulk discrepancies found.")

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.dataframe(bulk_locations_df, use_container_width=True)

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations")
    st.dataframe(empty_bulk_locations_df, use_container_width=True)

# --- SELF-TEST (WARN vs FAIL classification; no rule changes) ---
elif selected_nav == "Self-Test":
    st.subheader("✅ Rule Self-Checks (Read-only)")
    problems = []
    notes = []

    # 1) OB/IB should be excluded
    if any(filtered_inventory_df["LocationName"].str.upper().str.startswith(("OB", "IB"))):
        problems.append("OB/IB locations leaked into filtered inventory.")

    # 2) Partial bins pattern validation
    pb = get_partial_bins(filtered_inventory_df)
    if not pb.empty:
        s2 = pb["LocationName"].astype(str)
        mask_ok = (
            s2.str.endswith("01") &
            ~s2.str.startswith("111") &
            ~s2.str.upper().str.startswith("TUN") &
            s2.str[0].str.isdigit()
        )
        if (~mask_ok).any():
            problems.append("Some Partial Bins fail the 01/111/TUN/digit rule.")

    # 3) Full-rack Qty range check -> classify as WARN when properly flagged
    s3 = filtered_inventory_df["LocationName"].astype(str)
    full_mask = ((~s3.str.endswith("01")) | s3.str.startswith("111")) & s3.str.isnumeric()
    fdf = filtered_inventory_df.loc[full_mask].copy()
    offenders = pd.DataFrame()
    not_flagged = pd.DataFrame()

    if not fdf.empty:
        offenders = fdf[~fdf["Qty"].between(6, 15)].copy()

        if not offenders.empty and not discrepancy_df.empty:
            # Choose key set for matching
            if "PalletId" in offenders.columns and "PalletId" in discrepancy_df.columns:
                key_cols = ["LocationName", "PalletId"]
            else:
                key_cols = [c for c in ["LocationName", "WarehouseSku", "CustomerLotReference", "Qty"]
                            if c in offenders.columns and c in discrepancy_df.columns]

            if key_cols:
                off_keys = offenders[key_cols].drop_duplicates()
                disc_filt = discrepancy_df
                if "Issue" in disc_filt.columns:
                    disc_filt = disc_filt[disc_filt["Issue"] == "Partial Pallet needs to be moved to Partial Location"]
                disc_keys = disc_filt[key_cols].drop_duplicates()
                merged = off_keys.merge(disc_keys, on=key_cols, how="left", indicator=True)
                missing_mask = merged["_merge"].eq("left_only")
                if missing_mask.any():
                    not_flagged = offenders.merge(merged.loc[missing_mask, key_cols], on=key_cols, how="inner")
            else:
                notes.append("Self-Test: could not build a reliable match key; skipping 'not-flagged' verification.")

    # 4) Ensure MISSING is not included in discrepancy base set
    if "MISSING" in filtered_inventory_df["LocationName"].str.upper().unique():
        problems.append("MISSING found in filtered inventory (should be separate).")

    # --- Reporting ---
    if problems:
        st.error("❌ FAIL")
        for p in problems:
            st.write("- ", p)
    else:
        if offenders.empty:
            st.success("🎉 PASS — All baseline rules intact (no full-rack Qty offenders found).")
        else:
            if not_flagged.empty:
                st.warning(f"⚠️ WARN — {len(offenders)} full-rack rows have Qty outside 6..15 (expected as discrepancies, and all are flagged).")
                with st.expander("Show sample offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in offenders.columns]
                    st.dataframe(offenders[show_cols].head(10), use_container_width=True)
                if st.button("Go to Rack Discrepancies"):
                    st.session_state["pending_nav"] = "Rack Discrepancies"
                    _rerun()
            else:
                st.error(f"❌ FAIL — {len(not_flagged)} full-rack offenders are NOT shown in Rack Discrepancies (possible regression).")
                with st.expander("Show un-flagged offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in not_flagged.columns]
                    st.dataframe(not_flagged[show_cols].head(10), use_container_width=True)
                st.info("These rows should have Issue='Partial Pallet needs to be moved to Partial Location'. Investigate matching keys or transforms.")