import streamlit as st
import pandas as pd
import requests
import os
import re
from pathlib import Path
from streamlit_lottie import st_lottie  # pip install streamlit-lottie

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Bin Helper", layout="wide", initial_sidebar_state="expanded")

# -------------------- APP VERSION --------------------
APP_VERSION = "v1.3.3"

# -------------------- SESSION STATE --------------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Dashboard"
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "refresh_triggered" not in st.session_state:
    st.session_state.refresh_triggered = False

# ---- FIX LOG SETTINGS ----
FIX_LOG_COLUMNS = [
    "view","row_key","LocationName","PalletId","WarehouseSku","CustomerLotReference",
    "Issue","Note","FixedBy","FixedAt","Status"
]
FIX_LOG_PATH = Path(os.getenv("FIX_LOG_PATH", "fix_log.csv"))

# Initialize fix log in state
if "fix_log" not in st.session_state:
    if FIX_LOG_PATH.exists():
        try:
            st.session_state.fix_log = pd.read_csv(FIX_LOG_PATH)
        except Exception:
            st.session_state.fix_log = pd.DataFrame(columns=FIX_LOG_COLUMNS)
    else:
        st.session_state.fix_log = pd.DataFrame(columns=FIX_LOG_COLUMNS)

# Load resolved item keys from log
if not st.session_state.fix_log.empty and "row_key" in st.session_state.fix_log.columns:
    resolved_keys = st.session_state.fix_log.query("Status == 'resolved'")["row_key"].tolist()
    st.session_state.resolved_items = set(resolved_keys)

# -------------------- AUTO REFRESH --------------------
if st.session_state.auto_refresh or st.session_state.refresh_triggered:
    st.session_state.refresh_triggered = False
    st.rerun()

# -------------------- SIDEBAR CONTROLS --------------------
st.sidebar.markdown("### 🔄 Auto Refresh")
if st.sidebar.button("🔁 Refresh Now"):
    st.session_state.refresh_triggered = True
st.session_state.auto_refresh = st.sidebar.checkbox("Enable Auto Refresh", value=st.session_state.auto_refresh)

# Global quick filter for views
loc_query = st.sidebar.text_input("🔎 Filter by Location (contains)", "")

# -------------------- LOTTIE LOADER --------------------
def load_lottie(url: str):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_icon = load_lottie("https://assets10.lottiefiles.com/packages/lf20_jcikwtux.json")

# -------------------- LOAD DATA --------------------
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url    = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

@st.cache_data(show_spinner=True)
def load_data(inventory_url, master_url):
    inv = pd.read_excel(inventory_url, engine="openpyxl")
    # Try specific sheet, then fall back to first
    try:
        master = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    except Exception:
        master = pd.read_excel(master_url, engine="openpyxl")
    return inv, master

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
    st.stop()

# -------------------- DATA PREP --------------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

def _loc_series(df):
    return df["LocationName"].astype(str).str.strip()

def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    s = _loc_series(df).str.upper()
    mask = (~s.isin(["DAMAGE", "MISSING", "IBDAMAGE"])) & (~s.str.startswith("IB"))
    return df[mask].copy()

# -------------------- MASTER LOCATIONS EXTRACTION --------------------
def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())

# Expanded candidates (includes your headers)
MASTER_CANDIDATE_KEYS = {
    "locationname",
    "location",
    "bin",
    "loc",
    "locationcode",
    "masterlocation",
    "masterlocations",
    "racklocationcolumn",   # "Rack Location Column"
    "emptylocations",       # "Empty Locations"
}

# Hotfix rename to help detection (optional)
if "LocationName" not in master_df.columns:
    rename_map = {}
    if "Rack Location Column" in master_df.columns:
        rename_map["Rack Location Column"] = "LocationName"
    elif "Empty Locations" in master_df.columns:
        rename_map["Empty Locations"] = "LocationName"
    if rename_map:
        master_df = master_df.rename(columns=rename_map)

