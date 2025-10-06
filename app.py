import streamlit as st
import pandas as pd
import re
import requests

from streamlit_lottie import st_lottie  # pip install streamlit-lottie

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Bin Helper", layout="wide", initial_sidebar_state="expanded")

# -------------------- APP VERSION --------------------
APP_VERSION = "v1.3.4"  # robust master column detection + cleanup

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
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

lottie_icon = load_lottie("https://assets10.lottiefiles.com/packages/lf20_jcikwtux.json")

# -------------------- LOAD DATA --------------------
# Remote canonical sources (Streamlit Cloud)
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url    = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

@st.cache_data(show_spinner=True)
def load_data(inventory_url, master_url):
    # Inventory
    inventory_df = pd.read_excel(inventory_url, engine="openpyxl")

    # Master (try specific sheet, else first sheet)
    try:
        master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    except Exception:
        master_df = pd.read_excel(master_url, engine="openpyxl")
    return inventory_df, master_df

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
    st.stop()

# -------------------- DATA PREP --------------------
# Numeric coercion
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

def _loc_series(df: pd.DataFrame) -> pd.Series:
    return df["LocationName"].astype(str).str.strip()

# Exclusions (DAMAGE/IBDAMAGE/MISSING and any "IB*" locations)
def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    s = _loc_series(df).str.upper()
    mask = (~s.isin(["DAMAGE", "MISSING", "IBDAMAGE"])) & (~s.str.startswith("IB"))
    return df[mask].copy()

# -------------------- MASTER LOCATIONS EXTRACTION --------------------
def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())

# Accept any of these if found (we'll union them)
MASTER_CANDIDATE_KEYS = {
    "locationname",
    "location",
    "racklocationcolumn",
    "emptylocations",
    "bin",
    "loc",
    "locationcode",
    "masterlocation",
    "masterlocations",
}

# Fast validator for location-looking strings (keeps TUN#### or alphanumeric bins, no spaces)
_location_re = re.compile(r"^(?:TUN\d{3,}|[A-Z]?\d{5,}|[A-Z]{1,3}\d{3,})$")

def _is_valid_location(val: str) -> bool:
    v = str(val).strip().upper()
    # Drop obvious noise
    if not v or v in {"N/A", "#N/A", "NA"}:
        return False
    if "EMPTY" in v or "UNASSIGNED" in v or "UNNAMED" in v or "SHEET" in v or "MASTER" in v:
        return False
    if " " in v or "\t" in v:
        return False
    # Accept common formats
    return bool(_location_re.match(v))

def extract_master_locations(master_df: pd.DataFrame):
    # Gather any candidate columns present
    candidate_cols = []
    for col in master_df.columns:
        if _normalize_col(col) in MASTER_CANDIDATE_KEYS:
            candidate_cols.append(col)

    # If none detected, attempt a heuristic: any object-like column with many non-null values
    if not candidate_cols:
        for col in master_df.columns:
            ser = master_df[col]
            if ser.dtype == "object" or str(ser.dtype).startswith("string"):
                if ser.notna().sum() >= 5:  # arbitrary threshold
                    candidate_cols.append(col)

    if not candidate_cols:
        st.error(
            "⚠️ Could not detect a master location column in **Empty Bin Formula.xlsx**.\n"
            f"Found columns: `{list(master_df.columns)}`\n"
            "Try renaming your location column to one of: LocationName, Location, Rack Location Column, Empty Locations."
        )
        st.stop()

    # Union values from all candidate columns, clean, uppercase, dedupe
    master_values = []
    for col in candidate_cols:
        ser = master_df[col].dropna().astype(str).str.strip()
        master_values.extend(ser.tolist())

    # Clean & filter to plausible locations
    master_locations = sorted(set(v.upper() for v in master_values if _is_valid_location(v)))
    if not master_locations:
        st.error(
            "⚠️ After cleaning, no valid master locations were found. "
            "Please check the sheet headers and ensure the location list isn't mixed with notes."
        )
        st.stop()

    return master_locations, candidate_cols

master_locations, master_cols_used = extract_master_locations(master_df)

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

# -------------------- INVENTORY FILTER (DAMAGE/MISSING already excluded for most logic) --------------------
filtered_inventory_df = exclude_damage_missing(inventory_df)

