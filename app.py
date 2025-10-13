# -*- coding: utf-8 -*-
import os
import csv
import re
import time  # KPI animations
import hashlib  # <-- for file hash (trend de-dup)
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# Try AgGrid; fall back gracefully if not installed
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
    from st_aggrid.shared import JsCode
    _AGGRID_AVAILABLE = True
except Exception:
    _AGGRID_AVAILABLE = False
    AgGrid = None
    GridOptionsBuilder = None
    GridUpdateMode = None
    JsCode = None

# --- PAGE CONFIG ---
st.set_page_config(page_title="Bin Helper", layout="wide")

# --- THEME COLORS (2-color palette) ---
BLUE = "#1f77b4"  # Plotly classic blue
RED = "#d62728"   # Plotly classic red
px.defaults.template = "plotly_white"

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
if "inventory_path" not in st.session_state:
    st.session_state.inventory_path = None  # set when user uploads

# --- UTIL: rerun wrapper ---
def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# --- Lottie helpers ---
def _load_lottie(url: str):
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def show_banner():
    with st.container():
        col_a, col_b = st.columns([1, 3])
        with col_a:
            data = None
            for u in [
                "https://assets10.lottiefiles.com/packages/lf20_9kmmv9.json",  # forklift
                "https://assets2.lottiefiles.com/packages/lf20_1pxqjqps.json",  # barcode boxes
                "https://assets9.lottiefiles.com/packages/lf20_wnqlfojb.json",  # logistics
                "https://assets10.lottiefiles.com/packages/lf20_j1adxtyb.json",  # fallback
            ]:
                data = _load_lottie(u)
                if data: break
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

show_banner()

# ===== Paths =====
LOG_DIR = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
os.makedirs(LOG_DIR, exist_ok=True)
DATA_DIR = os.path.join(os.path.dirname(LOG_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)
resolved_file = os.path.join(LOG_DIR, "resolved_discrepancies.csv")
TRENDS_FILE = os.path.join(LOG_DIR, "trend_history.csv")

DEFAULT_INVENTORY_FILE = "ON_HAND_INVENTORY.xlsx"
DEFAULT_MASTER_FILE = "Empty Bin Formula.xlsx"

# ===== Sidebar =====
def _clear_cache_and_rerun():
    try:
        st.cache_data.clear()
    except Exception:
        pass
    st.session_state["kpi_run_id"] = datetime.now().strftime("%H%M%S%f")
    _rerun()

def _save_uploaded_inventory(uploaded) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\.\-]+", "_", uploaded.name)
    out_path = os.path.join(DATA_DIR, f"{ts}__{safe_name}")
    with open(out_path, "wb") as f:
        f.write(uploaded.getbuffer())
    return out_path

def _file_md5(path: str) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

with st.sidebar:
    st.subheader("📦 Upload Inventory")
    up = st.file_uploader("Upload new ON_HAND_INVENTORY.xlsx", type=["xlsx"], key="inv_upload")
    auto_record = st.toggle("Auto-record trend on new upload (recommended)", value=True, key="auto_record_trend")
    if up is not None:
        saved_path = _save_uploaded_inventory(up)
        st.session_state.inventory_path = saved_path
        st.success(f"Saved: {os.path.basename(saved_path)}")
        if auto_record:
            st.session_state["pending_trend_record"] = True

    st.subheader("⚡ Performance")
    st.toggle("Fast tables (limit to 1000 rows)", value=False, key="fast_tables")
    st.button("🔄 Refresh Data", on_click=_clear_cache_and_rerun)

    st.subheader("🎨 Card Style")
    card_style = st.selectbox("Choose KPI card style", ["Neon Glow", "Glassmorphism", "Blueprint"], index=0)

    st.subheader("✨ Dashboard Animations")
    st.toggle("Animate KPI counters", value=True, key="animate_kpis")

    st.subheader("🧭 Trends")
    st.caption("Snapshots are stored in logs/trend_history.csv")
    if st.button("Record snapshot now"):
        st.session_state["pending_trend_record"] = True

# ===== Cached Loader =====
@st.cache_data(ttl=120, show_spinner=False)
def _load_excel(path: str, sheet_name=0):
    return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")

inventory_file = st.session_state.inventory_path or DEFAULT_INVENTORY_FILE
master_file = DEFAULT_MASTER_FILE

try:
    inventory_df = _load_excel(inventory_file)
except Exception as e:
    st.error(f"Failed to load inventory file: {inventory_file}. Error: {e}")
    st.stop()

try:
    master_df = _load_excel(master_file, sheet_name="Master Locations")
except Exception:
    master_df = _load_excel(master_file)
    st.warning("Sheet 'Master Locations' not found; used the first sheet instead.")

# ===== Normalization: force LOT & PalletId to whole numeric strings =====
def normalize_whole_number(val) -> str:
    """Return digits-only string without leading zeros (e.g., '9062716'). Empty if no digits."""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    # Handle typical Excel whole-number floats like '9062716.0'
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = s.split(".")[0]
    else:
        s = re.sub(r"\D", "", s)  # keep digits only
    s = s.lstrip("0")
    return s if s else ""