def extract_master_locations(master_df: pd.DataFrame):
    chosen_col = None
    for col in master_df.columns:
        if _normalize_col(col) in MASTER_CANDIDATE_KEYS or col == "LocationName":
            chosen_col = col
            break

    if chosen_col is None:
        # heuristic fallback
        object_like = [
            c for c in master_df.columns
            if master_df[c].dtype == "object" or str(master_df[c].dtype).startswith("string")
        ]
        if object_like:
            # pick the object-like column with most non-nulls
            object_like.sort(key=lambda c: master_df[c].notna().sum(), reverse=True)
            chosen_col = object_like[0]

    if chosen_col is None:
        st.error(
            "⚠️ Could not auto-detect the Master Location column in **Empty Bin Formula.xlsx**.\n\n"
            f"Found columns: `{list(master_df.columns)}`\n\n"
            "Rename the correct column to `LocationName` (or one of: Location, Bin, Loc, Location Code, MasterLocation)."
        )
        st.stop()

    master_locations = (
        master_df[chosen_col]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .unique()
        .tolist()
    )
    return master_locations, chosen_col

master_locations, master_col = extract_master_locations(master_df)

# Occupied locations from inventory (after exclusions)
occupied_locations = (
    exclude_damage_missing(inventory_df)["LocationName"]
    .dropna()
    .astype(str)
    .str.strip()
    .str.upper()
    .unique()
    .tolist()
)

# -------------------- BUSINESS LOGIC --------------------
filtered_inventory_df = exclude_damage_missing(inventory_df)

def get_partial_bins(df: pd.DataFrame) -> pd.DataFrame:
    df = exclude_damage_missing(df)
    loc = _loc_series(df)
    mask = (
        loc.str.endswith("01")
        & (~loc.str.startswith("111"))
        & (~loc.str.upper().str.startswith("TUN"))
        & (loc.str[0].str.isdigit())
        & (df["Qty"] > 0)
    )
    return df[mask].copy()

