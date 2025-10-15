# app.py — Bin Helper (v2.9.0)
# Everything restored + AI NLQ (OpenAI/Azure), Master integration, mappings, filters, charts,
# Bulk row-select + pallet dropdown, discrepancies, slideshow, fix logs, refresh/file-watch.

import os
import re
import json
import time
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import numpy as np
import streamlit as st

# Optional libs
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
    AGGRID_AVAILABLE = True
except Exception:
    AGGRID_AVAILABLE = False

try:
    import altair as alt
    ALTAIR_AVAILABLE = True
except Exception:
    ALTAIR_AVAILABLE = False

try:
    from streamlit_lottie import st_lottie
    LOTTIE_AVAILABLE = True
except Exception:
    LOTTIE_AVAILABLE = False

# ========== Preferences & Constants ==========
PRIMARY_COLOR = "#1E88E5"  # blue
ACCENT_COLOR = "#E53935"   # red

LEVEL_BOTTOM = "01"  # rack level bottom suffix
FULL_PALLET_BOXES = 15
PARTIAL_PALLET_MAX_BOXES = 5

RACK_PREFIX = "111"   # all rack locations start with '111' for full pallet bins (even if 01)
IGNORE_PREFIXES = ("TUN",)  # ignore tunnels, etc.

DEFAULT_LOG_DIR_WIN = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
DEFAULT_LOG_DIR_CLOUD = "/mount/src/bin-helper/logs"

# Defaults for files (override in UI)
DEFAULT_INVENTORY_BALANCES = "INVENTORY_BALANCES.xlsx"
DEFAULT_ON_HAND_INVENTORY = "ON_HAND_INVENTORY.xlsx"
DEFAULT_MASTER_LOCATIONS = "Empty Bin Formula.xlsx"

# Secrets & env keys
ENV_OPENAI_KEY = "OPENAI_API_KEY"
ENV_AZURE_KEY = "AZURE_OPENAI_API_KEY"
ENV_AZURE_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
ENV_AZURE_DEPLOYMENT = "AZURE_OPENAI_DEPLOYMENT"
ENV_LOG_DIR = "BIN_HELPER_LOG_DIR"