# Ensure numeric columns exist
def ensure_numeric_col(df: pd.DataFrame, col: str, default: float | int = 0):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)
    else:
        df[col] = default

ensure_numeric_col(inventory_df, "Qty", 0)
ensure_numeric_col(inventory_df, "PalletCount", 0)

# Ensure key text cols exist
for c in ["LocationName", "PalletId", "CustomerLotReference", "WarehouseSku"]:
    if c not in inventory_df.columns:
        inventory_df[c] = ""
inventory_df["LocationName"] = inventory_df["LocationName"].astype(str)

# **Apply normalization** for PalletId & LOT now, once
inventory_df["PalletId"] = inventory_df["PalletId"].apply(normalize_whole_number)
inventory_df["CustomerLotReference"] = inventory_df["CustomerLotReference"].apply(normalize_whole_number)

# ===== Rules / helpers =====
def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["LocationName"].str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])].copy()

filtered_inventory_df = exclude_damage_missing(inventory_df)
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())

# Master list extract
def extract_master_locations(df: pd.DataFrame) -> set:
    for c in df.columns:
        if "location" in str(c).lower():
            s = df[c].dropna().astype(str).str.strip()
            return set(s.unique().tolist())
    s = df.iloc[:, 0].dropna().astype(str).str.strip()
    return set(s.unique().tolist())

master_locations = extract_master_locations(master_df)

def get_partial_bins(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    s = df2["LocationName"].astype(str)
    mask = (s.str.endswith("01") & ~s.str.startswith("111") & ~s.str.upper().str.startswith("TUN") & s.str[0].str.isdigit())
    return df2.loc[mask].copy()

def get_full_pallet_bins(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    s = df2["LocationName"].astype(str)
    mask = (((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric() & df2["Qty"].between(6, 15))
    return df2.loc[mask].copy()

def get_empty_partial_bins(master_locs: set, occupied_locs: set) -> pd.DataFrame:
    series = pd.Series(list(master_locs), dtype=str)
    mask = (series.str.endswith("01") & ~series.str.startswith("111") & ~series.str.upper().str.startswith("TUN") & series.str[0].str.isdigit())
    partial_candidates = set(series[mask])
    empty_partial = sorted(partial_candidates - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

# Multi-pallet rule for ALL rack locations (full + partial) using normalized PalletId
def _find_multi_pallet_all_racks(df: pd.DataFrame):
    """
    Returns (summary_df, details_df)
      - summary_df: LocationName | DistinctPallets
      - details_df: normalized core columns + Issue + DistinctPallets
    Rack:
      * Partial rack: numeric, endswith "01", not starting "111"
      * Full rack: numeric, and ((not endswith "01") or startswith "111")
    """
    df2 = exclude_damage_missing(df).copy()
    df2["LocationName"] = df2["LocationName"].astype(str).str.strip()
    # PalletId already normalized at load
    s = df2["LocationName"].astype(str)
    is_numeric = s.str.isnumeric()
    rack_df = df2[is_numeric].copy()
    if rack_df.empty:
        return pd.DataFrame(columns=["LocationName", "DistinctPallets"]), pd.DataFrame()

    grp = (rack_df.groupby("LocationName")["PalletId"]
           .nunique(dropna=True)
           .reset_index(name="DistinctPallets"))
    viol = grp[grp["DistinctPallets"] > 1]
    if viol.empty:
        return grp.iloc[0:0], pd.DataFrame()

    viol_locs = set(viol["LocationName"])
    details = rack_df[rack_df["LocationName"].isin(viol_locs)].copy()

    # Issue per rack type
    locs = details["LocationName"].astype(str)
    details["Issue"] = [
        "Multiple pallets in partial bin"
        if (loc.endswith("01") and not loc.startswith("111"))
        else "Multiple pallets in rack location"
        for loc in locs
    ]
    details = details.merge(viol, on="LocationName", how="left")
    return viol.sort_values("DistinctPallets", ascending=False), details

# ===== Build views =====
empty_bins_view_df = pd.DataFrame({
    "LocationName": sorted([loc for loc in master_locations if (loc not in occupied_locations and not str(loc).endswith("01"))])
})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].str.upper().isin(["DAMAGE", "IBDAMAGE"])].copy()
missing_df = inventory_df[inventory_df["LocationName"].str.upper() == "MISSING"].copy()

# ===== Bulk discrepancy logic =====
bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

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

# ===== Rack discrepancies (enhanced) =====
def analyze_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    results = []

    # Partial errors: qty>5 or >1 pallet (based on PalletCount from WMS)
    p_df = get_partial_bins(df2)
    if not p_df.empty:
        pe = p_df[(p_df["Qty"] > 5) | (p_df["PalletCount"] > 1)]
        for _, row in pe.iterrows():
            issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
            rec = row.to_dict(); rec["Issue"] = issue
            results.append(rec)

    # Full rack qty errors
    s = df2["LocationName"].astype(str)
    full_mask = (((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric())
    f_df = df2.loc[full_mask]
    if not f_df.empty:
        fe = f_df[~f_df["Qty"].between(6, 15)]
        for _, row in fe.iterrows():
            rec = row.to_dict()
            rec["Issue"] = "Partial Pallet needs to be moved to Partial Location"
            results.append(rec)

    # Multi-pallet (normalized PalletId) for ALL racks
    mp_summary, mp_details = _find_multi_pallet_all_racks(df2)
    if not mp_details.empty:
        results += mp_details.to_dict("records")

    out = pd.DataFrame(results)
    if not out.empty:
        keep_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Issue"] if c in out.columns]
        out = out.drop_duplicates(subset=keep_cols)
    return out

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ===== Bulk locations capacity views =====
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
            "LocationName": location, "Zone": zone,
            "PalletCount": count, "MaxAllowed": max_allowed,
            "EmptySlots": max(0, empty_slots)
        })
        if empty_slots > 0:
            empty_bulk_locations.append({"LocationName": location, "Zone": zone, "EmptySlots": empty_slots})

bulk_locations_df = pd.DataFrame(bulk_locations)
empty_bulk_locations_df = pd.DataFrame(empty_bulk_locations)

# ===== Logging =====
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
            action, batch_id, discrepancy_type, _row_key(row, discrepancy_type),
            row.get("LocationName", ""), row.get("PalletId", ""), row.get("WarehouseSku", ""),
            row.get("CustomerLotReference", ""), row.get("Qty", ""), row.get("Issue", ""),
            note, selected_lot
        ])

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str):
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
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

