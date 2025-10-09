# -*- coding: utf-8 -*-
import os
import csv
import re
from datetime import datetime
import uuid
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
        try:
            st.experimental_rerun()
        except Exception:
            pass

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
    candidate_urls = [
        "https://assets10.lottiefiles.com/packages/lf20_9kmmv9.json",  # forklift
        "https://assets2.lottiefiles.com/packages/lf20_1pxqjqps.json",  # barcode boxes
        "https://assets9.lottiefiles.com/packages/lf20_wnqlfojb.json",  # logistics
        "https://assets10.lottiefiles.com/packages/lf20_j1adxtyb.json", # fallback
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
                ### Bin Helper

                Fast, visual lookups for **Empty**, **Partial**, **Full**, **Damages**, and **Missing** — all by your warehouse rules.
                """,
                unsafe_allow_html=True
            )

# ---- Persistent banner on every tab ----
show_banner()

# --- FILE PATHS ---
inventory_file = "ON_HAND_INVENTORY.xlsx"
master_file = "Empty Bin Formula.xlsx"

# Preferred logs directory (auto-created) — per your preference
LOG_DIR = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Store the action log in your logs folder and include Issue
resolved_file = os.path.join(LOG_DIR, "resolved_discrepancies.csv")

# --- LOAD DATA ---
inventory_df = pd.read_excel(inventory_file, engine="openpyxl")
master_df = pd.read_excel(master_file, sheet_name="Master Locations", engine="openpyxl")

# --- DATA PREP (PRESERVED RULES) ---
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)
inventory_df["LocationName"] = inventory_df["LocationName"].astype(str)

# Exclude OB and IB globally
inventory_df = inventory_df[~inventory_df["LocationName"].str.upper().str.startswith(("OB", "IB"))].copy()

# Bulk rules (unchanged)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

# --- MASTER LOCATIONS (robust parse; intent unchanged) ---
def extract_master_locations(df: pd.DataFrame) -> set:
    """
    Get the list of master locations.
    We preserve intent by taking the column that looks like 'location' (or the first col),
    trimming and returning unique non-null strings.
    """
    for c in df.columns:
        if "location" in str(c).lower():
            s = df[c].dropna().astype(str).str.strip()
            return set(s.unique().tolist())
    s = df.iloc[:, 0].dropna().astype(str).str.strip()
    return set(s.unique().tolist())

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
    mask = (
        ((~s.str.endswith("01")) | (s.str.startswith("111")))
        & s.str.isnumeric()
        & df2["Qty"].between(6, 15)
    )
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

    # Partial errors
    p_df = get_partial_bins(df2)
    if not p_df.empty:
        pe = p_df[(p_df["Qty"] > 5) | (p_df["PalletCount"] > 1)]
        for _, row in pe.iterrows():
            issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
            rec = row.to_dict()
            rec["Issue"] = issue
            results.append(rec)

    # Full rack errors
    s = df2["LocationName"].astype(str)
    full_mask = (((~s.str.endswith("01")) | s.str.startswith("111")) & s.str.isnumeric())
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
                "LocationName": location, "Zone": zone, "EmptySlots": empty_slots
            })

bulk_locations_df = pd.DataFrame(bulk_locations)
empty_bulk_locations_df = pd.DataFrame(empty_bulk_locations)

# --- LOGGING (ENHANCED: Action, BatchId, RowKey + include Issue + OneDrive path) ---
def _row_key(row: dict, discrepancy_type: str) -> str:
    fields = [
        str(row.get("LocationName", "")),
        str(row.get("PalletId", "")),
        str(row.get("WarehouseSku", "")),
        str(row.get("CustomerLotReference", "")),
        str(row.get("Qty", "")),
        discrepancy_type
    ]
    return "\n".join(fields)

def _write_header_if_needed(writer, file_exists: bool):
    if not file_exists:
        writer.writerow([
            "Timestamp", "Action", "BatchId", "DiscrepancyType", "RowKey",
            "LocationName", "PalletId", "WarehouseSku", "CustomerLotReference",
            "Qty", "Issue", "Note", "SelectedLOT"
        ])

def log_action(row: dict, note: str, selected_lot: str, discrepancy_type: str, action: str, batch_id: str):
    file_exists = os.path.isfile(resolved_file)
    with open(resolved_file, mode="a", newline="") as f:
        w = csv.writer(f)
        _write_header_if_needed(w, file_exists)
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action,
            batch_id,
            discrepancy_type,
            _row_key(row, discrepancy_type),
            row.get("LocationName", ""),
            row.get("PalletId", ""),
            row.get("WarehouseSku", ""),
            row.get("CustomerLotReference", ""),
            row.get("Qty", ""),
            row.get("Issue", ""),   # <-- Include Issue in log
            note,
            selected_lot
        ])

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str):
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")  # sortable
    for _, r in df_rows.iterrows():
        log_action(r.to_dict(), note, selected_lot, discrepancy_type, action, batch_id)
    return batch_id

def read_action_log() -> pd.DataFrame:
    if os.path.isfile(resolved_file):
        try:
            return pd.read_csv(resolved_file)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

# --- DISPLAY HELPERS (LOT formatting, narrow columns) ---
CORE_COLS = ["LocationName", "WarehouseSku", "PalletId", "CustomerLotReference", "Qty"]

def _lot_to_str(x):
    # Display LOT numbers as whole numbers (no decimals/scientific)
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    # Numeric types
    if isinstance(x, (int,)):
        return str(int(x))
    if isinstance(x, float):
        return str(int(round(x)))
    s = str(x).strip()
    # Strings like "9063350.0"
    if re.fullmatch(r"\d+(\.0+)?", s):
        return s.split(".")[0]
    return s

def ensure_core(df: pd.DataFrame, include_issue: bool = False) -> pd.DataFrame:
    """Return df with only core columns (and Issue if requested), adding blanks if cols are missing, with LOT formatted."""
    if df is None or df.empty:
        out = pd.DataFrame(columns=CORE_COLS + (["Issue"] if include_issue else []))
        return out
    out = df.copy()
    for c in CORE_COLS:
        if c not in out.columns:
            out[c] = ""
    # LOT formatting for display
    out["CustomerLotReference"] = out["CustomerLotReference"].apply(_lot_to_str)
    cols = CORE_COLS.copy()
    if include_issue and "Issue" in out.columns:
        cols += ["Issue"]
    return out[cols]

def style_issue_red(df: pd.DataFrame):
    """Make Issue column red & bold if present."""
    if "Issue" in df.columns:
        return df.style.set_properties(subset=["Issue"], **{"color": "red", "font-weight": "bold"})
    return df

# --- NAVIGATION (safe default + pending_nav pattern) ---
nav_options = [
    "Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
    "Partial Bins", "Damages", "Missing",
    "Rack Discrepancies", "Bulk Discrepancies",
    "Bulk Locations", "Empty Bulk Locations", "Self-Test"
]

_default_nav = st.session_state.get("nav", "Dashboard")
if "pending_nav" in st.session_state:
    _default_nav = st.session_state.pop("pending_nav", _default_nav)
try:
    _default_index = nav_options.index(_default_nav) if _default_nav in nav_options else 0
except ValueError:
    _default_index = 0

selected_nav = st.radio("🔍 Navigate:", nav_options, index=_default_index, horizontal=True, key="nav")
st.markdown("---")

# --- DASHBOARD VIEW (extra charts) ---
if selected_nav == "Dashboard":
    st.subheader("📊 Bin Helper Dashboard")

    # KPIs with "View" buttons
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Empty Bins", len(empty_bins_view_df))
        if st.button("View", key="btn_empty"):
            st.session_state["pending_nav"] = "Empty Bins"; _rerun()
    with col2:
        st.metric("Empty Partial Bins", len(empty_partial_bins_df))
        if st.button("View", key="btn_empty_partial"):
            st.session_state["pending_nav"] = "Empty Partial Bins"; _rerun()
    with col3:
        st.metric("Partial Bins", len(partial_bins_df))
        if st.button("View", key="btn_partial"):
            st.session_state["pending_nav"] = "Partial Bins"; _rerun()
    with col4:
        st.metric("Full Pallet Bins", len(full_pallet_bins_df))
        if st.button("View", key="btn_full"):
            st.session_state["pending_nav"] = "Full Pallet Bins"; _rerun()
    with col5:
        st.metric("Damages", len(damages_df))
        if st.button("View", key="btn_damage"):
            st.session_state["pending_nav"] = "Damages"; _rerun()
    with col6:
        st.metric("Missing", len(missing_df))
        if st.button("View", key="btn_missing"):
            st.session_state["pending_nav"] = "Missing"; _rerun()

    # Original bar
    kpi_data = {
        "Category": ["Empty Bins", "Empty Partial Bins", "Partial Bins", "Full Pallet Bins", "Damages", "Missing"],
        "Count": [
            len(empty_bins_view_df), len(empty_partial_bins_df), len(partial_bins_df),
            len(full_pallet_bins_df), len(damages_df), len(missing_df)
        ]
    }
    kpi_df = pd.DataFrame(kpi_data)
    st.plotly_chart(px.bar(kpi_df, x="Category", y="Count", title="Bin Status Distribution", text="Count"),
                    use_container_width=True)

    # NEW: Racks Full vs Empty (unique rack locations)
    def is_rack_slot(loc: str) -> bool:
        s = str(loc)
        return s.isnumeric() and (((not s.endswith("01")) or s.startswith("111")))

    rack_master = {loc for loc in master_locations if is_rack_slot(loc)}
    rack_full_used = set(full_pallet_bins_df["LocationName"].astype(str).unique())
    rack_empty = rack_master - occupied_locations

    pie_df = pd.DataFrame({"Status": ["Full", "Empty"],
                           "Locations": [len(rack_full_used & rack_master), len(rack_empty)]})
    st.plotly_chart(px.pie(pie_df, names="Status", values="Locations", title="Racks: Full vs Empty (unique slots)"),
                    use_container_width=True)

    # NEW: Bulk Zones — Used vs Empty capacity (sum across slots)
    if not bulk_locations_df.empty:
        bulk_zone = bulk_locations_df.groupby("Zone").agg(
            Used=("PalletCount", "sum"),
            Capacity=("MaxAllowed", "sum")
        ).reset_index()
        bulk_zone["Empty"] = (bulk_zone["Capacity"] - bulk_zone["Used"]).clip(lower=0)
        bulk_stack = bulk_zone.melt(id_vars="Zone", value_vars=["Used", "Empty"],
                                    var_name="Type", value_name="Count")
        st.plotly_chart(px.bar(bulk_stack, x="Zone", y="Count", color="Type", barmode="stack",
                               title="Bulk Zones: Used vs Empty Capacity"),
                        use_container_width=True)

    # NEW: Damages vs Missing
    dm_df = pd.DataFrame({"Status": ["Damages", "Missing"], "Count": [len(damages_df), len(missing_df)]})
    st.plotly_chart(px.bar(dm_df, x="Status", y="Count", text="Count", title="Damages vs Missing"),
                    use_container_width=True)

# --- TAB VIEWS (5 columns everywhere; no extra grouping) ---
elif selected_nav == "Empty Bins":
    st.subheader("Empty Bins")
    display = ensure_core(empty_bins_view_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    st.dataframe(display, use_container_width=True)

elif selected_nav == "Empty Partial Bins":
    st.subheader("Empty Partial Bins")
    display = ensure_core(empty_partial_bins_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    st.dataframe(display, use_container_width=True)

elif selected_nav == "Partial Bins":
    st.subheader("Partial Bins")
    st.dataframe(ensure_core(partial_bins_df), use_container_width=True)

elif selected_nav == "Full Pallet Bins":
    st.subheader("Full Pallet Bins")
    st.dataframe(ensure_core(full_pallet_bins_df), use_container_width=True)

elif selected_nav == "Damages":
    st.subheader("Damaged Pallets")
    st.dataframe(ensure_core(damages_df), use_container_width=True)

elif selected_nav == "Missing":
    st.subheader("Missing Pallets")
    st.dataframe(ensure_core(missing_df), use_container_width=True)

# --- DISCREPANCIES (Issue in red + Fix LOT + Undo) ---
elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    if not discrepancy_df.empty:
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique()])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="rack_lot_filter")
        filt = discrepancy_df if sel_lot == "(All)" else discrepancy_df[discrepancy_df["CustomerLotReference"].apply(_lot_to_str) == sel_lot]
        rack_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(rack_display), use_container_width=True)

        csv_data = discrepancy_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Rack Discrepancies CSV", csv_data, "rack_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = sorted([_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique()])
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="rack_fix_lot")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="rack_fix_note")
            if st.button("Fix Selected LOT", key="rack_fix_btn"):
                rows_to_fix = discrepancy_df[discrepancy_df["CustomerLotReference"].apply(_lot_to_str) == chosen_lot]
                batch_id = log_batch(rows_to_fix, note, chosen_lot, "Rack", action="RESOLVE")
                st.success(f"Resolved {len(rows_to_fix)} rack discrepancy row(s) for LOT {chosen_lot}. BatchId={batch_id}")

        with st.expander("Recent discrepancy actions (Rack) & Undo"):
            log_df = read_action_log()
            if not log_df.empty:
                rack_log = log_df[log_df["DiscrepancyType"] == "Rack"].sort_values("Timestamp", ascending=False).head(20)
                st.dataframe(rack_log, use_container_width=True)
                if not rack_log.empty and st.button("Undo last Rack RESOLVE batch"):
                    # find last RESOLVE batch for Rack
                    last_resolve = log_df[(log_df["DiscrepancyType"] == "Rack") & (log_df["Action"] == "RESOLVE")]
                    if not last_resolve.empty:
                        last_batch = last_resolve.sort_values("Timestamp").iloc[-1]["BatchId"]
                        rows = last_resolve[last_resolve["BatchId"] == last_batch]
                        for _, r in rows.iterrows():
                            log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT",""), "Rack", "UNDO", str(last_batch))
                        st.success(f"UNDO recorded for batch {last_batch} ({len(rows)} row(s)).")
                    else:
                        st.info("No RESOLVE actions to undo for Rack.")
            else:
                st.info("No actions logged yet.")
    else:
        st.info("No rack discrepancies found.")

elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")

    if not bulk_df.empty:
        # ---- Existing LOT filter (preserved) ----
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique()])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="bulk_lot_filter")
        filt = bulk_df if sel_lot == "(All)" else bulk_df[bulk_df["CustomerLotReference"].apply(_lot_to_str) == sel_lot]

        # =======================
        # NEW: Grouped by Location
        # =======================
        st.markdown("#### Grouped by Location (click to expand)")
        loc_search = st.text_input("Search location (optional)", value="", key="bulk_loc_search")
        df2 = filt.copy()
        if loc_search.strip():
            df2 = df2[df2["LocationName"].astype(str).str.contains(loc_search.strip(), case=False, na=False)]

        if df2.empty:
            st.info("No bulk discrepancies match your filters.")
        else:
            # Build summaries by LocationName
            grp = df2.groupby("LocationName", dropna=False)
            # Sort groups by severity (count desc, then total Qty desc)
            summaries = []
            for gkey, gdf in grp:
                count = len(gdf)
                total_qty = pd.to_numeric(gdf.get("Qty", 0), errors="coerce").fillna(0).sum()
                summaries.append((gkey, count, total_qty))
            summaries.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)

            st.caption(f"{len(summaries)} grouped location(s)")
            display_cols = [c for c in [
                "WarehouseSku",            # SKU
                "CustomerLotReference",    # LOT Number
                "PalletId",                # Pallet ID
                "Qty",
                "Issue"
            ] if c in df2.columns]

            for gkey, count, total_qty in summaries:
                gdf = grp.get_group(gkey).reset_index(drop=True)
                header = f"{gkey} • {count} row(s) • Qty Σ = {total_qty:+}"
                with st.expander(header, expanded=False):
                    st.dataframe(
                        style_issue_red(gdf[display_cols]),
                        use_container_width=True
                    )

                    # Per-row logging right here
                    st.write("Log a fix for a specific row:")
                    r_idx = st.selectbox(
                        f"Select row in {gkey} to log",
                        options=range(len(gdf)),
                        format_func=lambda i: f"{gdf.iloc[i].get('WarehouseSku','')} | "
                                              f"LOT { _lot_to_str(gdf.iloc[i].get('CustomerLotReference','')) } | "
                                              f"Pallet {gdf.iloc[i].get('PalletId','')}",
                        key=f"bulk_row_select_{gkey}"
                    )
                    note = st.text_input("Note (optional)", key=f"bulk_note_{gkey}_{r_idx}")

                    if st.button(f"Log Fix for selected row", key=f"bulk_logfix_{gkey}_{r_idx}"):
                        row = gdf.iloc[r_idx].to_dict()
                        batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
                        log_action(
                            row=row,
                            note=note,
                            selected_lot=_lot_to_str(row.get("CustomerLotReference","")),
                            discrepancy_type="Bulk",
                            action="RESOLVE",
                            batch_id=batch_id
                        )
                        st.success(f"Logged fix for {gkey}. BatchId={batch_id}")

        # ---- Flat view (preserved) + CSV ----
        st.markdown("#### Flat view (all rows)")
        bulk_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(bulk_display), use_container_width=True)

        csv_data = bulk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Bulk Discrepancies CSV", csv_data, "bulk_discrepancies.csv", "text/csv")

        # ---- Fix by LOT (preserved) ----
        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique()])
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="bulk_fix_lot")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="bulk_fix_note")
            if st.button("Fix Selected LOT", key="bulk_fix_btn"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"].apply(_lot_to_str) == chosen_lot]
                batch_id = log_batch(rows_to_fix, note, chosen_lot, "Bulk", action="RESOLVE")
                st.success(f"Resolved {len(rows_to_fix)} bulk discrepancy row(s) for LOT {chosen_lot}. BatchId={batch_id}")

        # ---- Recent actions + Undo (preserved) ----
        with st.expander("Recent discrepancy actions (Bulk) & Undo"):
            log_df = read_action_log()
            if not log_df.empty:
                bulk_log = log_df[log_df["DiscrepancyType"] == "Bulk"].sort_values("Timestamp", ascending=False).head(20)
                st.dataframe(bulk_log, use_container_width=True)

                if not bulk_log.empty and st.button("Undo last Bulk RESOLVE batch"):
                    last_resolve = log_df[(log_df["DiscrepancyType"] == "Bulk") & (log_df["Action"] == "RESOLVE")]
                    if not last_resolve.empty:
                        last_batch = last_resolve.sort_values("Timestamp").iloc[-1]["BatchId"]
                        rows = last_resolve[last_resolve["BatchId"] == last_batch]
                        for _, r in rows.iterrows():
                            log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT",""), "Bulk", "UNDO", str(last_batch))
                        st.success(f"UNDO recorded for batch {last_batch} ({len(rows)} row(s)).")
                    else:
                        st.info("No RESOLVE actions to undo for Bulk.")
            else:
                st.info("No actions logged yet.")
    else:
        st.info("No bulk discrepancies found.")

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.dataframe(bulk_locations_df, use_container_width=True)

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations")
    st.dataframe(empty_bulk_locations_df, use_container_width=True)

# --- SELF-TEST (WARN vs FAIL; preserved) ---
elif selected_nav == "Self-Test":
    st.subheader("✅ Rule Self-Checks (Read-only)")
    problems = []

    # 1) OB/IB excluded
    if any(filtered_inventory_df["LocationName"].str.upper().str.startswith(("OB", "IB"))):
        problems.append("OB/IB locations leaked into filtered inventory.")

    # 2) Partial pattern
    pb = get_partial_bins(filtered_inventory_df)
    if not pb.empty:
        s2 = pb["LocationName"].astype(str)
        mask_ok = (s2.str.endswith("01") & ~s2.str.startswith("111") & ~s2.str.upper().str.startswith("TUN") & s2.str[0].str.isdigit())
        if (~mask_ok).any():
            problems.append("Some Partial Bins fail the 01/111/TUN/digit rule.")

    # 3) Full-rack Qty range -> WARN if properly flagged
    s3 = filtered_inventory_df["LocationName"].astype(str)
    full_mask = (((~s3.str.endswith("01")) | s3.str.startswith("111")) & s3.str.isnumeric())
    fdf = filtered_inventory_df.loc[full_mask].copy()
    offenders = pd.DataFrame()
    not_flagged = pd.DataFrame()
    if not fdf.empty:
        offenders = fdf[~fdf["Qty"].between(6, 15)].copy()
        if not offenders.empty and not discrepancy_df.empty:
            if "PalletId" in offenders.columns and "PalletId" in discrepancy_df.columns:
                key_cols = ["LocationName", "PalletId"]
            else:
                key_cols = [c for c in ["LocationName", "WarehouseSku", "CustomerLotReference", "Qty"] if c in offenders.columns and c in discrepancy_df.columns]
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

    # 4) MISSING not in filtered base
    if "MISSING" in filtered_inventory_df["LocationName"].str.upper().unique():
        problems.append("MISSING found in filtered inventory (should be separate).")

    if problems:
        st.error("❌ FAIL")
        for p in problems:
            st.write("- ", p)
    else:
        if offenders.empty:
            st.success("🎉 PASS — All baseline rules intact (no full-rack Qty offenders found).")
        else:
            if not not_flagged.empty:
                st.error(f"❌ FAIL — {len(not_flagged)} full-rack offenders are NOT shown in Rack Discrepancies (possible regression).")
                with st.expander("Show un-flagged offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in not_flagged.columns]
                    st.dataframe(not_flagged[show_cols].head(10), use_container_width=True)
            else:
                st.warning(f"⚠️ WARN — {len(offenders)} full-rack rows have Qty outside 6..15 (expected discrepancies, and all are flagged).")
                with st.expander("Show sample offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in offenders.columns]
                    st.dataframe(offenders[show_cols].head(10), use_container_width=True)
                if st.button("Go to Rack Discrepancies"):