# Page config
st.set_page_config(
    page_title="Bin Helper — Inventory Dashboard (AI NLQ)",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Theme/CSS
NEON_CSS = f"""
<style>
:root {{
  --primary: {PRIMARY_COLOR};
  --accent: {ACCENT_COLOR};
}}
.neon-card {{
  background: #0c0f14;
  border: 1px solid rgba(30,136,229,0.35);
  border-radius: 12px;
  padding: 14px 16px;
  box-shadow: 0 0 12px rgba(30,136,229,0.25), inset 0 0 8px rgba(30,136,229,0.2);
  color: #E6F0FA;
}}
.metric-number {{
  font-size: 1.8rem;
  font-weight: 700;
  color: #E6F0FA;
  text-shadow: 0 0 10px rgba(30,136,229,0.6);
}}
.metric-label {{
  color: #9EC9FF;
  font-weight: 600;
}}
.neon-card:hover {{
  transform: translateY(-1px);
  box-shadow: 0 0 14px rgba(30,136,229,0.35), inset 0 0 10px rgba(30,136,229,0.25);
}}
.kpi-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}}
.block-container {{
  padding-top: 12px !important;
}}
</style>
"""
st.markdown(NEON_CSS, unsafe_allow_html=True)

# ============================== Logging ==============================
def resolve_log_dir() -> str:
    path = os.environ.get(ENV_LOG_DIR) or st.secrets.get("bin_helper_log_dir", None)
    if path:
        os.makedirs(path, exist_ok=True)
        return path
    if os.name == "nt":
        path = DEFAULT_LOG_DIR_WIN
    else:
        path = DEFAULT_LOG_DIR_CLOUD
    os.makedirs(path, exist_ok=True)
    return path

LOG_DIR = resolve_log_dir()
LOG_FILE = os.path.join(LOG_DIR, f"bin_helper_{time.strftime('%Y%m%d')}.log")

def safe_log(msg: str):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

with st.sidebar:
    st.caption("**Logs:**")
    st.code(f"{LOG_FILE}", language="text")

# ============================== Small Utils ==============================
def file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0

def is_rack_location(loc: str) -> bool:
    s = str(loc or "")
    return s.startswith(RACK_PREFIX)

def is_ignored_location(loc: str) -> bool:
    s = str(loc or "")
    return any(s.startswith(p) for p in IGNORE_PREFIXES)
# ============================== File Helpers ==============================
def list_sheets(path: str) -> List[str]:
    try:
        engine = "openpyxl" if path.lower().endswith("xlsx") else "xlrd"
        with pd.ExcelFile(path, engine=engine) as xf:
            return xf.sheet_names
    except Exception as e:
        safe_log(f"list_sheets error on {path}: {e}")
        return []

@st.cache_data(show_spinner=False, ttl=300)
def load_excel(path: str, sheet: Optional[str] = None, header: int = 0) -> pd.DataFrame:
    engine = "openpyxl" if path.lower().endswith("xlsx") else "xlrd"
    try:
        return pd.read_excel(path, sheet_name=sheet, header=header, engine=engine)
    except Exception as e:
        safe_log(f"load_excel error on {path}: {e}")
        return pd.DataFrame()

def normalize_lot_number(x: Any) -> str:
    s = "" if pd.isna(x) else str(x)
    digits = re.sub(r"\D+", "", s)
    if digits == "": return ""
    digits = digits.lstrip("0")
    return digits if digits else "0"

def preserve_pallet_id(x: Any) -> str:
    return "" if pd.isna(x) else str(x)

def _get_col(df: pd.DataFrame, candidates_lower: List[str]) -> Optional[str]:
    for c in df.columns:
        cl = c.lower()
        if cl in candidates_lower:
            return c
    return None

# ============================== Column Mapping Model ==============================
# We persist mapping per file path
if "mappings" not in st.session_state:
    st.session_state["mappings"] = {}  # {path: {std_field: src_col}}

STD_FIELDS = ["Location", "Qty", "PalletID", "LOT", "SKU", "Zone", "MaxAllowed"]

def mapping_ui(label: str, path: str, df: pd.DataFrame):
    st.markdown(f"**{label} mapping**")
    cols = ["(none)"] + df.columns.astype(str).tolist()
    mapping = st.session_state["mappings"].get(path, {})

    def sel(std_name, default_candidates):
        default = mapping.get(std_name)
        if default not in cols:
            # try auto-detect
            auto = _get_col(df, [x.lower() for x in default_candidates])
            default = auto if auto in df.columns else "(none)"
        return st.selectbox(f"{std_name}", options=cols, index=cols.index(default) if default in cols else 0, key=f"{label}_{std_name}")

    loc = sel("Location", ["location", "locationname", "bin", "binlocation"])
    qty = sel("Qty", ["qty", "quantity", "onhandqty", "on_hand_qty"])
    pid = sel("PalletID", ["palletid", "pallet_id", "pallet", "palletnumber"])
    lot = sel("LOT", ["lot", "lotnumber", "lot_number"])
    sku = sel("SKU", ["sku", "item", "itemnumber", "product"])
    zone = sel("Zone", ["zone", "area", "section"])
    cap = sel("MaxAllowed", ["maxallowed", "max_allowed", "capacity"])

    st.session_state["mappings"][path] = {
        "Location": None if loc == "(none)" else loc,
        "Qty": None if qty == "(none)" else qty,
        "PalletID": None if pid == "(none)" else pid,
        "LOT": None if lot == "(none)" else lot,
        "SKU": None if sku == "(none)" else sku,
        "Zone": None if zone == "(none)" else zone,
        "MaxAllowed": None if cap == "(none)" else cap,
    }

def apply_mapping(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    """Return a new df with standardized columns when present."""
    df2 = df.copy()
    for std in STD_FIELDS:
        src = mapping.get(std)
        if src and src in df2.columns:
            df2[std] = df2[src]
    return df2
# ============================== Master Integration & Derivations ==============================
def ends_with_level01(loc: str) -> bool:
    s = str(loc or "")
    return s.endswith(LEVEL_BOTTOM)

def build_df_src(inv_df: pd.DataFrame, hand_df: pd.DataFrame, master_df: pd.DataFrame,
                 map_inv: Dict[str, Optional[str]],
                 map_hand: Dict[str, Optional[str]],
                 map_master: Dict[str, Optional[str]]) -> pd.DataFrame:
    """
    Use Master Locations as reference for empties:
    - Union master locations with any locations appearing in inv/on-hand (outer).
    - Compute Qty per location (sum or max depending on source; we'll use sum).
    - Apply rules (Empty Partial etc.) using standardized columns.
    """
    # Standardize
    inv = apply_mapping(inv_df, map_inv)
    hand = apply_mapping(hand_df, map_hand)
    mast = apply_mapping(master_df, map_master)

    # Prefer Inventory Balances; if empty, use On Hand
    src = inv if (not inv.empty and "Location" in inv.columns) else hand

    # Guard
    if src is None or src.empty or "Location" not in src.columns:
        # If master has no 'Location', we cannot proceed with master-based logic
        if mast.empty or "Location" not in mast.columns:
            return pd.DataFrame()  # caller will handle
        # Build shell from master only
        base = mast[["Location"]].drop_duplicates().copy()
        base["Qty"] = 0
    else:
        # Build aggregated qty by location
        if "Qty" in src.columns:
            agg = src.groupby("Location", dropna=False)["Qty"].sum(min_count=1).reset_index()
        else:
            tmp = src.copy()
            tmp["__qty_fallback"] = 0
            agg = tmp.groupby("Location", dropna=False)["__qty_fallback"].sum().reset_index().rename(columns={"__qty_fallback": "Qty"})
        base = agg.copy()

    # Merge with Master to bring all known locations
    if "Location" in mast.columns:
        all_locs = pd.DataFrame({"Location": mast["Location"].dropna().astype(str).unique()})
        base = all_locs.merge(base, on="Location", how="left")
        base["Qty"] = base["Qty"].fillna(0)
    else:
        base["Location"] = base["Location"].astype(str)

    # Bring additional fields (Zone, MaxAllowed) from master when available
    for extra in ["Zone", "MaxAllowed"]:
        if extra in mast.columns:
            # prefer first/mode value
            dm = mast.groupby("Location")[extra].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().shape[0] else "")
            base = base.merge(dm.reset_index(), on="Location", how="left")
        else:
            base[extra] = "" if extra != "MaxAllowed" else np.nan

    # Derive flags
    base["__is_rack"] = base["Location"].map(is_rack_location)
    base["__is_ignored"] = base["Location"].map(is_ignored_location)
    base["__is_level01"] = base["Location"].map(ends_with_level01)
    base["__qty"] = pd.to_numeric(base["Qty"], errors="coerce").fillna(0)

    # Apply normalization for any detailed rows later:
    # We still want detailed rows for Bulk Locations, Discrepancies, etc.
    detail = src.copy() if src is not None else pd.DataFrame()
    for col in ["LOT", "PalletID"]:
        if col in detail.columns:
            if col == "LOT":
                detail[col] = detail[col].map(normalize_lot_number)
            if col == "PalletID":
                detail[col] = detail[col].map(preserve_pallet_id)

    # Business rules:
    # - Empty Partial = ends with 01 (excluding 111/TUN), and Qty == 0
    # - Partial = ends with 01 (excluding 111/TUN) and Qty > 0
    # - Full = occupied 111*** or non-01 with Qty > 5
    # - Empty excludes Empty Partial
    base["__is_empty_partial"] = (~base["__is_rack"]) & (~base["__is_ignored"]) & (base["__is_level01"]) & (base["__qty"] <= 0)
    base["__is_partial"] = (~base["__is_rack"]) & (~base["__is_ignored"]) & (base["__is_level01"]) & (base["__qty"] > 0)
    base["__is_full"] = (base["__is_rack"] & (base["__qty"] > 0)) | ((~base["__is_level01"]) & (base["__qty"] > PARTIAL_PALLET_MAX_BOXES))
    base["__is_empty"] = (base["__qty"] <= 0) & (~base["__is_empty_partial"])
    base["__is_bulk"] = ~base["__is_rack"]

    # Attach references for other views
    base["__detail_rows_present"] = not detail.empty
    base.attrs["detail"] = detail  # stash for other tabs to use (Streamlit will keep in memory)

    return base

def compute_kpis(df: pd.DataFrame) -> Dict[str, int]:
    if df.empty:
        return dict(empty_bins=0, empty_partial_bins=0, full_pallet_bins=0, partial_bins=0, damages=0, missing=0)
    kpis = {
        "empty_bins": int((df["__is_empty"] & ~df["__is_ignored"]).sum()),
        "empty_partial_bins": int((df["__is_empty_partial"] & ~df["__is_ignored"]).sum()),
        "partial_bins": int((df["__is_partial"] & ~df["__is_ignored"]).sum()),
        "full_pallet_bins": int((df["__is_full"] & ~df["__is_ignored"]).sum()),
    }
    # Damages / Missing inferred from Location string in detail if available; otherwise from base
    damages = 0
    missing = 0
    if not df.empty:
        loc_series = df["Location"].astype(str)
        damages += int(loc_series.str.contains("DAMAGE|IBDAMAGE", case=False, na=False).sum())
        missing += int(loc_series.str.contains("MISSING", case=False, na=False).sum())
    kpis["damages"] = damages
    kpis["missing"] = missing
    return kpis
# ============================== AI NLQ Providers ==============================
def get_ai_provider() -> str:
    return st.session_state.get("ai_provider", "openai")

def _openai_client():
    try:
        from openai import OpenAI
        api_key = os.environ.get(ENV_OPENAI_KEY) or st.secrets.get("openai_api_key")
        if not api_key:
            return None
        return OpenAI(api_key=api_key)
    except Exception as e:
        safe_log(f"OpenAI client error: {e}")
        return None

def _azure_openai_client():
    try:
        from openai import AzureOpenAI
        api_key = os.environ.get(ENV_AZURE_KEY) or st.secrets.get("azure_openai_api_key")
        endpoint = os.environ.get(ENV_AZURE_ENDPOINT) or st.secrets.get("azure_openai_endpoint")
        api_version = st.secrets.get("azure_openai_api_version", "2024-02-01")
        if not api_key or not endpoint:
            return None
        return AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
    except Exception as e:
        safe_log(f"Azure OpenAI client error: {e}")
        return None

ALLOWED_DATASETS = {"bulk", "racks", "all"}
ALLOWED_OPS = {"eq", "neq", "lt", "lte", "gt", "gte", "contains", "startswith", "endswith", "in", "regex"}
FIELD_ALIASES = {
    "Location": ["location", "locationname", "bin", "binlocation"],
    "Qty": ["qty", "quantity", "onhandqty", "on_hand_qty"],
    "PalletID": ["palletid", "pallet_id", "pallet", "palletnumber"],
    "LOT": ["lot", "lotnumber", "lot_number"],
}

SYSTEM_PROMPT = """
You are a planner that converts natural-language warehouse inventory queries into a STRICT JSON plan.

Constraints:
- Output ONLY valid JSON, no markdown, no comments.
- Use keys: dataset ('bulk'|'racks'|'all'), filters (list of {field, op, value}), sort (list of {field, asc}), select (list), limit (int).
- Use only these operators: eq, neq, lt, lte, gt, gte, contains, startswith, endswith, in, regex.
- Field names must be from allowed schema or common aliases: Location, Qty, PalletID, LOT, SKU, Zone, PalletCount, MaxAllowed, EmptySlots and derived fields __is_rack, __is_bulk, __is_partial, __is_empty, __is_full.
- If user asks for "partial pallets", set a filter "__is_partial" eq true.
- If user asks "bulk", set dataset:"bulk". If "racks", dataset:"racks".
- If user asks "5 pallets or less", use filter PalletCount lte 5.
- Default limit = 500 unless user specifies a smaller/larger safe size.
"""

def _llm_chat_completion(provider: str, messages: List[Dict[str, str]], model: Optional[str] = None) -> Optional[str]:
    try:
        if provider == "openai":
            client = _openai_client()
            if not client: return None
            use_model = model or st.secrets.get("openai_model", "gpt-4o-mini")
            resp = client.chat.completions.create(model=use_model, messages=messages, temperature=0)
            return resp.choices[0].message.content
        elif provider == "azure":
            client = _azure_openai_client()
            if not client: return None
            deployment = os.environ.get(ENV_AZURE_DEPLOYMENT) or st.secrets.get("azure_openai_deployment")
            if not deployment:
                safe_log("Missing AZURE_OPENAI_DEPLOYMENT / secrets.azure_openai_deployment")
                return None
            resp = client.chat.completions.create(model=deployment, messages=messages, temperature=0)
            return resp.choices[0].message.content
        else:
            return None
    except Exception as e:
        safe_log(f"LLM completion error ({provider}): {e}")
        return None

def validate_plan(plan: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(plan, dict): return False, "Plan must be a JSON object."
    ds = plan.get("dataset", "all")
    if ds not in ALLOWED_DATASETS: return False, f"Invalid dataset: {ds}"
    filters = plan.get("filters", [])
    if not isinstance(filters, list): return False, "filters must be a list."
    for f in filters:
        if not isinstance(f, dict): return False, "Each filter must be an object."
        if f.get("op") not in ALLOWED_OPS: return False, f"Unsupported operator: {f.get('op')}"
        if not isinstance(f.get("field"), str): return False, "field must be a string."
    limit = plan.get("limit", 500)
    if not isinstance(limit, int) or limit < 1 or limit > 20000:
        return False, "limit must be 1..20000"
    return True, ""

def llm_parse_to_plan(user_query: str) -> Optional[Dict[str, Any]]:
    provider = get_ai_provider()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user", "content": user_query.strip()},
    ]
    content = _llm_chat_completion(provider, messages)
    if not content:
        return None
    try:
        plan = json.loads(content)
    except Exception as e:
        safe_log(f"Plan JSON parse error: {e} | Content head: {content[:200]}")
        return None
    ok, err = validate_plan(plan)
    if not ok:
        safe_log(f"Plan validation failed: {err} | Plan: {plan}")
        return None
    return plan

def regex_fallback(user_query: str) -> Dict[str, Any]:
    q = (user_query or "").lower()
    dataset = "all"
    if "bulk" in q:
        dataset = "bulk"
    elif "rack" in q or "racks" in q:
        dataset = "racks"

    filters = []
    if "partial" in q and "pallet" in q:
        filters.append({"field": "__is_partial", "op": "eq", "value": True})
    if "full" in q and "pallet" in q:
        filters.append({"field": "__is_full", "op": "eq", "value": True})
    if "empty" in q:
        filters.append({"field": "__is_empty", "op": "eq", "value": True})

    m_lte = re.search(r"(\d+)\s*(pallets?)?\s*(or\s*less|<=|at\s*most)", q)
    m_gte = re.search(r"(\d+)\s*(pallets?)?\s*(or\s*more|>=|at\s*least)", q)
    m_between = re.search(r"between\s*(\d+)\s*and\s*(\d+)", q)
    m_eq = re.search(r"\bexactly\s*(\d+)\s*pallets?\b", q)
    if m_between:
        a, b = int(m_between.group(1)), int(m_between.group(2))
        filters += [{"field": "PalletCount", "op": "gte", "value": min(a, b)},
                    {"field": "PalletCount", "op": "lte", "value": max(a, b)}]
    elif m_lte:
        filters.append({"field": "PalletCount", "op": "lte", "value": int(m_lte.group(1))})
    elif m_gte:
        filters.append({"field": "PalletCount", "op": "gte", "value": int(m_gte.group(1))})
    elif m_eq:
        filters.append({"field": "PalletCount", "op": "eq", "value": int(m_eq.group(1))})

    return {"dataset": dataset, "filters": filters, "sort": [], "select": [], "limit": 500}

def resolve_field_name(df: pd.DataFrame, req_field: str) -> Optional[str]:
    if req_field in df.columns:
        return req_field
    if req_field in {"__is_rack", "__is_bulk", "__is_partial", "__is_empty", "__is_full"} and req_field in df.columns:
        return req_field
    aliases = FIELD_ALIASES.get(req_field, [])
    for a in aliases:
        for c in df.columns:
            if c.lower() == a:
                return c
    for c in df.columns:
        if c.lower() == req_field.lower():
            return c
    return None

def apply_filter(df: pd.DataFrame, field: str, op: str, value: Any) -> pd.Series:
    s = df[field]
    try:
        if op == "eq":   return s.astype(str) == str(value) if s.dtype == "object" else s == value
        if op == "neq":  return s.astype(str) != str(value) if s.dtype == "object" else s != value
        if op == "lt":   return pd.to_numeric(s, errors="coerce") < float(value)
        if op == "lte":  return pd.to_numeric(s, errors="coerce") <= float(value)
        if op == "gt":   return pd.to_numeric(s, errors="coerce") > float(value)
        if op == "gte":  return pd.to_numeric(s, errors="coerce") >= float(value)
        if op == "contains":   return s.astype(str).str.contains(str(value), case=False, na=False)
        if op == "startswith": return s.astype(str).str.startswith(str(value))
        if op == "endswith":   return s.astype(str).str.endswith(str(value))
        if op == "in":
            vals = value if isinstance(value, list) else [value]
            vals = set(map(str, vals))
            return s.astype(str).isin(vals)
        if op == "regex":
            pat = str(value)
            if re.search(r"[^\w\s\.\*\+\?\|\(\)\[\]\{\}\-]", pat):
                return pd.Series([False] * len(df), index=df.index)
            return s.astype(str).str.contains(pat, regex=True, na=False)
    except Exception:
        return pd.Series([False] * len(df), index=df.index)
    return pd.Series([True] * len(df), index=df.index)

def execute_plan(plan: Dict[str, Any], df_all: pd.DataFrame) -> pd.DataFrame:
    ds = plan.get("dataset", "all")
    filters = plan.get("filters", [])
    sort = plan.get("sort", [])
    select = plan.get("select", [])
    limit = plan.get("limit", 500)

    df = df_all.copy()
    if ds == "bulk":
        df = df[df["__is_bulk"] & ~df["__is_ignored"]]
    elif ds == "racks":
        df = df[df["__is_rack"] & ~df["__is_ignored"]]
    else:
        df = df[~df["__is_ignored"]]

    for f in filters:
        req_field, op, val = f.get("field"), f.get("op"), f.get("value")
        if op not in ALLOWED_OPS or not isinstance(req_field, str):
            continue
        if req_field in {"__is_rack", "__is_bulk", "__is_partial", "__is_empty", "__is_full"}:
            if req_field in df.columns and isinstance(val, bool) and op == "eq":
                df = df[df[req_field] == val]
            continue
        field = resolve_field_name(df, req_field)
        if not field: continue
        mask = apply_filter(df, field, op, val)
        df = df[mask]

    # select & sort
    if select:
        cols = []
        for c in select:
            rc = resolve_field_name(df, c) or c
            if rc in df.columns and rc not in cols:
                cols.append(rc)
        if cols:
            df = df[cols]
    if sort:
        by = []
        ascending = []
        for srt in sort:
            rf = resolve_field_name(df, srt.get("field"))
            if rf:
                by.append(rf); ascending.append(bool(srt.get("asc", True)))
        if by:
            try:
                df = df.sort_values(by=by, ascending=ascending)
            except Exception:
                pass

    return df.head(limit)
# ============================== Sidebar Settings ==============================
with st.sidebar:
    st.subheader("🔎 Ask Bin Helper (AI NLQ)")
    use_ai = st.toggle("Use AI NLQ (beta)", value=True)
    provider = st.selectbox("AI Provider", ["openai", "azure", "none"], index=0 if use_ai else 2)
    st.session_state["ai_provider"] = provider if use_ai else "none"
    debug_ai = st.checkbox("Show JSON Plan (debug)", value=False)

    st.divider()
    st.subheader("⚙️ Data Sources")
    inv_bal_path = st.text_input("Inventory Balances file", value=DEFAULT_INVENTORY_BALANCES, key="p_inv")
    on_hand_path = st.text_input("On Hand Inventory file", value=DEFAULT_ON_HAND_INVENTORY, key="p_hand")
    master_path = st.text_input("Master Locations file", value=DEFAULT_MASTER_LOCATIONS, key="p_master")

    # File-change detection
    if "mtimes" not in st.session_state:
        st.session_state["mtimes"] = {}
    cur_mtimes = {
        "inv": file_mtime(inv_bal_path),
        "hand": file_mtime(on_hand_path),
        "mast": file_mtime(master_path),
    }
    changed = False
    for k, v in cur_mtimes.items():
        if st.session_state["mtimes"].get(k, 0) != v and v != 0:
            changed = True
            st.session_state["mtimes"][k] = v
    if changed:
        st.info("Detected file changes — clearing caches.")
        st.cache_data.clear()

    # Refresh Now
    if st.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.experimental_rerun()

    st.divider()
    st.subheader("📑 Sheet & Header")
    # Sheet selectors
    inv_sheets = list_sheets(inv_bal_path)
    hand_sheets = list_sheets(on_hand_path)
    mast_sheets = list_sheets(master_path)
    inv_sheet = st.selectbox("Inventory Balances Sheet", options=inv_sheets if inv_sheets else ["(default)"])
    hand_sheet = st.selectbox("On Hand Sheet", options=hand_sheets if hand_sheets else ["(default)"])
    mast_sheet = st.selectbox("Master Locations Sheet", options=mast_sheets if mast_sheets else ["(default)"])
    inv_header = st.number_input("Inventory header row (0-based)", min_value=0, max_value=50, value=0, step=1)
    hand_header = st.number_input("On Hand header row (0-based)", min_value=0, max_value=50, value=0, step=1)
    mast_header = st.number_input("Master header row (0-based)", min_value=0, max_value=50, value=0, step=1)

# ============================== Load Dataframes ==============================
df_inv_raw = load_excel(inv_bal_path, None if inv_sheet == "(default)" else inv_sheet, inv_header)
df_hand_raw = load_excel(on_hand_path, None if hand_sheet == "(default)" else hand_sheet, hand_header)
df_mast_raw = load_excel(master_path, None if mast_sheet == "(default)" else mast_sheet, mast_header)

# Preview / Mapping UI
st.subheader("🧭 Bin Helper")
st.caption("Inventory dashboard with AI natural-language queries + Master-based empty-bin logic")

p1, p2, p3 = st.columns(3)
with p1:
    st.markdown("**Inventory Balances preview**")
    st.dataframe(df_inv_raw.head(10), use_container_width=True, height=160)
with p2:
    st.markdown("**On Hand Inventory preview**")
    st.dataframe(df_hand_raw.head(10), use_container_width=True, height=160)
with p3:
    st.markdown("**Master Locations preview**")
    st.dataframe(df_mast_raw.head(10), use_container_width=True, height=160)

st.markdown("### Column Mapping")
c1, c2, c3 = st.columns(3)
with c1:
    mapping_ui("Inventory", inv_bal_path, df_inv_raw if not df_inv_raw.empty else pd.DataFrame(columns=["Location","Qty"]))
with c2:
    mapping_ui("OnHand", on_hand_path, df_hand_raw if not df_hand_raw.empty else pd.DataFrame(columns=["Location","Qty"]))
with c3:
    mapping_ui("Master", master_path, df_mast_raw if not df_mast_raw.empty else pd.DataFrame(columns=["Location"]))

map_inv = st.session_state["mappings"].get(inv_bal_path, {})
map_hand = st.session_state["mappings"].get(on_hand_path, {})
map_mast = st.session_state["mappings"].get(master_path, {})

# Build source dataframe (Master-aware)
df_src = pd.DataFrame()  # ensure defined
try:
    df_src = build_df_src(df_inv_raw, df_hand_raw, df_mast_raw, map_inv, map_hand, map_mast)
except Exception as e:
    safe_log(f"build_df_src error: {e}")
    df_src = pd.DataFrame()

if df_src.empty:
    st.warning("No data after applying master integration and mappings. Check files, sheets, headers, and column mappings.")
# ============================== Global Filters ==============================
# Build choices from detail rows if present
detail_rows = df_src.attrs.get("detail", pd.DataFrame())
def choices(col):
    if col in detail_rows.columns:
        vals = detail_rows[col].dropna().astype(str).unique().tolist()
        vals = sorted([v for v in vals if v.strip() != ""])
        return vals
    return []

st.markdown("### Global Filters")
gf1, gf2, gf3, gf4, gf5 = st.columns([2,1,1,1,1])
with gf1:
    qtext = st.text_input("Search (multi-column contains)", value="", placeholder="Type to search Location/SKU/LOT/PalletID ...")
with gf2:
    sel_loc = st.selectbox("Location", options=["(any)"] + (choices("Location") if not detail_rows.empty else []))
with gf3:
    sel_sku = st.selectbox("SKU", options=["(any)"] + (choices("SKU") if not detail_rows.empty else []))
with gf4:
    sel_lot = st.selectbox("LOT Number", options=["(any)"] + (choices("LOT") if not detail_rows.empty else []))
with gf5:
    sel_pid = st.selectbox("Pallet ID", options=["(any)"] + (choices("PalletID") if not detail_rows.empty else []))

def apply_global_filters(df_table: pd.DataFrame) -> pd.DataFrame:
    df2 = df_table.copy()
    # text search across common columns
    if qtext.strip():
        pat = re.escape(qtext.strip())
        cols = [c for c in ["Location", "SKU", "LOT", "PalletID", "Zone"] if c in df2.columns]
        if cols:
            mask = pd.Series(False, index=df2.index)
            for c in cols:
                mask = mask | df2[c].astype(str).str.contains(pat, case=False, na=False)
            df2 = df2[mask]
    # dropdown filters
    if sel_loc != "(any)" and "Location" in df2.columns:
        df2 = df2[df2["Location"].astype(str) == sel_loc]
    if sel_sku != "(any)" and "SKU" in df2.columns:
        df2 = df2[df2["SKU"].astype(str) == sel_sku]
    if sel_lot != "(any)" and "LOT" in df2.columns:
        df2 = df2[df2["LOT"].astype(str) == sel_lot]
    if sel_pid != "(any)" and "PalletID" in df2.columns:
        df2 = df2[df2["PalletID"].astype(str) == sel_pid]
    return df2

# ============================== KPI Cards (Clickable) ==============================
kpis = compute_kpis(df_src)

if "active_kpi" not in st.session_state:
    st.session_state["active_kpi"] = None

st.markdown('<div class="kpi-grid">', unsafe_allow_html=True)
kpi_cols = st.columns(6)
kpi_defs = [
    ("Empty Bins", "empty_bins", PRIMARY_COLOR),
    ("Empty Partial Bins", "empty_partial_bins", PRIMARY_COLOR),
    ("Full Pallet Bins", "full_pallet_bins", ACCENT_COLOR),
    ("Partial Bins", "partial_bins", ACCENT_COLOR),
    ("Damages", "damages", "#FFA726"),
    ("Missing", "missing", "#FFEE58"),
]
for i, (label, keyname, color) in enumerate(kpi_defs):
    with kpi_cols[i]:
        st.markdown(
            f"""
            <div class="neon-card">
              <div class="metric-label">{label}</div>
              <div class="metric-number" style="color:{color}">{kpis.get(keyname,0)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(f"View {label}", key=f"btn_{keyname}"):
            st.session_state["active_kpi"] = keyname
st.markdown('</div>', unsafe_allow_html=True)

# Show active KPI result under the cards
def df_for_kpi(name: str) -> pd.DataFrame:
    df = df_src.copy()
    if name == "empty_bins":
        return df[df["__is_empty"] & ~df["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]]
    if name == "empty_partial_bins":
        return df[df["__is_empty_partial"] & ~df["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]]
    if name == "full_pallet_bins":
        return df[df["__is_full"] & ~df["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]]
    if name == "partial_bins":
        return df[df["__is_partial"] & ~df["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]]
    if name == "damages":
        return df[df["Location"].astype(str).str.contains("DAMAGE|IBDAMAGE", case=False, na=False)][["Location","Qty"]]
    if name == "missing":
        return df[df["Location"].astype(str).str.contains("MISSING", case=False, na=False)][["Location","Qty"]]
    return pd.DataFrame()

if st.session_state["active_kpi"]:
    st.markdown(f"**Showing: {st.session_state['active_kpi'].replace('_',' ').title()}**")
    vdf = apply_global_filters(df_for_kpi(st.session_state["active_kpi"]))
    st.dataframe(vdf, use_container_width=True, height=240)

# ============================== Charts (New + Existing placeholder) ==============================
st.subheader("📊 Charts")

# New charts
racks = df_src[(df_src["__is_rack"]) & (~df_src["__is_ignored"])]
rack_empty = int((racks["__is_empty"]).sum())
rack_full = int((racks["__is_full"]).sum())

bulk = df_src[(df_src["__is_bulk"]) & (~df_src["__is_ignored"])]
bulk_used = int((bulk["__qty"] > 0).sum())
bulk_empty = int((bulk["__qty"] <= 0).sum())

c1, c2 = st.columns(2)
with c1:
    st.caption("Racks: Empty vs Full")
    data_rack = pd.DataFrame({"Category": ["Empty", "Full"], "Count": [rack_empty, rack_full]})
    if ALTAIR_AVAILABLE:
        st.altair_chart(
            alt.Chart(data_rack).mark_bar().encode(
                x=alt.X("Category:N", sort=["Empty","Full"]),
                y="Count:Q",
                color=alt.Color("Category:N", scale=alt.Scale(domain=["Empty","Full"], range=[PRIMARY_COLOR, ACCENT_COLOR]), legend=None),
            ).properties(height=220),
            use_container_width=True
        )
    else:
        st.bar_chart(data_rack.set_index("Category"))
with c2:
    st.caption("Bulk: Used vs Empty")
    data_bulk = pd.DataFrame({"Category": ["Used", "Empty"], "Count": [bulk_used, bulk_empty]})
    if ALTAIR_AVAILABLE:
        st.altair_chart(
            alt.Chart(data_bulk).mark_bar().encode(
                x=alt.X("Category:N", sort=["Used","Empty"]),
                y="Count:Q",
                color=alt.Color("Category:N", scale=alt.Scale(domain=["Used","Empty"], range=[ACCENT_COLOR, PRIMARY_COLOR]), legend=None),
            ).properties(height=220),
            use_container_width=True
        )
    else:
        st.bar_chart(data_bulk.set_index("Category"))

# Existing charts placeholder (preserved hook)
st.caption("Existing charts area (plug in your previous visuals here).")
# ============================== Tabs ==============================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Empty Bins",
    "Full Pallet Bins",
    "Empty Partial Bins",
    "Partial Bins",
    "Bulk Locations",
    "Ask Bin Helper",
    "Discrepancies"
])

def show_table(df: pd.DataFrame, key: str, height: int = 420, allow_select: bool = False):
    if df.empty:
        st.info("No rows.")
        return None
    if AGGRID_AVAILABLE:
        gob = GridOptionsBuilder.from_dataframe(df)
        gob.configure_default_column(resizable=True, sortable=True, filter=True)
        if allow_select:
            gob.configure_selection(selection_mode="single", use_checkbox=False)
        grid_options = gob.build()
        return AgGrid(df, gridOptions=grid_options, height=height, theme="streamlit",
                      update_mode=GridUpdateMode.SELECTION_CHANGED, key=key)
    else:
        st.dataframe(df, use_container_width=True, height=height)
        return None

with tab1:
    st.subheader("Empty Bins")
    dfv = apply_global_filters(df_src[df_src["__is_empty"] & ~df_src["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]])
    show_table(dfv, key="t_empty")

with tab2:
    st.subheader("Full Pallet Bins")
    dfv = apply_global_filters(df_src[df_src["__is_full"] & ~df_src["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]])
    show_table(dfv, key="t_full")

with tab3:
    st.subheader("Empty Partial Bins")
    dfv = apply_global_filters(df_src[df_src["__is_empty_partial"] & ~df_src["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]])
    show_table(dfv, key="t_empty_partial")

with tab4:
    st.subheader("Partial Bins")
    dfv = apply_global_filters(df_src[df_src["__is_partial"] & ~df_src["__is_ignored"]][["Location","Qty","Zone","MaxAllowed"]])
    show_table(dfv, key="t_partial")

# ============================== Bulk Locations ==============================
with tab5:
    st.subheader("Bulk Locations — row select + pallet dropdown")
    detail = df_src.attrs.get("detail", pd.DataFrame())
    if detail.empty or "Location" not in detail.columns:
        st.info("No detailed rows available from source. Check Inventory/OnHand mapping.")
    else:
        # Keep only bulk rows for location aggregation
        bulk_locs = df_src[(df_src["__is_bulk"]) & (~df_src["__is_ignored"])][["Location"]].drop_duplicates()

        d_bulk = detail.merge(bulk_locs, on="Location", how="inner").copy()

        # Normalize essential fields
        if "LOT" in d_bulk.columns:
            d_bulk["LOT"] = d_bulk["LOT"].map(normalize_lot_number)
        if "PalletID" in d_bulk.columns:
            d_bulk["PalletID"] = d_bulk["PalletID"].map(preserve_pallet_id)

        # Aggregate PalletCount
        if "PalletID" in d_bulk.columns:
            agg = d_bulk.groupby("Location")["PalletID"].nunique().reset_index(name="PalletCount")
        else:
            if "Qty" in d_bulk.columns:
                agg = d_bulk.groupby("Location")["Qty"].apply(lambda s: int((pd.to_numeric(s, errors="coerce") > 0).sum())).reset_index(name="PalletCount")
            else:
                agg = d_bulk.groupby("Location").size().reset_index(name="PalletCount")

        # Zone/MaxAllowed from df_src (since master projected onto df_src)
        agg = agg.merge(df_src[["Location","Zone","MaxAllowed"]], on="Location", how="left").drop_duplicates()
        agg = agg[["Location","Zone","PalletCount","MaxAllowed"]]
        agg = apply_global_filters(agg)

        grid_resp = show_table(agg.rename(columns={"Location":"LocationName"}), key="bulk_grid", allow_select=True)

        selected_location = None
        if grid_resp and grid_resp.get("selected_rows"):
            selected_location = grid_resp["selected_rows"][0].get("LocationName")

        if not selected_location:
            st.caption("Or choose a location:")
            selected_location = st.selectbox("Bulk Location", options=[""] + sorted(agg["Location"].astype(str).unique().tolist()))

        if selected_location:
            df_loc = d_bulk[d_bulk["Location"] == selected_location].copy()
            # Pallet dropdown labels
            chosen_pallet_id = None
            if "PalletID" in df_loc.columns:
                df_loc["__label"] = df_loc.apply(lambda r: " — ".join([str(r.get("PalletID","") or ""),
                                                                       str(r.get("SKU","") or ""),
                                                                       str(r.get("LOT","") or "")]).strip(" — "), axis=1)
                unique_pallets = df_loc.dropna(subset=["PalletID"]).drop_duplicates(subset=["PalletID"])
                label_map = dict(zip(unique_pallets["__label"], unique_pallets["PalletID"]))
                chosen_label = st.selectbox(f"Pallets in {selected_location}", options=[""] + sorted(label_map.keys()))
                chosen_pallet_id = label_map.get(chosen_label)
            else:
                st.info("No PalletID column; showing rows instead.")

            st.markdown("**Details (SKU, LOT, PalletID)**")
            fields = [c for c in ["SKU","LOT","PalletID"] if c in df_loc.columns]
            if chosen_pallet_id and "PalletID" in df_loc.columns:
                detail_df = df_loc[df_loc["PalletID"] == chosen_pallet_id]
                st.dataframe(detail_df[fields].drop_duplicates() if fields else detail_df.head(50), use_container_width=True, height=220)
            else:
                st.dataframe(df_loc[fields].drop_duplicates() if fields else df_loc.head(50), use_container_width=True, height=220)

# ============================== Ask Bin Helper (NLQ) ==============================
with tab6:
    st.subheader("Natural-language Query")
    q = st.text_input("e.g., 'show me partial pallets in bulk locations with 5 pallets or less'", key="nlq")
    run = st.button("Run Query", type="primary")

    if run and q.strip():
        plan = None
        used_fallback = False
        if get_ai_provider() != "none":
            plan = llm_parse_to_plan(q)
        if plan is None:
            st.warning("AI NLQ unavailable or failed. Falling back to regex.")
            plan = regex_fallback(q)
            used_fallback = True
        if debug_ai:
            st.code(json.dumps(plan, indent=2), language="json")
        result = execute_plan(plan, df_src)
        st.success(f"Returned {len(result)} rows (limit {plan.get('limit', 500)}).{' [regex fallback]' if used_fallback else ''}")
        st.dataframe(apply_global_filters(result), use_container_width=True, height=420)

# ============================== Discrepancies ==============================
with tab7:
    st.subheader("🧩 Discrepancies")
    t1, t2, t3 = st.tabs(["Discrepancies (All)", "Bulk Discrepancies", "Damages & Missing (Slideshow)"])

    # Helper to hide MISSING
    def hide_missing(df: pd.DataFrame) -> pd.DataFrame:
        return df[~df["Location"].astype(str).str.contains("MISSING", case=False, na=False)] if "Location" in df.columns else df

    base = df_src.copy()
    base = hide_missing(base)

    with t1:
        st.caption("Rules: Hide 'MISSING'. Racks must have ≤ 1 pallet. 'Duplicate Pallets' lives only here.")
        detail = df_src.attrs.get("detail", pd.DataFrame())
        if detail.empty:
            st.info("No detailed rows available to compute duplicates.")
        else:
            if "PalletID" not in detail.columns:
                st.info("No PalletID column; cannot compute duplicate pallets.")
            else:
                # Rack-only duplicates by distinct pallets at a rack location
                racks = base[base["__is_rack"] & ~base["__is_ignored"]][["Location"]].drop_duplicates()
                d_rack = detail.merge(racks, on="Location", how="inner")
                grp = d_rack.groupby("Location")["PalletID"].nunique().reset_index(name="DistinctPallets")
                dup = grp[grp["DistinctPallets"] > 1].sort_values("DistinctPallets", ascending=False)
                if dup.empty:
                    st.info("No rack duplicate-pallet discrepancies found.")
                else:
                    show_table(dup, key="dup_racks", height=260)
                    st.markdown("**Details**")
                    for _, row in dup.head(250).iterrows():
                        loc = row["Location"]
                        with st.expander(f"{loc} — {int(row['DistinctPallets'])} pallets"):
                            rows = d_rack[d_rack["Location"] == loc]
                            fields = [c for c in ["SKU","LOT","PalletID"] if c in rows.columns]
                            st.dataframe(rows[fields].drop_duplicates() if fields else rows, use_container_width=True, height=200)

    with t2:
        st.caption("Bulk discrepancies: clickable details show only SKU, LOT Number, Pallet ID.")
        detail = df_src.attrs.get("detail", pd.DataFrame())
        if detail.empty:
            st.info("No detailed rows available for bulk discrepancies.")
        else:
            bulk_locs = base[base["__is_bulk"] & ~base["__is_ignored"]][["Location"]].drop_duplicates()
            d_bulk = detail.merge(bulk_locs, on="Location", how="inner")
            # Build discrepancy as multiple distinct pallets for (Location, SKU, LOT)
            if "PalletID" in d_bulk.columns:
                keys = ["Location"] + ([ "SKU"] if "SKU" in d_bulk.columns else []) + ([ "LOT"] if "LOT" in d_bulk.columns else [])
                grp = d_bulk.groupby(keys)["PalletID"].nunique().reset_index(name="DistinctPallets")
                disc = grp[grp["DistinctPallets"] > 1].copy()
            else:
                keys = ["Location"] + ([ "SKU"] if "SKU" in d_bulk.columns else []) + ([ "LOT"] if "LOT" in d_bulk.columns else [])
                grp = d_bulk.groupby(keys).size().reset_index(name="RowCount")
                disc = grp[grp["RowCount"] > 1].rename(columns={"RowCount":"DistinctPallets"})

            if disc.empty:
                st.info("No bulk discrepancies found.")
            else:
                # Pretty names
                disc = disc.rename(columns={"LOT":"LOT Number"})
                display_cols = [c for c in ["Location","SKU","LOT Number","DistinctPallets"] if c in disc.columns]
                st.dataframe(disc[display_cols].sort_values(["Location","DistinctPallets"], ascending=[True, False]),
                             use_container_width=True, height=300)

                st.markdown("**Details**")
                for _, r in disc.head(250).iterrows():
                    loc_v = r.get("Location")
                    sku_v = r.get("SKU", None)
                    lot_v = r.get("LOT Number", None)
                    title = f"{loc_v}"
                    if sku_v not in (None, ""): title += f" · SKU {sku_v}"
                    if lot_v not in (None, ""): title += f" · LOT {lot_v}"
                    with st.expander(title):
                        rows = d_bulk[d_bulk["Location"] == loc_v]
                        if sku_v not in (None, "") and "SKU" in rows.columns:
                            rows = rows[rows["SKU"] == sku_v]
                        if lot_v not in (None, "") and "LOT" in rows.columns:
                            rows = rows[rows["LOT"] == lot_v]
                        fields = [c for c in ["SKU","LOT","PalletID"] if c in rows.columns]
                        st.dataframe(rows[fields].drop_duplicates() if fields else rows, use_container_width=True, height=200)

    with t3:
        st.caption("Rotating view of Damages & Missing")
        # Build slideshow source
        slides = []
        slides.append(("Damages", base[base["Location"].astype(str).str.contains("DAMAGE|IBDAMAGE", case=False, na=False)][["Location","Qty"]]))
        slides.append(("Missing", base[base["Location"].astype(str).str.contains("MISSING", case=False, na=False)][["Location","Qty"]]))

        if "slide_idx" not in st.session_state:
            st.session_state["slide_idx"] = 0

        # Auto-advance every 10 seconds (approx) via rerun trick
        st.experimental_set_query_params(ts=str(int(time.time() // 10)))  # lightweight auto-refresh tick
        if st.button("⏭ Next"):
            st.session_state["slide_idx"] = (st.session_state["slide_idx"] + 1) % len(slides)

        title, sdf = slides[st.session_state["slide_idx"]]
        st.markdown(f"**{title}**")
        if sdf.empty:
            st.info(f"No {title.lower()} to show.")
        else:
            st.dataframe(sdf.head(100), use_container_width=True, height=280)

# ============================== Fix Logs (Issue CSV append) ==============================
st.subheader("🛠 Fix Logs")
st.caption("Safe CSV append. Includes Issue field. Files saved under your log directory.")
with st.form("fix_log_form", clear_on_submit=True):
    f1, f2, f3 = st.columns(3)
    with f1:
        fl_location = st.text_input("Location")
        fl_sku = st.text_input("SKU")
    with f2:
        fl_lot = st.text_input("LOT Number")
        fl_pallet = st.text_input("Pallet ID")
    with f3:
        fl_issue = st.selectbox("Issue", ["Wrong Location", "Wrong LOT", "Wrong SKU", "Duplicate Pallet", "Count Error", "Other"])
    fl_notes = st.text_area("Notes")
    submitted = st.form_submit_button("Save Log")
    if submitted:
        try:
            log_csv = os.path.join(LOG_DIR, f"fix_logs_{time.strftime('%Y%m%d')}.csv")
            row = {
                "Timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
                "Location": fl_location, "SKU": fl_sku, "LOT Number": fl_lot,
                "Pallet ID": fl_pallet, "Issue": fl_issue, "Notes": fl_notes
            }
            exists = os.path.exists(log_csv)
            df_row = pd.DataFrame([row])
            if exists:
                df_row.to_csv(log_csv, mode="a", header=False, index=False, encoding="utf-8")
            else:
                df_row.to_csv(log_csv, index=False, encoding="utf-8")
            st.success(f"Saved to {log_csv}")
        except Exception as e:
            st.error(f"Failed to save log: {e}")
            safe_log(f"Fix log error: {e}")
# ============================== Footer ==============================
if LOTTIE_AVAILABLE:
    try:
        st_lottie({"v":"5.4.2","fr":60,"ip":0,"op":50,"w":400,"h":120,"nm":"footer","ddd":0,"assets":[],"layers":[]},
                  height=60, key="lottie_footer")
    except Exception:
        pass
st.caption("Neon Warehouse theme · AI NLQ (OpenAI/Azure) with regex fallback · Master-based empty logic · Auto-refresh & file watch")