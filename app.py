# -*- coding: utf-8 -*-
"""
Bin Helper — streamlined, animated inventory dashboard with NLQ, discrepancies,
bulk capacity rules, fix logs, robust logging, and persistent trend analytics.
"""
# ---------- Imports ----------
import os
import csv
import re
import time  # KPI animations
import json  # Config file
import hashlib  # file hash (trend de-dup)
import tempfile  # SAFEGUARD: fallback dirs
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List, Union
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_lottie import st_lottie
import requests

# ---------- PAGE CONFIG (must be the first Streamlit call) ----------
st.set_page_config(page_title="Bin Helper", layout="wide")
# Allow BIN_HELPER_LOG_DIR via Streamlit Secrets as well (safe AFTER page_config)
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

# ---------- THEME COLORS ----------
BLUE = "#1f77b4"  # Plotly classic blue
RED  = "#d62728"  # Plotly classic red
GREEN = "#2ca02c"
px.defaults.template = "plotly_white"

# ---------- SESSION STATE ----------
if "filters" not in st.session_state:
    st.session_state.filters = {
        "LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""
    }
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "inventory_path" not in st.session_state:
    st.session_state.inventory_path = None  # set when user uploads
if "jump_intent" not in st.session_state:
    st.session_state.jump_intent = {}

# ---------- UTIL: rerun wrapper ----------
def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ---------- Lottie helpers ----------
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

# ===== Paths =====
PREFERRED_LOG_DIR = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
LOG_DIR, LOG_FALLBACK_USED = _resolve_writable_dir(PREFERRED_LOG_DIR, purpose="logs")
DATA_DIR, DATA_FALLBACK_USED = _resolve_writable_dir(os.path.join(os.path.dirname(LOG_DIR), "data"), purpose="data")
CONFIG_FILE = os.path.join(LOG_DIR, "config.json")
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
    auto_record = st.toggle("Auto-record snapshot on upload", value=True, key="auto_record_trend")
    # NEW: persistent trend controls
    auto_snapshot_on_start = st.toggle("Auto-snapshot on startup", value=True, key="auto_snapshot_on_start")
    snap_every_min = st.number_input("Auto-snapshot interval (minutes)", min_value=5, max_value=240, value=60, step=5, key="trend_interval_min")

    if up is not None:
        saved_path = _save_uploaded_inventory(up)
        st.session_state.inventory_path = saved_path
        st.success(f"Saved: {os.path.basename(saved_path)}")
        if auto_record:
            st.session_state["pending_trend_record"] = True

    st.subheader("⚡ Performance")
    st.toggle("Fast tables (limit to 1000 rows)", value=False, key="fast_tables")
    st.button("🔄 Refresh Data", on_click=_clear_cache_and_rerun)

    st.subheader("🏚️ Log Folder")
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
def normalize_lot_number(val) -> str:
    """Numeric-only, strip non-digits and leading zeros; keep empty if none."""
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
    - If integer-like (e.g., '123.0'), coerce to '123'.
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
inventory_df["PalletId"] = inventory_df["PalletId"].apply(normalize_pallet_id)  # keep alphanumeric
inventory_df["CustomerLotReference"] = inventory_df["CustomerLotReference"].apply(normalize_lot_number)

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
    # Full pallet bins: numeric locations that are (not '...01' OR starts with '111') and Qty between 6 and 15
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

def analyze_bulk_locations_grouped(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    # NEW: remove IB* locations from discrepancies
    df2 = df2[~df2["LocationName"].astype(str).str.upper().str.startswith("IB")]

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

# ===== Config: bulk capacity =====
DEFAULT_BULK_RULES = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def load_config() -> dict:
    cfg = {"bulk_rules": DEFAULT_BULK_RULES.copy()}
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "bulk_rules" in raw and isinstance(raw["bulk_rules"], dict):
                cfg["bulk_rules"] = {str(k).upper(): int(v) for k, v in raw["bulk_rules"].items()
                                     if str(k).upper() in DEFAULT_BULK_RULES}
    except Exception:
        pass
    return cfg

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

_config = load_config()
bulk_rules = _config.get("bulk_rules", DEFAULT_BULK_RULES).copy()

# ===== Build views (computed from bulk_rules) =====
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
                "LocationName": location, "Zone": zone,
                "PalletCount": count, "MaxAllowed": max_allowed,
                "EmptySlots": max(0, empty_slots)
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

# ===== Core table helpers =====
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

# Precomputed indices for speed
LOC_INDEX: Dict[str, pd.DataFrame] = {}
for loc, g in filtered_inventory_df.groupby(filtered_inventory_df["LocationName"].astype(str)):
    LOC_INDEX[str(loc)] = ensure_core(g)

# ===== Pallet label builder (QTY-first & sorted) =====
def _mk_pallet_labels(df: pd.DataFrame):
    """
    Returns:
      labels (List[str]): formatted labels sorted by Qty ASC (QTY 0..N first)
      label_to_key (Dict[str, Any]): label -> internal row key
      df_with_keys (pd.DataFrame)
    Label format: "QTY {qty} — {PalletId} — SKU {sku} — LOT {lot}"
    """
    df = df.copy()
    df["PalletId"] = df["PalletId"].apply(normalize_pallet_id)
    df["CustomerLotReference"] = df["CustomerLotReference"].apply(_lot_to_str)

    def _label(r):
        pid = r.get("PalletId", "") or "[blank]"
        sku = r.get("WarehouseSku", "") or "[no SKU]"
        lot = r.get("CustomerLotReference", "") or "[no LOT]"
        qty = r.get("Qty", 0)
        try:
            qty_i = int(qty)
        except Exception:
            qty_i = qty
        return f"QTY {qty_i:>3} — {pid} — SKU {sku} — LOT {lot}", qty_i

    df["_PID_KEY"] = df["PalletId"].where(df["PalletId"].astype(str).str.len() > 0, df.index.astype(str))
    uniq = df.drop_duplicates(subset=["_PID_KEY"]).copy()
    tmp = [(_label(r), r) for _, r in uniq.iterrows()]
    tmp_sorted = sorted(tmp, key=lambda t: (t[0][1] if isinstance(t[0][1], int) else 0, str(t[1].get("PalletId", ""))))
    labels = [t[0][0] for t in tmp_sorted]
    label_to_key = { (_label(r)[0]): r["_PID_KEY"] for _, r in uniq.iterrows() }
    return labels, label_to_key, df

PALLET_LABELS_BY_LOC: Dict[str, Tuple[List[str], dict, pd.DataFrame]] = {}
for loc, df in LOC_INDEX.items():
    PALLET_LABELS_BY_LOC[loc] = _mk_pallet_labels(df)

# ===== File freshness badge =====
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
    st.caption(f"**File:** {name} • **Modified:** {mtime.strftime('%Y-%m-%d %I:%M:%S %p') if mtime else 'n/a'} • **Age:** {age_txt} • **MD5:** {md5_short} • **Since last snapshot:** {since_snap}")

_file_freshness_panel()

# ===== Logging (with Reason codes) =====
def _resolved_has_reason() -> bool:
    try:
        if os.path.isfile(resolved_file):
            with open(resolved_file, "r", encoding="utf-8") as f:
                first = f.readline().strip()
                return "Reason" in first.split(",")
    except Exception:
        pass
    return False

RESOLVED_HEADER_V1 = [
    "Timestamp", "Action", "BatchId", "DiscrepancyType", "RowKey",
    "LocationName", "PalletId", "WarehouseSku", "CustomerLotReference",
    "Qty", "Issue", "Note", "SelectedLOT"
]
RESOLVED_HEADER_V2 = RESOLVED_HEADER_V1 + ["Reason"]

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

def log_action(row: dict, note: str, selected_lot: str, discrepancy_type: str, action: str, batch_id: str, reason: str = "") -> Tuple[bool, str, str]:
    has_reason = _resolved_has_reason()
    csv_row_v1 = [
        datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        action, batch_id, discrepancy_type, _row_key(row, discrepancy_type),
        row.get("LocationName", ""), row.get("PalletId", ""), row.get("WarehouseSku", ""),
        row.get("CustomerLotReference", ""), row.get("Qty", ""), row.get("Issue", ""),
        (f"[Reason: {reason}] " if reason and not has_reason else "") + (note or ""),
        selected_lot
    ]
    if has_reason:
        csv_row = csv_row_v1 + [reason]
        header = RESOLVED_HEADER_V2
    else:
        csv_row = csv_row_v1
        header = RESOLVED_HEADER_V1
    ok, used_path, err = _safe_append_csv(resolved_file, header, csv_row)
    return ok, used_path, err

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str, reason: str = "") -> Tuple[str, str]:
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    used_path = resolved_file
    for _, r in df_rows.iterrows():
        ok, upath, err = log_action(r.to_dict(), note, selected_lot, discrepancy_type, action, batch_id, reason=reason)
        used_path = upath
        if not ok:
            st.error(f"Failed to write action log.\n{err}")
            break
    return batch_id, used_path