# ===== Display helpers =====
CORE_COLS = ["LocationName", "WarehouseSku", "PalletId", "CustomerLotReference", "Qty"]

def _lot_to_str(x):
    # Reuse strict normalization for LOT display
    return normalize_whole_number(x)

def ensure_core(df: pd.DataFrame, include_issue: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=CORE_COLS + (["Issue"] if include_issue else []))
    out = df.copy()
    for c in CORE_COLS:
        if c not in out.columns:
            out[c] = ""
    # enforce display normalization
    if "PalletId" in out.columns:
        out["PalletId"] = out["PalletId"].apply(normalize_whole_number)
    if "CustomerLotReference" in out.columns:
        out["CustomerLotReference"] = out["CustomerLotReference"].apply(normalize_whole_number)
    cols = CORE_COLS.copy()
    if include_issue and "Issue" in out.columns:
        cols += ["Issue"]
    if "DistinctPallets" in out.columns:
        cols += ["DistinctPallets"]
    cols = [c for c in cols if c in out.columns]
    return out[cols]

def style_issue_red(df: pd.DataFrame):
    if "Issue" in df.columns:
        return df.style.set_properties(subset=["Issue"], **{"color": "red", "font-weight": "bold"})
    return df

def maybe_limit(df: pd.DataFrame) -> pd.DataFrame:
    return df.head(1000) if st.session_state.get("fast_tables", False) else df

