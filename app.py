# ================================================================
# Bin Helper - Streamlit App (v2.5.1)
# Author: Carlos Pacheco + M365 Copilot
# Notes:
#   - Preserves all business rules and UI from prior versions
#   - Adds: Fix Log (All), Discrepancies (All), Trends persistence
#     with startup & interval snapshots, Bulk drilldown, Flat Pallet
#     List (Bulk), new charts (Racks Empty vs Full, Bulk Used vs Empty)
#   - Includes AI NLQ (beta) with OpenAI/Azure OpenAI support and
#     safe regex fallback. Uses secrets/env if present.
#   - LOT normalization: digits-only, no leading zeros; Pallet IDs
#     retain alphanumerics.
#   - No CaseCount anywhere by request.
#   - Indentation normalized to 4 spaces throughout.
#
# ================================================================

from __future__ import annotations

import os
import io
import re
import sys
import json
import time
import math
import glob
import uuid
import textwrap
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import streamlit as st

# Optional visualization libs
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# AgGrid (Community features only)
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
# Optional: Lottie
try:
    from streamlit_lottie import st_lottie
    LOTTIE_AVAILABLE = True
except Exception:
    LOTTIE_AVAILABLE = False

# --- Pandas Excel engines (per environment guidance) ---
#   - read xlsx with openpyxl
#   - read xls with xlrd
#   - we will set engine explicitly where needed.

# ================================================================
# Global Constants & Theme
# ================================================================

APP_VERSION = "2.5.1"
APP_NAME = "Bin Helper"
SESSION_KEY_PREFIX = "bin_helper_"
DEFAULT_TAB = "Dashboard"

# Theme colors (Carlos prefers blue & red)
PRIMARY_BLUE = "#1E88E5"
PRIMARY_RED = "#E53935"
PRIMARY_GREEN = "#43A047"
PRIMARY_ORANGE = "#FB8C00"
PRIMARY_PURPLE = "#8E24AA"
PRIMARY_PINK = "#D81B60"
NEON_ACCENT = "#00E5FF"

# AgGrid theme
AGGRID_THEME = "balham"  # or "streamlit"

# Default paths (Windows + Streamlit Cloud)
DEFAULT_LOG_DIR_WIN = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
DEFAULT_LOG_DIR_CLOUD = "/mount/src/bin-helper/logs"
LOG_ENV_VAR = "BIN_HELPER_LOG_DIR"

# Trend CSV filename inside log dir
TRENDS_FILENAME = "trends.csv"

# Lottie animations (optional external URLs removed; we embed a tiny placeholder)
# We'll fallback to a small built-in animation-like structure if package available.
LOTTIE_DASHBOARD = {
    "v": "5.5.8",
    "fr": 30,
    "ip": 0,
    "op": 60,
    "w": 200,
    "h": 200,
    "nm": "neon_pulse",
    "ddd": 0,
    "assets": [],
    "layers": [],
}

# Auto-refresh (file change + TTL)
AUTO_REFRESH_TTL_SEC = 300  # 5 minutes

# Snapshot defaults
DEFAULT_AUTO_SNAPSHOT_ON_START = True
DEFAULT_AUTO_SNAPSHOT_INTERVAL_MIN = 30

# Column mapping defaults (keys we need)
REQUIRED_COLS = {
    "Location": ["Location", "Bin", "Loc", "LocationCode", "BinCode"],
    "SKU": ["SKU", "Item", "ItemNumber", "ItemCode", "Product"],
    "LOT": ["LOT", "LotNumber", "CustomerLotReference", "Lot"],
    "PalletID": ["PalletID", "Pallet", "LPN", "LicensePlate", "PalletNo", "PalletNumber"],
    "QTY": ["QTY", "Quantity", "QtyOnHand", "QTYOnHand", "OnHandQty", "Qty"],
}

# Business Rule constants
FULL_PALLET_QTY_THRESHOLD = 6  # >5 => full pallet for non-'111' non-01 racks
PARTIAL_PALLET_MAX = 5  # <=5 => partial
LEVEL_BOTTOM = "01"
RACK_PREFIX = "111"

# ================================================================
# Session State Helpers
# ================================================================

def _ss_get(key: str, default=None):
    return st.session_state.get(SESSION_KEY_PREFIX + key, default)

def _ss_set(key: str, value):
    st.session_state[SESSION_KEY_PREFIX + key] = value
    return value

# ================================================================
# Utility / Files / Paths
# ================================================================

def get_log_dir() -> str:
    """
    Determine the active logs directory with safe fallbacks.
    Preference order:
        1) BIN_HELPER_LOG_DIR (env/secrets)
        2) Windows OneDrive default
        3) Streamlit Cloud default
        4) ./logs
    Ensure the directory exists.
    """
    # 1) env
    env_dir = os.getenv(LOG_ENV_VAR)
    if env_dir:
        try:
            os.makedirs(env_dir, exist_ok=True)
            return env_dir
        except Exception:
            pass

    # 2) Windows default
    try:
        os.makedirs(DEFAULT_LOG_DIR_WIN, exist_ok=True)
        return DEFAULT_LOG_DIR_WIN
    except Exception:
        pass

    # 3) Cloud default
    try:
        os.makedirs(DEFAULT_LOG_DIR_CLOUD, exist_ok=True)
        return DEFAULT_LOG_DIR_CLOUD
    except Exception:
        pass

    # 4) Local fallback
    local_dir = os.path.abspath("./logs")
    os.makedirs(local_dir, exist_ok=True)
    return local_dir


def safe_csv_append(df: pd.DataFrame, path: str):
    """
    Appends a DataFrame to a CSV safely (create if not exists).
    Ensures directory exists. Newline handling for UTF-8.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) or os.stat(path).st_size == 0:
        df.to_csv(path, mode="w", index=False, encoding="utf-8", lineterminator="\n")
    else:
        df.to_csv(path, mode="a", index=False, header=False, encoding="utf-8", lineterminator="\n")


def read_excel_with_selector(
    file_path: str,
    sheet_name: Optional[str],
    header_row_index: int,
) -> pd.DataFrame:
    """
    Read Excel with explicit engine and header row. Robust to .xlsx/.xls.
    """
    if not file_path:
        return pd.DataFrame()

    if not os.path.exists(file_path):
        return pd.DataFrame()

    ext = os.path.splitext(file_path)[1].lower()
    engine = "openpyxl" if ext == ".xlsx" else "xlrd"

    try:
        if sheet_name is None or sheet_name == "":
            df = pd.read_excel(file_path, engine=engine, header=header_row_index)
        else:
            df = pd.read_excel(file_path, engine=engine, sheet_name=sheet_name, header=header_row_index)
        # Normalize column header whitespace
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        st.warning(f"Failed to read Excel: {e}")
        return pd.DataFrame()


def detect_sheets(file_path: str) -> List[str]:
    """
    Return available sheet names for Excel file.
    """
    if not file_path or not os.path.exists(file_path):
        return []
    ext = os.path.splitext(file_path)[1].lower()
    engine = "openpyxl" if ext == ".xlsx" else "xlrd"
    try:
        xl = pd.ExcelFile(file_path, engine=engine)
        return xl.sheet_names
    except Exception:
        return []


def touch_file(path: str):
    """
    Touch a file for updated mtime.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("")
    os.utime(path, None)


def get_file_mtime(path: str) -> float:
    """
    Get last modified time. Returns 0 if missing.
    """
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def file_has_changed(path: str, state_key: str) -> bool:
    """
    Compare stored mtime in session vs current file mtime.
    """
    current = get_file_mtime(path)
    last = _ss_get(state_key, 0.0)
    if current > last:
        _ss_set(state_key, current)
        return True
    return False