def read_action_log() -> pd.DataFrame:
    try:
        if os.path.isfile(resolved_file):
            return pd.read_csv(resolved_file, engine="python")
        fb_dir, _ = _resolve_writable_dir(None, purpose="logs")
        fb_path = os.path.join(fb_dir, os.path.basename(resolved_file))
        if os.path.isfile(fb_path):
            return pd.read_csv(fb_path, engine="python")
    except Exception:
        pass
    return pd.DataFrame()

# --- Common "Download Fix Log" button ---
def download_fix_log_button(where_key: str = "fixlog"):
    log_df = read_action_log()
    if log_df.empty:
        st.info("No fix actions logged yet.")
    else:
        st.download_button(
            "Download Fix Log (resolved_discrepancies.csv)",
            log_df.to_csv(index=False).encode("utf-8"),
            file_name="resolved_discrepancies.csv",
            mime="text/csv",
            key=f"dl_fixlog_{where_key}"
        )

# ===== Discrepancies (calculations) =====
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

def analyze_discrepancies(df: pd.DataFrame) -> pd.DataFrame:
    df2 = exclude_damage_missing(df)
    # NEW: remove IB* locations from discrepancies
    df2 = df2[~df2["LocationName"].astype(str).str.upper().str.startswith("IB")]

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
    try:
        _, mp_details = _find_multi_pallet_all_racks(df2)
        if mp_details is not None and not mp_details.empty:
            results += mp_details.to_dict("records")
    except Exception as e:
        # Don't break the page; just warn and continue
        st.warning(f"Multi-pallet check skipped: {e}")

# ===== Duplicate Pallets (case-insensitive) =====
def build_duplicate_pallets(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    base = df.copy()
    # NEW: remove IB* locations from duplicate discrepancy calculations
    base = base[~base["LocationName"].astype(str).str.upper().str.startswith("IB")]

    base["PalletId"] = base["PalletId"].apply(normalize_pallet_id)
    base["PalletId_norm"] = base["PalletId"].astype(str).str.strip().str.upper()

    grp = base.groupby("PalletId_norm")["LocationName"].nunique().reset_index(name="DistinctLocations")
    dups = grp[(grp["PalletId_norm"].astype(str).str.len() > 0) & (grp["DistinctLocations"] > 1)] \
            .sort_values("DistinctLocations", ascending=False)

    if dups.empty:
        return dups.rename(columns={"PalletId_norm": "PalletId"}), pd.DataFrame()

    dup_ids = set(dups["PalletId_norm"])
    details = base[base["PalletId_norm"].isin(dup_ids)].copy()
    dups = dups.rename(columns={"PalletId_norm": "PalletId"})
    return dups, ensure_core(details)

dups_summary_df, dups_detail_df = build_duplicate_pallets(filtered_inventory_df)

# ===== Natural Language Query (Ask Bin Helper) =====
from dataclasses import dataclass
@dataclass
class NLQResult:
    df: pd.DataFrame
    explanation: str
    warning: str = ""

def _num_from_text(s: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", s or "")]

def parse_comparator(q: str):
    ql = (q or "").lower()
    # between X and Y
    m_between = re.search(r"between\s+(\d+)\s+and\s+(\d+)", ql)
    if m_between:
        a, b = int(m_between.group(1)), int(m_between.group(2))
        lo, hi = min(a, b), max(a, b)
        return ("between", lo, hi)
    # ≤ like "or less", "at most", "<=", "≤"
    if re.search(r"(or\s+less|at\s+most|<=|≤)", ql):
        nums = _num_from_text(ql)
        return ("le", max(nums or [0]))
    # ≥ like "or more", "at least", ">=", "≥"
    if re.search(r"(or\s+more|at\s+least|>=|≥)", ql):
        nums = _num_from_text(ql)
        return ("ge", max(nums or [0]))
    # = exactly
    if re.search(r"(\bexactly\b|\bequal(?:s)?\s+to\b|==)", ql):
        nums = _num_from_text(ql)
        return ("eq", nums[0] if nums else 0)
    nums = _num_from_text(ql)
    if nums:
        return ("eq", nums[0])
    return (None, None)

def parse_nl_query(q: str) -> NLQResult:
    ql = (q or "").strip().lower()
    if not ql:
        return NLQResult(pd.DataFrame(), "Type something like: 'show me bulk locations with 5 pallets or less'.")
    # --- BULK domain ---
    if "bulk" in ql:
        df = bulk_locations_df.copy()
        if "empty slot" in ql or "empty slots" in ql or "available" in ql:
            cmp = parse_comparator(ql)
            if cmp[0] == "between":
                _, lo, hi = cmp
                df = df[(df["EmptySlots"] >= lo) & (df["EmptySlots"] <= hi)]
                return NLQResult(df, f"Bulk locations with EmptySlots between {lo} and {hi}.")
            elif cmp[0] == "le":
                _, n = cmp
                df = df[df["EmptySlots"] <= n]
                return NLQResult(df, f"Bulk locations with EmptySlots ≤ {n}.")
            elif cmp[0] == "ge":
                _, n = cmp
                df = df[df["EmptySlots"] >= n]
                return NLQResult(df, f"Bulk locations with EmptySlots ≥ {n}.")
            elif cmp[0] == "eq":
                _, n = cmp
                df = df[df["EmptySlots"] == n]
                return NLQResult(df, f"Bulk locations with EmptySlots == {n}.")
            else:
                df = df[df["EmptySlots"] >= 1]
                return NLQResult(df, "Bulk locations with at least 1 EmptySlot.")
        cmp = parse_comparator(ql)
        if cmp[0] == "between":
            _, lo, hi = cmp
            df = df[(df["PalletCount"] >= lo) & (df["PalletCount"] <= hi)]
            return NLQResult(df, f"Bulk locations with PalletCount between {lo} and {hi}.")
        elif cmp[0] == "le":
            _, n = cmp
            df = df[df["PalletCount"] <= n]
            return NLQResult(df, f"Bulk locations with PalletCount ≤ {n}.")
        elif cmp[0] == "ge":
            _, n = cmp
            df = df[df["PalletCount"] >= n]
            return NLQResult(df, f"Bulk locations with PalletCount ≥ {n}.")
        elif cmp[0] == "eq":
            _, n = cmp
            df = df[df["PalletCount"] == n]
            return NLQResult(df, f"Bulk locations with PalletCount == {n}.")
        else:
            return NLQResult(df, "All bulk locations.")
    # --- DUPLICATES ---
    if "duplicate" in ql or "duplicates" in ql:
        m_pid = re.search(r"(?:pallet|pallet id)\s+([A-Za-z0-9\-]+)", q or "", re.IGNORECASE)
        if m_pid:
            pid_norm = m_pid.group(1).strip().upper()
            det = dups_detail_df[dups_detail_df["PalletId"].astype(str).str.strip().str.upper() == pid_norm]
            return NLQResult(ensure_core(det), f"Duplicate detail for PalletId {pid_norm}.")
        return NLQResult(dups_summary_df.copy(), "Duplicate pallet summary (PalletId with distinct location count).")
    # --- PARTIAL / FULL / RACK MULTI-PALLET ---
    if "partial bin" in ql or "partial bins" in ql or "partial" in ql:
        df = ensure_core(partial_bins_df)
        m = re.search(r"aisle\s+(\d{3})", ql)
        if m:
            prefix = m.group(1)
            df = df[df["LocationName"].astype(str).str.startswith(prefix)]
            return NLQResult(df, f"Partial bins in aisle {prefix}.")
        return NLQResult(df, "All partial bins.")
    if "full" in ql and "bin" in ql:
        return NLQResult(ensure_core(full_pallet_bins_df), "Full pallet bins.")
    if "rack" in ql and ("multiple" in ql or "more than one" in ql or ">1" in ql):
        viol_summary, details = _find_multi_pallet_all_racks(filtered_inventory_df)
        if details is None or details.empty:
            return NLQResult(pd.DataFrame(), "No rack locations with multiple pallets.")
        return NLQResult(ensure_core(details, include_issue=True), "Rack locations with multiple pallets (detail).")
    # --- DAMAGES / MISSING ---
    if "damage" in ql or "damaged" in ql:
        return NLQResult(ensure_core(damages_df), "Damaged pallets.")
    if "missing" in ql:
        return NLQResult(ensure_core(missing_df), "Missing pallets.")
    # --- Pallet / LOT / SKU / Location queries ---
    m_pid = re.search(r"(?:pallet|pallet id)\s+([A-Za-z0-9\-]+)", q or "", re.IGNORECASE)
    if m_pid:
        pid = normalize_pallet_id(m_pid.group(1))
        base = ensure_core(filtered_inventory_df)
        df = base[base["PalletId"].astype(str).str.strip().str.upper() == pid.strip().upper()]
        return NLQResult(df, f'Where is pallet "{pid}"?')
    m_lot = re.search(r"(?:lot|lot number)\s+(\d+)", q or "", re.IGNORECASE)
    if m_lot:
        lot = normalize_lot_number(m_lot.group(1))
        base = ensure_core(filtered_inventory_df)
        df = base[base["CustomerLotReference"].astype(str).str.contains(lot, case=False, na=False)]
        return NLQResult(df, f'Rows for LOT Number "{lot}".')
    m_sku = re.search(r"(?:sku)\s+([A-Za-z0-9\-]+)", q or "", re.IGNORECASE)
    if m_sku:
        sku = m_sku.group(1)
        base = ensure_core(filtered_inventory_df)
        df = base[base["WarehouseSku"].astype(str).str.contains(sku, case=False, na=False)]
        return NLQResult(df, f'Rows for SKU containing "{sku}".')
    m_loc = re.search(r"(?:location|bin)\s+(?:contains|like)\s+([A-Za-z0-9\-]+)", q or "", re.IGNORECASE)
    if m_loc:
        frag = m_loc.group(1)
        base = ensure_core(filtered_inventory_df)
        df = base[base["LocationName"].astype(str).str.contains(frag, case=False, na=False)]
        return NLQResult(df, f'Rows where Location contains "{frag}".')
    # Fallback
    base = ensure_core(filtered_inventory_df)
    guess = (q or "").strip()
    if guess in LOC_INDEX:
        return NLQResult(LOC_INDEX[guess], f"Rows for location {guess}.")
    frag = re.escape(guess)
    mask = (
        base["LocationName"].astype(str).str.contains(frag, case=False, na=False)
        | base["PalletId"].astype(str).str.contains(frag, case=False, na=False)
        | base["WarehouseSku"].astype(str).str.contains(frag, case=False, na=False)
        | base["CustomerLotReference"].astype(str).str.contains(normalize_lot_number(guess), case=False, na=False)
    )
    df = base[mask]
    return NLQResult(df, f'Fallback search across Location, PalletId, SKU, LOT for "{guess}".')

def page_ask_bin_helper():
    st.subheader("🧠 Ask Bin Helper (Beta)")
    st.caption("Try: 'show me bulk locations with 5 pallets or less', 'bulk with at least 1 empty slot', 'find pallet JTL00496', 'partial bins in aisle 114', 'duplicates for pallet JTL00496'.")
    ex1, ex2, ex3, ex4 = st.columns(4)
    if ex1.button("Bulk ≤ 5 pallets"): st.session_state["ask_nlq"] = "show me bulk locations with 5 pallets or less"
    if ex2.button("Bulk ≥ 1 empty slot"): st.session_state["ask_nlq"] = "bulk locations with at least 1 empty slot"
    if ex3.button("Find pallet JTL00496"): st.session_state["ask_nlq"] = "find pallet JTL00496"
    if ex4.button("Partial in aisle 114"): st.session_state["ask_nlq"] = "partial bins in aisle 114"
    q = st.text_input("Your request", value=st.session_state.get("ask_nlq", ""), placeholder='e.g., "show me bulk locations with 5 pallets or less"')
    if q.strip():
        res = parse_nl_query(q)
        st.markdown(f"**Understood:** {res.explanation}")
        if res.warning:
            st.warning(res.warning)
        if res.df is None or res.df.empty:
            st.info("No rows found for this query.")
        else:
            cols = set(res.df.columns.str.lower())
            maybe_inv = {"locationname","warehousesku","palletid","customerlotreference"}.issubset(cols)
            show_df = ensure_core(res.df) if maybe_inv else res.df
            render_lazy_df(show_df, key="ask_results", use_core=False)
            st.download_button("Download results (CSV)", show_df.to_csv(index=False).encode("utf-8"),
                               file_name="ask-bin-helper-results.csv", mime="text/csv")

# ===== KPI Card CSS & extras =====
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
/* Skeleton loader */
.skel-row{height:14px;background:linear-gradient(90deg,#eee,#f5f5f5,#eee);background-size:200% 100%;
animation:skel 1.2s ease-in-out infinite;margin:8px 0;border-radius:6px;}
@keyframes skel{0%{background-position:200% 0}100%{background-position:-200% 0}}
"""
    neon = f"""
div[data-testid="stMetric"] {{
  color: #e8f0ff;
  background: radial-gradient(120% 120% at 0% 0%, #0b1220 0%, #101a2e 55%, #0b1220 100%);
  border: 1px solid rgba(31,119,180, .35);
  box-shadow: 0 0 12px rgba(31,119,180, .35), inset 0 0 10px rgba(31,119,180, .15);
}}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{ color: rgba(200,220,255,.9); }}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ color: {BLUE}; text-shadow: 0 0 12px rgba(31,119,180,.5); }}
div[data-testid="stMetric"]:hover {{
  box-shadow: 0 0 18px rgba(31,119,180,.55), inset 0 0 12px rgba(31,119,180,.22);
}}
"""
    glass = f"""
div[data-testid="stMetric"] {{
  color: #0e1730;
  background: linear-gradient(160deg, rgba(255,255,255,.55) 0%, rgba(255,255,255,.25) 100%);
  border: 1px solid rgba(15,35,65,.15);
  box-shadow: 0 10px 30px rgba(0,0,0,.08);
  backdrop-filter: blur(10px);
}}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{ color: rgba(14,23,48,.8); }}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ color: {BLUE}; }}
div[data-testid="stMetric"]:hover {{ box-shadow: 0 14px 36px rgba(0,0,0,.12); }}
"""
    blueprint = f"""