# -------------------- BUSINESS LOGIC HELPERS --------------------
def get_partial_bins(df: pd.DataFrame) -> pd.DataFrame:
    df = exclude_damage_missing(df)
    loc = _loc_series(df)
    mask = (
        loc.str.endswith("01")
        & (~loc.str.startswith("111"))                  # not rack 111***
        & (~loc.str.upper().str.startswith("TUN"))      # exclude tunnels
        & (loc.str[0].str.isdigit())                    # first char is a digit
        & (df["Qty"] > 0)                               # not empty
    )
    return df[mask].copy()

def get_full_pallet_bins(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full = occupied 111***  OR  (not ending with 01 AND Qty > 5)
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
    # Candidates that look like partials by naming convention (from the master list)
    candidates = [
        loc for loc in master_locs
        if loc.endswith("01")
        and (not loc.startswith("111"))
        and (not str(loc).upper().startswith("TUN"))
        and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_empty_bins_view(master_locs, occupied_locs) -> pd.DataFrame:
    empty_all = sorted(set(master_locs) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_all})

# -------------------- BULK ROW LOGIC (Inventory only) --------------------
# Zone capacity rules; includes A, B, I (and others)
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def analyze_bulk_rows(df: pd.DataFrame):
    df = df.copy()
    df["Zone"] = _loc_series(df).str[0].str.upper()

    # Use unique pallet IDs per location if available to avoid double counting
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
            continue  # not a tracked bulk zone

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

# Total QTY in Bulk Zones KPI (sum Qty where first char is a tracked bulk zone)
bulk_zone_letters = set(bulk_rules.keys())
bulk_zone_qty_total = filtered_inventory_df[
    _loc_series(filtered_inventory_df).str[0].str.upper().isin(bulk_zone_letters)
]["Qty"].sum()

# -------------------- VIEWS --------------------
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
empty_bins_view_df = get_empty_bins_view(master_locations, occupied_locations)

damages_df = inventory_df[_loc_series(inventory_df).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[_loc_series(inventory_df).str.upper() == "MISSING"]

# Placeholder so the menu doesn't error out (future feature)
rack_discrepancies_df = pd.DataFrame()

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
    if lottie_icon:
        st_lottie(lottie_icon, height=150)
    st.markdown(
        f"<h1 style='text-align:center; color:#2E86C1;'>📊 Bin-Helper Dashboard "
        f"<span style='font-size:18px; color:gray;'>({APP_VERSION})</span></h1>",
        unsafe_allow_html=True,
    )

    total_bins_occupied = len(full_pallet_bins_df) + len(partial_bins_df)
    total_empty_bins = len(empty_bins_view_df) + len(empty_partial_bins_df)

    # Show which columns were used from master (small help text)
    with st.expander("ℹ️ Master columns used"):
        st.write(master_cols_used)

    kpi_data = [
        {"title": "Total Bins Occupied", "value": int(total_bins_occupied), "icon": "📦"},
        {"title": "Total Empty Bins", "value": int(total_empty_bins), "icon": "🗑️"},
        {"title": "Bulk Locations", "value": int(len(bulk_locations_df)), "icon": "📍"},
        {"title": "Empty Bulk Locations", "value": int(len(empty_bulk_locations_df)), "icon": "🧯"},
        {"title": "Bulk Discrepancies", "value": int(len(bulk_discrepancies_df)), "icon": "⚠️"},
        {"title": "Total QTY in Bulk Zones", "value": int(bulk_zone_qty_total), "icon": "🔢"},
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
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    st.subheader(f"{st.session_state.active_view}")

    # Special guidance/warnings for Full / Partial tabs
    if st.session_state.active_view in ("Full Pallet Bins", "Partial Bins"):
        st.markdown(
            "<div style='background:#ffe6e6; color:#8a0000; padding:10px; border-left:4px solid #ff0000;'>"
            "⚠️ Rules: <b>Full</b> = starts with 111 OR (not ending with 01 and Qty > 5); "
            "<b>Partial</b> = ends with 01, not 111***, not TUN*, Qty > 0."
            "</div>",
            unsafe_allow_html=True,
        )

    if st.session_state.active_view == "Rack Discrepancies":
        st.info("Rack Discrepancies view is reserved for a future rule set. No records to display yet.")

    df_show = apply_location_filter(raw_df)
    st.dataframe(df_show, use_container_width=True)