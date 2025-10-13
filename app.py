# -*- coding: utf-8 -*-
import os
import csv
import re
import time  # KPI animations
import json  # (19) Config file
import hashlib  # for file hash (trend de-dup)
import tempfile  # SAFEGUARD: fallback dirs
from datetime import datetime, timedelta
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
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "resolved_items" not in st.session_state:
    st.session_state.resolved_items = set()
if "inventory_path" not in st.session_state:
    st.session_state.inventory_path = None  # set when user uploads
# (2) Quick Jump scratch space
if "jump_intent" not in st.session_state:
    st.session_state.jump_intent = {}

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
        # (8) If file exists but header differs, we keep appending; first write header if not exists
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

# ===== Config: bulk capacity (19) =====
DEFAULT_BULK_RULES = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

def load_config() -> dict:
    cfg = {"bulk_rules": DEFAULT_BULK_RULES.copy()}
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "bulk_rules" in raw and isinstance(raw["bulk_rules"], dict):
                cfg["bulk_rules"] = {k.upper(): int(v) for k, v in raw["bulk_rules"].items()
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

empty_bins_view_df = pd.DataFrame({"LocationName": sorted(
    [loc for loc in master_locations if (loc not in occupied_locations and not str(loc).endswith("01"))]
)})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damages_df = inventory_df[inventory_df["LocationName"].str.upper().isin(["DAMAGE", "IBDAMAGE"])].copy()
missing_df = inventory_df[inventory_df["LocationName"].str.upper() == "MISSING"].copy()
bulk_locations_df, empty_bulk_locations_df = build_bulk_views()

# ===== (14) Precomputed indices for speed =====
CORE_COLS = ["LocationName", "WarehouseSku", "PalletId", "CustomerLotReference", "Qty"]

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

def _lot_to_str(x): return normalize_whole_number(x)

def maybe_limit(df: pd.DataFrame) -> pd.DataFrame:
    return df.head(1000) if st.session_state.get("fast_tables", False) else df

# Location -> core rows
LOC_INDEX: dict[str, pd.DataFrame] = {}
for loc, g in filtered_inventory_df.groupby(filtered_inventory_df["LocationName"].astype(str)):
    LOC_INDEX[str(loc)] = ensure_core(g)

# Location -> unique pallet choice labels + key map (used in Bulk Locations)
def _mk_pallet_labels(df: pd.DataFrame):
    df = df.copy()
    df["PalletId"] = df["PalletId"].apply(normalize_whole_number)
    df["CustomerLotReference"] = df["CustomerLotReference"].apply(_lot_to_str)

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

    # key = pallet id if present else row index
    df["_PID_KEY"] = df["PalletId"].where(df["PalletId"].astype(str).str.len() > 0, df.index.astype(str))
    uniq = df.drop_duplicates(subset=["_PID_KEY"])
    labels = [ _label(r) for _, r in uniq.iterrows() ]
    label_to_key = { _label(r): r["_PID_KEY"] for _, r in uniq.iterrows() }
    return labels, label_to_key, df

PALLET_LABELS_BY_LOC: dict[str, tuple[list[str], dict, pd.DataFrame]] = {}
for loc, df in LOC_INDEX.items():
    PALLET_LABELS_BY_LOC[loc] = _mk_pallet_labels(df)

# ===== (16) File freshness badge =====
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
    # time since last snapshot
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

# ===== Logging (8) with Reason codes (backward compatible) =====
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

def log_action(row: dict, note: str, selected_lot: str, discrepancy_type: str, action: str, batch_id: str, reason: str = "") -> tuple[bool, str, str]:
    has_reason = _resolved_has_reason()
    csv_row_v1 = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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

def log_batch(df_rows: pd.DataFrame, note: str, selected_lot: str, discrepancy_type: str, action: str, reason: str = "") -> tuple[str, str]:
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
            # be lenient if column count changed
            return pd.read_csv(resolved_file, engine="python")
        fb_dir, _ = _resolve_writable_dir(None, purpose="logs")
        fb_path = os.path.join(fb_dir, os.path.basename(resolved_file))
        if os.path.isfile(fb_path):
            return pd.read_csv(fb_path, engine="python")
    except Exception:
        pass
    return pd.DataFrame()

# ===== Discrepancies =====
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

# ===== (7) Duplicate Pallets (PalletId in >1 Location) =====
def build_duplicate_pallets(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = df.copy()
    base["PalletId"] = base["PalletId"].apply(normalize_whole_number)
    grp = base.groupby("PalletId")["LocationName"].nunique().reset_index(name="DistinctLocations")
    dups = grp[(grp["PalletId"].astype(str).str.len() > 0) & (grp["DistinctLocations"] > 1)].sort_values("DistinctLocations", ascending=False)
    if dups.empty:
        return dups, pd.DataFrame()
    dup_ids = set(dups["PalletId"])
    details = base[base["PalletId"].isin(dup_ids)].copy()
    return dups, ensure_core(details)

dups_summary_df, dups_detail_df = build_duplicate_pallets(filtered_inventory_df)

# ===== KPI Card CSS (unchanged) =====
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
    "Bulk Locations", "Empty Bulk Locations", "Duplicate Pallets", "Trends", "Config", "Self-Test"
]
_default_nav = st.session_state.get("nav", "Dashboard")
if "pending_nav" in st.session_state:
    _default_nav = st.session_state.pop("pending_nav", _default_nav)
try:
    _default_index = nav_options.index(_default_nav) if _default_nav in nav_options else 0
except ValueError:
    _default_index = 0

# (2) Quick Jump (scan/enter): pallet id or location
def _handle_quick_jump():
    q = st.session_state.get("quick_jump_text", "").strip()
    if not q:
        st.session_state.jump_intent = {}
        return
    q_norm = normalize_whole_number(q)

    # Try Pallet ID first
    match_rows = filtered_inventory_df[filtered_inventory_df["PalletId"].apply(normalize_whole_number) == q_norm]
    if not match_rows.empty:
        loc = str(match_rows.iloc[0]["LocationName"])
        st.session_state.jump_intent = {"type": "pallet", "location": loc, "pallet_id": q_norm}
        st.session_state["pending_nav"] = "Bulk Locations" if loc and loc[0].upper() in bulk_rules else "Rack Discrepancies"
        _rerun(); return

    # Try Location (as-typed)
    if q in LOC_INDEX:
        st.session_state.jump_intent = {"type": "location", "location": q}
        st.session_state["pending_nav"] = "Bulk Locations" if q and q[0].upper() in bulk_rules else "Rack Discrepancies"
        _rerun(); return

    # Fallback: numeric q might be a location like '11400804'
    if q_norm in LOC_INDEX:
        st.session_state.jump_intent = {"type": "location", "location": q_norm}
        st.session_state["pending_nav"] = "Bulk Locations" if q_norm and q_norm[0].upper() in bulk_rules else "Rack Discrepancies"
        _rerun(); return

    # If not found, keep the text (no nav)
    st.session_state.jump_intent = {"type": "none", "raw": q}

selected_nav = st.radio("🔍 Navigate:", nav_options, index=_default_index, horizontal=True, key="nav")

# (2) Quick Jump bar (works on Enter/Scan)
st.text_input(
    "Quick Jump (scan or type Pallet ID or Location and press Enter)",
    value="",
    key="quick_jump_text",
    placeholder="e.g., 9062716 or A123 or 11400804",
    on_change=_handle_quick_jump
)
st.markdown("---")

# ===== (11) Trends: deltas for KPIs =====
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
    return {
        "EmptyBins": len(empty_bins_view_df),
        "EmptyPartialBins": len(empty_partial_bins_df),
        "PartialBins": len(partial_bins_df),
        "FullPalletBins": len(full_pallet_bins_df),
        "Damages": len(damages_df),
        "Missing": len(missing_df),
    }

def _kpi_deltas(hist: pd.DataFrame, now: dict) -> dict[str, dict]:
    # returns {key: {"vs_last": int|None, "vs_yday": int|None}}
    out = {k: {"vs_last": None, "vs_yday": None} for k in now}
    if hist is None or hist.empty:
        return out
    # last snapshot
    last = hist.iloc[-1] if not hist.empty else None
    # same day -1 (closest)
    yday = None
    try:
        day_ago = datetime.now() - timedelta(days=1)
        ydf = hist[(hist["Timestamp"].dt.date == day_ago.date())]
        if not ydf.empty:
            yday = ydf.iloc[-1]
        else:
            # nearest older than 24h if exact date missing
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
    return "  |  ".join(parts) if parts else None

# Append trend snapshot if requested
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

# ===== KPI helper with delta support =====
def _animate_metric(ph, label: str, value: int | float, delta_text: str | None = None, duration_ms: int = 600, steps: int = 20):
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
        # final lock-in with delta
        ph.metric(label, v_end, delta=delta_text)
    except Exception:
        ph.metric(label, value, delta=delta_text)

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
    # (11) Compute deltas
    hist = _read_trends()
    now = _current_kpis()
    deltas = _kpi_deltas(hist, now)

    def _dx(key_name):
        # map display -> trend key
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

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    k1 = col1.empty(); k2 = col2.empty(); k3 = col3.empty(); k4 = col4.empty(); k5 = col5.empty(); k6 = col6.empty()
    _animate_metric(k1, "Empty Bins", kpi_vals["Empty Bins"], delta_text=_dx("Empty Bins"))
    _animate_metric(k2, "Empty Partial Bins", kpi_vals["Empty Partial Bins"], delta_text=_dx("Empty Partial Bins"))
    _animate_metric(k3, "Partial Bins", kpi_vals["Partial Bins"], delta_text=_dx("Partial Bins"))
    _animate_metric(k4, "Full Pallet Bins", kpi_vals["Full Pallet Bins"], delta_text=_dx("Full Pallet Bins"))
    _animate_metric(k5, "Damages", kpi_vals["Damages"], delta_text=_dx("Damages"))
    _animate_metric(k6, "Missing", kpi_vals["Missing"], delta_text=_dx("Missing"))

    # KPI view buttons
    if col1.button("View", key="btn_empty"): st.session_state["pending_nav"] = "Empty Bins"; _rerun()
    if col2.button("View", key="btn_empty_partial"): st.session_state["pending_nav"] = "Empty Partial Bins"; _rerun()
    if col3.button("View", key="btn_partial"): st.session_state["pending_nav"] = "Partial Bins"; _rerun()
    if col4.button("View", key="btn_full"): st.session_state["pending_nav"] = "Full Pallet Bins"; _rerun()
    if col5.button("View", key="btn_damage"): st.session_state["pending_nav"] = "Damages"; _rerun()
    if col6.button("View", key="btn_missing"): st.session_state["pending_nav"] = "Missing"; _rerun()

    # Distribution chart
    kpi_df = pd.DataFrame({"Category": list(kpi_vals.keys()), "Count": list(kpi_vals.values())})
    kpi_df["Group"] = kpi_df["Category"].apply(lambda c: "Exceptions" if c in ["Damages", "Missing"] else "Bins")
    fig_kpi = px.bar(kpi_df, x="Category", y="Count", color="Group", text="Count",
                     title="Bin Status Distribution", color_discrete_map={"Bins": BLUE, "Exceptions": RED})
    fig_kpi.update_traces(textposition="outside")
    fig_kpi.update_layout(xaxis_title="", yaxis_title="Count", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig_kpi, use_container_width=True)

    # Rack pie
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

    # Bulk zone stacked (Used vs Empty)
    if not pd.DataFrame(bulk_locations_df).empty:
        bulk_zone = bulk_locations_df.groupby("Zone").agg(Used=("PalletCount", "sum"), Capacity=("MaxAllowed", "sum")).reset_index()
        bulk_zone["Empty"] = (bulk_zone["Capacity"] - bulk_zone["Used"]).clip(lower=0)
        bulk_stack = bulk_zone.melt(id_vars="Zone", value_vars=["Used", "Empty"], var_name="Type", value_name="Count")
        fig_bulk = px.bar(bulk_stack, x="Zone", y="Count", color="Type", barmode="stack",
                          title="Bulk Zones: Used vs Empty Capacity", color_discrete_map={"Used": BLUE, "Empty": RED})
        fig_bulk.update_layout(xaxis_title="Zone", yaxis_title="Pallets", showlegend=True, margin=dict(t=60, b=40, l=10, r=10))
        st.plotly_chart(fig_bulk, use_container_width=True)

        # (12) Zone heat block by utilization ratio
        bulk_zone["Utilization"] = bulk_zone["Used"] / bulk_zone["Capacity"].replace(0, pd.NA)
        fig_heat = px.bar(
            bulk_zone, x="Zone", y="Utilization", color="Utilization",
            title="Zone Heat (Utilization: Used/Capacity)", range_y=[0, 1.2],
            color_continuous_scale=[[0.0, BLUE], [0.8, "#ff7f0e"], [1.0, RED]]
        )
        fig_heat.update_traces(marker_line_color="rgba(0,0,0,0.2)", marker_line_width=1)
        fig_heat.update_layout(yaxis_tickformat=".0%", coloraxis_showscale=False, margin=dict(t=60, b=40, l=10, r=10))
        st.plotly_chart(fig_heat, use_container_width=True)

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
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="rack_lot_filter", help="Only non-empty LOTs are shown. Use (All) to see every row.")
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
        st.dataframe(maybe_limit(rack_display), use_container_width=True)
        csv_data = discrepancy_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Rack Discrepancies CSV", csv_data, "rack_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
        lot_choices = sorted({_lot_to_str(x) for x in discrepancy_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)})
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="rack_fix_lot")
            reason = st.selectbox("Reason", reasons, index=0, key="rack_fix_reason")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="rack_fix_note")
            if st.button("Fix Selected LOT", key="rack_fix_btn"):
                rows_to_fix = discrepancy_df[discrepancy_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Rack", action="RESOLVE", reason=reason)
                st.success(f"Resolved {len(rows_to_fix)} rack discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
                data = _load_lottie("https://assets10.lottiefiles.com/packages/lf20_jbrw3hcz.json")
                if data: st_lottie(data, height=90, key=f"rack_fix_success_{batch_id}", loop=False, speed=1.2)
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
                            ok, upath, err = log_action(r.to_dict(), f"UNDO of batch {last_batch}", r.get("SelectedLOT", ""), "Rack", "UNDO", str(last_batch), reason="Undo")
                            if not ok:
                                st.error(f"Failed to write UNDO action. {err}")
                                break
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
        lots = ["(All)"] + sorted([_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)])
        sel_lot = st.selectbox("Filter by LOT", lots, index=0, key="bulk_lot_filter", help="Only non-empty LOTs are shown. Use (All) to see every row.")
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
                reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
                reason = st.selectbox("Reason", reasons, index=0, key="bulk_sel_reason")
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
                    batch_id, used_path = log_batch(sel_rows, note, selected_lot_value, "Bulk", action="RESOLVE", reason=reason)
                    st.success(f"Logged fix for {len(sel_rows)} row(s).")
                    st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")

        st.markdown("#### Flat view (all rows)")
        bulk_display = ensure_core(filt, include_issue=True)
        st.dataframe(maybe_limit(bulk_display), use_container_width=True)
        csv_data = bulk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Bulk Discrepancies CSV", csv_data, "bulk_discrepancies.csv", "text/csv")

        st.markdown("### ✅ Fix discrepancy by LOT")
        lot_choices = sorted({_lot_to_str(x) for x in bulk_df["CustomerLotReference"].dropna().unique() if _lot_to_str(x)})
        reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
        if lot_choices:
            chosen_lot = st.selectbox("Select LOT to fix", lot_choices, key="bulk_fix_lot")
            reason = st.selectbox("Reason", reasons, index=0, key="bulk_fix_reason")
            note = st.text_input(f"Add note for LOT {chosen_lot}", key="bulk_fix_note")
            if st.button("Fix Selected LOT", key="bulk_fix_btn"):
                rows_to_fix = bulk_df[bulk_df["CustomerLotReference"].map(_lot_to_str) == chosen_lot]
                batch_id, used_path = log_batch(rows_to_fix, note, chosen_lot, "Bulk", action="RESOLVE", reason=reason)
                st.success(f"Resolved {len(rows_to_fix)} bulk discrepancy row(s) for LOT {chosen_lot}.")
                st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")
        else:
            st.info("No valid LOTs available to fix.")
    else:
        st.info("No bulk discrepancies found.")

elif selected_nav == "Bulk Locations":
    st.subheader("Bulk Locations")
    st.caption("Click a location or use Quick Jump, then pick a pallet from the dropdown.")

    # High-contrast row highlight for over-capacity (GRID mode)
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

    # (2) Quick Jump: show jump result panel even without grid selection
    jump = st.session_state.get("jump_intent", {}) or {}

    def _render_location_detail(loc: str, preselect_pallet: str | None = None, key_prefix: str = ""):
        loc = str(loc)
        rows = LOC_INDEX.get(loc, pd.DataFrame())
        if rows.empty:
            st.warning(f"No pallets found for location {loc}."); return
        labels, label_to_key, full_df = PALLET_LABELS_BY_LOC.get(loc, ([], {}, rows))
        choices = ["(All)"] + labels
        default_index = 0
        if preselect_pallet:
            # Pick label that contains the pallet id
            for i, lab in enumerate(labels, start=1):
                if preselect_pallet in lab:
                    default_index = i; break
        selected_label = st.selectbox(f"Pallets at {loc}", choices, index=default_index, key=f"{key_prefix}pallet_dd_{loc}")
        if selected_label == "(All)":
            show_df = full_df
        else:
            chosen_key = label_to_key.get(selected_label, None)
            show_df = full_df if chosen_key is None else full_df[full_df["_PID_KEY"] == chosen_key]
        st.dataframe(ensure_core(show_df), use_container_width=True)

    if ui_mode.startswith("Grid") and _AGGRID_AVAILABLE and not parent_df.empty:
        # Build grid of bulk locations (no enterprise features)
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
        sel_rows = pd.DataFrame(grid_resp.get("selected_rows", []))
        if not sel_rows.empty:
            sel_loc = str(sel_rows.iloc[0]["LocationName"])
            _render_location_detail(sel_loc, key_prefix="grid_")

        # Jump panel (preferred if Quick Jump was used)
        if jump.get("type") in ("pallet", "location") and jump.get("location"):
            st.markdown("#### Jump Result")
            _render_location_detail(jump["location"], preselect_pallet=jump.get("pallet_id"), key_prefix="jump_")
    else:
        # Expanders fallback
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
                    _render_location_detail(loc, key_prefix="exp_")

        # Jump panel when not using grid
        if jump.get("type") in ("pallet", "location") and jump.get("location"):
            st.markdown("#### Jump Result")
            _render_location_detail(jump["location"], preselect_pallet=jump.get("pallet_id"), key_prefix="jump2_")

elif selected_nav == "Empty Bulk Locations":
    st.subheader("Empty Bulk Locations")
    st.dataframe(maybe_limit(empty_bulk_locations_df), use_container_width=True)

elif selected_nav == "Duplicate Pallets":
    st.subheader("Duplicate Pallets (same Pallet ID in multiple locations)")
    if dups_summary_df.empty:
        st.success("No duplicate pallets found. ✅")
    else:
        st.write("Summary (PalletId with count of distinct locations):")
        st.dataframe(dups_summary_df, use_container_width=True)
        # Select a PalletId to see details & log
        opt = ["(Select)"] + dups_summary_df["PalletId"].astype(str).tolist()
        sel_pid = st.selectbox("Choose a duplicate Pallet ID", opt, index=0)
        if sel_pid != "(Select)":
            det = dups_detail_df[dups_detail_df["PalletId"].astype(str) == str(sel_pid)]
            st.dataframe(det.sort_values("LocationName"), use_container_width=True)
            with st.expander("Log Fix for this Pallet ID"):
                reasons = ["Relocated", "Consolidated", "Data correction", "Damaged pull-down", "Other"]
                reason = st.selectbox("Reason", reasons, index=0, key="dup_fix_reason")
                note = st.text_input("Note (optional)", key="dup_fix_note")
                if st.button("Log Fix for this Pallet ID"):
                    batch_id, used_path = log_batch(det, note, selected_lot="", discrepancy_type="Duplicate", action="RESOLVE", reason=reason)
                    st.success(f"Logged fix for PalletId {sel_pid} across {det['LocationName'].nunique()} locations.")
                    st.caption(f"📝 Logged to: `{used_path}` • BatchId={batch_id}")

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

elif selected_nav == "Config":
    st.subheader("⚙️ Config — Bulk Capacity Rules (A..I)")
    st.caption("Edit and **Save** to apply. This writes to `config.json` in your logs folder.")

    # Current rules (with defaults if file missing)
    cur = _config.get("bulk_rules", DEFAULT_BULK_RULES).copy()

    zones = list(DEFAULT_BULK_RULES.keys())  # ['A'..'I']
    cols = st.columns(len(zones))

    new_rules = {}
    for i, z in enumerate(zones):
        with cols[i]:
            new_rules[z] = st.number_input(
                f"{z}",
                min_value=0,
                max_value=50,
                value=int(cur.get(z, DEFAULT_BULK_RULES[z])),
                step=1,
                key=f"cfg_{z}"
            )

    save_col, apply_col = st.columns([1, 3])
    with save_col:
        if st.button("💾 Save", type="primary", use_container_width=True, key="cfg_save"):
            _config["bulk_rules"] = {k: int(v) for k, v in new_rules.items()}
            save_config(_config)
            # Update in-memory rules so the app uses them immediately
            bulk_rules.update(_config["bulk_rules"])
            st.success("Saved `config.json`. Click **Apply** to rebuild zone views.")
    with apply_col:
        if st.button("⚙️ Apply (rebuild zone capacity views)", use_container_width=True, key="cfg_apply"):
            # Recompute the bulk location derived views using the new rules
            global bulk_locations_df, empty_bulk_locations_df, bulk_rules
            bulk_rules = _config["bulk_rules"].copy()
            bulk_locations_df, empty_bulk_locations_df = build_bulk_views()
            st.success("Bulk zone capacity views rebuilt.")
            _rerun()

    st.markdown("———")
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

    st.markdown("———")