# ===== KPI Card CSS (no f-strings in CSS) =====
def _inject_card_css(style: str):
    common = """
div[data-testid="stMetric"] {
  border-radius: 12px;
  padding: 12px 14px;
  transition: box-shadow .2s ease, transform .08s ease, border-color .2s ease, background .2s ease;
  border: 1px solid transparent;
}
div[data-testid="stMetric"]:hover { transform: translateY(-1px); }
div[data-testid="stMetric"] [data-testid="stMetricLabel"] { font-weight: 600; letter-spacing: .2px; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-weight: 800; }
.stButton>button { transition: transform .05s ease, box-shadow .2s ease; }
.stButton>button:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(0,0,0,.18); }
/* Responsive: stack columns & wrap nav on small screens */
@media (max-width: 900px) {
  section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"] {
    width: 100% !important; flex: 1 1 100% !important; padding-bottom: 8px;
  }
  div[data-testid="stRadio"] div[role="radiogroup"] {
    display: flex; flex-wrap: wrap; gap: 6px 10px; justify-content: center;
  }
  .stDataFrame, .stTable { font-size: 0.92rem; }
}
"""
    neon = """
/* NEON GLOW */
div[data-testid="stMetric"] {
  color: #e8f0ff;
  background: radial-gradient(120% 120% at 0% 0%, #0b1220 0%, #101a2e 55%, #0b1220 100%);
  border: 1px solid rgba(31,119,180, .35);
  box-shadow: 0 0 12px rgba(31,119,180, .35), inset 0 0 10px rgba(31,119,180, .15);
}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] { color: rgba(200,220,255,.9); }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: __BLUE__; text-shadow: 0 0 12px rgba(31,119,180,.5); }
div[data-testid="stMetric"]:hover {
  box-shadow: 0 0 18px rgba(31,119,180,.55), inset 0 0 12px rgba(31,119,180,.22);
}
"""
    glass = """
/* GLASSMORPHISM */
div[data-testid="stMetric"] {
  color: #0e1730;
  background: linear-gradient(160deg, rgba(255,255,255,.55) 0%, rgba(255,255,255,.25) 100%);
  border: 1px solid rgba(15,35,65,.15);
  box-shadow: 0 10px 30px rgba(0,0,0,.08);
  backdrop-filter: blur(10px);
}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] { color: rgba(14,23,48,.8); }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: __BLUE__; }
div[data-testid="stMetric"]:hover { box-shadow: 0 14px 36px rgba(0,0,0,.12); }
"""
    blueprint = """
/* BLUEPRINT */
div[data-testid="stMetric"] {
  color: #d7e9ff;
  background:
  linear-gradient(#0b1f33 1px, transparent 1px) 0 0/100% 22px,
  linear-gradient(90deg, #0b1f33 1px, transparent 1px) 0 0/22px 100%,
  linear-gradient(160deg, #07233e 0%, #0a2949 60%, #061a2d 100%);
  border: 1px dashed rgba(120,170,220,.45);
  box-shadow: inset 0 0 0 1px rgba(31,119,180,.25), 0 10px 24px rgba(0,0,0,.22);
}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] { color: #b7d1f3; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: __BLUE__; text-shadow: 0 0 8px rgba(31,119,180,.45); }
div[data-testid="stMetric"]:hover {
  box-shadow: inset 0 0 0 1px rgba(31,119,180,.45), 0 14px 28px rgba(0,0,0,.28);
}
"""
    exception_hint = """
/* Exception accent for Damages/Missing (columns 5 & 6) */
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(5) div[data-testid="stMetric"],
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(6) div[data-testid="stMetric"] {
  border-color: rgba(214,39,40,.5) !important;
  box-shadow: 0 0 12px rgba(214,39,40,.45), inset 0 0 10px rgba(214,39,40,.18) !important;
}
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(5) div[data-testid="stMetric"] [data-testid="stMetricValue"],
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(6) div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  color: __RED__ !important; text-shadow: 0 0 10px rgba(214,39,40,.45) !important;
}
"""
    bundle = common + (neon if style == "Neon Glow" else glass if style == "Glassmorphism" else blueprint) + exception_hint
    bundle = bundle.replace("__BLUE__", BLUE).replace("__RED__", RED)
    st.markdown(f"<style>{bundle}</style>", unsafe_allow_html=True)

_inject_card_css(card_style)

