# -*- coding: utf-8 -*-
import os
import csv
import re
import time
import json
import hashlib
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List, Union

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from streamlit_lottie import st_lottie
import requests

# ---- Optional AgGrid (graceful fallback) ----
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

# ---- Env override for log dir via Streamlit Secrets ----
try:
    if "BIN_HELPER_LOG_DIR" in st.secrets:
        os.environ["BIN_HELPER_LOG_DIR"] = st.secrets["BIN_HELPER_LOG_DIR"]
except Exception:
    pass

# ---- Page config ----
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---- Theme / palette ----
BLUE = "#1f77b4"  # Preferred blue
RED = "#d62728"   # Preferred red
GREEN = "#2ca02c"
px.defaults.template = "plotly_white"

# ---- Session state ----
if "filters" not in st.session_state:
    st.session_state.filters = {
        "LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""
    }
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "inventory_path" not in st.session_state:
    st.session_state.inventory_path = None
if "jump_intent" not in st.session_state:
    st.session_state.jump_intent = {}
if "last_nav_value" not in st.session_state:
    st.session_state.last_nav_value = "Dashboard"

# ---- Rerun helper ----
def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ---- Lottie ----
def _load_lottie(url: str):
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# =================== Global CSS (nav + hero + micro animations) ===================
def _inject_global_css():
    css = f"""
    <style>
    /* --- App background vibe (subtle gradient) --- */
    .main .block-container {{
        padding-top: 1.2rem;
    }}
    body {{
        background: linear-gradient(180deg, #f8fbff 0%, #ffffff 120%);
    }}

    /* --- Shimmer placeholder --- */
    .skel-row {{
        height: 14px;
        background: linear-gradient(90deg,#eee,#f5f5f5,#eee);
        background-size: 200% 100%;
        animation: skel 1.2s ease-in-out infinite;
        margin: 8px 0;
        border-radius: 6px;
    }}
    @keyframes skel {{
        0% {{background-position:200% 0}}
        100% {{background-position:-200% 0}}
    }}

    /* --- Ripple click effect for buttons --- */
    .stButton>button {{
        position: relative;
        overflow: hidden;
        transition: transform .05s ease, box-shadow .2s ease;
        border-radius: 10px;
    }}
    .stButton>button:hover {{
        transform: translateY(-1px);
        box-shadow: 0 6px 18px rgba(0,0,0,.18);
    }}
    .stButton>button:active::after {{
        content: "";
        position: absolute;
        left: 50%;
        top: 50%;
        width: 0; height: 0;
        background: radial-gradient(circle, rgba(31,119,180,.25) 10%, transparent 60%);
        transform: translate(-50%,-50%);
        animation: ripple .45s ease-out;
        border-radius: 50%;
    }}
    @keyframes ripple {{
        to {{ width: 300px; height:300px; opacity: 0; }}
    }}

    /* --- Top nav: glossy pills with sliding glow --- */
    .nav-wrap {{
        position: sticky;
        top: 0;
        z-index: 999;
        background: linear-gradient(180deg, rgba(255,255,255,.9), rgba(255,255,255,.75));
        backdrop-filter: blur(8px);
        border-bottom: 1px solid rgba(31,119,180,.10);
        padding: 8px 0 10px 0;
        margin: -16px 0 8px 0;
    }}
    .nav-row {{
        display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    }}
    .nav-pill {{
        cursor: pointer;
        padding: 8px 14px;
        border-radius: 12px;
        border: 1px solid rgba(31,119,180,.22);
        background: linear-gradient(160deg, rgba(255,255,255,.9), rgba(245,250,255,.75));
        transition: box-shadow .25s, transform .05s, border-color .25s, background .25s, color .25s;
        color: #0e1730;
        font-weight: 600;
        letter-spacing: .2px;
    }}
    .nav-pill:hover {{
        transform: translateY(-1px);
        box-shadow: 0 8px 22px rgba(31,119,180,.18);
        border-color: rgba(31,119,180,.55);
    }}
    .nav-pill.active {{
        color: #0a2746;
        border-color: rgba(31,119,180,.66);
        box-shadow: 0 10px 26px rgba(31,119,180,.22), inset 0 0 10px rgba(31,119,180,.18);
        background: radial-gradient(120% 120% at 10% 10%, #eaf4ff 0%, #ffffff 65%);
        position: relative;
    }}
    .nav-underline {{
        height: 3px;
        background: linear-gradient(90deg, {BLUE}, {RED});
        border-radius: 3px;
        transition: width .25s ease, transform .25s ease;
        margin-top: 6px;
    }}

    /* --- KPI Metric cards (3 styles) --- */
    div[data-testid="stMetric"] {{
        border-radius: 12px;
        padding: 12px 14px;
        transition: box-shadow .2s ease, transform .08s ease, border-color .2s ease, background .2s ease;
        border: 1px solid transparent;
    }}
    div[data-testid="stMetric"]:hover {{ transform: translateY(-1px); }}
    div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{
        font-weight: 600; letter-spacing: .2px;
    }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
        font-weight: 800;
    }}

    /* Neon Glow */
    .metric-neon div[data-testid="stMetric"] {{
        color: #e8f0ff;
        background: radial-gradient(120% 120% at 0% 0%, #0b1220 0%, #101a2e 55%, #0b1220 100%);
        border: 1px solid rgba(31,119,180, .35);
        box-shadow: 0 0 12px rgba(31,119,180, .35), inset 0 0 10px rgba(31,119,180, .15);
    }}
    .metric-neon div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
        color: {BLUE}; text-shadow: 0 0 12px rgba(31,119,180,.5);
    }}
    .metric-neon div[data-testid="stMetric"]:hover {{
        box-shadow: 0 0 18px rgba(31,119,180,.55), inset 0 0 12px rgba(31,119,180,.22);
    }}

    /* Glassmorphism */
    .metric-glass div[data-testid="stMetric"] {{
        color: #0e1730;
        background: linear-gradient(160deg, rgba(255,255,255,.55) 0%, rgba(255,255,255,.25) 100%);
        border: 1px solid rgba(15,35,65,.15);
        box-shadow: 0 10px 30px rgba(0,0,0,.08);
        backdrop-filter: blur(10px);
    }}
    .metric-glass div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ color: {BLUE}; }}

    /* Blueprint */
    .metric-blueprint div[data-testid="stMetric"] {{
        color: #d7e9ff;
        background:
            linear-gradient(#0b1f33 1px, transparent 1px) 0 0/100% 22px,
            linear-gradient(90deg, #0b1f33 1px, transparent 1px) 0 0/22px 100%,
            linear-gradient(160deg, #07233e 0%, #0a2949 60%, #061a2d 100%);
        border: 1px dashed rgba(120,170,220,.45);
        box-shadow: inset 0 0 0 1px rgba(31,119,180,.25), 0 10px 24px rgba(0,0,0,.22);
    }}
    .metric-blueprint div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
        color: {BLUE}; text-shadow: 0 0 8px rgba(31,119,180,.45);
    }}

    /* Damages/Missing pulse */
    @keyframes pulseGlow {{
        0% {{ box-shadow: 0 0 0px rgba(214,39,40,.0), inset 0 0 0 rgba(214,39,40,0); }}
        50% {{ box-shadow: 0 0 16px rgba(214,39,40,.35), inset 0 0 8px rgba(214,39,40,.15); }}
        100% {{ box-shadow: 0 0 0px rgba(214,39,40,.0), inset 0 0 0 rgba(214,39,40,0); }}
    }}
    .discPulse div[data-testid="stMetric"] {{ animation: pulseGlow 1.6s ease-in-out infinite; }}

    /* Mobile adjustments */
    @media (max-width: 900px) {{
      section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"] {{
        width: 100% !important; flex: 1 1 100% !important; padding-bottom: 8px;
      }}
      .stDataFrame, .stTable {{ font-size: 0.92rem; }}
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

_injected = _inject_global_css()

# =================== Helpers ===================
def _clear_cache_and_rerun():
    try:
        st.cache_data.clear()
    except Exception:
        pass
    st.session_state["kpi_run_id"] = datetime.now().strftime("%H%M%S%f")
    _rerun()

def _file_md5(path: str) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def _resolve_writable_dir(preferred: Optional[str], purpose: str = "logs") -> Tuple[str, bool]:
    candidates = []
    env_override = os.environ.get("BIN_HELPER_LOG_DIR")
    if env_override:
        candidates.append(env_override)
    if preferred:
        candidates.append(preferred)
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(app_dir, purpose))
    candidates.append(os.path.join(tempfile.gettempdir(), f"bin-helper-{purpose}"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test_path = os.path.join(d, ".write_test")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_path)
            return d, (d != preferred)
        except Exception:
            continue
    d = os.getcwd()
    try:
        test_path = os.path.join(d, ".write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
        return d, True
    except Exception:
        return d, True

def _safe_append_csv(path: str, header: List[str], row: List) -> Tuple[bool, str, str]:
    def _try_write(p: str) -> Tuple[bool, str]:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        file_exists = os.path.isfile(p)
        with open(p, mode="a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow(header)
            w.writerow(row)
        return True, p

    try:
        ok, used = _try_write(path)
        return True, used, ""
    except Exception as e:
        fb_dir, _ = _resolve_writable_dir(None, purpose="logs")
        fb_path = os.path.join(fb_dir, os.path.basename(path))
        try:
            ok, used = _try_write(fb_path)
            return True, used, f"Primary write failed: {e}"
        except Exception as e2:
            return False, path, f"Primary write failed: {e}; Fallback failed: {e2}"

# ---- Preferred paths ----
PREFERRED_LOG_DIR = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
LOG_DIR, LOG_FALLBACK_USED = _resolve_writable_dir(PREFERRED_LOG_DIR, purpose="logs")
DATA_DIR, DATA_FALLBACK_USED = _resolve_writable_dir(os.path.join(os.path.dirname(LOG_DIR), "data"), purpose="data")
CONFIG_FILE = os.path.join(LOG_DIR, "config.json")
RESOLVED_FILE = os.path.join(LOG_DIR, "resolved_discrepancies.csv")
TRENDS_FILE = os.path.join(LOG_DIR, "trend_history.csv")
DEFAULT_INVENTORY_FILE = "ON_HAND_INVENTORY.xlsx"
DEFAULT_MASTER_FILE = "Empty Bin Formula.xlsx"

# =================== Sidebar ===================
with st.sidebar:
    st.subheader("📦 Upload Inventory")
    up = st.file_uploader("Upload new ON_HAND_INVENTORY.xlsx", type=["xlsx"], key="inv_upload")
    auto_record = st.toggle("Auto-record trend on new upload (recommended)", value=True, key="auto_record_trend")

    def _save_uploaded_inventory(uploaded) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w.\-]+", "_", uploaded.name)
        out_path = os.path.join(DATA_DIR, f"{ts}__{safe_name}")
        with open(out_path, "wb") as f:
            f.write(uploaded.getbuffer())
        return out_path

    if up is not None:
        saved_path = _save_uploaded_inventory(up)
        st.session_state.inventory_path = saved_path
        st.success(f"Saved: {os.path.basename(saved_path)}")
        if auto_record:
            st.session_state["pending_trend_record"] = True

    st.subheader("⚡ Performance")
    st.toggle("Fast tables (limit to 1000 rows)", value=False, key="fast_tables")
    st.button("🔄 Refresh Data", on_click=_clear_cache_and_rerun)

    st.subheader("🗂️ Log Folder")
    st.caption(f"Path: `{LOG_DIR}`")
    if LOG_DIR.lower().startswith(PREFERRED_LOG_DIR.lower()):
        if LOG_FALLBACK_USED:
            st.warning("Using fallback log folder (preferred path not writable here).")
        else:
            st.success("Writing to preferred log folder.")
    else:
        st.info("Using environment/auto-resolved log folder.")

    st.subheader("🎨 Card Style")
    card_style = st.selectbox("Choose KPI card style", ["Neon Glow", "Glassmorphism", "Blueprint"], index=0)

    st.subheader("✨ Dashboard Animations")
    st.toggle("Animate KPI counters", value=True, key="animate_kpis")

    st.subheader("📈 Trends")
    st.caption("Snapshots are stored in logs/trend_history.csv")
    if st.button("Record snapshot now"):
        st.session_state["pending_trend_record"] = True

# =================== Caching: Excel loader ===================
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

# =================== Normalization ===================
def normalize_lot_number(val) -> str:
    """
    Numeric-only, strip non-digits and leading zeros; keep empty if none.
    """
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = s.split(".")[0]
    else:
        s = re.sub(r"\D", "", s)
    s = s.lstrip("0")
    return s if s else ""

def normalize_pallet_id(val) -> str:
    """
    Preserve alphanumeric Pallet IDs (e.g., 'JTL00496').
    - Trim whitespace.
    - If value is pure integer-like (e.g., '123.0'), coerce to '123'.
    - Otherwise, keep as-is.
    """
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = s.split(".")[0]
    return s

def ensure_numeric_col(df: pd.DataFrame, col: str, default: Union[float, int] = 0):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)
    else:
        df[col] = default

ensure_numeric_col(inventory_df, "Qty", 0)
ensure_numeric_col(inventory_df, "PalletCount", 0)
for c in ["LocationName", "PalletId", "CustomerLotReference", "WarehouseSku"]:
    if c not in inventory_df.columns:
        inventory_df[c] = ""

inventory_df["LocationName"] = inventory_df["LocationName"].astype(str)
inventory_df["PalletId"] = inventory_df["PalletId"].apply(normalize_pallet_id)
inventory_df["CustomerLotReference"] = inventory_df["CustomerLotReference"].apply(normalize_lot_number)

# =================== Rules / helpers ===================
SPECIALS = ["DAMAGE", "MISSING", "IBDAMAGE"]

def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["LocationName"].str.upper().isin(SPECIALS)].copy()

filtered_inventory_df = exclude_damage_missing(inventory_df)
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())

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
    mask = (
        s.str.endswith("01")
        & ~s.str.startswith("111")
        & ~s.str.upper().str.startswith("TUN")
        & (s.str.slice(0, 1).str.isdigit())
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
        series.str.endswith("01")
        & ~series.str.startswith("111")
        & ~series.str.upper().str.startswith("TUN")
        & (series.str.slice(0, 1).str.isdigit())
    )
    partial_candidates = set(series[mask])
    empty_partial = sorted(partial_candidates - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def _find_multi_pallet_all_racks(df: pd.DataFrame):
    df2 = exclude_damage_missing(df).copy()
    df2["LocationName"] = df2["LocationName"].astype(str).str.strip()
    s = df2["LocationName"].astype(str)
    rack_df = df2[s.str.isnumeric()].copy()
    if rack_df.empty:
        return pd.DataFrame(columns=["LocationName", "DistinctPallets"]), pd.DataFrame()
    grp = (rack_df.groupby("LocationName")["PalletId"].nunique(dropna=True).reset_index(name="DistinctPallets"))
    viol = grp[grp["DistinctPallets"] > 1]
    if viol.empty:
        return grp.iloc[0:0], pd.DataFrame()
    viol_locs = set(viol["LocationName"])
    details = rack_df[rack_df["LocationName"].isin(viol_locs)].copy()
    locs = details["LocationName"].astype(str)
    details["Issue"] = [
        "Multiple pallets in partial bin" if (loc.endswith("01") and not loc.startswith("111"))
        else "Multiple pallets in rack location"
        for loc in locs
    ]
    details = details.merge(viol, on="LocationName", how="left")
    return viol.sort_values("DistinctPallets", ascending=False), details

# ---- Bulk capacity config ----
DEFAULT_BULK_RULES = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def load_config() -> dict:
    cfg = {"bulk_rules": DEFAULT_BULK_RULES.copy()}
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "bulk_rules" in raw and isinstance(raw["bulk_rules"], dict):
                cfg["bulk_rules"] = {k.upper(): int(v) for k, v in raw["bulk_rules"].items() if str(k).upper() in DEFAULT_BULK_RULES}
    except Exception:
        pass
    return cfg

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

_config = load_config()
bulk_rules = _config.get("bulk_rules", DEFAULT_BULK_RULES).copy()

# ---- Build bulk views ----
def build_bulk_views():
    bulk_locations = []
    empty_bulk_locations = []
    location_counts = filtered_inventory_df.groupby("LocationName").size().reset_index(name="PalletCount")

    for _, row in location_counts.iterrows():
        location = str(row["LocationName"])
        count = int(row["PalletCount"])
        if not location:
            continue
        zone = location[0].upper()
        if zone in bulk_rules:
            max_allowed = bulk_rules[zone]
            empty_slots = max_allowed - count
            bulk_locations.append({
                "LocationName": location, "Zone": zone, "PalletCount": count,
                "MaxAllowed": max_allowed, "EmptySlots": max(0, empty_slots)
            })
            if empty_slots > 0:
                empty_bulk_locations.append({"LocationName": location, "Zone": zone, "EmptySlots": empty_slots})
    return pd.DataFrame(bulk_locations), pd.DataFrame(empty_bulk_locations)

empty_bins_view_df = pd.DataFrame({
    "LocationName": sorted([loc for loc in master_locations if (loc not in occupied_locations and not str(loc).endswith("01"))])
})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].str.upper().isin(["DAMAGE", "IBDAMAGE"])].copy()
missing_df = inventory_df[inventory_df["LocationName"].str.upper() == "MISSING"].copy()
bulk_locations_df, empty_bulk_locations_df = build_bulk_views()

# ---- Core table helper ----
CORE_COLS = ["LocationName", "WarehouseSku", "PalletId", "CustomerLotReference", "Qty"]

def ensure_core(df: pd.DataFrame, include_issue: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=CORE_COLS + (["Issue"] if include_issue else []))
    out = df.copy()
    for c in CORE_COLS:
        if c not in out.columns:
            out[c] = ""
    if "PalletId" in out.columns:
        out["PalletId"] = out["PalletId"].apply(normalize_pallet_id)
    if "CustomerLotReference" in out.columns:
        out["CustomerLotReference"] = out["CustomerLotReference"].apply(normalize_lot_number)
    cols = CORE_COLS.copy()
    if include_issue and "Issue" in out.columns:
        cols += ["Issue"]
    if "DistinctPallets" in out.columns:
        cols += ["DistinctPallets"]
    cols = [c for c in cols if c in out.columns]
    return out[cols]

def _lot_to_str(x): return normalize_lot_number(x)
def maybe_limit(df: pd.DataFrame) -> pd.DataFrame:
    return df.head(1000) if st.session_state.get("fast_tables", False) else df

# ---- Precompute LOC index & labels ----
LOC_INDEX: Dict[str, pd.DataFrame] = {}
for loc, g in filtered_inventory_df.groupby(filtered_inventory_df["LocationName"].astype(str)):
    LOC_INDEX[str(loc)] = ensure_core(g)

def _mk_pallet_labels(df: pd.DataFrame):
    df = df.copy()
    df["PalletId"] = df["PalletId"].apply(normalize_pallet_id)
    df["CustomerLotReference"] = df["CustomerLotReference"].apply(_lot_to_str)

    def _label(r):
        pid = r.get("PalletId", "") or "[blank]"
        sku = r.get("WarehouseSku", "") or "[no SKU]"
        lot = r.get("CustomerLotReference", "") or "[no LOT]"
        qty = r.get("Qty", 0)
        try: qty = int(qty)
        except Exception: pass
        return f"{pid} — SKU {sku} — LOT {lot} — Qty {qty}"

    df["_PID_KEY"] = df["PalletId"].where(df["PalletId"].astype(str).str.len() > 0, df.index.astype(str))
    uniq = df.drop_duplicates(subset=["_PID_KEY"])
    labels = [_label(r) for _, r in uniq.iterrows()]
    label_to_key = {_label(r): r["_PID_KEY"] for _, r in uniq.iterrows()}
    return labels, label_to_key, df

PALLET_LABELS_BY_LOC: Dict[str, Tuple[List[str], dict, pd.DataFrame]] = {}
for loc, df in LOC_INDEX.items():
    PALLET_LABELS_BY_LOC[loc] = _mk_pallet_labels(df)

# ---- File freshness badge ----
def _file_freshness_panel():
    name = os.path.basename(inventory_file)
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(inventory_file))
        age = datetime.now() - mtime
        age_txt = f"{int(age.total_seconds()//60)} min" if age < timedelta(hours=2) else f"{age.days} d {int((age.seconds)//3600)} h"
    except Exception:
        mtime = None; age_txt = "n/a"
    md5 = _file_md5(inventory_file)
    md5_short = md5[:8] if md5 else "n/a"
    since_snap = "n/a"
    if os.path.isfile(TRENDS_FILE):
        try:
            hist = pd.read_csv(TRENDS_FILE)
            if not hist.empty and "Timestamp" in hist.columns:
                ts_last = pd.to_datetime(hist["Timestamp"].iloc[-1])
                since = datetime.now() - ts_last
                since_snap = f"{int(since.total_seconds()//60)} min"
        except Exception:
            pass
    st.caption(f"**File:** {name} • **Modified:** {mtime.strftime('%Y-%m-%d %H:%M:%S') if mtime else 'n/a'} • **Age:** {age_txt} • **MD5:** {md5_short} • **Since last snapshot:** {since_snap}")

_file_freshness_panel()

# =================== Logging (with Reason) ===================
def _resolved_has_reason() -> bool:
    try:
        if os.path.isfile(RESOLVED_FILE):
            with open(RESOLVED_FILE, "r", encoding="utf-8") as f:
                first = f.readline().strip()
            return "Reason" in first.split(",")
    except Exception:
        pass
    return False

RESOLVED_HEADER_V1 = [
    "Timestamp","Action","BatchId","DiscrepancyType","RowKey",
    "LocationName","PalletId","WarehouseSku","CustomerLotReference","Qty","Issue","Note","SelectedLOT"
]
RESOLVED_HEADER_V2 = RESOLVED_HEADER_V1 + ["Reason"]

def _row_key(row: dict, discrepancy_type: str) -> str:
    fields = [
        str(row.get("LocationName","")),
        str(row.get("PalletId","")),
        str(row.get("WarehouseSku","")),
        str(row.get("CustomerLotReference","")),
        str(row.get("Qty","")),
        discrepancy_type
    ]
    return "\n".join(fields)

def log_action(row: dict, note: str, selected_lot: str, discrepancy_type: str, action: str, batch_id: str, reason: str = "") -> Tuple[bool, str, str]:
    has_reason = _resolved_has_reason()
    csv_row_v1 = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        action, batch_id, discrepancy_type, _row_key(row, discrepancy_type),
        row.get("LocationName",""), row.get("PalletId",""), row.get("WarehouseSku",""),
        row.get("CustomerLotReference",""), row.get("Qty",""), row.get("Issue",""),
        (f"[Reason: {reason}] " if reason and not has_reason else "") + (note or ""),
        selected_lot
    ]
    if has_reason:
        csv_row = csv_row_v1 + [reason]
        header = RESOLVED_HEADER_V2
    else:
        csv_row = csv_row_v1
        header = RESOLVED_HEADER_V1
    ok, used_path, err = _safe_append_csv(RESOLVED_FILE, header, csv_row)
    return ok, used_path, err

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str, reason: str = "") -> Tuple[str, str]:
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    used_path = RESOLVED_FILE
    for _, r in df_rows.iterrows():
        ok, upath, err = log_action(r.to_dict(), note, selected_lot, discrepancy_type, action, batch_id, reason=reason)
        used_path = upath
        if not ok:
            st.error(f"Failed to write action log.\n{err}")
            break
    return batch_id, used_path

def read_action_log() -> pd.DataFrame:
    try:
        if os.path.isfile(RESOLVED_FILE):
            return pd.read_csv(RESOLVED_FILE, engine="python")
        fb_dir, _ = _resolve_writable_dir(None, purpose="logs")
        fb_path = os.path.join(fb_dir, os.path.basename(RESOLVED_FILE))
        if os.path.isfile(fb_path):
            return pd.read_csv(fb_path, engine="python")
    except Exception:
        pass
    return pd.DataFrame()

def download_fix_log_button(where_key: str = "fixlog"):
    log_df = read_action_log()
    if log_df.empty:
        st.info("No fix actions logged yet.")
    else:
        st.download_button(
            "Download Fix Log (resolved_discrepancies.csv)",
            log_df.to_csv(index=False).encode("utf-8"),
            file_name="resolved_discrepancies.csv", mime="text/csv",
            key=f"dl_fixlog_{where_key}"
        )

# =================== Discrepancies ===================
def analyze_bulk_locations_grouped(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    results = []
    letter_mask = df2["LocationName"].str.slice(0,1).str.upper().isin(bulk_rules.keys())
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

def analyze_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    results = []

    # Partial bin issues
    p_df = get_partial_bins(df2)
    if not p_df.empty:
        pe = p_df[(p_df["Qty"] > 5) | (p_df["PalletCount"] > 1)]
        for _, row in pe.iterrows():
            issue = "Qty too high for partial bin" if row["Qty"] > 5 else "Multiple pallets in partial bin"
            rec = row.to_dict(); rec["Issue"] = issue
            results.append(rec)

    # Full rack issues
    s = df2["LocationName"].astype(str)
    full_mask = ((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric()
    f_df = df2.loc[full_mask]
    if not f_df.empty:
        fe = f_df[~f_df["Qty"].between(6, 15)]
        for _, row in fe.iterrows():
            rec = row.to_dict()
            rec["Issue"] = "Partial Pallet needs to be moved to Partial Location"
            results.append(rec)

    # Multi-pallet in racks
    _, mp_details = _find_multi_pallet_all_racks(df2)
    if not mp_details.empty:
        results += mp_details.to_dict("records")

    out = pd.DataFrame(results)
    if not out.empty:
        keep_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Issue"] if c in out.columns]
        out = out.drop_duplicates(subset=keep_cols)
    return out

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ---- Duplicate Pallets (used under All) ----
def build_duplicate_pallets(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    base = df.copy()
    base["PalletId"] = base["PalletId"].apply(normalize_pallet_id)
    base["PalletId_norm"] = base["PalletId"].astype(str).str.strip().str.upper()
    grp = base.groupby("PalletId_norm")["LocationName"].nunique().reset_index(name="DistinctLocations")
    dups = grp[(grp["PalletId_norm"].astype(str).str.len() > 0) & (grp["DistinctLocations"] > 1)].sort_values("DistinctLocations", ascending=False)
    if dups.empty:
        return dups.rename(columns={"PalletId_norm": "PalletId"}), pd.DataFrame()
    dup_ids = set(dups["PalletId_norm"])
    details = base[base["PalletId_norm"].isin(dup_ids)].copy()
    dups = dups.rename(columns={"PalletId_norm": "PalletId"})
    return dups, ensure_core(details)

dups_summary_df, dups_detail_df = build_duplicate_pallets(filtered_inventory_df)

# =================== Card CSS wrapper ===================
def _wrap_metric_style(style: str):
    class_map = {
        "Neon Glow": "metric-neon",
        "Glassmorphism": "metric-glass",
        "Blueprint": "metric-blueprint",
    }
    cls = class_map.get(style, "metric-neon")
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)

def _unwrap_metric_style():
    st.markdown('</div>', unsafe_allow_html=True)

# =================== Skeleton helper ===================
def show_skeleton(n_rows: int = 8):
    with st.container():
        for _ in range(n_rows):
            st.markdown('<div class="skel-row"></div>', unsafe_allow_html=True)

# =================== Lazy DF ===================
def render_lazy_df(df: pd.DataFrame, key: str, page_size: int = 500, use_core: bool = False, include_issue: bool = False):
    if use_core:
        df = ensure_core(df, include_issue=include_issue)
    total = len(df)
    page = int(st.session_state.get(f"{key}_page", 1))
    end = min(page * page_size, total)
    st.caption(f"Showing **{end}** of **{total}** rows")
    st.dataframe(df.head(end), use_container_width=True)
    c1, c2, _ = st.columns([1,1,8])
    if end < total and c1.button("Load more", key=f"{key}_more"):
        st.session_state[f"{key}_page"] = page + 1
        _rerun()
    if page > 1 and c2.button("Reset", key=f"{key}_reset"):
        st.session_state[f"{key}_page"] = 1
        _rerun()

# =================== Hero Banner ===================
def _hero():
    col_a, col_b = st.columns([1, 3])
    with col_a:
        data = None
        for u in [
            "https://assets10.lottiefiles.com/packages/lf20_9kmmv9.json",
            "https://assets2.lottiefiles.com/packages/lf20_1pxqjqps.json",
            "https://assets9.lottiefiles.com/packages/lf20_wnqlfojb.json",
            "https://assets10.lottiefiles.com/packages/lf20_j1adxtyb.json",
        ]:
            data = _load_lottie(u)
            if data: break
        if data:
            st_lottie(data, height=140, key="banner_lottie", speed=1.0, loop=True)
        else:
            st.info("Banner animation unavailable")
    with col_b:
        st.markdown(
            f"""
            #### Bin Helper

            Fast, visual lookups for Empty, Partial, Full, Damages, and Missing — all by your rules.  
            **Speed** for the floor. **Accuracy** for the count.
            """,
            unsafe_allow_html=True,
        )

# =================== NAV ===================
nav_options = [
    "Dashboard",
    "Empty Bins", "Full Pallet Bins", "Empty Partial Bins", "Partial Bins",
    "Damages", "Missing",
    "Discrepancies (All)",
    "Bulk Locations", "Empty Bulk Locations",
    "Bulk Discrepancies",
    "Trends", "Config", "Self-Test"
]

_default_nav = st.session_state.get("nav", "Dashboard")
if "pending_nav" in st.session_state:
    _default_nav = st.session_state.pop("pending_nav", _default_nav)

# ---- Custom glossy nav pills with animated underline ----
def _render_top_nav(active: str):
    st.markdown('<div class="nav-wrap">', unsafe_allow_html=True)
    c = st.container()
    with c:
        cols = st.columns([1,8,1])
        with cols[1]:
            # Build the pills
            pill_cols = st.columns(len(nav_options))
            active_idx = nav_options.index(active) if active in nav_options else 0
            clicks = []
            for i, (pc, label) in enumerate(zip(pill_cols, nav_options)):
                with pc:
                    is_active = (i == active_idx)
                    btn = st.button(label, key=f"navpill_{i}", use_container_width=True)
                    clicks.append(btn)
                    st.markdown(
                        f"""
                        <script>
                        const el = window.parent.document.querySelector('button[kind="secondary"]#navpill_{i}');
                        if (el) {{
                          el.classList.add('nav-pill');
                          {"el.classList.add('active');" if is_active else ""}
                        }}
                        </script>
                        """,
                        unsafe_allow_html=True,
                    )
            # Underline (visual only)
            st.markdown('<div class="nav-underline"></div>', unsafe_allow_html=True)

            # Handle clicks
            for i, was_clicked in enumerate(clicks):
                if was_clicked:
                    st.session_state["nav"] = nav_options[i]
                    # Micro transition shimmer
                    show_skeleton(3)
                    _rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# Render hero + nav
_hero()
_render_top_nav(st.session_state.get("nav", _default_nav))

# Fallback radio (kept for accessibility & keyboard navigation)
selected_nav = st.radio("🔎 Navigate:", nav_options, index=nav_options.index(_default_nav) if _default_nav in nav_options else 0,
                        horizontal=True, key="nav")
_nav_changed = selected_nav != st.session_state.get("last_nav_value")
st.session_state["last_nav_value"] = selected_nav

# Quick Jump bar with hint chips
def _handle_quick_jump():
    q = st.session_state.get("quick_jump_text", "").strip()
    if not q:
        st.session_state.jump_intent = {}
        return
    q_pid = q.upper()
    try:
        pid_series = filtered_inventory_df["PalletId"].astype(str).str.strip().str.upper()
    except Exception:
        pid_series = pd.Series([], dtype=str)
    match_rows = filtered_inventory_df[pid_series == q_pid]
    if not match_rows.empty:
        loc = str(match_rows.iloc[0]["LocationName"])
        st.session_state.jump_intent = {"type": "pallet", "location": loc, "pallet_id": q}
        st.session_state["pending_nav"] = "Bulk Locations" if loc and loc[0].upper() in bulk_rules else "Discrepancies (All)"
        _rerun(); return
    if q in LOC_INDEX:
        st.session_state.jump_intent = {"type": "location", "location": q}
        st.session_state["pending_nav"] = "Bulk Locations" if q and q[0].upper() in bulk_rules else "Discrepancies (All)"
        _rerun(); return
    if q.isnumeric() and q in LOC_INDEX:
        st.session_state.jump_intent = {"type": "location", "location": q}
        st.session_state["pending_nav"] = "Bulk Locations" if q and q[0].upper() in bulk_rules else "Discrepancies (All)"
        _rerun(); return
    st.session_state.jump_intent = {"type": "none", "raw": q}

st.text_input(
    "Quick Jump (scan or type Pallet ID or Location and press Enter)",
    value="", key="quick_jump_text",
    placeholder="e.g., JTL00496 or A123 or 11400804",
    on_change=_handle_quick_jump
)
st.caption("Hints: • Pallet like `JTL00496` • Rack `11400804` • Bulk `A120`")

st.markdown("---")

# =================== Trends & KPI helpers ===================
def _read_trends() -> pd.DataFrame:
    if not os.path.isfile(TRENDS_FILE): return pd.DataFrame()
    try:
        df = pd.read_csv(TRENDS_FILE)
        if not df.empty and "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()

def _current_kpis() -> dict:
    return {
        "EmptyBins": len(empty_bins_view_df),
        "EmptyPartialBins": len(empty_partial_bins_df),
        "PartialBins": len(partial_bins_df),
        "FullPalletBins": len(full_pallet_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
    }

def _kpi_deltas(hist: pd.DataFrame, now: dict) -> Dict[str, dict]:
    out = {k: {"vs_last": None, "vs_yday": None} for k in now}
    if hist is None or hist.empty: return out
    last = hist.iloc[-1] if not hist.empty else None
    yday = None
    try:
        day_ago = datetime.now() - timedelta(days=1)
        ydf = hist[(hist["Timestamp"].dt.date == day_ago.date())]
        if not ydf.empty:
            yday = ydf.iloc[-1]
        else:
            ydf2 = hist[hist["Timestamp"] <= (datetime.now() - timedelta(hours=24))]
            if not ydf2.empty:
                yday = ydf2.iloc[-1]
    except Exception:
        pass
    for k in now:
        try:
            if last is not None and k in last: out[k]["vs_last"] = int(now[k]) - int(last[k])
            if yday is not None and k in yday: out[k]["vs_yday"] = int(now[k]) - int(yday[k])
        except Exception:
            pass
    return out

def _delta_text(d):
    if d is None: return None
    arrow = "▲" if d > 0 else "▼" if d < 0 else "■"
    return f"{arrow}{abs(d)}"

def _delta_combo_text(vs_last, vs_yday):
    parts = []
    if vs_last is not None: parts.append(f"{_delta_text(vs_last)} vs last")
    if vs_yday is not None: parts.append(f"{_delta_text(vs_yday)} vs 24h")
    return " \n".join(parts) if parts else None

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
            return False, TRENDS_FILE, ""
    header = list(row.keys())
    csv_row = [row[h] for h in header]
    ok, used_path, err = _safe_append_csv(TRENDS_FILE, header, csv_row)
    return ok, used_path, err

if st.session_state.get("pending_trend_record", False):
    took, used_path, err = _append_trend_snapshot(_current_kpis(), inventory_file)
    if took:
        st.success(f"📈 Trend snapshot recorded → `{used_path}`")
        if err: st.info(f"(Used fallback) {err}")
    else:
        st.info("Trend snapshot skipped (same file as last snapshot).")
    st.session_state["pending_trend_record"] = False

def _animate_metric(ph, label: str, value: Union[int, float], delta_text: Optional[str] = None, duration_ms: int = 600, steps: int = 20):
    try:
        v_end = int(value)
        if not st.session_state.get("animate_kpis", True) or v_end <= 0:
            ph.metric(label, v_end, delta=delta_text); return
        steps = max(8, min(40, steps))
        sleep_s = max(0.01, duration_ms / 1000.0 / steps)
        for i in range(1, steps + 1):
            v = int(round(v_end * i / steps))
            ph.metric(label, v)
            time.sleep(sleep_s)
        ph.metric(label, v_end, delta=delta_text)
    except Exception:
        ph.metric(label, value, delta=delta_text)

def _kpi_label(base: str, icon: str, alert: bool = False) -> str:
    return f"{icon} {base}" + (" 🔴" if alert else "")

# =================== Embedded pages ===================
def page_rack_discrepancies(embed_key: str = "rack"):
    st.subheader("Rack Discrepancies")
    if not discrepancy_df.empty:
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key=f"{embed_key}_lot_filter",
                               help="Only non-empty LOTs are shown. Use (All) to see every row.")
        filt = discrepancy_df if sel_lot == "(All)" else discrepancy_df[discrepancy_df["CustomerLotReference"].map(_lot_to_str) == sel_lot]

        with st.expander("▶ Multi‑Pallet Summary (by Location)"):
            if "Issue" in filt.columns:
                mp_only = filt[filt["Issue"].isin(["Multiple pallets in rack location", "Multiple pallets in partial bin"])]
            else:
                mp_only = pd.DataFrame()
            if not mp_only.empty:
                summary_cnt = (
                    mp_only.groupby("LocationName")["PalletId"].nunique(dropna=True)
                    .reset_index(name="DistinctPallets").sort_values("DistinctPallets", ascending=False)
                )
                all_ids = (
                    mp_only.groupby("LocationName")["PalletId"]
                    .apply(lambda s: ", ".join(sorted({normalize_pallet_id(x) for x in s if normalize_pallet_id(x)})))
                    .reset_index(name="AllPalletIDs")
                )
                mp_summary_tbl = summary_cnt.merge(all_ids, on="LocationName", how="left")
                render_lazy_df(mp_summary_tbl, key=f"{embed_key}_mp_summary")
            else:
                st.info("No multi‑pallet rack locations in the current filter.")

        rack_display = ensure_core(filt, include_issue=True)
        render_lazy_df(rack_display, key=f"{embed_key}_disc_table")
        st.download_button("Download Rack Discrepancies CSV", discrepancy_df.to_csv(index=False).encode("utf-8"),
                           "rack_discrepancies.csv", "text/csv", key=f"{embed_key}_dl_rack")

        st.markdown("### ✅ Fix discrepancy by LOT")
        reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
        lot_choices = sorted({_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)})
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key=f"{embed_key}_fix_lot")
            reason = st.selectbox("Reason", reasons, index=0, key=f"{embed_key}_fix_reason")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key=f"{embed_key}_fix_note")
            if st.button("Fix Selected LOT", key=f"{embed_key}_fix_btn"):
                rows_to_fix = discrepancy_df[discrepancy_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Rack", action="RESOLVE", reason=reason)
                st.success(f"Resolved {len(rows_to_fix)} rack discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
        else:
            st.info("No valid LOTs available to fix.")

        with st.expander("Recent discrepancy actions (Rack) & Undo"):
            log_df = read_action_log()
            if not log_df.empty:
                rack_log = log_df[log_df["DiscrepancyType"] == "Rack"].sort_values("Timestamp", ascending=False).tail(50)
                render_lazy_df(rack_log, key=f"{embed_key}_actions_recent")
            else:
                st.info("No actions logged yet.")
        st.markdown("#### Fix Log")
        download_fix_log_button(where_key=f"{embed_key}_rack_fixlog")
    else:
        st.info("No rack discrepancies found.")

def page_bulk_discrepancies(embed_key: str = "bulk"):
    st.subheader("Bulk Discrepancies")
    if not bulk_df.empty:
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key=f"{embed_key}_lot_filter",
                               help="Only non-empty LOTs are shown. Use (All) to see every row.")
        filt = bulk_df if sel_lot == "(All)" else bulk_df[bulk_df["CustomerLotReference"].map(_lot_to_str) == sel_lot]

        loc_search = st.text_input("Search location (optional)", value="", key=f"{embed_key}_loc_search")
        df2 = filt.copy()
        if loc_search.strip():
            df2 = df2[df2["LocationName"].astype(str).str.contains(loc_search.strip(), case=False, na=False)]

        st.markdown("#### Grouped by Location")
        if not _AGGRID_AVAILABLE:
            st.warning("`streamlit-aggrid` is not installed. Add `streamlit-aggrid==0.3.5` to requirements.txt.")
            render_lazy_df(ensure_core(df2, include_issue=True), key=f"{embed_key}_plain_group", use_core=False)
        else:
            skel_ph = st.empty()
            with skel_ph.container():
                show_skeleton(8)
            show_cols = [c for c in ["LocationName","WarehouseSku","CustomerLotReference","PalletId","Qty","Issue"] if c in df2.columns]
            grid_df = df2[show_cols].copy()
            grid_df["CustomerLotReference"] = grid_df["CustomerLotReference"].apply(_lot_to_str)
            quick_text = st.text_input("Quick filter (search all columns)", value="", key=f"{embed_key}_aggrid_quickfilter")
            expand_all = st.toggle("Expand all groups", value=False, key=f"{embed_key}_expand_all")

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
                               allow_unsafe_jscode=True, fit_columns_on_grid_load=True, height=500, theme="streamlit",
                               quickFilterText=quick_text)
            skel_ph.empty()

            sel_rows = pd.DataFrame(grid_resp.get("selected_rows", []))
            st.caption(f"Selected rows: {len(sel_rows)}")

            with st.expander("Log Fix for selected rows"):
                reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
                reason = st.selectbox("Reason", reasons, index=0, key=f"{embed_key}_sel_reason")
                note = st.text_input("Note (optional)", value="", key=f"{embed_key}_aggrid_note")
                selected_lot_value = "(Multiple)"
                if not sel_rows.empty and "CustomerLotReference" in sel_rows.columns:
                    lots_sel = set(sel_rows["CustomerLotReference"].apply(_lot_to_str).tolist())
                    if len(lots_sel) == 1:
                        selected_lot_value = list(lots_sel)[0]
                st.write(f"Selected LOT (auto): **{selected_lot_value}**")
                if st.button("Log Fix for selected row(s)", disabled=sel_rows.empty, key=f"{embed_key}_logfix_sel"):
                    for req in ["LocationName","PalletId","WarehouseSku","CustomerLotReference","Qty","Issue"]:
                        if req not in sel_rows.columns: sel_rows[req] = ""
                    batch_id, used_path = log_batch(sel_rows, note, selected_lot_value, "Bulk", action="RESOLVE", reason=reason)
                    st.success(f"Logged fix for {len(sel_rows)} row(s).")
                    st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")

        st.markdown("#### Flat view (all rows)")
        bulk_display = ensure_core(filt, include_issue=True)
        render_lazy_df(bulk_display, key=f"{embed_key}_disc_flat")
        st.download_button("Download Bulk Discrepancies CSV", bulk_df.to_csv(index=False).encode("utf-8"),
                           "bulk_discrepancies.csv", "text/csv", key=f"{embed_key}_dl_bulk")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = sorted({_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)})
        reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key=f"{embed_key}_fix_lot")
            reason = st.selectbox("Reason", reasons, index=0, key=f"{embed_key}_fix_reason")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key=f"{embed_key}_fix_note")
            if st.button("Fix Selected LOT", key=f"{embed_key}_fix_btn"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Bulk", action="RESOLVE", reason=reason)
                st.success(f"Resolved {len(rows_to_fix)} bulk discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
        else:
            st.info("No valid LOTs available to fix.")

        st.markdown("#### Fix Log")
        download_fix_log_button(where_key=f"{embed_key}_bulk_fixlog")
    else:
        st.info("No bulk discrepancies found.")

def page_bulk_locations():
    st.subheader("Bulk Locations")
    if bulk_locations_df.empty:
        st.info("No bulk activity found.")
        return
    counts = (
        filtered_inventory_df
        .assign(_Zone=filtered_inventory_df["LocationName"].astype(str).str.slice(0,1).str.upper())
        .loc[lambda d: d["_Zone"].isin(bulk_rules.keys())]
        .groupby("LocationName").size().reset_index(name="PalletCount")
        .sort_values(["LocationName"])
    )
    for _, r in counts.iterrows():
        loc = str(r["LocationName"])
        cnt = int(r["PalletCount"])
        with st.expander(f"{loc} — {cnt} pallet(s)", expanded=False):
            df = LOC_INDEX.get(loc, pd.DataFrame())
            df = ensure_core(df)
            labels, _, df_norm = PALLET_LABELS_BY_LOC.get(loc, ([], {}, df))
            if labels:
                st.write("Pallets here:")
                st.markdown("\n".join([f"- {label}" for label in labels]))
            else:
                st.dataframe(maybe_limit(df_norm), use_container_width=True)

# =================== Dashboard ===================
if selected_nav == "Dashboard":
    _wrap_metric_style(card_style)

    kpi_vals = {
        "Empty Bins": len(empty_bins_view_df),
        "Empty Partial Bins": len(empty_partial_bins_df),
        "Partial Bins": len(partial_bins_df),
        "Full Pallet Bins": len(full_pallet_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
    }
    hist = _read_trends()
    now = _current_kpis()
    deltas = _kpi_deltas(hist, now)

    def _dx(name):
        m = {
            "Empty Bins": "EmptyBins",
            "Empty Partial Bins": "EmptyPartialBins",
            "Partial Bins": "PartialBins",
            "Full Pallet Bins": "FullPalletBins",
            "Damages": "Damages",
            "Missing": "Missing",
        }
        k = m[name]
        return _delta_combo_text(deltas[k]["vs_last"], deltas[k]["vs_yday"])

    LBL_EMPTY = _kpi_label("Empty Bins", "📦")
    LBL_EMPTY_PART = _kpi_label("Empty Partial Bins", "🧩")
    LBL_PARTIAL = _kpi_label("Partial Bins", "📉")
    LBL_FULL = _kpi_label("Full Pallet Bins", "🧱")
    LBL_DAMAGE = _kpi_label("Damages", "🛑", alert=(kpi_vals["Damages"] > 0))
    LBL_MISSING = _kpi_label("Missing", "🚫", alert=(kpi_vals["Missing"] > 0))

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    k1 = col1.empty(); k2 = col2.empty(); k3 = col3.empty()
    k4 = col4.empty(); k5 = col5.empty(); k6 = col6.empty()

    _animate_metric(k1, LBL_EMPTY, kpi_vals["Empty Bins"], delta_text=_dx("Empty Bins"))
    _animate_metric(k2, LBL_EMPTY_PART, kpi_vals["Empty Partial Bins"], delta_text=_dx("Empty Partial Bins"))
    _animate_metric(k3, LBL_PARTIAL, kpi_vals["Partial Bins"], delta_text=_dx("Partial Bins"))
    _animate_metric(k4, LBL_FULL, kpi_vals["Full Pallet Bins"], delta_text=_dx("Full Pallet Bins"))
    _animate_metric(k5, LBL_DAMAGE, kpi_vals["Damages"], delta_text=_dx("Damages"))
    _animate_metric(k6, LBL_MISSING, kpi_vals["Missing"], delta_text=_dx("Missing"))

    # View buttons
    if col1.button("View", key="btn_empty"): st.session_state["pending_nav"] = "Empty Bins"; _rerun()
    if col2.button("View", key="btn_empty_partial"): st.session_state["pending_nav"] = "Empty Partial Bins"; _rerun()
    if col3.button("View", key="btn_partial"): st.session_state["pending_nav"] = "Partial Bins"; _rerun()
    if col4.button("View", key="btn_full"): st.session_state["pending_nav"] = "Full Pallet Bins"; _rerun()
    if col5.button("View", key="btn_damage"): st.session_state["pending_nav"] = "Damages"; _rerun()
    if col6.button("View", key="btn_missing"): st.session_state["pending_nav"] = "Missing"; _rerun()

    # Discrepancy KPI (All)
    dup_detail_with_issue = dups_detail_df.copy()
    if not dup_detail_with_issue.empty:
        if "Issue" not in dup_detail_with_issue.columns:
            dup_detail_with_issue["Issue"] = "Duplicate Pallet ID across locations"
        else:
            dup_detail_with_issue["Issue"] = dup_detail_with_issue["Issue"].replace("", "Duplicate Pallet ID across locations")

    all_disc_parts = []
    if not discrepancy_df.empty: all_disc_parts.append(ensure_core(discrepancy_df.assign(Issue=discrepancy_df.get("Issue","")), include_issue=True))
    if not bulk_df.empty: all_disc_parts.append(ensure_core(bulk_df.assign(Issue=bulk_df.get("Issue","")), include_issue=True))
    if not dup_detail_with_issue.empty: all_disc_parts.append(ensure_core(dup_detail_with_issue, include_issue=True))
    if all_disc_parts:
        _all_disc_df = pd.concat(all_disc_parts, ignore_index=True)
        _all_disc_df = _all_disc_df.drop_duplicates(subset=["LocationName","PalletId","WarehouseSku","CustomerLotReference","Issue"])
        disc_count = len(_all_disc_df)
    else:
        disc_count = 0

    st.markdown("### 🚨 All Discrepancies")
    c_disc, c_btn = st.columns([3,1])
    with st.container():
        wrap_class = "discPulse" if disc_count > 0 else ""
        st.markdown(f'<div class="{wrap_class}">', unsafe_allow_html=True)
        c_disc.metric("🚧 All Discrepancies", disc_count)
        st.markdown('</div>', unsafe_allow_html=True)
    if c_btn.button("View All", use_container_width=True):
        st.session_state["pending_nav"] = "Discrepancies (All)"; _rerun()

    if disc_count == 0 and kpi_vals["Damages"] == 0 and kpi_vals["Missing"] == 0:
        st.balloons()

    # --------- Composition pie & Bulk capacity ----------
    cA, cB = st.columns([1, 1])
    with cA:
        st.markdown("#### Inventory Composition")
        s_all = inventory_df["LocationName"].astype(str)
        is_rack = s_all.str.isnumeric()
        is_bulk = s_all.str.slice(0,1).str.upper().isin(bulk_rules.keys())
        is_special = s_all.str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"])
        comp = pd.DataFrame({
            "Category": ["Rack", "Bulk", "Special"],
            "Count": [int(is_rack.sum()), int(is_bulk.sum()), int(is_special.sum())]
        })
        fig_comp = px.pie(
            comp, values="Count", names="Category",
            color="Category",
            color_discrete_map={"Rack": BLUE, "Bulk": GREEN, "Special": RED},
            hole=0.35
        )
        fig_comp.update_layout(showlegend=True, height=340)
        st.plotly_chart(fig_comp, use_container_width=True)

    with cB:
        st.markdown("#### Bulk Capacity by Zone — Occupied vs Empty")
        if bulk_locations_df.empty:
            st.info("No bulk locations found.")
        else:
            zstats = (
                bulk_locations_df
                .groupby("Zone")
                .agg(Occupied=("PalletCount","sum"), Capacity=("MaxAllowed","sum"))
                .reset_index()
            )
            zstats["Empty"] = (zstats["Capacity"] - zstats["Occupied"]).clip(lower=0)
            zlong = zstats.melt(id_vars=["Zone"], value_vars=["Occupied","Empty"], var_name="Status", value_name="Pallets")
            fig = px.bar(zlong, x="Zone", y="Pallets", color="Status",
                         color_discrete_map={"Occupied": BLUE, "Empty": GREEN}, barmode="stack", height=340)
            st.plotly_chart(fig, use_container_width=True)

    _unwrap_metric_style()

# =================== Other tabs ===================
elif selected_nav == "Empty Bins":
    st.subheader("Empty Bins")
    render_lazy_df(empty_bins_view_df, key="empty_bins")

elif selected_nav == "Full Pallet Bins":
    st.subheader("Full Pallet Bins")
    render_lazy_df(ensure_core(full_pallet_bins_df), key="full_bins", use_core=False)

elif selected_nav == "Empty Partial Bins":
    st.subheader("Empty Partial Bins")
    render_lazy_df(empty_partial_bins_df, key="empty_partial")

elif selected_nav == "Partial Bins":
    st.subheader("Partial Bins")
    render_lazy_df(ensure_core(partial_bins_df), key="partial_bins", use_core=False)

elif selected_nav == "Damages":
    st.subheader("Damages")
    render_lazy_df(ensure_core(damages_df), key="damages", use_core=False)

elif selected_nav == "Missing":
    st.subheader("Missing")
    render_lazy_df(ensure_core(missing_df), key="missing", use_core=False)

elif selected_nav == "Discrepancies (All)":
    st.subheader("All Discrepancies")
    tabs = st.tabs(["Rack", "Bulk", "Duplicate Pallets"])
    with tabs[0]:
        page_rack_discrepancies(embed_key="rack_all")
    with tabs[1]:
        page_bulk_discrepancies(embed_key="bulk_all")
    with tabs[2]:
        if dups_summary_df.empty:
            st.info("No duplicate pallets across locations.")
        else:
            st.markdown("#### Duplicate Pallet Summary")
            render_lazy_df(dups_summary_df.rename(columns={"PalletId":"PalletId (Norm)"}), key="dup_summary")
            st.markdown("#### Duplicate Pallet Details")
            render_lazy_df(ensure_core(dups_detail_df), key="dup_detail", use_core=True)

elif selected_nav == "Bulk Locations":
    page_bulk_locations()

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations (per configured capacity)")
    if empty_bulk_locations_df.empty:
        st.info("No empty bulk slots (based on current rules).")
    else:
        render_lazy_df(empty_bulk_locations_df, key="empty_bulk")

elif selected_nav == "Bulk Discrepancies":
    page_bulk_discrepancies(embed_key="bulk_tab")

elif selected_nav == "Trends":
    st.subheader("Trends")
    hist = _read_trends()
    if hist.empty:
        st.info("No trend history yet. Upload a file or click 'Record snapshot now' in the sidebar.")
    else:
        kpis = ["EmptyBins","EmptyPartialBins","PartialBins","FullPalletBins","Damages","Missing"]
        hist = hist.dropna(subset=["Timestamp"]).sort_values("Timestamp")
        fig = go.Figure()
        colors = {
            "EmptyBins": BLUE, "EmptyPartialBins": "#1480e6", "PartialBins": "#4e9fe6",
            "FullPalletBins": "#e67e22", "Damages": RED, "Missing": "#8e44ad"
        }
        for k in kpis:
            if k in hist.columns:
                fig.add_trace(go.Scatter(x=hist["Timestamp"], y=hist[k], name=k, mode="lines+markers",
                                         line=dict(color=colors.get(k, "#444"), width=2)))
        fig.update_layout(height=460, hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(hist, use_container_width=True)

elif selected_nav == "Config":
    st.subheader("Configuration")
    st.markdown("**Bulk capacity by zone (max pallets per location first letter)**")
    with st.form("bulk_cfg"):
        cols = st.columns(len(DEFAULT_BULK_RULES))
        new_rules = {}
        for (zone, default), c in zip(DEFAULT_BULK_RULES.items(), cols):
            new_rules[zone] = c.number_input(zone, min_value=0, max_value=50, value=int(bulk_rules.get(zone, default)))
        submitted = st.form_submit_button("Save")
    if submitted:
        cfg = {"bulk_rules": {k: int(v) for k, v in new_rules.items()}}
        save_config(cfg)
        st.success("Saved. Please refresh to apply.")
    st.caption(f"Config file: `{CONFIG_FILE}`")

elif selected_nav == "Self-Test":
    st.subheader("Self-Test")
    st.markdown("**LOT normalization examples**")
    ex = pd.DataFrame({"Raw": ["9063615", "09063615", "90-63615", "9063615.0", "LOT#9063615", None],
                       "Normalized": [normalize_lot_number(x) for x in ["9063615", "09063615", "90-63615", "9063615.0", "LOT#9063615", None]]})
    st.table(ex)
    st.markdown("**Pallet ID normalization examples**")
    ex2 = pd.DataFrame({"Raw": ["JTL00496", "123.0", "  123  ", None],
                        "Normalized": [normalize_pallet_id(x) for x in ["JTL00496", "123.0", "  123  ", None]]})
    st.table(ex2)
    st.markdown("**Paths**")
    st.write({"LOG_DIR": LOG_DIR, "DATA_DIR": DATA_DIR, "CONFIG_FILE": CONFIG_FILE, "TRENDS_FILE": TRENDS_FILE, "RESOLVED_FILE": RESOLVED_FILE})
    st.markdown("**Environment Override**")
    st.write({"BIN_HELPER_LOG_DIR": os.environ.get("BIN_HELPER_LOG_DIR", "")})
    st.success("All tests rendered.")
``