div[data-testid="stMetric"] {{
  color: #d7e9ff;
  background:
  linear-gradient(#0b1f33 1px, transparent 1px) 0 0/100% 22px,
  linear-gradient(90deg, #0b1f33 1px, transparent 1px) 0 0/22px 100%,
  linear-gradient(160deg, #07233e 0%, #0a2949 60%, #061a2d 100%);
  border: 1px dashed rgba(120,170,220,.45);
  box-shadow: inset 0 0 0 1px rgba(31,119,180,.25), 0 10px 24px rgba(0,0,0,.22);
}}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{ color: #b7d1f3; }}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ color: {BLUE}; text-shadow: 0 0 8px rgba(31,119,180,.45); }}
div[data-testid="stMetric"]:hover {{
  box-shadow: inset 0 0 0 1px rgba(31,119,180,.45), 0 14px 28px rgba(0,0,0,.28);
}}
"""
    exception_hint = f"""
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(5) div[data-testid="stMetric"],
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(6) div[data-testid="stMetric"] {{
  border-color: rgba(214,39,40,.5) !important;
  box-shadow: 0 0 12px rgba(214,39,40,.45), inset 0 0 10px rgba(214,39,40,.18) !important;
}}
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(5) div[data-testid="stMetric"] [data-testid="stMetricValue"],
section.main div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(6) div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
  color: {RED} !important; text-shadow: 0 0 10px rgba(214,39,40,.45) !important;
}}
"""
    bundle = common + (neon if style == "Neon Glow" else glass if style == "Glassmorphism" else blueprint) + exception_hint
    st.markdown(f"<style>{bundle}</style>", unsafe_allow_html=True)

_inject_card_css(card_style)

# ===== Helper: Lazy-load table & skeleton =====
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

def show_skeleton(n_rows: int = 8):
    with st.container():
        for _ in range(n_rows):
            st.markdown('<div class="skel-row"></div>', unsafe_allow_html=True)

# ===== Robust single KPI helper =====
def _animate_metric(ph, label: str, value, delta_text=None, duration_ms: int = 600, steps: int = 20):
    try:
        v_end = int(value) if value is not None else 0
        d_text = None if delta_text in (None, "") else str(delta_text)
        if not st.session_state.get("animate_kpis", True) or v_end <= 0:
            try:
                ph.metric(label, v_end, delta=d_text)
            except Exception:
                st.metric(label, v_end, delta=d_text)
            return
        steps = max(8, min(40, int(steps)))
        sleep_s = max(0.01, float(duration_ms) / 1000.0 / steps)
        for i in range(1, steps + 1):
            v = int(round(v_end * i / steps))
            try:
                ph.metric(label, v)
            except Exception:
                st.metric(label, v)
            time.sleep(sleep_s)
        try:
            ph.metric(label, v_end, delta=d_text)
        except Exception:
            st.metric(label, v_end, delta=d_text)
    except Exception:
        try:
            ph.metric(label, value if value is not None else 0, delta=delta_text if delta_text else None)
        except Exception:
            st.metric(label, value if value is not None else 0, delta=delta_text if delta_text else None)

# ===== NAV =====
nav_options = [
    "Dashboard", "Empty Bins", "Full Pallet Bins", "Empty Partial Bins",
    "Partial Bins", "Damages", "Missing",
    "Discrepancies (All)",
    "Ask Bin Helper (Beta)",
    "Bulk Locations", "Empty Bulk Locations",
    "Fix Log (All)",
    "Trends", "Config", "Self-Test"
]
_default_nav = st.session_state.get("nav", "Dashboard")
if "pending_nav" in st.session_state:
    _default_nav = st.session_state.pop("pending_nav", _default_nav)
try:
    _default_index = nav_options.index(_default_nav) if _default_nav in nav_options else 0
except ValueError:
    _default_index = 0

# Quick Jump (scan/enter): pallet id or location
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

selected_nav = st.radio("🔍 Navigate:", nav_options, index=_default_index, horizontal=True, key="nav")
st.text_input(
    "Quick Jump (scan or type Pallet ID or Location and press Enter)",
    value="",
    key="quick_jump_text",
    placeholder="e.g., JTL00496 or A123 or 11400804",
    on_change=_handle_quick_jump
)
st.markdown("---")

# ===== Trends helpers (deltas for KPIs) =====
def _read_trends() -> pd.DataFrame:
    if not os.path.isfile(TRENDS_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(TRENDS_FILE)
        if not df.empty and "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()

def _current_kpis() -> dict:
    dam_qty = int(pd.to_numeric(damages_df["Qty"], errors="coerce").fillna(0).sum()) if ("Qty" in damages_df.columns and not damages_df.empty) else 0
    # inventory composition snapshot
    s_all = inventory_df["LocationName"].astype(str)
    is_rack = s_all.str.isnumeric()
    is_bulk = s_all.str[0].str.upper().isin(bulk_rules.keys())
    is_special = s_all.str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"])
    # bulk usage snapshot
    bulk_used  = int(bulk_locations_df["PalletCount"].sum()) if not bulk_locations_df.empty else 0
    bulk_empty = int(bulk_locations_df["EmptySlots"].sum()) if not bulk_locations_df.empty else 0
    return {
        "EmptyBins": len(empty_bins_view_df),
        "EmptyPartialBins": len(empty_partial_bins_df),
        "PartialBins": len(partial_bins_df),
        "FullPalletBins": len(full_pallet_bins_df),
        "Damages": dam_qty,
        "Missing": len(missing_df),
        # extras for trends
        "RackCount": int(is_rack.sum()),
        "BulkCount": int(is_bulk.sum()),
        "SpecialCount": int(is_special.sum()),
        "BulkUsed": bulk_used,
        "BulkEmpty": bulk_empty,
        "FileMD5": _file_md5(inventory_file)
    }

def _kpi_deltas(hist: pd.DataFrame, now: dict) -> Dict[str, dict]:
    out = {k: {"vs_last": None, "vs_yday": None} for k in now}
    if hist is None or hist.empty:
        return out
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
            if last is not None and k in last:
                out[k]["vs_last"] = int(now[k]) - int(last[k])
            if yday is not None and k in yday:
                out[k]["vs_yday"] = int(now[k]) - int(yday[k])
        except Exception:
            pass
    return out

def _delta_text(d):
    if d is None: return None
    arrow = "▲" if d > 0 else "▼" if d < 0 else "■"
    return f"{arrow}{abs(d)}"

def _delta_combo_text(vs_last, vs_yday):
    parts = []
    if vs_last is not None:
        parts.append(f"{_delta_text(vs_last)} vs last")
    if vs_yday is not None:
        parts.append(f"{_delta_text(vs_yday)} vs 24h")
    return " \\\n".join(parts) if parts else None

# ===== NEW: Trend snapshot management =====
TREND_HEADER = [
    "Timestamp",
    "EmptyBins", "EmptyPartialBins", "PartialBins", "FullPalletBins",
    "Damages", "Missing",
    "RackCount", "BulkCount", "SpecialCount",
    "BulkUsed", "BulkEmpty",
    "FileMD5"
]

def _ensure_trend_file():
    os.makedirs(os.path.dirname(TRENDS_FILE), exist_ok=True)
    if not os.path.isfile(TRENDS_FILE):
        with open(TRENDS_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(TREND_HEADER)

def _append_trend_row(kpis: dict):
    row = [
        datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        kpis["EmptyBins"], kpis["EmptyPartialBins"], kpis["PartialBins"], kpis["FullPalletBins"],
        kpis["Damages"], kpis["Missing"],
        kpis["RackCount"], kpis["BulkCount"], kpis["SpecialCount"],
        kpis["BulkUsed"], kpis["BulkEmpty"],
        kpis["FileMD5"]
    ]
    _ensure_trend_file()
    _safe_append_csv(TRENDS_FILE, TREND_HEADER, row)

def _last_snapshot_age_minutes() -> Optional[int]:
    df = _read_trends()
    if df.empty: return None
    try:
        last_ts = pd.to_datetime(df["Timestamp"].iloc[-1])
        return int((datetime.now() - last_ts).total_seconds() // 60)
    except Exception:
        return None

def _auto_snapshot_if_needed():
    """
    - Create trend_history.csv if missing.
    - If 'Auto-snapshot on startup' is ON and file is empty -> write an initial snapshot.
    - If last snapshot older than interval -> write a new snapshot.
    - Respect manual 'pending_trend_record'.
    """
    _ensure_trend_file()
    interval_min = int(st.session_state.get("trend_interval_min", 60))
    kpis_now = _current_kpis()

    # Manual trigger from sidebar
    if st.session_state.get("pending_trend_record", False):
        _append_trend_row(kpis_now)
        st.session_state["pending_trend_record"] = False

    df = _read_trends()
    if df.empty and st.session_state.get("auto_snapshot_on_start", True):
        _append_trend_row(kpis_now)
        return
    age = _last_snapshot_age_minutes()
    if age is None or age >= interval_min:
        _append_trend_row(kpis_now)

# 🔔 Run the auto snapshot guard once per run (keeps Trends persistent)
_auto_snapshot_if_needed()

# ===== Dashboard =====
if selected_nav == "Dashboard":
    st.subheader("📊 Bin Helper Dashboard")

    # ---- KPI row ----
    kpi_vals = {
        "Empty Bins": len(empty_bins_view_df),
        "Empty Partial Bins": len(empty_partial_bins_df),
        "Partial Bins": len(partial_bins_df),
        "Full Pallet Bins": len(full_pallet_bins_df),
        "Damages": int(pd.to_numeric(damages_df["Qty"], errors="coerce").fillna(0).sum()) if ("Qty" in damages_df.columns and not damages_df.empty) else 0,
        "Missing": len(missing_df),
    }
    hist = _read_trends()
    now = _current_kpis()
    deltas = _kpi_deltas(hist, now)
    def _dx(key_name):
        m = {
            "Empty Bins": "EmptyBins",
            "Empty Partial Bins": "EmptyPartialBins",
            "Partial Bins": "PartialBins",
            "Full Pallet Bins": "FullPalletBins",
            "Damages": "Damages",
            "Missing": "Missing",
        }
        k = m[key_name]
        return _delta_combo_text(deltas[k]["vs_last"], deltas[k]["vs_yday"])

    LBL_EMPTY = "📦 Empty Bins"
    LBL_EMPTY_PART = "🪧 Empty Partial Bins"
    LBL_PARTIAL = "📍 Partial Bins"
    LBL_FULL = "🧱 Full Pallet Bins"
    LBL_DAMAGE = "🛑 Damages" + (" 🔴" if kpi_vals["Damages"] > 0 else "")
    LBL_MISSING = "🚫 Missing" + (" 🔴" if kpi_vals["Missing"] > 0 else "")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    k1 = col1.empty(); k2 = col2.empty(); k3 = col3.empty(); k4 = col4.empty(); k5 = col5.empty(); k6 = col6.empty()
    _animate_metric(k1, LBL_EMPTY, kpi_vals["Empty Bins"], delta_text=_dx("Empty Bins"))
    _animate_metric(k2, LBL_EMPTY_PART, kpi_vals["Empty Partial Bins"], delta_text=_dx("Empty Partial Bins"))
    _animate_metric(k3, LBL_PARTIAL, kpi_vals["Partial Bins"], delta_text=_dx("Partial Bins"))
    _animate_metric(k4, LBL_FULL, kpi_vals["Full Pallet Bins"], delta_text=_dx("Full Pallet Bins"))
    _animate_metric(k5, LBL_DAMAGE, kpi_vals["Damages"], delta_text=_dx("Damages"))
    _animate_metric(k6, LBL_MISSING, kpi_vals["Missing"], delta_text=_dx("Missing"))

    if col1.button("View", key="btn_empty"): st.session_state["pending_nav"] = "Empty Bins"; _rerun()
    if col2.button("View", key="btn_empty_partial"): st.session_state["pending_nav"] = "Empty Partial Bins"; _rerun()
    if col3.button("View", key="btn_partial"): st.session_state["pending_nav"] = "Partial Bins"; _rerun()
    if col4.button("View", key="btn_full"): st.session_state["pending_nav"] = "Full Pallet Bins"; _rerun()
    if col5.button("View", key="btn_damage"): st.session_state["pending_nav"] = "Damages"; _rerun()
    if col6.button("View", key="btn_missing"): st.session_state["pending_nav"] = "Missing"; _rerun()

    # ---- Racks Empty vs Full / Bulk Used vs Empty ----
    c0a, c0b = st.columns([1, 1])
    with c0a:
        st.markdown("#### Racks: Empty vs Full")
        rack_empty = int(len(empty_bins_view_df))
        rack_full = int(len(full_pallet_bins_df))
        df_rack_ef = pd.DataFrame({"Status": ["Empty", "Full"], "Count": [rack_empty, rack_full]})
        fig_rack_ef = px.pie(
            df_rack_ef, values="Count", names="Status",
            color="Status", color_discrete_map={"Empty": RED, "Full": BLUE}, hole=0.45
        )
        fig_rack_ef.update_layout(showlegend=True, height=320)
        st.plotly_chart(fig_rack_ef, use_container_width=True)

    with c0b:
        st.markdown("#### Bulk Floor: Used vs Empty Slots")
        if bulk_locations_df.empty:
            st.info("No bulk locations in current data.")
        else:
            bulk_used = int(bulk_locations_df["PalletCount"].sum())
            bulk_empty = int(bulk_locations_df["EmptySlots"].sum())
            df_bulk_ue = pd.DataFrame({"Status": ["Used", "Empty"], "Count": [bulk_used, bulk_empty]})
            fig_bulk_ue = px.pie(
                df_bulk_ue, values="Count", names="Status",
                color="Status", color_discrete_map={"Empty": RED, "Used": BLUE}, hole=0.45
            )
            fig_bulk_ue.update_layout(showlegend=True, height=320)
            st.plotly_chart(fig_bulk_ue, use_container_width=True)

    # ---- Inventory Composition / Multi‑Pallet Hotspots ----
    cA, cB = st.columns([1, 1])
    with cA:
        st.markdown("#### Inventory Composition")
        s_all = inventory_df["LocationName"].astype(str)
        is_rack = s_all.str.isnumeric()
        is_bulk = s_all.str[0].str.upper().isin(bulk_rules.keys())
        is_special = s_all.str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"])
        comp = pd.DataFrame({
            "Category": ["Rack", "Bulk", "Special"],
            "Count": [int(is_rack.sum()), int(is_bulk.sum()), int(is_special.sum())]
        })
        fig_comp = px.pie(
            comp, values="Count", names="Category",
            color="Category", color_discrete_map={"Rack": BLUE, "Bulk": "#2ca02c", "Special": RED}, hole=0.35
        )
        fig_comp.update_layout(showlegend=True, height=340)
        st.plotly_chart(fig_comp, use_container_width=True)

    with cB:
        st.markdown("#### Multi‑Pallet Hotspots (Top 10)")
        viol_summary, _ = _find_multi_pallet_all_racks(filtered_inventory_df)
        if viol_summary.empty:
            st.info("No rack locations with >1 pallet.")
        else:
            top10 = viol_summary.sort_values("DistinctPallets", ascending=False).head(10)
            fig_hot = px.bar(top10, x="LocationName", y="DistinctPallets", color_discrete_sequence=[RED])
            fig_hot.update_layout(xaxis_title="Location", yaxis_title="# Distinct Pallets", height=340)
            st.plotly_chart(fig_hot, use_container_width=True)

    # ---- Partial Bins by Aisle ----
    st.markdown("#### Partial Bins by Aisle (Top 12)")
    if partial_bins_df.empty:
        st.info("No partial bins in current data.")
    else:
        loc_series = partial_bins_df["LocationName"].astype(str)
        aisles = loc_series.where(loc_series.str.len() >= 3, None).str[:3]
        aisle_counts = aisles.value_counts().reset_index()
        aisle_counts.columns = ["Aisle", "PartialBinCount"]
        top_aisles = aisle_counts.head(12).sort_values("PartialBinCount", ascending=True)
        fig_aisle = px.bar(
            top_aisles, x="PartialBinCount", y="Aisle",
            orientation="h", color_discrete_sequence=[BLUE]
        )
        fig_aisle.update_layout(xaxis_title="Count", yaxis_title="Aisle", height=360)
        st.plotly_chart(fig_aisle, use_container_width=True)

    # ---- Bulk by Zone; Top SKUs in Partial ----
    c3a, c3b = st.columns([1, 1])
    with c3a:
        st.markdown("#### Bulk Occupancy by Zone (Used vs Empty)")
        if bulk_locations_df.empty:
            st.info("No bulk occupancy to display.")
        else:
            z = bulk_locations_df.groupby("Zone").agg(
                Used=("PalletCount", "sum"),
                Empty=("EmptySlots", "sum")
            ).reset_index()
            z = z.sort_values("Zone")
            z_melt = z.melt(id_vars="Zone", value_vars=["Used", "Empty"],
                            var_name="Status", value_name="Count")
            fig_z = px.bar(
                z_melt, x="Zone", y="Count", color="Status",
                color_discrete_map={"Empty": RED, "Used": BLUE},
                barmode="stack"
            )
            fig_z.update_layout(height=360)
            st.plotly_chart(fig_z, use_container_width=True)

    with c3b:
        st.markdown("#### Top SKUs in Partial Bins (Top 10)")
        if partial_bins_df.empty:
            st.info("No data.")
        else:
            sku_counts = partial_bins_df["WarehouseSku"].astype(str).value_counts().reset_index().head(10)
            sku_counts.columns = ["SKU", "PartialPallets"]
            fig_sku = px.bar(
                sku_counts.sort_values("PartialPallets"),
                x="PartialPallets", y="SKU",
                orientation="h", color_discrete_sequence=[BLUE]
            )
            fig_sku.update_layout(height=360)
            st.plotly_chart(fig_sku, use_container_width=True)

    # ---- Search Center ----
    st.markdown("### 🔎 Search Center")
    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        q_loc = st.text_input("Location contains", value=st.session_state.filters.get("LocationName", ""))
    with sc2:
        q_pid = st.text_input("Pallet ID contains", value=st.session_state.filters.get("PalletId", ""))
    with sc3:
        q_sku = st.text_input("SKU contains", value=st.session_state.filters.get("WarehouseSku", ""))
    with sc4:
        q_lot = st.text_input("LOT Number contains (numbers only)", value=st.session_state.filters.get("CustomerLotReference", ""))

    if any([q_loc, q_pid, q_sku, q_lot]):
        base = ensure_core(filtered_inventory_df)
        df_show = base.copy()
        if q_loc:
            df_show = df_show[df_show["LocationName"].astype(str).str.contains(q_loc, case=False, na=False)]
        if q_pid:
            df_show = df_show[df_show["PalletId"].astype(str).str.contains(q_pid, case=False, na=False)]
        if q_sku:
            df_show = df_show[df_show["WarehouseSku"].astype(str).str.contains(q_sku, case=False, na=False)]
        if q_lot:
            q_lot_norm = normalize_lot_number(q_lot)
            df_show = df_show[df_show["CustomerLotReference"].astype(str).str.contains(q_lot_norm, case=False, na=False)]
        st.caption("Results")
        render_lazy_df(maybe_limit(df_show), key="search_center", use_core=False)

    # ---- Recent Fix Actions ----
    with st.expander("🕘 Recent Actions (last 20)"):
        log_df = read_action_log()
        if log_df.empty:
            st.info("No actions logged yet.")
        else:
            recent = log_df.sort_values("Timestamp", ascending=False).head(20)
            render_lazy_df(recent, key="recent_actions", page_size=400)

# === end Dashboard ===

elif selected_nav == "Empty Bins":
    st.subheader("Empty Bins")
    display = ensure_core(empty_bins_view_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    render_lazy_df(display, key="empty_bins")

elif selected_nav == "Empty Partial Bins":
    st.subheader("Empty Partial Bins")
    display = ensure_core(empty_partial_bins_df.assign(WarehouseSku="", PalletId="", CustomerLotReference="", Qty=""))
    render_lazy_df(display, key="empty_partial_bins")

elif selected_nav == "Partial Bins":
    st.subheader("Partial Bins")
    render_lazy_df(ensure_core(partial_bins_df), key="partial_bins")

elif selected_nav == "Full Pallet Bins":
    st.subheader("Full Pallet Bins")
    render_lazy_df(ensure_core(full_pallet_bins_df), key="full_bins")

elif selected_nav == "Damages":
    st.subheader("Damaged Pallets")
    render_lazy_df(ensure_core(damages_df), key="damages")

elif selected_nav == "Missing":
    st.subheader("Missing Pallets")
    render_lazy_df(ensure_core(missing_df), key="missing")

elif selected_nav == "Discrepancies (All)":
    st.subheader("🛠️ Discrepancies — All")
    with st.expander("Fix Log (All)"):
        download_fix_log_button(where_key="all_fixlog")

    # Build data fresh (already computed above, but make sure we have frames)
    rack_all_df = discrepancy_df.copy()  # Issues found in racks/partials (from analyze_discrepancies)
    bulk_issues_df = bulk_df.copy()      # Bulk locations over capacity (from analyze_bulk_locations_grouped)
    dups_summary = dups_summary_df.copy()
    dups_detail = dups_detail_df.copy()

    t1, t2, t3 = st.tabs(["Rack", "Bulk", "Duplicate"])

    # ---- Rack tab ----
    with t1:
        st.markdown("#### Rack / Partial Issues")
        if rack_all_df is None or rack_all_df.empty:
            st.success("No rack/partial discrepancies found.")
        else:
            # Keep columns tight and readable
            show_rack = ensure_core(rack_all_df, include_issue=True)
            render_lazy_df(show_rack, key="disc_rack", use_core=False, include_issue=True)

            # Quick counts by issue type
            with st.expander("Issue Breakdown"):
                if "Issue" in rack_all_df.columns:
                    brk = rack_all_df["Issue"].value_counts(dropna=False).reset_index()
                    brk.columns = ["Issue", "Count"]
                    st.dataframe(brk, use_container_width=True)
                else:
                    st.info("No Issue column available.")

    # ---- Bulk tab ----
    with t2:
        st.markdown("#### Bulk Over-Capacity Issues")
        if bulk_issues_df is None or bulk_issues_df.empty:
            st.success("No bulk over-capacity found.")
        else:
            # Add a helper column showing how many over capacity (if possible)
            tmp = bulk_issues_df.copy()
            # Try to compute actual overage per location (optional)
            try:
                tmp["OverBy"] = (
                    tmp.groupby("LocationName")["LocationName"].transform("count")
                    - tmp["LocationName"].map(
                        bulk_locations_df.set_index("LocationName")["MaxAllowed"]
                        if not bulk_locations_df.empty and "MaxAllowed" in bulk_locations_df.columns
                        else {}
                    )
                )
            except Exception:
                pass

            show_bulk = ensure_core(tmp, include_issue=True)
            render_lazy_df(show_bulk, key="disc_bulk", use_core=False, include_issue=True)

            with st.expander("Summary by Location"):
                try:
                    loc_counts = tmp.groupby("LocationName").size().reset_index(name="PalletsListed")
                    if not bulk_locations_df.empty and "MaxAllowed" in bulk_locations_df.columns:
                        loc_counts = loc_counts.merge(
                            bulk_locations_df[["LocationName", "MaxAllowed"]],
                            on="LocationName", how="left"
                        )
                    st.dataframe(loc_counts.sort_values("PalletsListed", ascending=False),
                                 use_container_width=True)
                except Exception:
                    st.info("Summary not available.")

    # ---- Duplicate tab ----
    with t3:
        st.markdown("#### Duplicate Pallets (same PalletId in multiple locations)")
        csum, cdet = st.columns([1, 2])

        with csum:
            st.markdown("**Summary (by PalletId)**")
            if dups_summary is None or dups_summary.empty:
                st.success("No duplicate pallets detected.")
            else:
                st.dataframe(dups_summary, use_container_width=True)

        with cdet:
            st.markdown("**Details**")
            if dups_detail is None or dups_detail.empty:
                st.info("Select a PalletId from summary and filter below, or review full details.")
            show_dup_details = ensure_core(dups_detail)
            render_lazy_df(show_dup_details, key="disc_dups_detail", use_core=False)

            # Simple filter for details
            with st.expander("Filter details"):
                pid_filter = st.text_input("PalletId contains", "")
                if pid_filter.strip():
                    filt = show_dup_details[
                        show_dup_details["PalletId"].astype(str).str.contains(pid_filter, case=False, na=False)
                    ]
                    if filt.empty:
                        st.info("No matching rows.")
                    else:
                        st.dataframe(filt, use_container_width=True)

elif selected_nav == "Ask Bin Helper (Beta)":
    page_ask_bin_helper()

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.caption("Click a location or use Quick Jump, then pick a pallet from the dropdown.")
    st.markdown(
        """
<style>
.ag-theme-streamlit .ag-row.overCapRow { background-color:#ffe3e6 !important; }
.ag-theme-streamlit .ag-row.overCapRow .ag-cell { color:#7f1d1d; font-weight:600; }
</style>
""",
        unsafe_allow_html=True
    )
    ui_mode_default_index = 1 if _AGGRID_AVAILABLE else 0
    ui_mode = st.radio(
        "View mode",
        ["Expanders", "Grid (select a location)", "Flat Pallet List (Bulk)"],
        index=ui_mode_default_index,
        horizontal=True,
        key="bulk_loc_mode"
    )
    search = st.text_input("Search location (optional)", value=st.session_state.get("bulk_loc_search2", ""), key="bulk_loc_search2")
    parent_df = bulk_locations_df.copy()
    if not parent_df.empty and search.strip():
        parent_df = parent_df[parent_df["LocationName"].astype(str).str.contains(search.strip(), case=False, na=False)]
    if not parent_df.empty:
        over_mask = parent_df["PalletCount"] > parent_df["MaxAllowed"]
        if over_mask.any():
            st.warning(f"{int(over_mask.sum())} location(s) exceed max allowed pallets. Highlighted in red.")
    jump = st.session_state.get("jump_intent", {}) or {}

    def _render_location_detail(loc: str, preselect_pallet: Optional[str] = None, key_prefix: str = ""):
        loc = str(loc)
        rows = LOC_INDEX.get(loc, pd.DataFrame())
        if rows.empty:
            st.warning(f"No pallets found for location {loc}."); return
        labels, label_to_key, full_df = PALLET_LABELS_BY_LOC.get(loc, ([], {}, rows))
        choices = ["(All)"] + labels
        default_index = 0
        if preselect_pallet:
            for i, lab in enumerate(labels, start=1):
                if preselect_pallet.upper() in lab.upper():
                    default_index = i; break
        selected_label = st.selectbox(f"Pallets at {loc}", choices, index=default_index, key=f"{key_prefix}pallet_dd_{loc}")
        if selected_label == "(All)":
            show_df = full_df
        else:
            chosen_key = label_to_key.get(selected_label, None)
            show_df = full_df if chosen_key is None else full_df[full_df["_PID_KEY"] == chosen_key]
        render_lazy_df(ensure_core(show_df), key=f"bulk_loc_rows_{loc}")

    if ui_mode == "Grid (select a location)" and _AGGRID_AVAILABLE and not parent_df.empty:
        skel_ph = st.empty()
        with skel_ph.container():
            show_skeleton(8)
        show_cols = ["LocationName", "Zone", "PalletCount", "MaxAllowed", "EmptySlots"]
        grid_df = parent_df[show_cols].copy()
        gb = GridOptionsBuilder.from_dataframe(grid_df)
        gb.configure_default_column(resizable=True, filter=True, sortable=True, floatingFilter=True)
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
        gb.configure_selection("single", use_checkbox=True)
        grid_options = gb.build()
        grid_resp = AgGrid(grid_df, gridOptions=grid_options, update_mode=GridUpdateMode.SELECTION_CHANGED,
                           allow_unsafe_jscode=True, fit_columns_on_grid_load=True, height=540, theme="streamlit")
        skel_ph.empty()
        sel_rows = pd.DataFrame(grid_resp.get("selected_rows", []))
        if not sel_rows.empty:
            sel_loc = str(sel_rows.iloc[0]["LocationName"])
            _render_location_detail(sel_loc, key_prefix="grid_")
        if jump.get("type") in ("pallet", "location") and jump.get("location"):
            st.markdown("#### Jump Result")
            _render_location_detail(jump["location"], preselect_pallet=jump.get("pallet_id"), key_prefix="jump_")

    elif ui_mode == "Flat Pallet List (Bulk)":
        st.markdown("#### Flat Pallet List (Bulk)")
        base = ensure_core(filtered_inventory_df)
        is_bulk_row = base["LocationName"].astype(str).str[0].str.upper().isin(bulk_rules.keys())
        bulk_flat = base[is_bulk_row].copy()
        only_low = st.toggle("Only show Qty ≤ 5", value=True, key="bulk_flat_lowqty")
        if only_low:
            bulk_flat = bulk_flat[pd.to_numeric(bulk_flat["Qty"], errors="coerce").fillna(0) <= 5]
        colA, colB, colC, colD = st.columns(4)
        with colA: f_loc = st.text_input("Filter: Location", "")
        with colB: f_pid = st.text_input("Filter: Pallet ID", "")
        with colC: f_sku = st.text_input("Filter: SKU", "")
        with colD: f_lot = st.text_input("Filter: LOT", "")
        if f_loc: bulk_flat = bulk_flat[bulk_flat["LocationName"].astype(str).str.contains(f_loc, case=False, na=False)]
        if f_pid: bulk_flat = bulk_flat[bulk_flat["PalletId"].astype(str).str.contains(f_pid, case=False, na=False)]
        if f_sku: bulk_flat = bulk_flat[bulk_flat["WarehouseSku"].astype(str).str.contains(f_sku, case=False, na=False)]
        if f_lot:
            lot_norm = normalize_lot_number(f_lot)
            bulk_flat = bulk_flat[bulk_flat["CustomerLotReference"].astype(str).str.contains(lot_norm, case=False, na=False)]
        bulk_flat["Qty_num"] = pd.to_numeric(bulk_flat["Qty"], errors="coerce").fillna(0)
        bulk_flat = bulk_flat.sort_values("Qty_num", ascending=True).drop(columns=["Qty_num"])
        render_lazy_df(bulk_flat, key="bulk_flat_all", use_core=False, page_size=500)
        st.download_button("Download (Bulk Flat Pallets CSV)", bulk_flat.to_csv(index=False).encode("utf-8"),
                           "bulk_pallets_flat.csv", "text/csv")
        if jump.get("type") in ("pallet", "location") and jump.get("location"):
            st.markdown("#### Jump Result")
            _render_location_detail(jump["location"], preselect_pallet=jump.get("pallet_id"), key_prefix="jump2_")

    else:
        if parent_df.empty:
            st.info("No bulk locations found.")
        else:
            df_show = parent_df.sort_values(["Zone", "LocationName"])
            for _, r in df_show.iterrows():
                loc = str(r["LocationName"])
                over_by = int(r["PalletCount"] - r["MaxAllowed"])
                over_badge = f' <span style="color:#b00020;font-weight:700;">✗ OVER {over_by}</span>' if over_by > 0 else ""
                header = f"{loc} — {int(r['PalletCount'])}/{int(r['MaxAllowed'])} (Empty {int(r['EmptySlots'])}){over_badge}"
                with st.expander(header, expanded=False):
                    _render_location_detail(loc, key_prefix="exp_")
        if jump.get("type") in ("pallet", "location") and jump.get("location"):
            st.markdown("#### Jump Result")
            _render_location_detail(jump["location"], preselect_pallet=jump.get("pallet_id"), key_prefix="jump2_")

    with st.expander("Fix Log"):
        download_fix_log_button(where_key="bulk_locations_fixlog")

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations")
    render_lazy_df(empty_bulk_locations_df, key="empty_bulk_locs")

elif selected_nav == "Fix Log (All)":
    st.subheader("🧾 Fix Log (All Types)")
    log_df = read_action_log()
    if log_df.empty:
        st.info("No fix actions logged yet.")
    else:
        df = log_df.copy()
        for c in ["Timestamp","Action","DiscrepancyType","LocationName","PalletId","WarehouseSku","CustomerLotReference","Qty","Issue","Note","SelectedLOT","BatchId"]:
            if c not in df.columns:
                df[c] = ""
        try:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        except Exception:
            pass
        df = df.sort_values("Timestamp", ascending=False)
        c1, c2, c3 = st.columns([2,2,3])
        with c1:
            types = ["(All)"] + sorted([x for x in df["DiscrepancyType"].astype(str).unique() if x])
            sel_type = st.selectbox("Discrepancy Type", types, index=0)
        with c2:
            min_d = pd.to_datetime(df["Timestamp"]).min() if pd.to_datetime(df["Timestamp"], errors="coerce").notna().any() else None
            max_d = pd.to_datetime(df["Timestamp"]).max() if pd.to_datetime(df["Timestamp"], errors="coerce").notna().any() else None
            date_range = st.date_input("Date range", value=(min_d.date() if min_d is not None else None,
                                                            max_d.date() if max_d is not None else None))
        with c3:
            q = st.text_input("Search (Location, Pallet, SKU, LOT, Issue, Note, Batch)", "")
        filt = df
        if sel_type != "(All)":
            filt = filt[filt["DiscrepancyType"] == sel_type]
        if pd.to_datetime(filt["Timestamp"], errors="coerce").notna().any():
            try:
                if isinstance(date_range, tuple) and len(date_range) == 2 and all(date_range):
                    start_dt = pd.to_datetime(str(date_range[0]))
                    end_dt   = pd.to_datetime(str(date_range[1])) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
                    ts = pd.to_datetime(filt["Timestamp"], errors="coerce")
                    filt = filt[(ts >= start_dt) & (ts <= end_dt)]
            except Exception:
                pass
        if q.strip():
            qi = re.escape(q.strip())
            mask = (
                filt["LocationName"].astype(str).str.contains(qi, case=False, na=False) |
                filt["PalletId"].astype(str).str.contains(qi, case=False, na=False) |
                filt["WarehouseSku"].astype(str).str.contains(qi, case=False, na=False) |
                filt["CustomerLotReference"].astype(str).str.contains(qi, case=False, na=False) |
                filt["Issue"].astype(str).str.contains(qi, case=False, na=False) |
                filt["Note"].astype(str).str.contains(qi, case=False, na=False) |
                filt["BatchId"].astype(str).str.contains(qi, case=False, na=False)
            )
            filt = filt[mask]
        cols_order = ["Timestamp","Action","DiscrepancyType","BatchId",
                      "LocationName","PalletId","WarehouseSku","CustomerLotReference","Qty",
                      "Issue","SelectedLOT","Reason","Note"]
        cols_final = [c for c in cols_order if c in filt.columns] + [c for c in filt.columns if c not in cols_order]
        st.caption(f"Rows: **{len(filt)}** (newest first)")
        st.dataframe(filt[cols_final], use_container_width=True)
        cdl1, cdl2 = st.columns([1,1])
        with cdl1:
            st.download_button("Download (filtered CSV)", filt[cols_final].to_csv(index=False).encode("utf-8"),
                               "fixlog_filtered.csv", "text/csv", key="dl_fixlog_filtered_all")
        with cdl2:
            st.download_button("Download (raw full CSV)", df.to_csv(index=False).encode("utf-8"),
                               "resolved_discrepancies.csv", "text/csv", key="dl_fixlog_full_all")

elif selected_nav == "Trends":
    st.subheader("📈 Trends Over Time (Persistent)")
    hist = _read_trends()
    if hist.empty:
        st.info("Building initial trend history… try refreshing once. The app will persist snapshots automatically.")
    else:
        st.caption(f"Snapshots: **{len(hist)}** • File: {os.path.basename(TRENDS_FILE)}")
        # Normalize types
        for c in ["EmptyBins","EmptyPartialBins","PartialBins","FullPalletBins","Damages","Missing",
                  "RackCount","BulkCount","SpecialCount","BulkUsed","BulkEmpty"]:
            if c in hist.columns:
                hist[c] = pd.to_numeric(hist[c], errors="coerce").fillna(0)

        # ---- 1) KPI time series
        st.markdown("#### KPI Time Series")
        kpi_long = hist.melt(id_vars=["Timestamp","FileMD5"],
                             value_vars=["EmptyBins","EmptyPartialBins","PartialBins","FullPalletBins","Damages","Missing"],
                             var_name="Metric", value_name="Value")
        fig_kpi = px.line(kpi_long, x="Timestamp", y="Value", color="Metric",
                          color_discrete_sequence=[BLUE, RED, "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"])
        fig_kpi.update_layout(height=360, legend_title_text="Metric")
        st.plotly_chart(fig_kpi, use_container_width=True)

        # ---- 2) Bulk capacity over time (Used vs Empty)
        st.markdown("#### Bulk Capacity Over Time — Used vs Empty")
        bulk_melt = hist.melt(id_vars=["Timestamp"], value_vars=["BulkUsed","BulkEmpty"],
                              var_name="Status", value_name="Count")
        fig_bulk = px.bar(bulk_melt, x="Timestamp", y="Count", color="Status",
                          color_discrete_map={"BulkUsed": BLUE, "BulkEmpty": RED}, barmode="stack")
        fig_bulk.update_layout(height=340)
        st.plotly_chart(fig_bulk, use_container_width=True)

        # ---- 3) Composition over time (Rack / Bulk / Special)
        st.markdown("#### Inventory Composition Over Time")
        comp_melt = hist.melt(id_vars=["Timestamp"], value_vars=["RackCount","BulkCount","SpecialCount"],
                              var_name="Category", value_name="Count")
        fig_comp = px.area(comp_melt, x="Timestamp", y="Count", color="Category",
                           color_discrete_map={"RackCount": BLUE, "BulkCount": GREEN, "SpecialCount": RED},
                           groupnorm=None)
        fig_comp.update_layout(height=340)
        st.plotly_chart(fig_comp, use_container_width=True)

        # ---- 4) Fix activity analytics (from resolved_discrepancies.csv)
        st.markdown("#### Fix Activity (from Fix Log)")
        log_df = read_action_log()
        if log_df.empty:
            st.info("No fix actions yet — this section will populate as you log resolves.")
        else:
            try:
                log_df["Timestamp"] = pd.to_datetime(log_df["Timestamp"], errors="coerce")
            except Exception:
                pass
            # Resolved by day & Reason
            log_df["Date"] = log_df["Timestamp"].dt.date
            reason_col = "Reason" if "Reason" in log_df.columns else None
            if reason_col is None:
                # if earlier logs used "[Reason: ...] " prefix in Note, try to extract
                log_df["ReasonEx"] = log_df["Note"].astype(str).str.extract(r"\[Reason:\s*(.*?)\]").fillna("(Unknown)")
                reason_col = "ReasonEx"
            resolved = log_df[log_df["Action"].astype(str).str.upper()=="RESOLVE"].copy()
            if not resolved.empty:
                grp = resolved.groupby(["Date", reason_col]).size().reset_index(name="Count")
                fig_res = px.bar(grp, x="Date", y="Count", color=reason_col, barmode="stack",
                                 title="Resolved actions per day by Reason")
                fig_res.update_layout(height=340, legend_title_text="Reason")
                st.plotly_chart(fig_res, use_container_width=True)
                # Cumulative fixes
                by_day = resolved.groupby("Date").size().reset_index(name="DailyCount").sort_values("Date")
                by_day["CumulativeFixes"] = by_day["DailyCount"].cumsum()
                fig_cum = px.line(by_day, x="Date", y="CumulativeFixes", markers=True, title="Cumulative Fixes Over Time")
                fig_cum.update_layout(height=320)
                st.plotly_chart(fig_cum, use_container_width=True)
            else:
                st.info("No RESOLVE actions in the log yet.")

        with st.expander("Show trend table"):
            render_lazy_df(hist, key="trend_table", page_size=400)
        st.download_button("Download trend_history.csv", hist.to_csv(index=False).encode("utf-8"),
                           "trend_history.csv", "text/csv")

elif selected_nav == "Config":
    st.subheader("⚙️ Config — Bulk Capacity Rules (A..I)")
    st.caption("Edit and **Save** to apply. This writes to `config.json` in your logs folder.")
    cur = _config.get("bulk_rules", DEFAULT_BULK_RULES).copy()
    zones = list(DEFAULT_BULK_RULES.keys())
    cols = st.columns(len(zones))
    new_rules = {}
    for i, z in enumerate(zones):
        with cols[i]:
            new_rules[z] = st.number_input(
                f"{z}", min_value=0, max_value=50,
                value=int(cur.get(z, DEFAULT_BULK_RULES[z])),
                step=1, key=f"cfg_{z}"
            )
    save_col, apply_col = st.columns([1, 3])
    with save_col:
        if st.button("💾 Save", type="primary", use_container_width=True, key="cfg_save"):
            _config["bulk_rules"] = {k: int(v) for k, v in new_rules.items()}
            save_config(_config)
            bulk_rules.update(_config["bulk_rules"])
            st.success("Saved `config.json`. Click **Apply** to rebuild zone views.")
    with apply_col:
        if st.button("⚙️ Apply (rebuild zone capacity views)", use_container_width=True, key="cfg_apply"):
            bulk_rules = _config["bulk_rules"].copy()
            bulk_locations_df, empty_bulk_locations_df = build_bulk_views()
            st.success("Bulk zone capacity views rebuilt.")
            _rerun()
    st.markdown("— — —")
    st.caption(f"Config file: `{CONFIG_FILE}`")
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            st.code(f.read(), language="json")
    else:
        st.info("No config file yet. Save once to create `config.json` in your logs folder.")

elif selected_nav == "Self-Test":
    st.subheader("🧪 Self‑Test / Diagnostics")
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown("**Paths & Folders**")
        st.write("- Logs folder:", LOG_DIR)
        st.write("- Data folder:", DATA_DIR)
        st.write("- Config file:", CONFIG_FILE)
        st.write("- Resolved actions log:", resolved_file)
        st.write("- Trends history:", TRENDS_FILE)
        if LOG_FALLBACK_USED:
            st.warning("Using fallback log folder (preferred path not writable).")
        else:
            st.success("Writing to preferred log folder or env override.")
        st.markdown("**Environment override**")
        st.code(os.environ.get("BIN_HELPER_LOG_DIR", "(not set)"))
    with c2:
        st.markdown("**In‑memory indices**")
        st.write("LOC_INDEX locations:", len(LOC_INDEX))
        if PALLET_LABELS_BY_LOC:
            any_loc = next(iter(PALLET_LABELS_BY_LOC))
            labels, _, _ = PALLET_LABELS_BY_LOC[any_loc]
            st.write(f"Sample location: {any_loc} (pallet choices: {len(labels)})")
        st.markdown("**Inventory summary**")
        st.write("- Rows:", len(inventory_df))
        st.write("- Unique locations:", len(occupied_locations))
        st.write("- Master locations:", len(master_locations))
        st.markdown("**Duplicate Pallets**")
        if dups_summary_df.empty:
            st.success("No duplicate pallets detected.")
        else:
            st.warning(f"Duplicate pallet IDs found: {len(dups_summary_df)}")
        st.markdown("**Pallet ID Audit (alphanumeric)**")
        try:
            pid_series = inventory_df["PalletId"].astype(str)
            has_letters = pid_series.str.contains(r"[A-Za-z]", na=False)
            count_alpha = int(has_letters.sum())
            st.write(f"- Pallet IDs with letters: **{count_alpha}**")
            if count_alpha > 0:
                sample_alpha = inventory_df[has_letters][["LocationName", "PalletId", "WarehouseSku", "CustomerLotReference"]].head(25)
                render_lazy_df(ensure_core(sample_alpha), key="pallet_alpha_sample")
        except Exception:
            st.info("Pallet ID audit skipped (no PalletId column or parsing error).")
        st.markdown("— — —")
        st.caption("Tip: If deploying on Streamlit Cloud, set a secret `BIN_HELPER_LOG_DIR` to `/mount/src/bin-helper/logs` to keep logs persistent.")