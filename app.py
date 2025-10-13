# -*- coding: utf-8 -*-
import os
import csv
import re
import time  # KPI animations
import hashlib  # <-- for file hash (trend de-dup)
import tempfile  # SAFEGUARD: fallback dirs
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# Allow BIN_HELPER_LOG_DIR via Streamlit Secrets as well
try:
    if "BIN_HELPER_LOG_DIR" in st.secrets:
        os.environ["BIN_HELPER_LOG_DIR"] = st.secrets["BIN_HELPER_LOG_DIR"]
except Exception:
    pass

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
                "https://assets10.lottiefiles.com/packages/lf20_9kmmv9.json",
                "https://assets2.lottiefiles.com/packages/lf20_1pxqjqps.json",
                "https://assets9.lottiefiles.com/packages/lf20_wnqlfojb.json",
                "https://assets10.lottiefiles.com/packages/lf20_j1adxtyb.json",
            ]:
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

show_banner()

# ===== SAFEGUARD: robust path resolution & file append =====
def _resolve_writable_dir(preferred: str | None, purpose: str = "logs") -> tuple[str, bool]:
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

def _safe_append_csv(path: str, header: list[str], row: list) -> tuple[bool, str, str]:
    def _try_write(p: str) -> tuple[bool, str]:
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

# ===== Paths =====
PREFERRED_LOG_DIR = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
LOG_DIR, LOG_FALLBACK_USED = _resolve_writable_dir(PREFERRED_LOG_DIR, purpose="logs")
DATA_DIR, DATA_FALLBACK_USED = _resolve_writable_dir(os.path.join(os.path.dirname(LOG_DIR), "data"), purpose="data")
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
    safe_name = re.sub(r"[^\w.\-]+", "_", uploaded.name)
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

    st.subheader("🗂 Log Folder")
    st.caption(f"Path: `{LOG_DIR}`")
    if LOG_FALLBACK_USED:
        st.warning("Using fallback log folder (preferred path not writable here).")
    else:
        st.success("Writing to preferred log folder.")

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

# ===== Normalization =====
def normalize_whole_number(val) -> str:
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

def ensure_numeric_col(df: pd.DataFrame, col: str, default: float | int = 0):
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
inventory_df["PalletId"] = inventory_df["PalletId"].apply(normalize_whole_number)
inventory_df["CustomerLotReference"] = inventory_df["CustomerLotReference"].apply(normalize_whole_number)

# ===== Rules / helpers =====
def exclude_damage_missing(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["LocationName"].str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])].copy()

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
        & (s.str[0].str.isdigit())
    )
    return df2.loc[mask].copy()