# ===== NAV =====
nav_options = [
    "Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
    "Partial Bins", "Damages", "Missing",
    "Rack Discrepancies", "Bulk Discrepancies",
    "Bulk Locations", "Empty Bulk Locations", "Trends", "Self-Test"
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

# ===== KPI helper =====
def _animate_metric(ph, label: str, value: int | float, duration_ms: int = 600, steps: int = 20):
    try:
        v_end = int(value)
        if not st.session_state.get("animate_kpis", True) or v_end <= 0:
            ph.metric(label, v_end); return
        steps = max(8, min(40, steps))
        sleep_s = max(0.01, duration_ms / 1000.0 / steps)
        for i in range(1, steps + 1):
            v = int(round(v_end * i / steps))
            ph.metric(label, v)
            time.sleep(sleep_s)
    except Exception:
        ph.metric(label, value)

def _current_kpis() -> dict:
    return {
        "EmptyBins": len(empty_bins_view_df),
        "EmptyPartialBins": len(empty_partial_bins_df),
        "PartialBins": len(partial_bins_df),
        "FullPalletBins": len(full_pallet_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
    }

def _append_trend_snapshot(kpis: dict, src_path: str):
    os.makedirs(os.path.dirname(TRENDS_FILE), exist_ok=True)
    file_hash = _file_md5(src_path) if src_path else ""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {"Timestamp": ts, "FileName": os.path.basename(src_path) if src_path else "", "FileHash": file_hash, **kpis}
    exists = os.path.isfile(TRENDS_FILE)
    try:
        hist = pd.read_csv(TRENDS_FILE) if exists else pd.DataFrame()
    except Exception:
        hist = pd.DataFrame()
    if not hist.empty and "FileHash" in hist.columns:
        last_hash = str(hist.iloc[-1].get("FileHash", ""))
        if file_hash and file_hash == last_hash:
            return False
    with open(TRENDS_FILE, mode="a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists: w.writeheader()
        w.writerow(row)
    return True

if st.session_state.get("pending_trend_record", False):
    took = _append_trend_snapshot(_current_kpis(), inventory_file)
    st.success("📈 Trend snapshot recorded.") if took else st.info("Trend snapshot skipped (same file as last snapshot).")
    st.session_state["pending_trend_record"] = False

# ===== Dashboard =====
if selected_nav == "Dashboard":
    st.subheader("📊 Bin Helper Dashboard")
    kpi_vals = {
        "Empty Bins": len(empty_bins_view_df),
        "Empty Partial Bins": len(empty_partial_bins_df),
        "Partial Bins": len(partial_bins_df),
        "Full Pallet Bins": len(full_pallet_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
    }
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    k1 = col1.empty(); k1_btn = col1.button("View", key="btn_empty")
    k2 = col2.empty(); k2_btn = col2.button("View", key="btn_empty_partial")
    k3 = col3.empty(); k3_btn = col3.button("View", key="btn_partial")
    k4 = col4.empty(); k4_btn = col4.button("View", key="btn_full")
    k5 = col5.empty(); k5_btn = col5.button("View", key="btn_damage")
    k6 = col6.empty(); k6_btn = col6.button("View", key="btn_missing")
    _animate_metric(k1, "Empty Bins", kpi_vals["Empty Bins"])
    _animate_metric(k2, "Empty Partial Bins", kpi_vals["Empty Partial Bins"])
    _animate_metric(k3, "Partial Bins", kpi_vals["Partial Bins"])
    _animate_metric(k4, "Full Pallet Bins", kpi_vals["Full Pallet Bins"])
    _animate_metric(k5, "Damages", kpi_vals["Damages"])
    _animate_metric(k6, "Missing", kpi_vals["Missing"])
    if k1_btn: st.session_state["pending_nav"] = "Empty Bins"; _rerun()
    if k2_btn: st.session_state["pending_nav"] = "Empty Partial Bins"; _rerun()
    if k3_btn: st.session_state["pending_nav"] = "Partial Bins"; _rerun()
    if k4_btn: st.session_state["pending_nav"] = "Full Pallet Bins"; _rerun()
    if k5_btn: st.session_state["pending_nav"] = "Damages"; _rerun()
    if k6_btn: st.session_state["pending_nav"] = "Missing"; _rerun()

    # Bin Status Distribution
    kpi_df = pd.DataFrame({"Category": list(kpi_vals.keys()), "Count": list(kpi_vals.values())})
    kpi_df["Group"] = kpi_df["Category"].apply(lambda c: "Exceptions" if c in ["Damages", "Missing"] else "Bins")
    fig_kpi = px.bar(kpi_df, x="Category", y="Count", color="Group", text="Count",
                     title="Bin Status Distribution", color_discrete_map={"Bins": BLUE, "Exceptions": RED})
    fig_kpi.update_traces(textposition="outside")
    fig_kpi.update_layout(xaxis_title="", yaxis_title="Count", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig_kpi, use_container_width=True)

    # Racks Full vs Empty
    def is_rack_slot(loc: str) -> bool:
        s = str(loc)
        return s.isnumeric() and (((not s.endswith("01")) or s.startswith("111")))
    rack_master = {loc for loc in master_locations if is_rack_slot(loc)}
    rack_full_used = set(full_pallet_bins_df["LocationName"].astype(str).unique())
    rack_empty = rack_master - occupied_locations
    pie_df = pd.DataFrame({"Status": ["Full", "Empty"], "Locations": [len(rack_full_used & rack_master), len(rack_empty)]})
    fig_rack_pie = px.pie(pie_df, names="Status", values="Locations",
                          title="Racks: Full vs Empty (unique slots)",
                          color="Status", color_discrete_map={"Full": BLUE, "Empty": RED})
    fig_rack_pie.update_layout(showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig_rack_pie, use_container_width=True)

    # Bulk Zones
    if not bulk_locations_df.empty:
        bulk_zone = bulk_locations_df.groupby("Zone").agg(Used=("PalletCount", "sum"), Capacity=("MaxAllowed", "sum")).reset_index()
        bulk_zone["Empty"] = (bulk_zone["Capacity"] - bulk_zone["Used"]).clip(lower=0)
        bulk_stack = bulk_zone.melt(id_vars="Zone", value_vars=["Used", "Empty"], var_name="Type", value_name="Count")
        fig_bulk = px.bar(bulk_stack, x="Zone", y="Count", color="Type", barmode="stack",
                          title="Bulk Zones: Used vs Empty Capacity", color_discrete_map={"Used": BLUE, "Empty": RED})
        fig_bulk.update_layout(xaxis_title="Zone", yaxis_title="Pallets", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
        st.plotly_chart(fig_bulk, use_container_width=True)

    # Damages vs Missing
    dm_df = pd.DataFrame({"Status": ["Damages", "Missing"], "Count": [len(damages_df), len(missing_df)]})
    fig_dm = px.bar(dm_df, x="Status", y="Count", text="Count", title="Damages vs Missing",
                    color="Status", color_discrete_map={"Damages": RED, "Missing": BLUE})
    fig_dm.update_traces(textposition="outside")
    fig_dm.update_layout(xaxis_title="", yaxis_title="Count", showlegend=False, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig_dm, use_container_width=True)

elif selected_nav == "Empty Bins":
    st.subheader("Empty Bins")
    display = ensure_core(empty_bins_view_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    st.dataframe(maybe_limit(display), use_container_width=True)

elif selected_nav == "Empty Partial Bins":
    st.subheader("Empty Partial Bins")
    display = ensure_core(empty_partial_bins_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    st.dataframe(maybe_limit(display), use_container_width=True)

elif selected_nav == "Partial Bins":
    st.subheader("Partial Bins")
    st.dataframe(maybe_limit(ensure_core(partial_bins_df)), use_container_width=True)

elif selected_nav == "Full Pallet Bins":
    st.subheader("Full Pallet Bins")
    st.dataframe(maybe_limit(ensure_core(full_pallet_bins_df)), use_container_width=True)

elif selected_nav == "Damages":
    st.subheader("Damaged Pallets")
    st.dataframe(maybe_limit(ensure_core(damages_df)), use_container_width=True)

elif selected_nav == "Missing":
    st.subheader("Missing Pallets")
    st.dataframe(maybe_limit(ensure_core(missing_df)), use_container_width=True)

elif selected_nav == "Rack Discrepancies":
    st.subheader("Rack Discrepancies")
    if not discrepancy_df.empty:
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique()])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="rack_lot_filter")
        filt = discrepancy_df if sel_lot == "(All)" else discrepancy_df[discrepancy_df["CustomerLotReference"].apply(_lot_to_str) == sel_lot]

        with st.expander("▶ Multi‑Pallet Summary (by Location)"):
            if "Issue" in filt.columns:
                mp_only = filt[filt["Issue"].isin(["Multiple pallets in rack location", "Multiple pallets in partial bin"])]
            else:
                mp_only = pd.DataFrame()
            if not mp_only.empty:
                summary_cnt = (mp_only.groupby("LocationName")["PalletId"].nunique(dropna=True)
                               .reset_index(name="DistinctPallets").sort_values("DistinctPallets", ascending=False))
                all_ids = (mp_only.groupby("LocationName")["PalletId"]
                           .apply(lambda s: ", ".join(sorted({normalize_whole_number(x) for x in s if normalize_whole_number(x)})))
                           .reset_index(name="AllPalletIDs"))
                mp_summary_tbl = summary_cnt.merge(all_ids, on="LocationName", how="left")
                st.dataframe(mp_summary_tbl, use_container_width=True)
            else:
                st.info("No multi‑pallet rack locations in the current filter.")

        rack_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(maybe_limit(rack_display)), use_container_width=True)

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
                data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                if data:
                    st_lottie(data, height=90, key=f"rack_fix_success_{batch_id}", loop=False, speed=1.2)
        with st.expander("Recent discrepancy actions (Rack) & Undo"):
            log_df = read_action_log()
            if not log_df.empty:
                rack_log = log_df[log_df["DiscrepancyType"] == "Rack"].sort_values("Timestamp", ascending=False).head(20)
                st.dataframe(maybe_limit(rack_log), use_container_width=True)
                if not rack_log.empty and st.button("Undo last Rack RESOLVE batch"):
                    last_resolve = log_df[(log_df["DiscrepancyType"] == "Rack") & (log_df["Action"] == "RESOLVE")]
                    if not last_resolve.empty:
                        last_batch = last_resolve.sort_values("Timestamp").iloc[-1]["BatchId"]
                        rows = last_resolve[last_resolve["BatchId"] == last_batch]
                        for _, r in rows.iterrows():
                            log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT", ""), "Rack", "UNDO", str(last_batch))
                        st.success(f"UNDO recorded for batch {last_batch} ({len(rows)} row(s)).")
                        data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                        if data:
                            st_lottie(data, height=90, key=f"rack_undo_success_{last_batch}", loop=False, speed=1.2)
                    else:
                        st.info("No RESOLVE actions to undo for Rack.")
            else:
                st.info("No actions logged yet.")
    else:
        st.info("No rack discrepancies found.")

elif selected_nav == "Bulk Discrepancies":
    st.subheader("Bulk Discrepancies")
    if not bulk_df.empty:
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique()])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="bulk_lot_filter")
        filt = bulk_df if sel_lot == "(All)" else bulk_df[bulk_df["CustomerLotReference"].apply(_lot_to_str) == sel_lot]

        loc_search = st.text_input("Search location (optional)", value="", key="bulk_loc_search")
        df2 = filt.copy()
        if loc_search.strip():
            df2 = df2[df2["LocationName"].astype(str).str.contains(loc_search.strip(), case=False, na=False)]

        st.markdown("#### Grouped by Location (AgGrid)")
        if not _AGGRID_AVAILABLE:
            st.warning("`streamlit-aggrid` is not installed. Add `streamlit-aggrid==0.3.5` to requirements.txt.")
        else:
            show_cols = [c for c in ["LocationName", "WarehouseSku", "CustomerLotReference", "PalletId", "Qty", "Issue"] if c in df2.columns]
            grid_df = df2[show_cols].copy()
            grid_df["CustomerLotReference"] = grid_df["CustomerLotReference"].apply(_lot_to_str)
            quick_text = st.text_input("Quick filter (search all columns)", value="", key="bulk_aggrid_quickfilter")
            expand_all = st.toggle("Expand all groups", value=False)
            gb = GridOptionsBuilder.from_dataframe(grid_df)
            gb.configure_default_column(resizable=True, filter=True, sortable=True, floatingFilter=True)
            gb.configure_column("LocationName", rowGroup=True, hide=True)
            if "WarehouseSku" in grid_df.columns: gb.configure_column("WarehouseSku", pinned="left")
            if "Qty" in grid_df.columns: gb.configure_column("Qty", pinned="right")
            if "Issue" in grid_df.columns: gb.configure_column("Issue", cellStyle={"color": RED, "fontWeight": "bold"})
            gb.configure_selection("multiple", use_checkbox=True, groupSelectsChildren=True, groupSelectsFiltered=True)
            gb.configure_pagination(enabled=True, paginationAutoPageSize=False, paginationPageSize=100)
            gb.configure_side_bar()
            if "Qty" in grid_df.columns: gb.configure_column("Qty", aggFunc="sum")
            if JsCode is not None:
                get_row_style = JsCode("""
                function(params) {
                  if (params.data && params.data.Issue && params.data.Issue.length > 0) {
                    return { 'background-color': '#fff0f0' };
                  }
                  return null;
                }
                """)
                gb.configure_grid_options(getRowStyle=get_row_style)
            gb.configure_grid_options(groupDefaultExpanded=(-1 if expand_all else 0),
                                      animateRows=True, enableRangeSelection=True,
                                      suppressAggFuncInHeader=False, domLayout="normal")
            grid_options = gb.build()
            grid_resp = AgGrid(grid_df, gridOptions=grid_options, update_mode=GridUpdateMode.SELECTION_CHANGED,
                               allow_unsafe_jscode=True, fit_columns_on_grid_load=True, height=500,
                               theme="streamlit", quickFilterText=quick_text)
            sel_rows = pd.DataFrame(grid_resp.get("selected_rows", []))
            st.caption(f"Selected rows: {len(sel_rows)}")
            with st.expander("Log Fix for selected rows"):
                note = st.text_input("Note (optional)", value="", key="bulk_aggrid_note")
                selected_lot_value = "(Multiple)"
                if not sel_rows.empty and "CustomerLotReference" in sel_rows.columns:
                    lots_sel = set(sel_rows["CustomerLotReference"].apply(_lot_to_str).tolist())
                    if len(lots_sel) == 1:
                        selected_lot_value = list(lots_sel)[0]
                st.write(f"Selected LOT (auto): **{selected_lot_value}**")
                if st.button("Log Fix for selected row(s)", disabled=sel_rows.empty):
                    for req in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty", "Issue"]:
                        if req not in sel_rows.columns: sel_rows[req] = ""
                    batch_id = log_batch(sel_rows, note, selected_lot_value, "Bulk", action="RESOLVE")
                    st.success(f"Logged fix for {len(sel_rows)} row(s). BatchId={batch_id}")
                    data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                    if data: st_lottie(data, height=90, key=f"bulk_fix_success_{batch_id}", loop=False, speed=1.2)

        st.markdown("#### Flat view (all rows)")
        bulk_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(maybe_limit(bulk_display)), use_container_width=True)
        csv_data = bulk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Bulk Discrepancies CSV", csv_data, "bulk_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique()])
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="bulk_fix_lot")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="bulk_fix_note")
            if st.button("Fix Selected LOT", key="bulk_fix_btn"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"].apply(_lot_to_str) == chosen_lot]
                batch_id = log_batch(rows_to_fix, note, chosen_lot, "Bulk", action="RESOLVE")
                st.success(f"Resolved {len(rows_to_fix)} bulk discrepancy row(s) for LOT {chosen_lot}. BatchId={batch_id}")
                data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                if data: st_lottie(data, height=90, key=f"bulk_lot_fix_success_{batch_id}", loop=False, speed=1.2)
        with st.expander("Recent discrepancy actions (Bulk) & Undo"):
            log_df = read_action_log()
            if not log_df.empty:
                bulk_log = log_df[log_df["DiscrepancyType"] == "Bulk"].sort_values("Timestamp", ascending=False).head(20)
                st.dataframe(maybe_limit(bulk_log), use_container_width=True)
                if not bulk_log.empty and st.button("Undo last Bulk RESOLVE batch"):
                    last_resolve = log_df[(log_df["DiscrepancyType"] == "Bulk") & (log_df["Action"] == "RESOLVE")]
                    if not last_resolve.empty:
                        last_batch = last_resolve.sort_values("Timestamp").iloc[-1]["BatchId"]
                        rows = last_resolve[last_resolve["BatchId"] == last_batch]
                        for _, r in rows.iterrows():
                            log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT",""), "Bulk", "UNDO", str(last_batch))
                        st.success(f"UNDO recorded for batch {last_batch} ({len(rows)} row(s)).")
                        data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                        if data: st_lottie(data, height=90, key=f"bulk_undo_success_{last_batch}", loop=False, speed=1.2)
                    else:
                        st.info("No RESOLVE actions to undo for Bulk.")
            else:
                st.info("No actions logged yet.")
    else:
        st.info("No bulk discrepancies found.")

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.dataframe(maybe_limit(bulk_locations_df), use_container_width=True)

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations")
    st.dataframe(maybe_limit(empty_bulk_locations_df), use_container_width=True)

elif selected_nav == "Trends":
    st.subheader("📈 Trends Over Time")
    if not os.path.isfile(TRENDS_FILE):
        st.info("No trend snapshots yet. Upload a new inventory file or click 'Record snapshot now' in the sidebar.")
    else:
        try:
            hist = pd.read_csv(TRENDS_FILE)
        except Exception as e:
            st.error(f"Failed to read trend history: {e}")
            hist = pd.DataFrame()
        if not hist.empty:
            try:
                hist["Timestamp"] = pd.to_datetime(hist["Timestamp"])
            except Exception:
                pass
            hist = hist.sort_values("Timestamp")
            st.caption(f"Snapshots: **{len(hist)}** • File: {os.path.basename(TRENDS_FILE)}")
            with st.expander("Show trend table"):
                st.dataframe(hist, use_container_width=True)
                st.download_button("Download trend_history.csv", hist.to_csv(index=False).encode("utf-8"),
                                   "trend_history.csv", "text/csv")
            req_bins = ["EmptyBins", "EmptyPartialBins", "PartialBins", "FullPalletBins"]
            if all(c in hist.columns for c in req_bins):
                bins_long = hist.melt(id_vars=["Timestamp"], value_vars=req_bins, var_name="Series", value_name="Count")
                dash_map = {"EmptyBins": "dash", "EmptyPartialBins": "dot", "PartialBins": "dashdot", "FullPalletBins": "solid"}
                fig_bins = px.line(bins_long, x="Timestamp", y="Count", color="Series",
                                   title="Bins Trend (Empty / Empty Partial / Partial / Full)",
                                   color_discrete_map={s: BLUE for s in bins_long["Series"].unique()})
                for s in dash_map:
                    fig_bins.for_each_trace(lambda t: t.update(line=dict(dash=dash_map[s])) if t.name == s else ())
                fig_bins.update_traces(mode="lines+markers")
                fig_bins.update_layout(showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
                st.plotly_chart(fig_bins, use_container_width=True)
            req_exc = ["Damages", "Missing"]
            if all(c in hist.columns for c in req_exc):
                exc_long = hist.melt(id_vars=["Timestamp"], value_vars=req_exc, var_name="Status", value_name="Count")
                fig_exc = px.line(exc_long, x="Timestamp", y="Count", color="Status",
                                  title="Exceptions Trend (Damages vs Missing)",
                                  color_discrete_map={"Damages": RED, "Missing": BLUE})
                fig_exc.update_traces(mode="lines+markers")
                fig_exc.update_layout(showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
                st.plotly_chart(fig_exc, use_container_width=True)
        else:
            st.info("Trend log exists but is empty. Record a snapshot to begin.")

elif selected_nav == "Self-Test":
    st.subheader("✅ Rule Self-Checks (Read-only)")
    problems = []
    if any(filtered_inventory_df["LocationName"].str.upper().str.startswith(("OB", "IB"))):
        problems.append("OB/IB locations leaked into filtered inventory.")

    pb = get_partial_bins(filtered_inventory_df)
    if not pb.empty:
        s2 = pb["LocationName"].astype(str)
        mask_ok = (s2.str.endswith("01") & ~s2.str.startswith("111") & ~s2.str.upper().str.startswith("TUN") & s2.str[0].str.isdigit())
        if (~mask_ok).any():
            problems.append("Some Partial Bins fail the 01/111/TUN/digit rule.")

    s3 = filtered_inventory_df["LocationName"].astype(str)
    full_mask = (((~s3.str.endswith("01")) | s3.str.startswith("111")) & s3.str.isnumeric())
    fdf = filtered_inventory_df.loc[full_mask].copy()
    offenders = pd.DataFrame(); not_flagged = pd.DataFrame()
    if not fdf.empty:
        offenders = fdf[~fdf["Qty"].between(6, 15)].copy()
        if not offenders.empty and not discrepancy_df.empty:
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

    if "MISSING" in filtered_inventory_df["LocationName"].str.upper().unique():
        problems.append("MISSING found in filtered inventory (should be separate).")

    if problems:
        st.error("❌ FAIL")
        for p in problems: st.write("- ", p)
        if st.button("Go to Rack Discrepancies (review)"):
            st.session_state["pending_nav"] = "Rack Discrepancies"; _rerun()
    else:
        if offenders.empty:
            st.success("🎉 PASS — All baseline rules intact (no full-rack Qty offenders found).")
        else:
            if not not_flagged.empty:
                st.error(f"❌ FAIL — {len(not_flagged)} full-rack offenders are NOT shown in Rack Discrepancies (possible regression).")
                with st.expander("Show un-flagged offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in not_flagged.columns]
                    st.dataframe(maybe_limit(not_flagged[show_cols].head(10)), use_container_width=True)
                if st.button("Go to Rack Discrepancies"):
                    st.session_state["pending_nav"] = "Rack Discrepancies"; _rerun()
            else:
                st.warning(f"⚠️ WARN — {len(offenders)} full-rack rows have Qty outside 6..15 (expected discrepancies, and all are flagged).")
                with st.expander("Show sample offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in offenders.columns]
                    st.dataframe(maybe_limit(offenders[show_cols].head(10)), use_container_width=True)
                if st.button("Go to Rack Discrepancies"):