def get_full_pallet_bins(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full = (starts with 111) OR (not ending with 01 AND Qty > 5)
    Exclude TUN*, DAMAGE/MISSING/IB*
    """
    df = exclude_damage_missing(df)
    loc = _loc_series(df)
    mask_111 = loc.str.startswith("111")
    mask_non01_qty = (~loc.str.endswith("01")) & (df["Qty"] > 5)
    mask_not_tun = ~loc.str.upper().str.startswith("TUN")
    mask_digit = loc.str[0].str.isdigit()
    mask = (mask_111 | mask_non01_qty) & mask_not_tun & mask_digit
    return df[mask].copy()

def get_empty_partial_bins(master_locs, occupied_locs) -> pd.DataFrame:
    candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and (not loc.startswith("111")) and (not str(loc).upper().startswith("TUN")) and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_empty_bins_view(master_locs, occupied_locs) -> pd.DataFrame:
    empty_all = sorted(set(master_locs) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_all})

# -------------------- BULK ROW LOGIC (Inventory only) --------------------
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def analyze_bulk_rows(df: pd.DataFrame):
    df = df.copy()
    df["Zone"] = _loc_series(df).str[0].str.upper()

    # prefer nunique pallet IDs to avoid double count
    if "PalletId" in df.columns:
        row_counts = df.groupby("LocationName")["PalletId"].nunique()
    else:
        row_counts = df.groupby("LocationName")["Qty"].size()

    bulk_locations = []
    bulk_discrepancies = []
    empty_bulk_locations = []

    for location, count in row_counts.items():
        zone = str(location)[0].upper()
        max_pallets = bulk_rules.get(zone, None)
        if max_pallets is None:
            continue  # not a bulk zone we track

        entry = {
            "LocationName": location,
            "Zone": zone,
            "Pallets": int(count),
            "MaxAllowed": int(max_pallets),
        }
        bulk_locations.append(entry)

        if count > max_pallets:
            bulk_discrepancies.append({**entry, "Issue": f"Too many pallets in {location} (Max: {max_pallets})"})
        elif count < max_pallets:
            empty_bulk_locations.append({**entry, "Issue": f"{location} has empty pallet slots (Max: {max_pallets})"})

    return (
        pd.DataFrame(bulk_locations),
        pd.DataFrame(bulk_discrepancies),
        pd.DataFrame(empty_bulk_locations),
    )

bulk_locations_df, bulk_discrepancies_df, empty_bulk_locations_df = analyze_bulk_rows(filtered_inventory_df)

# -------------------- RACK DISCREPANCIES --------------------
def compute_rack_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Discrepancy heuristic:
      - Any 111*** rack (considered Full by rule) that has Qty <= 5 is flagged as 'Underfilled rack (expected full)'.
    Excludes DAMAGE/MISSING/IB*/TUN*.
    """
    df = exclude_damage_missing(df)
    loc = _loc_series(df)
    mask_111 = loc.str.startswith("111")
    mask_not_tun = ~loc.str.upper().str.startswith("TUN")
    mask_digit = loc.str[0].str.isdigit()

    rack_df = df[mask_111 & mask_not_tun & mask_digit].copy()
    underfilled = rack_df[rack_df["Qty"] <= 5].copy()
    if underfilled.empty:
        return pd.DataFrame(columns=["LocationName","Qty","PalletCount","Issue"])

    underfilled["Issue"] = underfilled.apply(
        lambda r: f"Underfilled rack (111*** expected full). Qty={int(r['Qty'])}, PalletCount={int(r.get('PalletCount',0))}",
        axis=1
    )
    return underfilled[["LocationName","Qty","PalletCount","Issue","PalletId","WarehouseSku","CustomerLotReference"]].copy()

rack_discrepancies_df = compute_rack_discrepancies(filtered_inventory_df)

# -------------------- BUILD OTHER VIEWS --------------------
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
empty_bins_view_df = get_empty_bins_view(master_locations, occupied_locations)
damages_df = inventory_df[_loc_series(inventory_df).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[_loc_series(inventory_df).str.upper() == "MISSING"]

# -------------------- FIX LOG HELPERS --------------------
def save_fix_log():
    try:
        st.session_state.fix_log.to_csv(FIX_LOG_PATH, index=False)
    except Exception:
        pass

def _safe_str(x):
    return "" if pd.isna(x) else str(x)

def make_row_key(view: str, row: dict | pd.Series) -> str:
    if isinstance(row, pd.Series):
        row = row.to_dict()
    parts = [
        view,
        _safe_str(row.get("LocationName", "")).upper(),
        _safe_str(row.get("PalletId", "")),
        _safe_str(row.get("WarehouseSku", "")),
        _safe_str(row.get("CustomerLotReference", "")),
    ]
    return "|".join(parts)

# ---- Modal dialog (Streamlit >= 1.30); safe no-op if not available ----
def _open_fix_dialog(view_name: str, row_dict: dict, issue_text: str):
    """Wrapper to open a dialog if supported; otherwise inline fallback."""
    try:
        @st.dialog("Fix Discrepancy")
        def _dialog(view_name_, row_dict_, issue_text_):
            st.write(f"**{row_dict_.get('LocationName','')}**")
            if issue_text_:
                st.caption(issue_text_)

            resolution = st.selectbox(
                "Resolution",
                ["Adjusted in WMS", "Moved pallet", "Corrected master", "Verified OK", "Other"],
                index=0,
            )
            note = st.text_area("What did you do to fix it?", placeholder="Add a short note…", height=120)

            if st.button("Save"):
                row_key = make_row_key(view_name_, row_dict_)
                entry = {
                    "view": view_name_,
                    "row_key": row_key,
                    "LocationName": row_dict_.get("LocationName", ""),
                    "PalletId": row_dict_.get("PalletId", ""),
                    "WarehouseSku": row_dict_.get("WarehouseSku", ""),
                    "CustomerLotReference": row_dict_.get("CustomerLotReference", ""),
                    "Issue": issue_text_,
                    "Note": f"[{resolution}] {note}".strip(),
                    "FixedBy": "Carlos Pacheco",
                    "FixedAt": pd.Timestamp.now().isoformat(),
                    "Status": "resolved",
                }
                st.session_state.fix_log = pd.concat(
                    [st.session_state.fix_log, pd.DataFrame([entry])],
                    ignore_index=True
                )
                st.session_state.resolved_items.add(row_key)
                save_fix_log()
                st.rerun()

        _dialog(view_name, row_dict, issue_text)
        return True
    except Exception:
        return False  # Dialog not available

def open_fix(view_name: str, row_dict: dict, issue_text: str):
    """Open modal if possible; otherwise inline form."""
    opened = _open_fix_dialog(view_name, row_dict, issue_text)
    if not opened:
        with st.form(f"inline_fix_form_{make_row_key(view_name, row_dict)}", clear_on_submit=True):
            st.write(f"**{row_dict.get('LocationName','')}** – {issue_text}")
            resolution = st.selectbox(
                "Resolution",
                ["Adjusted in WMS", "Moved pallet", "Corrected master", "Verified OK", "Other"],
                index=0,
            )
            note = st.text_area("What did you do to fix it?", height=100)
            submitted = st.form_submit_button("Save")
            if submitted:
                row_key = make_row_key(view_name, row_dict)
                entry = {
                    "view": view_name,
                    "row_key": row_key,
                    "LocationName": row_dict.get("LocationName", ""),
                    "PalletId": row_dict.get("PalletId", ""),
                    "WarehouseSku": row_dict.get("WarehouseSku", ""),
                    "CustomerLotReference": row_dict.get("CustomerLotReference", ""),
                    "Issue": issue_text,
                    "Note": f"[{resolution}] {note}".strip(),
                    "FixedBy": "Carlos Pacheco",
                    "FixedAt": pd.Timestamp.now().isoformat(),
                    "Status": "resolved",
                }
                st.session_state.fix_log = pd.concat(
                    [st.session_state.fix_log, pd.DataFrame([entry])],
                    ignore_index=True
                )
                st.session_state.resolved_items.add(row_key)
                save_fix_log()
                st.rerun()

# -------------------- GENERIC RENDERER WITH ROW "FIX" --------------------
def render_fixable_table(view_name: str, df: pd.DataFrame, display_cols: list[str], issue_col: str = "Issue"):
    df = df.copy()
    if df.empty:
        st.subheader(view_name)
        st.success("No issues 🎉")
        return

    # Build row key and last note map
    df["row_key"] = df.apply(lambda r: make_row_key(view_name, r), axis=1)

    if not st.session_state.fix_log.empty:
        last_notes = (
            st.session_state.fix_log
            .query("view == @view_name")
            .sort_values("FixedAt")
            .groupby("row_key")["Note"]
            .last()
            .to_dict()
        )
        df["Last Note"] = df["row_key"].map(last_notes).fillna("")
    else:
        df["Last Note"] = ""

    st.subheader(view_name)

    # Inline controls
    left, right = st.columns([1, 1])
    with left:
        hide_resolved = st.checkbox("Hide resolved", value=True, key=f"hide_{view_name.replace(' ','_')}")
    with right:
        if not st.session_state.fix_log.empty:
            st.download_button(
                "Download Fix Log (CSV)",
                st.session_state.fix_log.to_csv(index=False).encode("utf-8"),
                file_name="fix_log.csv",
                mime="text/csv",
            )

    # Apply hide resolved
    if hide_resolved:
        df = df[~df["row_key"].isin(st.session_state.resolved_items)]

    # Apply location filter
    if loc_query.strip():
        df = df[_loc_series(df).str.upper().str.contains(loc_query.strip().upper(), na=False)]

    if df.empty:
        st.info("Nothing to show with current filters.")
        return

    # Header
    widths = [3] * len(display_cols) + [3, 1.2]  # add Last Note + Action
    header_cols = st.columns(widths)
    for i, col in enumerate(display_cols):
        header_cols[i].markdown(f"**{col}**")
    header_cols[-2].markdown("**Last Note**")
    header_cols[-1].markdown("**Action**")

    # Rows
    for i, row in df.reset_index(drop=True).iterrows():
        row_cols = st.columns(widths)
        for j, col in enumerate(display_cols):
            val = row.get(col, "")
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            row_cols[j].write(val)

        row_cols[-2].write(row.get("Last Note", ""))

        # Button opens modal/inline form
        if row_cols[-1].button("Fix", key=f"fix_{view_name.replace(' ','_')}_{i}"):
            issue_text = row.get(issue_col, "")
            open_fix(view_name, row.to_dict(), str(issue_text))

# -------------------- SIDEBAR MENU --------------------
menu = st.sidebar.radio(
    "📂 Dashboard Menu",
    [
        "Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
        "Partial Bins", "Damages", "Missing", "Rack Discrepancies",
        "Bulk Locations", "Bulk Discrepancies", "Empty Bulk Locations",
    ]
)
st.session_state.active_view = menu

# -------------------- DASHBOARD VIEW --------------------
if st.session_state.active_view == "Dashboard":
    st_lottie(lottie_icon, height=150)
    st.markdown(
        f"<h1 style='text-align:center; color:#2E86C1;'>📊 Bin-Helper Dashboard "
        f"<span style='font-size:18px; color:gray;'>({APP_VERSION})</span></h1>",
        unsafe_allow_html=True,
    )

    total_bins_occupied = len(full_pallet_bins_df) + len(partial_bins_df)
    total_empty_bins = len(empty_bins_view_df) + len(empty_partial_bins_df)

    kpi_data = [
        {"title": "Total Bins Occupied", "value": total_bins_occupied, "icon": "📦"},
        {"title": "Total Empty Bins", "value": total_empty_bins, "icon": "🗑️"},
        {"title": "Bulk Locations", "value": len(bulk_locations_df), "icon": "📍"},
        {"title": "Empty Bulk Locations", "value": len(empty_bulk_locations_df), "icon": "🧯"},
        {"title": "Bulk Discrepancies", "value": len(bulk_discrepancies_df), "icon": "⚠️"},
    ]

    cols = st.columns(len(kpi_data))
    for i, item in enumerate(kpi_data):
        with cols[i]:
            st.markdown(
                f"""
                <div style="background-color:#1f77b4; padding:20px; border-radius:10px; text-align:center; color:white;">
                  <h3>{item['icon']} {item['title']}</h3>
                  <h2>{item['value']}</h2>
                </div>
                """,
                unsafe_allow_html=True,
            )

# -------------------- DISPLAY VIEWS --------------------
def apply_location_filter(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    if loc_query.strip():
        return df[_loc_series(df).str.upper().str.contains(loc_query.strip().upper(), na=False)]
    return df

# Registry
view_map = {
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df,
    "Bulk Locations": bulk_locations_df,
    "Bulk Discrepancies": bulk_discrepancies_df,
    "Empty Bulk Locations": empty_bulk_locations_df,
    "Rack Discrepancies": rack_discrepancies_df,
}

if st.session_state.active_view != "Dashboard":
    view_name = st.session_state.active_view

    # Row-level Fix UI for DISCREPANCY tabs (excluding Damages/Missing)
    if view_name == "Bulk Discrepancies":
        # Display columns for bulk discrepancies
        display_cols = ["LocationName", "Zone", "Pallets", "MaxAllowed", "Issue"]
        df_view = bulk_discrepancies_df.copy()
        # Ensure needed identifier columns exist (for row_key)
        for col in ["PalletId","WarehouseSku","CustomerLotReference"]:
            if col not in df_view.columns:
                df_view[col] = ""
        render_fixable_table("Bulk Discrepancies", df_view, display_cols, issue_col="Issue")

    elif view_name == "Rack Discrepancies":
        df_view = rack_discrepancies_df.copy()
        if df_view.empty:
            st.subheader("Rack Discrepancies")
            st.success("No rack discrepancies 🎉")
        else:
            # Ensure identifier columns exist
            for col in ["PalletId","WarehouseSku","CustomerLotReference"]:
                if col not in df_view.columns:
                    df_view[col] = ""
            display_cols = ["LocationName", "Qty", "PalletCount", "Issue"]
            render_fixable_table("Rack Discrepancies", df_view, display_cols, issue_col="Issue")

    else:
        raw_df = view_map.get(view_name, pd.DataFrame())
        st.subheader(view_name)
        st.dataframe(apply_location_filter(raw_df), use_container_width=True)