def get_full_pallet_bins(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    s = df2["LocationName"].astype(str)
    # OR between conditions is required
    mask = ((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric() & df2["Qty"].between(6, 15)
    return df2.loc[mask].copy()

def get_empty_partial_bins(master_locs: set, occupied_locs: set) -> pd.DataFrame:
    series = pd.Series(list(master_locs), dtype=str)
    mask = (
        series.str.endswith("01")
        & ~series.str.startswith("111")
        & ~series.str.upper().str.startswith("TUN")
        & (series.str[0].str.isdigit())
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

# ===== Rack discrepancies =====
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

    # Full rack issues (Qty outside 6..15 treated as partial needing move)
    s = df2["LocationName"].astype(str)
    full_mask = ((~s.str.endswith("01")) | (s.str.startswith("111"))) & s.str.isnumeric()
    f_df = df2.loc[full_mask]
    if not f_df.empty:
        fe = f_df[~f_df["Qty"].between(6, 15)]
        for _, row in fe.iterrows():
            rec = row.to_dict()
            rec["Issue"] = "Partial Pallet needs to be moved to Partial Location"
            results.append(rec)

    # Multi pallet in any rack location
    _, mp_details = _find_multi_pallet_all_racks(df2)
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

RESOLVED_HEADER = [
    "Timestamp", "Action", "BatchId", "DiscrepancyType", "RowKey",
    "LocationName", "PalletId", "WarehouseSku", "CustomerLotReference",
    "Qty", "Issue", "Note", "SelectedLOT"
]

def log_action(row: dict, note: str, selected_lot: str, discrepancy_type: str, action: str, batch_id: str) -> tuple[bool, str, str]:
    csv_row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        action, batch_id, discrepancy_type, _row_key(row, discrepancy_type),
        row.get("LocationName", ""), row.get("PalletId", ""), row.get("WarehouseSku", ""),
        row.get("CustomerLotReference", ""), row.get("Qty", ""), row.get("Issue", ""),
        note, selected_lot
    ]
    ok, used_path, err = _safe_append_csv(resolved_file, RESOLVED_HEADER, csv_row)
    return ok, used_path, err

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str) -> tuple[str, str]:
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    used_path = resolved_file
    for _, r in df_rows.iterrows():
        ok, upath, err = log_action(r.to_dict(), note, selected_lot, discrepancy_type, action, batch_id)
        used_path = upath
        if not ok:
            st.error(f"Failed to write action log.\n{err}")
            break
    return batch_id, used_path

def read_action_log() -> pd.DataFrame:
    try:
        if os.path.isfile(resolved_file):
            return pd.read_csv(resolved_file)
        fb_dir, _ = _resolve_writable_dir(None, purpose="logs")
        fb_path = os.path.join(fb_dir, os.path.basename(resolved_file))
        if os.path.isfile(fb_path):
            return pd.read_csv(fb_path)
    except Exception:
        pass
    return pd.DataFrame()

# ===== Display helpers =====
CORE_COLS = ["LocationName", "WarehouseSku", "PalletId", "CustomerLotReference", "Qty"]

def _lot_to_str(x):
    return normalize_whole_number(x)

def ensure_core(df: pd.DataFrame, include_issue: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=CORE_COLS + (["Issue"] if include_issue else []))
    out = df.copy()
    for c in CORE_COLS:
        if c not in out.columns:
            out[c] = ""
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

# === LOT choices utility (kept; harmless if unused) ===
def build_lot_choices_from_df(df: pd.DataFrame, col: str = "CustomerLotReference", include_all: bool = True) -> list[str]:
    if df is None or df.empty or col not in df.columns:
        return ["(All)"] if include_all else []
    series = df[col].map(_lot_to_str)
    vals = sorted({v for v in series if isinstance(v, str) and v.strip()})
    return (["(All)"] + vals) if include_all else vals

# ===== KPI Card CSS =====
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
            return False, TRENDS_FILE, ""
    header = list(row.keys())
    csv_row = [row[h] for h in header]
    ok, used_path, err = _safe_append_csv(TRENDS_FILE, header, csv_row)
    return ok, used_path, err

if st.session_state.get("pending_trend_record", False):
    took, used_path, err = _append_trend_snapshot(_current_kpis(), inventory_file)
    if took:
        st.success(f"📈 Trend snapshot recorded → `{used_path}`")
        if err:
            st.info(f"(Used fallback) {err}")
    else:
        st.info("Trend snapshot skipped (same file as last snapshot).")
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

    # Charts
    kpi_df = pd.DataFrame({"Category": list(kpi_vals.keys()), "Count": list(kpi_vals.values())})
    kpi_df["Group"] = kpi_df["Category"].apply(lambda c: "Exceptions" if c in ["Damages", "Missing"] else "Bins")
    fig_kpi = px.bar(kpi_df, x="Category", y="Count", color="Group", text="Count",
                     title="Bin Status Distribution", color_discrete_map={"Bins": BLUE, "Exceptions": RED})
    fig_kpi.update_traces(textposition="outside")
    fig_kpi.update_layout(xaxis_title="", yaxis_title="Count", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig_kpi, use_container_width=True)

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

    if not pd.DataFrame(bulk_locations_df).empty:
        bulk_zone = bulk_locations_df.groupby("Zone").agg(Used=("PalletCount", "sum"), Capacity=("MaxAllowed", "sum")).reset_index()
        bulk_zone["Empty"] = (bulk_zone["Capacity"] - bulk_zone["Used"]).clip(lower=0)
        bulk_stack = bulk_zone.melt(id_vars="Zone", value_vars=["Used", "Empty"], var_name="Type", value_name="Count")
        fig_bulk = px.bar(bulk_stack, x="Zone", y="Count", color="Type", barmode="stack",
                          title="Bulk Zones: Used vs Empty Capacity", color_discrete_map={"Used": BLUE, "Empty": RED})
        fig_bulk.update_layout(xaxis_title="Zone", yaxis_title="Pallets", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
        st.plotly_chart(fig_bulk, use_container_width=True)

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
        lots = build_lot_choices_from_df(discrepancy_df, include_all=True)
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="rack_lot_filter",
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
                    .apply(lambda s: ", ".join(sorted({normalize_whole_number(x) for x in s if normalize_whole_number(x)})))
                    .reset_index(name="AllPalletIDs")
                )
                mp_summary_tbl = summary_cnt.merge(all_ids, on="LocationName", how="left")
                st.dataframe(mp_summary_tbl, use_container_width=True)
            else:
                st.info("No multi‑pallet rack locations in the current filter.")

        rack_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(maybe_limit(rack_display)), use_container_width=True)

        csv_data = discrepancy_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Rack Discrepancies CSV", csv_data, "rack_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = build_lot_choices_from_df(discrepancy_df, include_all=False)
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="rack_fix_lot")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="rack_fix_note")
            if st.button("Fix Selected LOT", key="rack_fix_btn"):
                rows_to_fix = discrepancy_df[discrepancy_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Rack", action="RESOLVE")
                st.success(f"Resolved {len(rows_to_fix)} rack discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
                data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                if data:
                    st_lottie(data, height=90, key=f"rack_fix_success_{batch_id}", loop=False, speed=1.2)
        else:
            st.info("No valid LOTs available to fix.")

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
                            ok, upath, err = log_action(r.to_dict(), f"UNDO of batch {last_batch}",
                                                        r.get("SelectedLOT", ""), "Rack", "UNDO", str(last_batch))
                            if not ok:
                                st.error(f"Failed to write UNDO action. {err}")
                                break
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
        lots = build_lot_choices_from_df(bulk_df, include_all=True)
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="bulk_lot_filter",
                               help="Only non-empty LOTs are shown. Use (All) to see every row.")
        filt = bulk_df if sel_lot == "(All)" else bulk_df[bulk_df["CustomerLotReference"].map(_lot_to_str) == sel_lot]

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
                        if req not in sel_rows.columns:
                            sel_rows[req] = ""
                    batch_id, used_path = log_batch(sel_rows, note, selected_lot_value, "Bulk", action="RESOLVE")
                    st.success(f"Logged fix for {len(sel_rows)} row(s).")
                    st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
                    data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                    if data:
                        st_lottie(data, height=90, key=f"bulk_fix_success_{batch_id}", loop=False, speed=1.2)

        st.markdown("#### Flat view (all rows)")
        bulk_display = ensure_core(filt, include_issue=True)
        st.dataframe(style_issue_red(maybe_limit(bulk_display)), use_container_width=True)
        csv_data = bulk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Bulk Discrepancies CSV", csv_data, "bulk_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = build_lot_choices_from_df(bulk_df, include_all=False)
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="bulk_fix_lot")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="bulk_fix_note")
            if st.button("Fix Selected LOT", key="bulk_fix_btn"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Bulk", action="RESOLVE")
                st.success(f"Resolved {len(rows_to_fix)} bulk discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
                data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                if data:
                    st_lottie(data, height=90, key=f"bulk_lot_fix_success_{batch_id}", loop=False, speed=1.2)
        else:
            st.info("No valid LOTs available to fix.")
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
                            ok, upath, err = log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT",""), "Bulk", "UNDO", str(last_batch))
                            if not ok:
                                st.error(f"Failed to write UNDO action. {err}")
                                break
                        st.success(f"UNDO recorded for batch {last_batch} ({len(rows)} row(s)).")
                        data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                        if data:
                            st_lottie(data, height=90, key=f"bulk_undo_success_{last_batch}", loop=False, speed=1.2)
                    else:
                        st.info("No RESOLVE actions to undo for Bulk.")
            else:
                st.info("No actions logged yet.")
    else:
        st.info("No bulk discrepancies found.")

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.caption("Click a location, then use the dropdown to view pallets for that slot.")
    # High-contrast row highlight for over-capacity (GRID mode)
    st.markdown("""
    <style>
    .ag-theme-streamlit .ag-row.overCapRow { background-color:#ffe3e6 !important; }
    .ag-theme-streamlit .ag-row.overCapRow .ag-cell { color:#7f1d1d; font-weight:600; }
    </style>
    """, unsafe_allow_html=True)

    ui_mode_default_index = 1 if _AGGRID_AVAILABLE else 0
    ui_mode = st.radio("View mode", ["Expanders", "Grid (select a location)"],
                       index=ui_mode_default_index, horizontal=True, key="bulk_loc_mode")

    search = st.text_input("Search location (optional)", value="", key="bulk_loc_search2")
    parent_df = bulk_locations_df.copy()
    if not parent_df.empty and search.strip():
        parent_df = parent_df[parent_df["LocationName"].astype(str).str.contains(search.strip(), case=False, na=False)]
    if not parent_df.empty:
        over_mask = parent_df["PalletCount"] > parent_df["MaxAllowed"]
        if over_mask.any():
            st.warning(f"{over_mask.sum()} location(s) exceed max allowed pallets. Highlighted in red.")

    if ui_mode.startswith("Grid") and _AGGRID_AVAILABLE and not parent_df.empty:
        # Build grid of bulk locations (no enterprise features)
        show_cols = ["LocationName", "Zone", "PalletCount", "MaxAllowed", "EmptySlots"]
        grid_df = parent_df[show_cols].copy()

        gb = GridOptionsBuilder.from_dataframe(grid_df)
        gb.configure_default_column(resizable=True, filter=True, sortable=True, floatingFilter=True)
        # Highlight rows that exceed capacity
        if JsCode is not None:
            get_row_class = JsCode("""
            function(params) {
              if (params.data && (params.data.PalletCount > params.data.MaxAllowed)) {
                return 'overCapRow';
              }
              return null;
            }
            """)
            gb.configure_grid_options(getRowClass=get_row_class)
        gb.configure_pagination(enabled=True, paginationAutoPageSize=False, paginationPageSize=50)
        gb.configure_side_bar()
        # Single-select a location
        gb.configure_selection("single", use_checkbox=True)
        grid_options = gb.build()

        grid_resp = AgGrid(
            grid_df,
            gridOptions=grid_options,
            allow_unsafe_jscode=True,
            update_mode=GridUpdateMode.SELECTION_CHANGED,
            fit_columns_on_grid_load=True,
            height=540,
            theme="streamlit"
        )

        sel_rows = pd.DataFrame(grid_resp.get("selected_rows", []))
        if sel_rows.empty:
            st.info("Select a location row above to view its pallets.")
        else:
            sel_loc = str(sel_rows.iloc[0]["LocationName"])
            # Pull pallet rows for that location from inventory
            inv_bulk = filtered_inventory_df[
                filtered_inventory_df["LocationName"].astype(str) == sel_loc
            ].copy()

            if inv_bulk.empty:
                st.warning(f"No pallets found for location {sel_loc}.")
            else:
                # Build a dropdown of pallets
                inv_bulk["PalletId"] = inv_bulk["PalletId"].apply(normalize_whole_number)
                inv_bulk["CustomerLotReference"] = inv_bulk["CustomerLotReference"].apply(_lot_to_str)

                def _label(r):
                    pid = r.get("PalletId", "") or "[blank]"
                    sku = r.get("WarehouseSku", "") or "[no SKU]"
                    lot = r.get("CustomerLotReference", "") or "[no LOT]"
                    qty = r.get("Qty", 0)
                    try:
                        qty = int(qty)
                    except Exception:
                        pass
                    return f"{pid} — SKU {sku} — LOT {lot} — Qty {qty}"

                # Unique pallets by PalletId (fallback to row index if blank)
                inv_bulk["_PID_KEY"] = inv_bulk["PalletId"].where(inv_bulk["PalletId"].astype(str).str.len() > 0,
                                                                  inv_bulk.index.astype(str))
                unique_rows = inv_bulk.drop_duplicates(subset=["_PID_KEY"])
                choices = ["(All)"] + [ _label(r) for _, r in unique_rows.iterrows() ]
                selected_label = st.selectbox(f"Pallets at {sel_loc}", choices, index=0, key=f"pallet_dd_{sel_loc}")

                if selected_label == "(All)":
                    show_df = inv_bulk
                else:
                    # Find the row that matches the selected label
                    # build a quick map label -> _PID_KEY
                    label_to_key = { _label(r): r["_PID_KEY"] for _, r in unique_rows.iterrows() }
                    chosen_key = label_to_key.get(selected_label, None)
                    if chosen_key is None:
                        show_df = inv_bulk
                    else:
                        show_df = inv_bulk[inv_bulk["_PID_KEY"] == chosen_key]

                st.dataframe(ensure_core(show_df), use_container_width=True)

    else:
        # Expanders fallback (works without AgGrid)
        if parent_df.empty:
            st.info("No bulk locations found.")
        else:
            df_show = parent_df.sort_values(["Zone", "LocationName"])
            for _, r in df_show.iterrows():
                loc = str(r["LocationName"])
                over_by = int(r["PalletCount"] - r["MaxAllowed"])
                over_badge = f' <span style="color:#b00020;font-weight:700;">❗ OVER {over_by}</span>' if over_by > 0 else ""
                header = f"{loc} — {int(r['PalletCount'])}/{int(r['MaxAllowed'])} (Empty {int(r['EmptySlots'])}){over_badge}"
                with st.expander(header, expanded=False):
                    loc_rows = filtered_inventory_df[filtered_inventory_df["LocationName"].astype(str) == loc].copy()
                    # Build a dropdown here too
                    if loc_rows.empty:
                        st.info("No pallets in this location.")
                    else:
                        loc_rows["PalletId"] = loc_rows["PalletId"].apply(normalize_whole_number)
                        loc_rows["CustomerLotReference"] = loc_rows["CustomerLotReference"].apply(_lot_to_str)

                        def _label2(r):
                            pid = r.get("PalletId", "") or "[blank]"
                            sku = r.get("WarehouseSku", "") or "[no SKU]"
                            lot = r.get("CustomerLotReference", "") or "[no LOT]"
                            qty = r.get("Qty", 0)
                            try:
                                qty = int(qty)
                            except Exception:
                                pass
                            return f"{pid} — SKU {sku} — LOT {lot} — Qty {qty}"

                        loc_rows["_PID_KEY"] = loc_rows["PalletId"].where(loc_rows["PalletId"].astype(str).str.len() > 0,
                                                                          loc_rows.index.astype(str))
                        unique_rows2 = loc_rows.drop_duplicates(subset=["_PID_KEY"])
                        choices2 = ["(All)"] + [ _label2(r) for _, r in unique_rows2.iterrows() ]
                        selected_label2 = st.selectbox(f"Pallets at {loc}", choices2, index=0, key=f"pallet_dd_exp_{loc}")
                        if selected_label2 == "(All)":
                            show_df2 = loc_rows
                        else:
                            label_to_key2 = { _label2(r): r["_PID_KEY"] for _, r in unique_rows2.iterrows() }
                            chosen_key2 = label_to_key2.get(selected_label2, None)
                            if chosen_key2 is None:
                                show_df2 = loc_rows
                            else:
                                show_df2 = loc_rows[loc_rows["_PID_KEY"] == chosen_key2]
                        st.dataframe(ensure_core(show_df2), use_container_width=True)

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
                fig_bins = px.line(
                    bins_long, x="Timestamp", y="Count", color="Series",
                    title="Bins Trend (Empty / Empty Partial / Partial / Full)",
                    color_discrete_map={s: BLUE for s in bins_long["Series"].unique()}
                )
                for s in dash_map:
                    fig_bins.for_each_trace(lambda t: t.update(line=dict(dash=dash_map[s])) if t.name == s else ())
                fig_bins.update_traces(mode="lines+markers")
                fig_bins.update_layout(showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
                st.plotly_chart(fig_bins, use_container_width=True)

            req_exc = ["Damages", "Missing"]
            if all(c in hist.columns for c in req_exc):
                exc_long = hist.melt(id_vars=["Timestamp"], value_vars=req_exc, var_name="Status", value_name="Count")
                fig_exc = px.line(
                    exc_long, x="Timestamp", y="Count", color="Status",
                    title="Exceptions Trend (Damages vs Missing)",
                    color_discrete_map={"Damages": RED, "Missing": BLUE}
                )
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
    full_mask = ((~s3.str.endswith("01")) | s3.str.startswith("111")) & s3.str.isnumeric()
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
        for p in problems:
            st.write("- ", p)
        if st.button("Go to Rack Discrepancies (review)"):
            st.session_state["pending_nav"] = "Rack Discrepancies"
            _rerun()
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
                    st.session_state["pending_nav"] = "Rack Discrepancies"
                    _rerun()
            else:
                st.warning(f"⚠️ WARN — {len(offenders)} full-rack rows have Qty outside 6..15 (expected discrepancies, and all are flagged).")
                with st.expander("Show sample offenders (top 10)"):
                    show_cols = [c for c in ["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference", "Qty"] if c in offenders.columns]
                    st.dataframe(maybe_limit(offenders[show_cols].head(10)), use_container_width=True)
                if st.button("Go to Rack Discrepancies"):
                    st.session_state["pending_nav"] = "Rack Discrepancies"
                    _rerun()
