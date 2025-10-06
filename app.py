import streamlit as st
import pandas as pd
import requests
from datetime import datetime

from streamlit_lottie import st_lottie  # pip install streamlit-lottie

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Bin Helper", layout="wide", initial_sidebar_state="expanded")

# -------------------- APP VERSION --------------------
APP_VERSION = "v1.3.4"  # UI polish, bulk pallet count KPI, column limiting, discrepancy manager, rack rule

# -------------------- SESSION STATE --------------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Dashboard"
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "discrepancy_history" not in st.session_state:
    st.session_state.discrepancy_history = []  # list of dicts: {"key","when","type","action","note"}
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "refresh_triggered" not in st.session_state:
    st.session_state.refresh_triggered = False

# -------------------- AUTO REFRESH --------------------
if st.session_state.auto_refresh or st.session_state.refresh_triggered:
    st.session_state.refresh_triggered = False
    st.rerun()

# -------------------- SIDEBAR: CONTROLS --------------------
st.sidebar.markdown("### 🔄 Auto Refresh")
if st.sidebar.button("🔁 Refresh Now"):
    st.session_state.refresh_triggered = True
st.session_state.auto_refresh = st.sidebar.checkbox("Enable Auto Refresh", value=st.session_state.auto_refresh)

# Quick global filter
loc_query = st.sidebar.text_input("🔎 Filter by Location (contains)", "")

# Discrepancy Manager (Bulk & Rack)
st.sidebar.markdown("### 🛠️ Discrepancy Manager")
discrepancy_type = st.sidebar.selectbox("Choose Type", ["Bulk", "Rack"])
hide_resolved = st.sidebar.checkbox("Hide Resolved in Discrepancy Views", value=True)

# History Panel
with st.sidebar.expander("📜 Discrepancy History Log", expanded=False):
    if st.session_state.discrepancy_history:
        for h in reversed(st.session_state.discrepancy_history[-50:]):
            st.markdown(
                f"- **{h['type']}** · `{h['key']}` · {h['action']} · *{h['when']}*"
                + (f" · _{h['note']}_" if h.get("note") else "")
            )
    else:
        st.caption("No history yet.")

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
    inv_df = pd.read_excel(inventory_url, engine="openpyxl")
    # Master (try named sheet, else default)
    try:
        m_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    except Exception:
        m_df = pd.read_excel(master_url, engine="openpyxl")

    # QUICK FIX: accept your headers and map to LocationName automatically
    if "LocationName" not in m_df.columns:
        rename_map = {}
        if "Rack Location Column" in m_df.columns:
            rename_map["Rack Location Column"] = "LocationName"
        elif "Empty Locations" in m_df.columns:
            rename_map["Empty Locations"] = "LocationName"
        if rename_map:
            m_df = m_df.rename(columns=rename_map)

    return inv_df, m_df

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
    st.stop()

# -------------------- DATA PREP --------------------
# Numeric coercion
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0).astype(int)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0).astype(int)

# Convenience: normalized location series
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
    "locationname", "location", "bin", "loc", "locationcode", "masterlocation", "masterlocations",
}