def now_ts() -> str:
    """
    Return current timestamp (12-hour format requested).
    """
    return dt.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")


# ================================================================
# Column Mapping & Normalization
# ================================================================

def load_saved_mappings() -> Dict[str, Dict[str, str]]:
    """
    Load column mappings from user data dir.
    """
    cfg_dir = os.path.abspath("./.bin_helper")
    os.makedirs(cfg_dir, exist_ok=True)
    path = os.path.join(cfg_dir, "column_mappings.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_mappings(mappings: Dict[str, Dict[str, str]]):
    cfg_dir = os.path.abspath("./.bin_helper")
    os.makedirs(cfg_dir, exist_ok=True)
    path = os.path.join(cfg_dir, "column_mappings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2, ensure_ascii=False)


def propose_mapping(df: pd.DataFrame) -> Dict[str, str]:
    """
    Auto-propose a mapping from REQUIRED_COLS to df columns.
    """
    mapping = {}
    cols = {c.lower(): c for c in df.columns}
    for need, options in REQUIRED_COLS.items():
        match = None
        for opt in options:
            if opt.lower() in cols:
                match = cols[opt.lower()]
                break
        if match is None:
            # try partial contains
            for opt in options:
                for c in df.columns:
                    if opt.lower() in str(c).lower():
                        match = c
                        break
                if match:
                    break
        mapping[need] = match if match else ""
    return mapping


def apply_mapping(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """
    Rename columns to canonical names per mapping.
    Filters to only needed columns if available.
    """
    rename_map = {}
    for key, src in mapping.items():
        if src and src in df.columns:
            rename_map[src] = key
    out = df.rename(columns=rename_map)
    # Keep only canonical columns if present
    keep = [c for c in ["Location", "SKU", "LOT", "PalletID", "QTY"] if c in out.columns]
    out = out[keep].copy()
    return out


def normalize_lot(lot_value: Any) -> str:
    """
    LOT Number normalized to whole numeric strings (no decimals/letters),
    e.g., '9062716'; strip all non-digits and leading zeros in display/logic.
    Empty if nothing remains.
    """
    if pd.isna(lot_value):
        return ""
    s = str(lot_value)
    digits = re.sub(r"\D+", "", s)
    # strip leading zeros
    digits = digits.lstrip("0")
    return digits


def normalize_pallet_id(pallet_value: Any) -> str:
    """
    Pallet IDs should preserve alphanumeric characters (e.g., JTL00496).
    Do not strip alphanumerics; trim whitespace only.
    """
    if pd.isna(pallet_value):
        return ""
    s = str(pallet_value).strip()
    return s


def normalize_location(loc_value: Any) -> str:
    """
    Ensure location is a clean string (no trailing spaces).
    """
    if pd.isna(loc_value):
        return ""
    s = str(loc_value).strip()
    return s


def to_int_qty(v: Any) -> int:
    """
    Coerce to int. If float/str, parse best effort.
    """
    if pd.isna(v):
        return 0
    try:
        return int(round(float(str(v).strip())))
    except Exception:
        # Try digits-only fallback
        digits = re.sub(r"[^\d\-]+", "", str(v))
        try:
            return int(digits)
        except Exception:
            return 0


def normalize_inventory_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply normalization rules to canonical inventory columns.
    """
    if df.empty:
        return df
    out = df.copy()
    if "Location" in out.columns:
        out["Location"] = out["Location"].apply(normalize_location)
    if "SKU" in out.columns:
        out["SKU"] = out["SKU"].astype(str).str.strip()
    if "LOT" in out.columns:
        out["LOT"] = out["LOT"].apply(normalize_lot)
    if "PalletID" in out.columns:
        out["PalletID"] = out["PalletID"].apply(normalize_pallet_id)
    if "QTY" in out.columns:
        out["QTY"] = out["QTY"].apply(to_int_qty)
    return out


# ================================================================
# Business Rules & Computations
# ================================================================

def is_rack_location(location: str) -> bool:
    """
    Only rack locations should be considered for certain rules/heat map.
    For Bin Helper, racks often start with '111'.
    """
    if not location:
        return False
    return location.startswith(RACK_PREFIX)


def is_bottom_level(location: str) -> bool:
    """
    The location format is AAA BBB CC (e.g., 11100101).
    The last two digits are the level, where 01 = bottom and 02 = above.
    """
    if not location or len(location) < 2:
        return False
    level = location[-2:]
    return level == LEVEL_BOTTOM


def is_full_pallet_bin(location: str) -> bool:
    """
    111*** are full pallet bins, even if ending with 01; others typically
    non-01 with Qty > 5 are full pallet bins as well.
    """
    if not location:
        return False
    if location.startswith(RACK_PREFIX):
        return True
    # Non-rack: consider non-01 levels are full pallet slots
    # (This aligns with business rule Full Pallet = non-01 with qty > 5)
    level = location[-2:] if len(location) >= 2 else ""
    return level != LEVEL_BOTTOM


def is_partial_slot(location: str) -> bool:
    """
    Partial = ends with 01 (excluding those starting with 111 or TUN),
    and not empty.
    """
    if not location:
        return False
    starts_excluded = location.startswith("111") or location.upper().startswith("TUN")
    return (not starts_excluded) and is_bottom_level(location)


def compute_kpis(
    master_locations: pd.DataFrame,
    inventory: pd.DataFrame,
) -> Dict[str, int]:
    """
    Computes KPIs:
        - Empty Bins: master locations that are empty (not in inventory or qty<=0)
        - Empty Partial Bins: endswith '01' (excluding 111/TUN) that are empty
        - Full Pallet Bins: 111*** OR non-01 with Qty > 5
        - Empty Pallet Bins: bins designated as full pallet bins but empty
        - Damages & Missing: count total QTY in DAMAGE / MISSING categories if present
    """
    kpis = {
        "Empty Bins": 0,
        "Empty Partial Bins": 0,
        "Full Pallet Bins": 0,
        "Empty Pallet Bins": 0,
        "Damages": 0,
        "Missing": 0,
    }

    inv = inventory.copy()
    inv["QTY"] = inv["QTY"].fillna(0).astype(int)

    # Determine empties with master reference:
    # Master expected 'Location' column
    master_locs = set(master_locations["Location"].dropna().astype(str).str.strip().tolist()) if "Location" in master_locations.columns else set()
    inv_by_loc = inv.groupby("Location", dropna=False)["QTY"].sum().reset_index()
    inv_by_loc["Location"] = inv_by_loc["Location"].fillna("").astype(str).str.strip()
    inv_by_loc_map = {row["Location"]: int(row["QTY"]) for _, row in inv_by_loc.iterrows()}

    empty_bins = []
    empty_partial_bins = []
    empty_pallet_bins = []

    for loc in master_locs:
        qty = inv_by_loc_map.get(loc, 0)
        if qty <= 0:
            # Empty bin
            empty_bins.append(loc)
            # Empty partial
            if is_partial_slot(loc):
                empty_partial_bins.append(loc)
            # Empty full pallet bin
            if is_full_pallet_bin(loc):
                empty_pallet_bins.append(loc)

    kpis["Empty Bins"] = len(empty_bins)
    kpis["Empty Partial Bins"] = len(empty_partial_bins)
    kpis["Empty Pallet Bins"] = len(empty_pallet_bins)

    # Full Pallet Bins: any occupied 111*** OR non-01 with Qty > 5
    full_bins = 0
    for _, row in inv_by_loc.iterrows():
        loc = row["Location"]
        qty = int(row["QTY"])
        if not loc:
            continue
        if loc.startswith(RACK_PREFIX):
            if qty > 0:
                full_bins += 1
        else:
            # non-01 with Qty > 5
            if len(loc) >= 2 and loc[-2:] != LEVEL_BOTTOM and qty > PARTIAL_PALLET_MAX:
                full_bins += 1
    kpis["Full Pallet Bins"] = full_bins

    # Damages & Missing totals if present
    # Treat locations like "DAMAGE", "IBDAMAGE", "MISSING"
    damage_mask = inv["Location"].str.upper().str.contains("DAMAGE", na=False)
    missing_mask = inv["Location"].str.upper().str.contains("MISSING", na=False)
    kpis["Damages"] = int(inv.loc[damage_mask, "QTY"].sum())
    kpis["Missing"] = int(inv.loc[missing_mask, "QTY"].sum())

    return kpis


def categorize_inventory_rows(inv: pd.DataFrame) -> pd.DataFrame:
    """
    Adds helper columns for UI tabs:
       - Category: Empty Bin, Full Pallet Bin, Empty Partial Bin, Partial Bin, etc.
       - RackFlag: is rack
       - Level: last two digits
       - BulkFlag: inverse of rack in our simplified rule
    """
    if inv.empty:
        inv = inv.copy()
        inv["Category"] = ""
        inv["RackFlag"] = False
        inv["BulkFlag"] = False
        inv["Level"] = ""
        return inv

    df = inv.copy()
    df["RackFlag"] = df["Location"].apply(is_rack_location)
    df["Level"] = df["Location"].apply(lambda s: s[-2:] if isinstance(s, str) and len(s) >= 2 else "")
    df["BulkFlag"] = ~df["RackFlag"]

    # Precompute QTY by location to determine empty/non-empty
    by_loc_qty = df.groupby("Location", dropna=False)["QTY"].sum().reset_index()
    by_loc_qty_map = {row["Location"]: int(row["QTY"]) for _, row in by_loc_qty.iterrows()}

    def classify_row(row):
        loc = row["Location"]
        qty_total = by_loc_qty_map.get(loc, 0)
        if qty_total <= 0:
            # Empty categories will be derived via master reference; here we tag as 'Empty' placeholder
            if is_partial_slot(loc):
                return "Empty Partial Bin"
            if is_full_pallet_bin(loc):
                return "Empty Pallet Bin"
            return "Empty Bin"
        else:
            # Partial vs Full
            if loc.startswith(RACK_PREFIX):
                # Any qty in rack is considered full pallet bin occupancy
                return "Full Pallet Bin"
            else:
                level = loc[-2:] if isinstance(loc, str) and len(loc) >= 2 else ""
                if level != LEVEL_BOTTOM and qty_total > PARTIAL_PALLET_MAX:
                    return "Full Pallet Bin"
                # Otherwise partial if bottom-level slot and qty>0
                if level == LEVEL_BOTTOM:
                    return "Partial Bin"
                # else treat as non-bottom not-full -> partial
                return "Partial Bin"

    df["Category"] = df.apply(classify_row, axis=1)
    return df


def compute_racks_empty_vs_full(master_locations: pd.DataFrame, inv: pd.DataFrame) -> Tuple[int, int]:
    """
    Returns (racks_empty_count, racks_full_count).
    A rack is a location starting with '111'.
    Empty means qty <= 0 at that location compared to master reference.
    Full means any occupancy at rack location.
    """
    master_racks = set([loc for loc in master_locations["Location"].astype(str).str.strip().tolist() if str(loc).startswith(RACK_PREFIX)]) if "Location" in master_locations.columns else set()
    inv_by_loc = inv.groupby("Location", dropna=False)["QTY"].sum().reset_index()
    inv_by_loc["Location"] = inv_by_loc["Location"].fillna("").astype(str).str.strip()
    inv_map = {row["Location"]: int(row["QTY"]) for _, row in inv_by_loc.iterrows()}

    empty = 0
    full = 0
    for loc in master_racks:
        qty = inv_map.get(loc, 0)
        if qty <= 0:
            empty += 1
        else:
            full += 1
    return empty, full


def compute_bulk_used_vs_empty(master_locations: pd.DataFrame, inv: pd.DataFrame) -> Tuple[int, int]:
    """
    Returns (bulk_empty_count, bulk_used_count).
    Bulk is anything not rack (not starting '111'), based on master reference.
    """
    if "Location" not in master_locations.columns:
        return (0, 0)
    master_all = master_locations["Location"].astype(str).str.strip().tolist()
    bulk_master = [loc for loc in master_all if not str(loc).startswith(RACK_PREFIX)]
    inv_by_loc = inv.groupby("Location", dropna=False)["QTY"].sum().reset_index()
    inv_by_loc["Location"] = inv_by_loc["Location"].fillna("").astype(str).str.strip()
    inv_map = {row["Location"]: int(row["QTY"]) for _, row in inv_by_loc.iterrows()}

    empty = 0
    used = 0
    for loc in bulk_master:
        qty = inv_map.get(loc, 0)
        if qty <= 0:
            empty += 1
        else:
            used += 1
    return empty, used


# ================================================================
# Discrepancies
# ================================================================

def find_duplicate_pallets(inv: pd.DataFrame) -> pd.DataFrame:
    """
    Find duplicate pallets (same PalletID appearing more than once in different locations).
    Only shown under Discrepancies (All); standalone tab removed by request.
    """
    if inv.empty or "PalletID" not in inv.columns:
        return pd.DataFrame(columns=["PalletID", "Count", "Locations"])
    grp = inv.groupby("PalletID", dropna=False)["Location"].agg(list).reset_index()
    grp["Count"] = grp["Location"].apply(lambda L: len(set([x for x in L if x])))
    # Keep those that appear in more than 1 distinct location
    dup = grp[grp["Count"] > 1].copy()
    dup["Locations"] = dup["Location"].apply(lambda L: ", ".join(sorted(set([x for x in L if x]))))
    dup = dup.drop(columns=["Location"])
    dup = dup.sort_values(by="Count", ascending=False)
    return dup


def find_multi_pallet_per_rack(inv: pd.DataFrame) -> pd.DataFrame:
    """
    Discrepancy rule: all rack slots must have ≤ 1 pallet.
    Return locations in rack (111***) with more than 1 unique PalletID.
    """
    if inv.empty:
        return pd.DataFrame(columns=["Location", "PalletCount"])
    rack_df = inv[inv["Location"].astype(str).str.startswith(RACK_PREFIX)].copy()
    if rack_df.empty:
        return pd.DataFrame(columns=["Location", "PalletCount"])
    grp = rack_df.groupby("Location", dropna=False)["PalletID"].nunique().reset_index(name="PalletCount")
    bad = grp[grp["PalletCount"] > 1].copy()
    bad = bad.sort_values(by="PalletCount", ascending=False)
    return bad


def bulk_discrepancies(inv: pd.DataFrame) -> pd.DataFrame:
    """
    Bulk Discrepancies: display simplified columns only: SKU, LOT, PalletID.
    Hide MISSING in Discrepancies tab as requested (we won't list missing here).
    """
    if inv.empty:
        return pd.DataFrame(columns=["SKU", "LOT", "PalletID"])
    df = inv.copy()
    df = df[~df["Location"].str.upper().str.contains("MISSING", na=False)]
    df = df[~df["Location"].astype(str).str.startswith(RACK_PREFIX)]  # bulk only
    keep_cols = [c for c in ["SKU", "LOT", "PalletID"] if c in df.columns]
    return df[keep_cols].drop_duplicates()


# ================================================================
# Trends (Snapshots)
# ================================================================

def _trends_csv_path() -> str:
    return os.path.join(get_log_dir(), TRENDS_FILENAME)


def _read_trends() -> pd.DataFrame:
    path = _trends_csv_path()
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Timestamp", "Empty Bins", "Empty Partial Bins", "Full Pallet Bins", "Empty Pallet Bins", "Damages", "Missing"])
    try:
        df = pd.read_csv(path)
        # simple sanitize
        for col in ["Empty Bins", "Empty Partial Bins", "Full Pallet Bins", "Empty Pallet Bins", "Damages", "Missing"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df
    except Exception:
        return pd.DataFrame(columns=["Timestamp", "Empty Bins", "Empty Partial Bins", "Full Pallet Bins", "Empty Pallet Bins", "Damages", "Missing"])


def _snapshot_now(kpis: Dict[str, int] = None):
    """
    Capture a snapshot of KPIs into trends CSV.
    """
    if kpis is None:
        kpis = _ss_get("last_kpis", {})
    if not kpis:
        return
    ts = now_ts()
    row = pd.DataFrame([{
        "Timestamp": ts,
        "Empty Bins": kpis.get("Empty Bins", 0),
        "Empty Partial Bins": kpis.get("Empty Partial Bins", 0),
        "Full Pallet Bins": kpis.get("Full Pallet Bins", 0),
        "Empty Pallet Bins": kpis.get("Empty Pallet Bins", 0),
        "Damages": kpis.get("Damages", 0),
        "Missing": kpis.get("Missing", 0),
    }])
    safe_csv_append(row, _trends_csv_path())


def _maybe_startup_snapshot():
    """
    If enabled and not done yet in this session, snapshot at startup.
    """
    if _ss_get("did_startup_snapshot", False):
        return
    if _ss_get("auto_snapshot_on_start", DEFAULT_AUTO_SNAPSHOT_ON_START):
        try:
            _snapshot_now()
            _ss_set("did_startup_snapshot", True)
            # Show toast
            st.toast("Startup snapshot captured.", icon="✅")
        except Exception as e:
            st.toast(f"Startup snapshot skipped: {e}", icon="⚠️")


def _maybe_interval_snapshot():
    """
    Take snapshots at interval minutes across reruns (soft scheduler).
    Stores last snapshot time in session state.
    """
    minutes = int(_ss_get("auto_snapshot_interval_min", DEFAULT_AUTO_SNAPSHOT_INTERVAL_MIN))
    enabled = True  # Always enabled in v2.5.1 as per spec
    if not enabled or minutes <= 0:
        return
    now = time.time()
    last = float(_ss_get("last_interval_snapshot_ts", 0.0))
    if now - last >= minutes * 60:
        try:
            _snapshot_now()
            _ss_set("last_interval_snapshot_ts", now)
            st.toast(f"Interval snapshot captured (every {minutes} min).", icon="⏱️")
        except Exception as e:
            st.toast(f"Interval snapshot failed: {e}", icon="⚠️")


def _plot_trend(df: pd.DataFrame, metric: str):
    """
    Matplotlib line plot for selected metric over time.
    """
    if df.empty or metric not in df.columns:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.axis("off")
        return fig

    x = pd.to_datetime(df["Timestamp"], errors="coerce")
    y = pd.to_numeric(df[metric], errors="coerce").fillna(0)

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(x, y, color=PRIMARY_BLUE, marker="o", linewidth=2)
    ax.set_title(f"Trend: {metric}", color="white")
    ax.set_xlabel("Time", color="white")
    ax.set_ylabel(metric, color="white")
    ax.grid(True, alpha=0.3)
    ax.tick_params(colors="white")
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#222222")
    ax.xaxis.set_major_locator(MaxNLocator(6))
    fig.tight_layout()
    return fig


# ================================================================
# Fix Log (All) - Persistent
# ================================================================

def _fix_log_csv_path() -> str:
    return os.path.join(get_log_dir(), "fix_log_all.csv")


def append_fix_log(issue: str, location: str, pallet_id: str, before_qty: int, after_qty: int, note: str):
    """
    Append a single fix record with 'Issue' column required by Carlos.
    """
    row = pd.DataFrame([{
        "Timestamp": now_ts(),
        "Issue": issue,
        "Location": location,
        "PalletID": pallet_id,
        "BeforeQTY": before_qty,
        "AfterQTY": after_qty,
        "Note": note,
    }])
    safe_csv_append(row, _fix_log_csv_path())


def read_fix_log() -> pd.DataFrame:
    path = _fix_log_csv_path()
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Timestamp", "Issue", "Location", "PalletID", "BeforeQTY", "AfterQTY", "Note"])
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["Timestamp", "Issue", "Location", "PalletID", "BeforeQTY", "AfterQTY", "Note"])


# ================================================================
# Natural Language Query (AI NLQ Beta)
# ================================================================

def _get_ai_provider() -> str:
    """
    Detect AI provider: 'openai', 'azure_openai', or '' if none.
    We check streamlit secrets and env implicitly.
    """
    # Prioritize Azure if azure env present
    if os.getenv("AZURE_OPENAI_API_KEY") or st.secrets.get("AZURE_OPENAI_API_KEY", None):
        return "azure_openai"
    if os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", None):
        return "openai"
    return ""


def llm_parse_to_plan(query: str, provider: str) -> Dict[str, Any]:
    """
    Convert a natural language query into a safe executable plan.
    If provider keys not available, raise to trigger regex fallback.
    Plan example:
    {
      "action": "filter_bulk",
      "conditions": {"qty_lte": 5},
      "sort": {"by": "QTY", "ascending": True},
      "limit": 100
    }
    """
    if not provider:
        raise RuntimeError("No AI provider configured")

    # NOTE: We are not actually calling external APIs in this code artifact;
    # we outline how you'd call them. Implementation is stubbed to keep app runnable.
    # You can wire real calls using openai / azure openai SDKs if desired.
    # For now, we create a simple heuristic mapping to a plan.
    q = query.lower().strip()

    # Some light patterns first; real LLM call goes here if you wire keys.
    if "bulk" in q and ("five" in q or "≤5" in q or "<=5" in q or "less than 6" in q or "qty <= 5" in q):
        return {
            "action": "filter_bulk",
            "conditions": {"qty_lte": 5},
            "sort": {"by": "QTY", "ascending": True},
            "limit": 200
        }
    if "show" in q and "racks" in q and ("empty" in q or "full" in q):
        return {"action": "show_racks_summary"}

    # Default generic response
    return {"action": "help"}


def regex_parse_to_plan(query: str) -> Dict[str, Any]:
    """
    Fallback parser using regex patterns when AI not available.
    """
    q = query.lower()
    if re.search(r"(bulk).*(≤|<=|less than|under)\s*6|qty\s*<=?\s*5|five|5", q):
        return {
            "action": "filter_bulk",
            "conditions": {"qty_lte": 5},
            "sort": {"by": "QTY", "ascending": True},
            "limit": 200
        }
    if re.search(r"show.*racks.*(empty|full)", q):
        return {"action": "show_racks_summary"}
    if re.search(r"duplicates|duplicate pallets|dupe|same pallet", q):
        return {"action": "find_duplicates"}
    if re.search(r"missing|damage", q):
        return {"action": "show_damage_missing"}
    # Default
    return {"action": "help"}


def execute_plan(plan: Dict[str, Any], inv: pd.DataFrame, master: pd.DataFrame) -> Tuple[str, pd.DataFrame]:
    """
    Executes a parsed plan and returns (message, dataframe).
    """
    action = plan.get("action", "help")
    if action == "filter_bulk":
        df = inv.copy()
        df = df[~df["Location"].astype(str).str.startswith(RACK_PREFIX)]
        c = plan.get("conditions", {})
        if "qty_lte" in c:
            df = df[df["QTY"] <= int(c["qty_lte"])]
        sort = plan.get("sort", {})
        if sort:
            by = sort.get("by", "QTY")
            asc = bool(sort.get("ascending", True))
            if by in df.columns:
                df = df.sort_values(by=by, ascending=asc)
        if "limit" in plan:
            df = df.head(int(plan["limit"]))
        return ("Bulk pallets filtered by plan.", df)

    if action == "show_racks_summary":
        re_cnt, rf_cnt = compute_racks_empty_vs_full(master, inv)
        msg = f"Racks — Empty: {re_cnt} | Full/Used: {rf_cnt}"
        return (msg, pd.DataFrame({"Metric": ["Empty", "Full/Used"], "Count": [re_cnt, rf_cnt]}))

    if action == "find_duplicates":
        dup = find_duplicate_pallets(inv)
        return ("Duplicate pallets found:" if not dup.empty else "No duplicate pallets.", dup)

    if action == "show_damage_missing":
        df = inv.copy()
        mask = df["Location"].str.upper().str.contains("DAMAGE|MISSING", na=False)
        df = df[mask]
        return ("Damages & Missing rows:", df)

    return ("Try queries like 'show bulk with 5 or fewer', 'show racks empty vs full', 'find duplicates'.", pd.DataFrame())


# ================================================================
# UI Utilities (Theme, Animations, Sparklines, Toasts)
# ================================================================

def inject_base_styles():
    # Neon theme + dark mode styling
    st.markdown(
        f"""
        <style>
        :root {{
            --primary-blue: {PRIMARY_BLUE};
            --primary-red: {PRIMARY_RED};
            --neon: {NEON_ACCENT};
        }}
        .neon-card {{
            border-radius: 12px;
            padding: 12px 16px;
            background: linear-gradient(135deg, rgba(10,10,10,0.8), rgba(20,20,20,0.8));
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 0 16px rgba(0,229,255,0.15);
            transition: transform .2s ease, box-shadow .2s ease;
        }}
        .neon-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 0 22px rgba(0,229,255,0.25);
        }}
        .kpi-value {{
            font-size: 2rem;
            font-weight: 700;
            color: #fff;
        }}
        .kpi-label {{
            font-size: 0.9rem;
            color: #ddd;
        }}
        .pill-btn {{
            border-radius: 20px;
            padding: 6px 12px;
            border: 1px solid rgba(255,255,255,0.15);
            color: #fff !important;
        }}
        .tab-bar .stTabs [data-baseweb="tab-list"] {{
            gap: 10px;
        }}
        .tab-bar .stTabs [data-baseweb="tab"] {{
            background: rgba(255,255,255,0.06);
            border-radius: 10px;
            padding: 8px 12px;
        }}
        .sparkline-box {{
            height: 40px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def sparkline(data: List[int], color: str = PRIMARY_BLUE):
    fig, ax = plt.subplots(figsize=(2.5, 0.6))
    ax.plot(data, color=color, linewidth=2)
    ax.axis("off")
    st.pyplot(fig, clear_figure=True)


def render_kpi_card(label: str, value: int, color: str = PRIMARY_BLUE, spark: Optional[List[int]] = None, key: str = ""):
    with st.container():
        st.markdown('<div class="neon-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-label">{label}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-value" style="color:{color}">{value}</div>', unsafe_allow_html=True)
        if spark:
            sparkline(spark, color=color)
        st.markdown('</div>', unsafe_allow_html=True)


def aggrid_table(df: pd.DataFrame, height: int = 320, key: str = "grid", enable_selection: bool = False):
    if df is None or df.empty:
        st.info("No data.")
        return None
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_pagination(enabled=True, paginationAutoPageSize=False, paginationPageSize=50)
    gb.configure_side_bar()
    gb.configure_default_column(resizable=True, filter=True, sortable=True)
    if enable_selection:
        gb.configure_selection(selection_mode="single", use_checkbox=True)
    # Use blue/red accents via CSS theme (balham is fine)
    grid_options = gb.build()
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.SELECTION_CHANGED if enable_selection else GridUpdateMode.NO_UPDATE,
        theme=AGGRID_THEME,
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=False,
        enable_enterprise_modules=False,
        key=key,
    )
    return grid_response


# ================================================================
# Data Loading & Refresh Controls
# ================================================================

@dataclass
class FileConfig:
    path: str = ""
    sheet: str = ""
    header_row_index: int = 0
    mapping_name: str = ""  # key to saved mappings


def file_config_panel(label: str, default_path: str, saved_maps: Dict[str, Dict[str, str]], state_key: str) -> Tuple[FileConfig, Dict[str, str]]:
    """
    Renders a file configuration panel for a specific source (inventory, master).
    Returns (config, mapping).
    """
    with st.expander(f"{label} — File & Mapping", expanded=False):
        path = st.text_input(f"{label} File Path", value=_ss_get(f"{state_key}_path", default_path), key=f"{state_key}_path_text")
        _ss_set(f"{state_key}_path", path)
        sheets = detect_sheets(path) if path else []
        sheet = st.selectbox(f"{label} Sheet", options=[""] + sheets, index=0, key=f"{state_key}_sheet")
        header_row = st.number_input(f"{label} Header Row (0-based)", min_value=0, max_value=100, value=int(_ss_get(f"{state_key}_hdr", 0)), step=1, key=f"{state_key}_hdr_num")

        df_preview = read_excel_with_selector(path, sheet if sheet else None, header_row)
        st.caption(f"Preview top rows for {label}:")
        st.dataframe(df_preview.head(5), use_container_width=True)

        proposed = propose_mapping(df_preview) if not df_preview.empty else {k: "" for k in REQUIRED_COLS.keys()}
        mapping_name = st.text_input(f"{label} Mapping Name (save/load)", value=_ss_get(f"{state_key}_mapname", label.lower()), key=f"{state_key}_mapname_text")

        # Load mapping if exists
        loaded_mapping = saved_maps.get(mapping_name, proposed)
        st.write("Column Mapping:")
        m = {}
        cols = [""] + list(df_preview.columns)
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            m["Location"] = st.selectbox("Location →", options=cols, index=cols.index(loaded_mapping.get("Location", "")) if loaded_mapping.get("Location", "") in cols else 0, key=f"{state_key}_map_loc")
        with col2:
            m["SKU"] = st.selectbox("SKU →", options=cols, index=cols.index(loaded_mapping.get("SKU", "")) if loaded_mapping.get("SKU", "") in cols else 0, key=f"{state_key}_map_sku")
        with col3:
            m["LOT"] = st.selectbox("LOT Number →", options=cols, index=cols.index(loaded_mapping.get("LOT", "")) if loaded_mapping.get("LOT", "") in cols else 0, key=f"{state_key}_map_lot")
        with col4:
            m["PalletID"] = st.selectbox("Pallet ID →", options=cols, index=cols.index(loaded_mapping.get("PalletID", "")) if loaded_mapping.get("PalletID", "") in cols else 0, key=f"{state_key}_map_pallet")
        with col5:
            m["QTY"] = st.selectbox("QTY →", options=cols, index=cols.index(loaded_mapping.get("QTY", "")) if loaded_mapping.get("QTY", "") in cols else 0, key=f"{state_key}_map_qty")

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button(f"Save {label} Mapping", key=f"{state_key}_save_mapping", use_container_width=True):
                saved_maps[mapping_name] = m
                save_mappings(saved_maps)
                _ss_set(f"{state_key}_mapname", mapping_name)
                st.success(f"Saved mapping '{mapping_name}'")
        with c2:
            if st.button(f"Reload Proposed", key=f"{state_key}_reload_prop", use_container_width=True):
                # Just a toast; UI will update next rerun anyway
                st.toast("Reloaded proposed mapping. Adjust as needed.", icon="🔁")
        with c3:
            st.caption("Save a named mapping to reuse later.")

        cfg = FileConfig(path=path, sheet=sheet, header_row_index=int(header_row), mapping_name=mapping_name)
        return cfg, m


def load_and_prepare_inventory(inv_cfg: FileConfig, inv_map: Dict[str, str]) -> pd.DataFrame:
    """
    Load the inventory Excel and apply mapping/normalization.
    """
    df = read_excel_with_selector(inv_cfg.path, inv_cfg.sheet if inv_cfg.sheet else None, inv_cfg.header_row_index)
    if df.empty:
        return df
    df = apply_mapping(df, inv_map)
    df = normalize_inventory_df(df)
    return df


def load_and_prepare_master(master_cfg: FileConfig, master_map: Dict[str, str]) -> pd.DataFrame:
    """
    Load the master locations Excel and apply mapping.
    """
    df = read_excel_with_selector(master_cfg.path, master_cfg.sheet if master_cfg.sheet else None, master_cfg.header_row_index)
    if df.empty:
        return df
    df = apply_mapping(df, master_map)
    # master only needs Location column
    if "Location" in df.columns:
        df = df[["Location"]].drop_duplicates()
    return df


def refresh_controls(inv_cfg: FileConfig, master_cfg: FileConfig):
    """
    Render refresh controls: show active log path, file mtimes, Refresh Now button.
    Auto-refresh if files changed.
    """
    st.subheader("Data Refresh")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        log_dir = get_log_dir()
        st.caption("Log dir")
        st.code(log_dir, language="text")
    with c2:
        st.caption("Inventory mtime")
        st.code(dt.datetime.fromtimestamp(get_file_mtime(inv_cfg.path)).strftime("%Y-%m-%d %I:%M:%S %p") if inv_cfg.path and os.path.exists(inv_cfg.path) else "N/A", language="text")
    with c3:
        st.caption("Master mtime")
        st.code(dt.datetime.fromtimestamp(get_file_mtime(master_cfg.path)).strftime("%Y-%m-%d %I:%M:%S %p") if master_cfg.path and os.path.exists(master_cfg.path) else "N/A", language="text")
    with c4:
        if st.button("Refresh Now", type="primary", use_container_width=True):
            st.experimental_rerun()

    # Auto-refresh on file change or TTL
    inv_changed = file_has_changed(inv_cfg.path, "inv_mtime") if inv_cfg.path else False
    master_changed = file_has_changed(master_cfg.path, "master_mtime") if master_cfg.path else False
    last_ttl = float(_ss_get("last_ttl_refresh", 0.0))
    now = time.time()
    ttl_due = (now - last_ttl) >= AUTO_REFRESH_TTL_SEC

    if inv_changed or master_changed or ttl_due:
        _ss_set("last_ttl_refresh", now)
        st.toast("Auto-refresh triggered (file change or TTL).", icon="🔄")
        st.experimental_rerun()


# ================================================================
# Damage/Missing Slideshow
# ================================================================

def render_damage_missing_slideshow(inv: pd.DataFrame):
    """
    Small slideshow: cycles through damage & missing entries with counts.
    """
    df = inv.copy()
    if df.empty:
        st.info("No data for slideshow.")
        return
    damage_df = df[df["Location"].str.upper().str.contains("DAMAGE", na=False)]
    missing_df = df[df["Location"].str.upper().str.contains("MISSING", na=False)]

    slides = []
    for label, subset in [("DAMAGE", damage_df), ("MISSING", missing_df)]:
        if subset.empty:
            continue
        sums = subset.groupby("Location", dropna=False)["QTY"].sum().reset_index().sort_values(by="QTY", ascending=False).head(10)
        slides.append((label, sums))

    if not slides:
        st.info("No DAMAGE/MISSING entries to show.")
        return

    idx = int(time.time() // 5) % len(slides)  # rotate every 5 seconds across reruns
    label, sums = slides[idx]
    st.write(f"**{label} Top Locations (rotates every 5s)**")
    st.dataframe(sums, use_container_width=True)


# ================================================================
# Bulk Locations UI (row select → dropdown of pallets)
# ================================================================

def render_bulk_locations(inv: pd.DataFrame):
    st.subheader("Bulk Locations")
    bulk = inv[~inv["Location"].astype(str).str.startswith(RACK_PREFIX)].copy()
    if bulk.empty:
        st.info("No bulk rows.")
        return

    # Location summary
    loc_summary = bulk.groupby("Location", dropna=False).agg(
        Pallets=("PalletID", "nunique"),
        TotalQTY=("QTY", "sum")
    ).reset_index().sort_values(by=["Pallets", "TotalQTY"], ascending=[False, False])

    st.caption("Select a location row to inspect pallets.")
    resp = aggrid_table(loc_summary, height=340, key="bulk_loc_grid", enable_selection=True)

    selected_loc = None
    if resp and resp.get("selected_rows"):
        selected_loc = resp["selected_rows"][0].get("Location", None)

    if selected_loc:
        st.success(f"Selected location: {selected_loc}")
        subset = bulk[bulk["Location"] == selected_loc].copy()
        if subset.empty:
            st.info("No pallets at this location.")
            return
        # Dropdown listing all pallets in that location, sorted by QTY ascending
        subset = subset.sort_values(by="QTY", ascending=True)
        pallet_list = subset["PalletID"].tolist()
        default_idx = 0 if pallet_list else None
        sel_pallet = st.selectbox("Pallets at selected location (sorted by QTY asc)", options=pallet_list, index=default_idx)
        if sel_pallet:
            row = subset[subset["PalletID"] == sel_pallet].head(1)
            st.write("Pallet Details:")
            st.dataframe(row, use_container_width=True)

    st.divider()
    st.subheader("Flat Pallet List (Bulk)")
    c1, c2 = st.columns(2)
    with c1:
        qty_lte = st.number_input("Filter: QTY ≤", min_value=0, max_value=999999, value=5, step=1)
    with c2:
        st.caption("Defaults to 5 as requested.")
    flat = bulk.copy()
    flat = flat[flat["QTY"] <= qty_lte]
    flat = flat.sort_values(by=["QTY", "Location"], ascending=[True, True])
    aggrid_table(flat, height=360, key="bulk_flat_grid", enable_selection=False)


# ================================================================
# Dashboard Charts (existing + new ones)
# ================================================================

def plot_pie(labels: List[str], values: List[int], title: str, colors: List[str]):
    fig, ax = plt.subplots(figsize=(4.6, 4.6))
    ax.pie(values, labels=labels, colors=colors, autopct="%1.0f%%", startangle=140, textprops={"color": "white"})
    ax.set_title(title, color="white")
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#222222")
    st.pyplot(fig, clear_figure=True)


def plot_bar(categories: List[str], counts: List[int], title: str, color: str = PRIMARY_BLUE):
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.bar(categories, counts, color=color)
    ax.set_title(title, color="white")
    ax.tick_params(colors="white")
    for i, v in enumerate(counts):
        ax.text(i, v, str(v), ha="center", va="bottom", color="white", fontsize=10)
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#222222")
    st.pyplot(fig, clear_figure=True)


def render_new_charts(master: pd.DataFrame, inv: pd.DataFrame):
    st.subheader("New Charts")
    # Racks Empty vs Full
    re_cnt, rf_cnt = compute_racks_empty_vs_full(master, inv)
    c1, c2 = st.columns(2)
    with c1:
        plot_pie(["Racks Empty", "Racks Used"], [re_cnt, rf_cnt], "Racks: Empty vs Full", [PRIMARY_BLUE, PRIMARY_RED])
    # Bulk Used vs Empty
    be_cnt, bu_cnt = compute_bulk_used_vs_empty(master, inv)
    with c2:
        plot_pie(["Bulk Empty", "Bulk Used"], [be_cnt, bu_cnt], "Bulk: Empty vs Used", [PRIMARY_BLUE, PRIMARY_RED])


# ================================================================
# Main App (Part 2 implements full UI flow)
# ================================================================

# ================================================================
# App UI - Entry Point & Tabs
# ================================================================

def main():
    st.set_page_config(page_title=f"{APP_NAME} v{APP_VERSION}", layout="wide", page_icon="📦")
    inject_base_styles()

    # Header
    lh, rh = st.columns([3, 1])
    with lh:
        st.markdown(f"## {APP_NAME} — v{APP_VERSION}")
        st.caption("Neon warehouse theme • animations • AgGrid • saved mappings • AI NLQ (beta)")
    with rh:
        mode = st.toggle("Dark Theme", value=True)
        # For demo, we don't switch the actual Streamlit theme here; styles already dark

    if LOTTIE_AVAILABLE:
        with st.expander("✨ Animations", expanded=False):
            st_lottie(LOTTIE_DASHBOARD, height=120, key="lottie_dash")

    # Sidebar: AI NLQ + Quick Search
    with st.sidebar:
        st.markdown("### Ask Bin Helper (beta)")
        use_ai = st.toggle("Use AI NLQ (beta)", value=False, help="Converts natural language to a safe plan and executes it.")
        query = st.text_input("Type a query", value="", placeholder="e.g., show bulk with 5 or fewer")
        if st.button("Run", use_container_width=True):
            _ss_set("nlq_run", True)
            _ss_set("nlq_query", query)
            _ss_set("nlq_use_ai", use_ai)
        st.divider()
        st.markdown("### Quick Search")
        quick_sku = st.text_input("SKU contains", value="")
        quick_lot = st.text_input("LOT Number contains (digits only)", value="")
        quick_loc = st.text_input("Location starts with", value="")

    # Configuration: Files + Mappings
    st.markdown("### Data Sources & Mappings")

    saved_maps = load_saved_mappings()

    inv_cfg, inv_map = file_config_panel(
        label="Inventory",
        default_path=_ss_get("inv_default_path", ""),
        saved_maps=saved_maps,
        state_key="inv"
    )
    master_cfg, master_map = file_config_panel(
        label="Master Locations",
        default_path=_ss_get("master_default_path", ""),
        saved_maps=saved_maps,
        state_key="master"
    )

    # Load data
    inv_df = load_and_prepare_inventory(inv_cfg, inv_map)
    master_df = load_and_prepare_master(master_cfg, master_map)

    # Quick search filtering (non-destructive)
    filtered_inv = inv_df.copy()
    if quick_sku:
        filtered_inv = filtered_inv[filtered_inv["SKU"].str.contains(quick_sku, case=False, na=False)]
    if quick_lot:
        # Normalized digits; so contains on digits string
        filtered_inv = filtered_inv[filtered_inv["LOT"].str.contains(re.sub(r"\D+", "", quick_lot), na=False)]
    if quick_loc:
        filtered_inv = filtered_inv[filtered_inv["Location"].str.startswith(quick_loc)]

    # KPIs
    kpis = compute_kpis(master_df, inv_df)
    _ss_set("last_kpis", kpis)

    # Snapshots
    # read & maybe snapshot at startup and intervals
    trends_df = _read_trends()
    _ss_set("auto_snapshot_on_start", _ss_get("auto_snapshot_on_start", DEFAULT_AUTO_SNAPSHOT_ON_START))
    _ss_set("auto_snapshot_interval_min", _ss_get("auto_snapshot_interval_min", DEFAULT_AUTO_SNAPSHOT_INTERVAL_MIN))

    _maybe_startup_snapshot()
    _maybe_interval_snapshot()

    # KPI Row with clickable cards
    st.markdown("### KPIs")
    kc1, kc2, kc3, kc4, kc5, kc6 = st.columns(6)
    with kc1:
        if st.button(f"Empty Bins: {kpis['Empty Bins']}", key="kpi_empty", use_container_width=True):
            _ss_set("jump_to_tab", "Empty Bins")
    with kc2:
        if st.button(f"Empty Partial Bins: {kpis['Empty Partial Bins']}", key="kpi_empty_partial", use_container_width=True):
            _ss_set("jump_to_tab", "Empty Partial Bins")
    with kc3:
        if st.button(f"Full Pallet Bins: {kpis['Full Pallet Bins']}", key="kpi_full", use_container_width=True):
            _ss_set("jump_to_tab", "Full Pallet Bins")
    with kc4:
        if st.button(f"Empty Pallet Bins: {kpis['Empty Pallet Bins']}", key="kpi_empty_full", use_container_width=True):
            _ss_set("jump_to_tab", "Empty Pallet Bins")
    with kc5:
        if st.button(f"Damages: {kpis['Damages']}", key="kpi_damage", use_container_width=True):
            _ss_set("jump_to_tab", "Damages")
    with kc6:
        if st.button(f"Missing: {kpis['Missing']}", key="kpi_missing", use_container_width=True):
            _ss_set("jump_to_tab", "Missing")

    # Slideshow for damages & missing
    st.divider()
    render_damage_missing_slideshow(inv_df)
    st.divider()

    # Tabs
    tabs = st.tabs([
        "Dashboard",
        "Empty Bins",
        "Empty Partial Bins",
        "Full Pallet Bins",
        "Empty Pallet Bins",
        "Partial Bins",
        "Bulk Locations",
        "Discrepancies (All)",
        "Fix Log (All)",
        "Trends",
        "Data Refresh",
        "Ask Bin Helper (beta)"
    ])

    # Helper precomputations
    categorized = categorize_inventory_rows(inv_df)
    by_loc_qty = inv_df.groupby("Location", dropna=False)["QTY"].sum().reset_index()

    # ------------- Dashboard -------------
    with tabs[0]:
        st.subheader("Dashboard Overview")

        # Existing charts preserved (example placeholders)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.write("Existing Chart A (preserved)")
            # Provide a small bar as a placeholder of existing charts
            cats = ["Empty", "Full", "Partial"]
            vals = [
                kpis["Empty Bins"],
                kpis["Full Pallet Bins"],
                len(inv_df[inv_df["QTY"].between(1, PARTIAL_PALLET_MAX)])  # partial-ish
            ]
            plot_bar(cats, vals, "Bins Summary", PRIMARY_BLUE)
        with c2:
            st.write("Existing Chart B (preserved)")
            plot_pie(["Damages", "Missing"], [kpis["Damages"], kpis["Missing"]], "Damage vs Missing", [PRIMARY_ORANGE, PRIMARY_RED])
        with c3:
            st.write("Existing Chart C (preserved)")
            # simple distribution of QTY buckets
            buckets = pd.cut(inv_df["QTY"], bins=[-math.inf, 0, 5, 10, math.inf], labels=["0", "1-5", "6-10", "10+"])
            dist = buckets.value_counts().reindex(["0", "1-5", "6-10", "10+"], fill_value=0)
            plot_bar(list(dist.index.astype(str)), list(dist.values), "QTY Distribution", PRIMARY_PURPLE)

        st.divider()
        render_new_charts(master_df, inv_df)

    # ------------- Empty Bins -------------
    with tabs[1]:
        st.subheader("Empty Bins")
        # Use master reference: bins in master with qty <= 0
        inv_map_qty = {row["Location"]: int(row["QTY"]) for _, row in by_loc_qty.iterrows()}
        empties = []
        if "Location" in master_df.columns:
            for loc in master_df["Location"].astype(str).str.strip():
                if inv_map_qty.get(loc, 0) <= 0:
                    empties.append(loc)
        empty_df = pd.DataFrame({"Location": sorted(empties)})
        aggrid_table(empty_df, height=480, key="empty_bins")

    # ------------- Empty Partial Bins -------------
    with tabs[2]:
        st.subheader("Empty Partial Bins")
        empties = []
        if "Location" in master_df.columns:
            for loc in master_df["Location"].astype(str).str.strip():
                if is_partial_slot(loc) and by_loc_qty.set_index("Location")["QTY"].get(loc, 0) <= 0:
                    empties.append(loc)
        empty_partial_df = pd.DataFrame({"Location": sorted(empties)})
        aggrid_table(empty_partial_df, height=480, key="empty_partial_bins")

    # ------------- Full Pallet Bins -------------
    with tabs[3]:
        st.subheader("Full Pallet Bins")
        # Occupied 111*** or non-01 with Qty > 5
        flist = []
        for _, row in by_loc_qty.iterrows():
            loc = row["Location"]
            qty = int(row["QTY"])
            if not loc:
                continue
            if loc.startswith(RACK_PREFIX) and qty > 0:
                flist.append(loc)
            else:
                if len(loc) >= 2 and loc[-2:] != LEVEL_BOTTOM and qty > PARTIAL_PALLET_MAX:
                    flist.append(loc)
        full_df = pd.DataFrame({"Location": sorted(set(flist))})
        aggrid_table(full_df, height=480, key="full_pallet_bins")

    # ------------- Empty Pallet Bins -------------
    with tabs[4]:
        st.subheader("Empty Pallet Bins")
        empties = []
        if "Location" in master_df.columns:
            for loc in master_df["Location"].astype(str).str.strip():
                if is_full_pallet_bin(loc) and by_loc_qty.set_index("Location")["QTY"].get(loc, 0) <= 0:
                    empties.append(loc)
        empty_full_df = pd.DataFrame({"Location": sorted(empties)})
        aggrid_table(empty_full_df, height=480, key="empty_full_pallet_bins")

    # ------------- Partial Bins -------------
    with tabs[5]:
        st.subheader("Partial Bins")
        # ends with 01 (excluding starting 111 or TUN) and qty > 0
        plist = []
        for _, row in by_loc_qty.iterrows():
            loc = row["Location"]
            qty = int(row["QTY"])
            if not loc:
                continue
            if is_partial_slot(loc) and qty > 0:
                plist.append(loc)
        partial_df = pd.DataFrame({"Location": sorted(set(plist))})
        aggrid_table(partial_df, height=480, key="partial_bins")

    # ------------- Bulk Locations -------------
    with tabs[6]:
        render_bulk_locations(inv_df)

    # ------------- Discrepancies (All) -------------
    with tabs[7]:
        st.subheader("Discrepancies (All)")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Duplicate Pallets** (same PalletID in multiple locations)")
            dup_df = find_duplicate_pallets(inv_df)
            aggrid_table(dup_df, height=280, key="dup_pallets")
        with c2:
            st.markdown(f"**Rack > 1 Pallet** (violates ≤1 pallet rule in '{RACK_PREFIX}***')")
            multi_df = find_multi_pallet_per_rack(inv_df)
            aggrid_table(multi_df, height=280, key="rack_multi")

        st.divider()
        st.markdown("**Bulk Discrepancies** (SKU, LOT, PalletID only; MISSING hidden)")
        bulk_df = bulk_discrepancies(inv_df)
        aggrid_table(bulk_df, height=360, key="bulk_disc")

    # ------------- Fix Log (All) -------------
    with tabs[8]:
        st.subheader("Fix Log (All)")

        # Append new fix
        with st.expander("Add Fix Entry", expanded=False):
            issue = st.text_input("Issue (required)", value="", placeholder="Describe the issue")
            loc = st.text_input("Location", value="")
            pallet = st.text_input("Pallet ID", value="")
            before_qty = st.number_input("Before QTY", min_value=0, max_value=999999, value=0, step=1)
            after_qty = st.number_input("After QTY", min_value=0, max_value=999999, value=0, step=1)
            note = st.text_area("Note", value="", placeholder="Optional details")

            if st.button("Submit Fix", type="primary"):
                if not issue.strip():
                    st.error("Issue is required.")
                else:
                    append_fix_log(issue.strip(), loc.strip(), pallet.strip(), int(before_qty), int(after_qty), note.strip())
                    st.success("Fix entry saved.")
                    st.experimental_rerun()

        st.caption("Saved Fix Log entries:")
        fix_df = read_fix_log()
        aggrid_table(fix_df, height=420, key="fix_log_all")

    # ------------- Trends -------------
    with tabs[9]:
        st.subheader("Trends (Snapshots & Auto-Capture)")
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            auto_on = st.checkbox("Auto-snapshot on startup", value=_ss_get("auto_snapshot_on_start", DEFAULT_AUTO_SNAPSHOT_ON_START))
            _ss_set("auto_snapshot_on_start", auto_on)
        with c2:
            interval_min = st.number_input("Interval (minutes)", min_value=1, max_value=240, value=int(_ss_get("auto_snapshot_interval_min", DEFAULT_AUTO_SNAPSHOT_INTERVAL_MIN)), step=1)
            _ss_set("auto_snapshot_interval_min", interval_min)
        with c3:
            st.caption("Snapshots preserve KPIs over time for trend charts.")

        if st.button("Snapshot Now"):
            try:
                _snapshot_now(kpis)
                st.success("Snapshot captured.")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"Snapshot failed: {e}")

        # ------- NOTE: Indentation fixed here (keep df read inside the tab) -------
        df = _read_trends()
        if df is None or df.empty:
            st.warning("No trend data available yet. Create a snapshot to begin.")
        else:
            m1, m2 = st.columns([1, 3])
            with m1:
                metric_choice = st.selectbox(
                    "Metric",
                    ["Empty Bins", "Empty Partial Bins", "Full Pallet Bins", "Empty Pallet Bins", "Damages", "Missing"],
                    index=0
                )
            with m2:
                st.caption("Choose which KPI to chart over time.")

            try:
                fig = _plot_trend(df, metric_choice)
                st.pyplot(fig, clear_figure=True)
            except Exception as e:
                st.error(f"Trend plot failed: {e}")

            with st.expander("Snapshot Table", expanded=False):
                st.dataframe(df, use_container_width=True)

    # ------------- Data Refresh -------------
    with tabs[10]:
        refresh_controls(inv_cfg, master_cfg)

    # ------------- Ask Bin Helper (beta) -------------
    with tabs[11]:
        st.subheader("Ask Bin Helper (beta)")

        run = _ss_get("nlq_run", False)
        q = _ss_get("nlq_query", "")
        ua = _ss_get("nlq_use_ai", False)

        st.caption("Examples: 'show bulk with 5 or fewer', 'show racks empty vs full', 'find duplicates'")
        st.text_area("Current Query", value=q, disabled=True)

        plan = None
        msg = ""
        outdf = pd.DataFrame()

        if run and q.strip():
            try:
                if ua:
                    provider = _get_ai_provider()
                    plan = llm_parse_to_plan(q, provider)
                else:
                    raise RuntimeError("AI disabled")
            except Exception:
                plan = regex_parse_to_plan(q)

            msg, outdf = execute_plan(plan, inv_df, master_df)
            st.success(msg)
            st.code(json.dumps(plan, indent=2), language="json")
            aggrid_table(outdf, height=420, key="nlq_out")
        else:
            st.info("Enter a query in the left sidebar and click Run.")

    # Jump to tab if KPI clicked
    jump = _ss_get("jump_to_tab", "")
    if jump:
        st.experimental_set_query_params(tab=jump)
        _ss_set("jump_to_tab", "")


if __name__ == "__main__":
    main()