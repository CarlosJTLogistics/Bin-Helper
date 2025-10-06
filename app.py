import streamlit as st
import pandas as pd
import requests

from streamlit_lottie import st_lottie  # pip install streamlit-lottie

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Bin Helper", layout="wide", initial_sidebar_state="expanded")

# -------------------- APP VERSION --------------------
APP_VERSION = "v1.3.2"

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
    # Inventory
    inventory_df = pd.read_excel(inventory_url, engine="openpyxl")

    # Try to read the "Master Locations" sheet but fall back to the first sheet if needed
    try:
        master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    except Exception:
        # Read workbook to get first sheet
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

# Convenience: uppercased location for filtering
def _loc_series(df):
    return df["LocationName"].astype(str).str.strip()

# Exclusions (DAMAGE/IBDAMAGE/MISSING and any "IB*" locations)
def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    s = _loc_series(df).str.upper()
    mask = (~s.isin(["DAMAGE", "MISSING", "IBDAMAGE"])) & (~s.str.startswith("IB"))
    return df[mask].copy()

# -------------------- MASTER LOCATIONS EXTRACTION --------------------
def _normalize_col(name: str) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "")

MASTER_CANDIDATE_KEYS = {
    "locationname",
    "location",
    "bin",
    "loc",
    "locationcode",
    "masterlocation",
    "masterlocations",
}

def extract_master_locations(master_df: pd.DataFrame):
    chosen_col = None
    for col in master_df.columns:
        if _normalize_col(col) in MASTER_CANDIDATE_KEYS:
            chosen_col = col
            break

    if chosen_col is None:
        st.error(
            "⚠️ Could not auto-detect the Master Location column in **Empty Bin Formula.xlsx**.\n\n"
            f"Found columns: `{list(master_df.columns)}`\n\n"
            "Rename the correct column to `LocationName` (or one of: Location, Bin, Loc, Location Code, MasterLocation) and rerun."
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
# Zone capacity rules; adjust as needed
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

# -------------------- VIEWS (now using defined variables) --------------------
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
empty_bins_view_df = get_empty_bins_view(master_locations, occupied_locations)

damages_df = inventory_df[_loc_series(inventory_df).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[_loc_series(inventory_df).str.upper() == "MISSING"]

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
}

if st.session_state.active_view != "Dashboard":
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    st.subheader(f"{st.session_state.active_view}")

    # Special guidance/warnings
    if st.session_state.active_view in ("Full Pallet Bins", "Partial Bins"):
        st.markdown(
            "<div style='background:#ffe6e6; color:#8a0000; padding:10px; border-left:4px solid #ff0000;'>"
            "⚠️ These results follow current rules: "
            "<b>Full</b> = starts with 111 OR (not ending with 01 and Qty > 5); "
            "<b>Partial</b> = ends with 01, not 111***, not TUN*, Qty > 0."
            "</div>",
            unsafe_allow_html=True,
        )

    df_show = apply_location_filter(raw_df)
    st.dataframe(df_show, use_container_width=True)