def extract_master_locations(m_df: pd.DataFrame):
    # Prefer LocationName if the quick fix mapped it
    if "LocationName" in m_df.columns:
        chosen_col = "LocationName"
    else:
        chosen_col = None
        for col in m_df.columns:
            if _normalize_col(col) in MASTER_CANDIDATE_KEYS:
                chosen_col = col
                break

    if chosen_col is None:
        st.error(
            "⚠️ Could not auto-detect the Master Location column in **Empty Bin Formula.xlsx**.\n\n"
            f"Found columns: `{list(m_df.columns)}`\n\n"
            "Tip: rename your location column to one of: "
            "`LocationName`, `Location`, `Bin`, `Loc`, `Location Code`, `MasterLocation`.\n"
            "This app also recognizes your headers 'Rack Location Column' or 'Empty Locations' automatically."
        )
        st.stop()

    master_locations = (
        m_df[chosen_col]
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

# Filtered for most business logic
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
# Zone capacity rules; includes A, B, I plus others
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def analyze_bulk_rows(df: pd.DataFrame):
    df = df.copy()
    df["Zone"] = _loc_series(df).str[0].str.upper()

    # Unique pallet IDs per location (avoid double-counting)
    if "PalletId" in df.columns:
        row_counts = df.groupby("LocationName")["PalletId"].nunique()
    else:
        row_counts = df.groupby("LocationName")["Qty"].size()

    bulk_locations, bulk_discrepancies, empty_bulk_locations = [], [], []

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

    # Attach unique keys
    for lst in (bulk_discrepancies, empty_bulk_locations):
        for it in lst:
            it["Key"] = f"{it['LocationName']}|{it['Issue']}"

    return (
        pd.DataFrame(bulk_locations),
        pd.DataFrame(bulk_discrepancies),
        pd.DataFrame(empty_bulk_locations),
    )

bulk_locations_df, bulk_discrepancies_df, empty_bulk_locations_df = analyze_bulk_rows(filtered_inventory_df)

# KPI: Total **Pallet Count** in Bulk Zones (NOT Qty)
bulk_zone_letters = set(bulk_rules.keys())
if "PalletId" in filtered_inventory_df.columns:
    bulk_pallet_count_total = (
        filtered_inventory_df[_loc_series(filtered_inventory_df).str[0].str.upper().isin(bulk_zone_letters)]
        .dropna(subset=["PalletId"])
        .groupby("PalletId")
        .size()
        .shape[0]
    )
else:
    # Fallback: count rows assuming 1 row ~ 1 pallet
    bulk_pallet_count_total = int(
        filtered_inventory_df[_loc_series(filtered_inventory_df).str[0].str.upper().isin(bulk_zone_letters)].shape[0]
    )

# -------------------- RACK DISCREPANCIES (Partial needs to be moved) --------------------
# Rule: location starts with 111 (full pallet rack) AND Qty <= 5  -> must be moved
rack_df_base = filtered_inventory_df.copy()
rack_mask = _loc_series(rack_df_base).str.startswith("111") & (rack_df_base["Qty"] <= 5)
rack_discrepancies_df = rack_df_base[rack_mask].copy()
if not rack_discrepancies_df.empty:
    rack_discrepancies_df["Issue"] = "Partial needs to be moved"
    rack_discrepancies_df["Key"] = rack_discrepancies_df.apply(
        lambda r: f"{r['LocationName']}|{r['PalletId']}|{r['Issue']}", axis=1
    )

# -------------------- BUILD CORE VIEWS --------------------
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
empty_bins_view_df = get_empty_bins_view(master_locations, occupied_locations)

damages_df = inventory_df[_loc_series(inventory_df).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[_loc_series(inventory_df).str.upper() == "MISSING"]

# -------------------- TAB DISPLAY: LIMIT COLUMNS --------------------
DISPLAY_COLUMNS = [
    ("Qty", "QTY"),
    ("PalletId", "Pallet ID"),
    ("WarehouseSku", "Warehouse SKU"),
    ("LocationName", "Location"),
    ("CustomerLotReference", "LOT"),
]

def format_view(df: pd.DataFrame, allow_blank=True) -> pd.DataFrame:
    """Limit columns to the five fields and rename; fill blanks for missing fields if allow_blank."""
    if df is None or df.empty:
        # Return an empty DataFrame with the right headers
        return pd.DataFrame(columns=[lbl for _, lbl in DISPLAY_COLUMNS])

    df2 = df.copy()
    # Ensure all columns exist (for empty bins that only have Location)
    for src, _ in DISPLAY_COLUMNS:
        if src not in df2.columns:
            df2[src] = "" if allow_blank else None

    out = df2[[src for src, _ in DISPLAY_COLUMNS]].rename(columns={src: lbl for src, lbl in DISPLAY_COLUMNS})
    # Uppercase location consistently
    if "Location" in out.columns:
        out["Location"] = out["Location"].astype(str).str.strip().str.upper()
    return out

# Pre-format the six main tabs
view_empty_bins            = format_view(empty_bins_view_df[["LocationName"]], allow_blank=True)
view_full_pallet_bins      = format_view(full_pallet_bins_df)
view_empty_partial_bins    = format_view(empty_partial_bins_df[["LocationName"]], allow_blank=True)
view_partial_bins          = format_view(partial_bins_df)
view_damages               = format_view(damages_df)
view_missing               = format_view(missing_df)

# -------------------- DASHBOARD HOME (Polished) --------------------
# Light, animated KPI cards + Quick Links nav
home_css = """
<style>
.kpi-card {
  background: linear-gradient(135deg, #1f77b4 0%, #2E86C1 100%);
  color: #fff; padding: 18px 16px; border-radius: 14px;
  box-shadow: 0 8px 20px rgba(31,119,180,0.25); text-align:center;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-card:hover { transform: translateY(-3px); box-shadow: 0 12px 24px rgba(31,119,180,0.35); }
.kpi-title { font-size: 15px; opacity: .9; margin-bottom: 6px; }
.kpi-value { font-size: 28px; font-weight: 700; letter-spacing: .3px; }
.quick-btn {
  width: 100%; padding: 10px; border-radius: 10px; border: 0;
  background: #f4f6f9; cursor: pointer; transition: all .15s ease;
}
.quick-btn:hover { background: #e9eef5; transform: translateY(-2px); }
.quick-grid { display:grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
@media (max-width: 1200px){ .quick-grid{ grid-template-columns: repeat(3, 1fr);} }
@media (max-width: 768px){ .quick-grid{ grid-template-columns: repeat(2, 1fr);} }
</style>
"""
st.markdown(home_css, unsafe_allow_html=True)

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
    st_lottie(lottie_icon, height=130)
    st.markdown(
        f"<h1 style='text-align:center; color:#2E86C1;'>📊 Bin Helper "
        f"<span style='font-size:16px; color:gray;'>({APP_VERSION})</span></h1>",
        unsafe_allow_html=True,
    )

    # KPIs
    total_bins_occupied = len(view_full_pallet_bins) + len(view_partial_bins)
    total_empty_bins = len(view_empty_bins) + len(view_empty_partial_bins)

    kpi_data = [
        {"title": "Total Bins Occupied", "value": int(total_bins_occupied), "icon": "📦"},
        {"title": "Total Empty Bins", "value": int(total_empty_bins), "icon": "🗑️"},
        {"title": "Bulk Locations", "value": int(len(bulk_locations_df)), "icon": "📍"},
        {"title": "Empty Bulk Locations", "value": int(len(empty_bulk_locations_df)), "icon": "🧯"},
        {"title": "Bulk Discrepancies", "value": int(len(bulk_discrepancies_df)), "icon": "⚠️"},
        {"title": "Total Pallet Count in Bulk Zones", "value": int(bulk_pallet_count_total), "icon": "🧱"},
    ]

    cols = st.columns(len(kpi_data))
    for i, item in enumerate(kpi_data):
        with cols[i]:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-title">{item['icon']} {item['title']}</div>
                  <div class="kpi-value">{item['value']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Quick Links
    st.markdown("#### Quick Links")
    qlinks = [
        "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
        "Partial Bins", "Damages", "Missing",
    ]
    btn_cols = st.columns(6)
    for i, txt in enumerate(qlinks):
        if btn_cols[i].button(txt, key=f"qlink_{txt}"):
            st.session_state.active_view = txt
            st.rerun()

# -------------------- DISPLAY VIEWS (with filter) --------------------
def apply_location_filter(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    if loc_query.strip():
        return df[df["Location"].astype(str).str.upper().str.contains(loc_query.strip().upper(), na=False)]
    return df

view_map = {
    "Empty Bins": view_empty_bins,
    "Full Pallet Bins": view_full_pallet_bins,
    "Empty Partial Bins": view_empty_partial_bins,
    "Partial Bins": view_partial_bins,
    "Damages": view_damages,
    "Missing": view_missing,
}

# Prepare discrepancy views (respect Hide Resolved)
bulk_disc_view = bulk_discrepancies_df.copy()
if not bulk_disc_view.empty and hide_resolved:
    bulk_disc_view = bulk_disc_view[~bulk_disc_view["Key"].isin(st.session_state.resolved_items)]

rack_disc_view = rack_discrepancies_df.copy()
if not rack_disc_view.empty and hide_resolved:
    rack_disc_view = rack_disc_view[~rack_disc_view["Key"].isin(st.session_state.resolved_items)]

# Discrepancy Manager dropdown content
if discrepancy_type == "Bulk":
    disc_keys = bulk_disc_view["Key"].tolist() if not bulk_disc_view.empty else []
else:
    disc_keys = rack_disc_view["Key"].tolist() if not rack_disc_view.empty else []

selected_disc = st.sidebar.selectbox("Select a discrepancy to resolve", ["(none)"] + disc_keys)
if st.sidebar.button("✅ Mark as Resolved", disabled=(selected_disc == "(none)")):
    if selected_disc != "(none)":
        st.session_state.resolved_items.add(selected_disc)
        st.session_state.discrepancy_history.append({
            "key": selected_disc,
            "when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": discrepancy_type,
            "action": "Resolved",
            "note": "",
        })
        st.sidebar.success("Marked as resolved.")
        st.rerun()

# Tabs & other views
if st.session_state.active_view != "Dashboard":
    title = st.session_state.active_view
    st.subheader(title)

    # Show rule banner for Full / Partial
    if title in ("Full Pallet Bins", "Partial Bins"):
        st.markdown(
            "<div style='background:#ffe6e6; color:#8a0000; padding:10px; border-left:4px solid #ff0000;'>"
            "⚠️ Rules: <b>Full</b> = starts with 111 OR (not ending with 01 and Qty > 5); "
            "<b>Partial</b> = ends with 01, not 111***, not TUN*, Qty > 0."
            "</div>",
            unsafe_allow_html=True,
        )

    if title in view_map:
        df_show = apply_location_filter(view_map[title])
        st.dataframe(df_show, use_container_width=True)

    elif title == "Bulk Locations":
        st.dataframe(bulk_locations_df, use_container_width=True)

    elif title == "Empty Bulk Locations":
        st.dataframe(
            (bulk_disc_view[bulk_disc_view["Issue"].str.contains("empty pallet slots", na=False)][
                ["LocationName", "Zone", "Pallets", "MaxAllowed", "Issue"]
            ] if not bulk_disc_view.empty else empty_bulk_locations_df),
            use_container_width=True,
        )

    elif title == "Bulk Discrepancies":
        # Clickable drill-ins: show only SKU/LOT/Pallet for each location with issue
        if bulk_disc_view.empty:
            st.success("No bulk discrepancies 🎉")
        else:
            for _, row in bulk_disc_view.iterrows():
                loc = row["LocationName"]
                key = row["Key"]
                if hide_resolved and key in st.session_state.resolved_items:
                    continue
                with st.expander(f"⚠️ {row['Issue']}  —  {loc}"):
                    loc_df = filtered_inventory_df[_loc_series(filtered_inventory_df).str.upper() == str(loc).upper()]
                    if loc_df.empty:
                        st.caption("No pallet details found for this location.")
                    else:
                        drill = loc_df[["WarehouseSku", "CustomerLotReference", "PalletId"]].rename(
                            columns={
                                "WarehouseSku": "Warehouse SKU",
                                "CustomerLotReference": "LOT",
                                "PalletId": "Pallet ID",
                            }
                        )
                        st.dataframe(drill, use_container_width=True)

    elif title == "Rack Discrepancies":
        if rack_disc_view.empty:
            st.success("No rack discrepancies 🎉")
        else:
            # Only the two needed? The rule will ensure candidates = Qty <= 5 in 111***
            display_cols = ["LocationName", "Qty", "PalletId", "WarehouseSku", "CustomerLotReference", "Issue"]
            show = rack_disc_view[display_cols].rename(columns={
                "LocationName": "Location",
                "PalletId": "Pallet ID",
                "WarehouseSku": "Warehouse SKU",
                "CustomerLotReference": "LOT",
            })
            st.dataframe(show, use_container_width=True)