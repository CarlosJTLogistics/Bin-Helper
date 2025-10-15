# app.py — Bin Helper (Backup v2.6.0, 2025-10-15 AM)
# AI NLQ (OpenAI + Azure), Bulk row-select + pallet dropdown, Charts, Discrepancies
# Original tabs & rules preserved.

import os
import re
import json
import time
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import numpy as np
import streamlit as st

# Optional packages; app should still run if they are missing
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

# ========== USER PREFERENCES & CONSTANTS ==========
PRIMARY_COLOR = "#1E88E5"  # blue
ACCENT_COLOR = "#E53935"   # red

LEVEL_BOTTOM = "01"  # rack level bottom suffix
FULL_PALLET_BOXES = 15
PARTIAL_PALLET_MAX_BOXES = 5

# Location code format: AAA BBB CC (e.g., 11100101)
RACK_PREFIX = "111"  # all rack locations start with '111' for full pallet bins (even if 01)
IGNORE_PREFIXES = ("TUN",)  # e.g., tunnel or other non-rack

# Logs (Bin Helper) preference
DEFAULT_LOG_DIR_WIN = r"C:\Users\carlos.pacheco.MYA-LOGISTICS\OneDrive - JT Logistics\bin-helper\logs"
DEFAULT_LOG_DIR_CLOUD = "/mount/src/bin-helper/logs"  # via BIN_HELPER_LOG_DIR/Secrets

# App defaults for files (can be overridden in UI)
DEFAULT_INVENTORY_BALANCES = "INVENTORY_BALANCES.xlsx"
DEFAULT_ON_HAND_INVENTORY = "ON_HAND_INVENTORY.xlsx"
DEFAULT_MASTER_LOCATIONS = "Empty Bin Formula.xlsx"  # used elsewhere but not merged in this backup

# Secrets & environment keys
ENV_OPENAI_KEY = "OPENAI_API_KEY"
ENV_AZURE_KEY = "AZURE_OPENAI_API_KEY"
ENV_AZURE_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
ENV_AZURE_DEPLOYMENT = "AZURE_OPENAI_DEPLOYMENT"
ENV_LOG_DIR = "BIN_HELPER_LOG_DIR"

# ============================== PAGE CONFIG ==============================
st.set_page_config(
    page_title="Bin Helper — Inventory Dashboard (AI NLQ)",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================ THEME / CSS GARNISH ============================
NEON_CSS = f"""
<style>
:root {{
  --primary: {PRIMARY_COLOR};
  --accent: {ACCENT_COLOR};
}}
/* Neon Card */
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
/* Micro hover animation */
.neon-card:hover {{
  transform: translateY(-1px);
  box-shadow: 0 0 14px rgba(30,136,229,0.35), inset 0 0 10px rgba(30,136,229,0.25);
}}
/* KPI grid */
.kpi-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}}
/* Tab bar alignment */
.block-container {{
  padding-top: 16px !important;
}}
</style>
"""
st.markdown(NEON_CSS, unsafe_allow_html=True)

# ============================== LOGGING UTILS ==============================
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

# Display active log path (as requested)
with st.sidebar:
    st.caption("**Logs:**")
    st.code(f"{LOG_FILE}", language="text")
# ============================== DATA LOADERS ==============================
@st.cache_data(show_spinner=False, ttl=300)
def load_excel(path: str, sheet: Optional[str] = None, header: int = 0) -> pd.DataFrame:
    engine = "openpyxl" if path.lower().endswith("xlsx") else "xlrd"
    try:
        return pd.read_excel(path, sheet_name=sheet, header=header, engine=engine)
    except Exception as e:
        safe_log(f"load_excel error on {path}: {e}")
        return pd.DataFrame()

# ============================== NORMALIZATION ==============================
def normalize_lot_number(x: Any) -> str:
    """LOT Number normalized to whole numeric strings (no decimals/letters),
    strip non-digits and leading zeros in display/logic."""
    s = "" if pd.isna(x) else str(x)
    digits = re.sub(r"\D+", "", s)
    if digits == "":
        return ""
    digits = digits.lstrip("0")
    return digits if digits else "0"

def preserve_pallet_id(x: Any) -> str:
    """Pallet IDs preserve alphanumeric characters (e.g., JTL00496) and not stripped."""
    return "" if pd.isna(x) else str(x)

def is_rack_location(loc: str) -> bool:
    s = str(loc or "")
    return s.startswith(RACK_PREFIX)

def is_ignored_location(loc: str) -> bool:
    s = str(loc or "")
    return any(s.startswith(p) for p in IGNORE_PREFIXES)

def _get_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in df.columns:
        cl = c.lower()
        if cl in candidates:
            return c
    return None

def determine_bin_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies Bin Helper rules:
    - Empty Partial = ends with 01 (excluding those starting with 111 or TUN), and Qty == 0
    - Partial = ends with 01 (excluding those starting with 111 or TUN) and Qty > 0
    - Full = occupied 111*** or non-01 with Qty > 5
    - Empty excludes Empty Partial
    Notes:
    - CaseCount is removed from all logic (per instructions)
    """
    df = df.copy()
    col_location = _get_col(df, ["location", "locationname", "bin", "binlocation"])
    col_qty = _get_col(df, ["qty", "quantity", "onhandqty", "on_hand_qty"])
    col_pallet = _get_col(df, ["palletid", "pallet_id", "pallet", "palletnumber"])
    col_lot = _get_col(df, ["lot", "lotnumber", "lot_number"])
    col_sku = _get_col(df, ["sku", "item", "itemnumber", "product"])

    if col_location is None:
        df["__error"] = "Missing Location column"
        return df

    if col_lot:
        df[col_lot] = df[col_lot].map(normalize_lot_number)
    if col_pallet:
        df[col_pallet] = df[col_pallet].map(preserve_pallet_id)

    def ends_with_level01(loc: str) -> bool:
        s = str(loc or "")
        return s.endswith(LEVEL_BOTTOM)

    def qty_val(x) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    q = df[col_qty].map(qty_val) if col_qty else pd.Series([0.0] * len(df), index=df.index)

    df["__loc_col"] = col_location
    df["__qty_col"] = col_qty or ""
    df["__pallet_col"] = col_pallet or ""
    df["__lot_col"] = col_lot or ""
    df["__sku_col"] = col_sku or ""

    df["__is_rack"] = df[col_location].map(is_rack_location)
    df["__is_ignored"] = df[col_location].map(is_ignored_location)
    df["__is_level01"] = df[col_location].map(ends_with_level01)
    df["__qty"] = q

    df["__is_empty_partial"] = (~df["__is_rack"]) & (~df["__is_ignored"]) & (df["__is_level01"]) & (df["__qty"] <= 0)
    df["__is_partial"] = (~df["__is_rack"]) & (~df["__is_ignored"]) & (df["__is_level01"]) & (df["__qty"] > 0)
    df["__is_full"] = (df["__is_rack"] & (df["__qty"] > 0)) | ((~df["__is_level01"]) & (df["__qty"] > PARTIAL_PALLET_MAX_BOXES))

    df["__is_empty"] = (df["__qty"] <= 0) & (~df["__is_empty_partial"])
    df["__is_bulk"] = ~df["__is_rack"]
    return df
def compute_kpis(df: pd.DataFrame) -> Dict[str, int]:
    if df.empty:
        return dict(empty_bins=0, empty_partial_bins=0, full_pallet_bins=0, partial_bins=0, damages=0, missing=0)

    kpis = {
        "empty_bins": int((df["__is_empty"] & ~df["__is_ignored"]).sum()),
        "empty_partial_bins": int((df["__is_empty_partial"] & ~df["__is_ignored"]).sum()),
        "partial_bins": int((df["__is_partial"] & ~df["__is_ignored"]).sum()),
        "full_pallet_bins": int((df["__is_full"] & ~df["__is_ignored"]).sum()),
    }

    loc_col = df["__loc_col"].iloc[0] if "__loc_col" in df.columns and (df["__loc_col"] != "").any() else None
    if loc_col:
        loc_series = df[loc_col].astype(str)
        kpis["damages"] = int(loc_series.str.contains("DAMAGE|IBDAMAGE", case=False, na=False).sum())
        kpis["missing"] = int(loc_series.str.contains("MISSING", case=False, na=False).sum())
    else:
        kpis["damages"] = 0
        kpis["missing"] = 0

    return kpis
# ============================== AI PROVIDERS ==============================
def get_ai_provider() -> str:
    # "openai", "azure", or "none"
    return st.session_state.get("ai_provider", "openai")

def _openai_client():
    """Return OpenAI client or None if not configured."""
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
    """Return Azure OpenAI client or None if not configured."""
    try:
        from openai import AzureOpenAI
        api_key = os.environ.get(ENV_AZURE_KEY) or st.secrets.get("azure_openai_api_key")
        endpoint = os.environ.get(ENV_AZURE_ENDPOINT) or st.secrets.get("azure_openai_endpoint")
        api_version = st.secrets.get("azure_openai_api_version", "2024-02-01")
        if not api_key or not endpoint:
            return None
        client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
        return client
    except Exception as e:
        safe_log(f"Azure OpenAI client error: {e}")
        return None

# ============================== PLAN SCHEMA ==============================
ALLOWED_DATASETS = {"bulk", "racks", "all"}
ALLOWED_OPS = {"eq", "neq", "lt", "lte", "gt", "gte", "contains", "startswith", "endswith", "in", "regex"}
ALLOWED_FIELDS = {
    "Location", "LocationName", "Bin", "Zone", "Aisle", "Bay", "Level",
    "SKU", "Item", "ItemNumber", "Product", "LOT", "LOTNumber", "PalletID",
    "Qty", "Quantity", "OnHandQty", "PalletCount", "MaxAllowed", "EmptySlots",
    "__is_rack", "__is_bulk", "__is_partial", "__is_empty", "__is_full",
}
FIELD_ALIASES = {
    "Location": ["location", "locationname", "bin", "binlocation"],
    "Qty": ["qty", "quantity", "onhandqty", "on_hand_qty"],
    "PalletID": ["palletid", "pallet_id", "pallet", "palletnumber"],
    "LOT": ["lot", "lotnumber", "lot_number"],
}

def validate_plan(plan: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(plan, dict):
        return False, "Plan must be a JSON object."
    ds = plan.get("dataset", "all")
    if ds not in ALLOWED_DATASETS:
        return False, f"Invalid dataset: {ds}"
    filters = plan.get("filters", [])
    if not isinstance(filters, list):
        return False, "filters must be a list."
    for f in filters:
        if not isinstance(f, dict):
            return False, "Each filter must be an object."
        field = f.get("field")
        op = f.get("op")
        if op not in ALLOWED_OPS:
            return False, f"Unsupported operator: {op}"
        if not isinstance(field, str):
            return False, "field must be a string."
    limit = plan.get("limit", 2000)
    if not isinstance(limit, int) or limit < 1 or limit > 20000:
        return False, "limit must be an int between 1 and 20000."
    return True, ""

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
            if not client:
                return None
            use_model = model or st.secrets.get("openai_model", "gpt-4o-mini")
            resp = client.chat.completions.create(
                model=use_model,
                messages=messages,
                temperature=0,
            )
            return resp.choices[0].message.content
        elif provider == "azure":
            client = _azure_openai_client()
            if not client:
                return None
            deployment = os.environ.get(ENV_AZURE_DEPLOYMENT) or st.secrets.get("azure_openai_deployment")
            if not deployment:
                safe_log("Missing AZURE_OPENAI_DEPLOYMENT / secrets.azure_openai_deployment")
                return None
            resp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0,
            )
            return resp.choices[0].message.content
        else:
            return None
    except Exception as e:
        safe_log(f"LLM completion error ({provider}): {e}")
        return None

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
        safe_log(f"Plan JSON parse error: {e} | Content: {content[:200]}")
        return None
    ok, err = validate_plan(plan)
    if not ok:
        safe_log(f"Plan validation failed: {err} | Plan: {plan}")
        return None
    return plan
# ============================== REGEX FALLBACK ==============================
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

    m_lte = re.search(r"(\d+)\s*(pallets?)?\s*(or\s*less|or\s*lower|<=|at\s*most)", q)
    m_gte = re.search(r"(\d+)\s*(pallets?)?\s*(or\s*more|>=|at\s*least)", q)
    m_between = re.search(r"between\s*(\d+)\s*and\s*(\d+)", q)
    m_eq = re.search(r"\bexactly\s*(\d+)\s*pallets?\b", q)

    if m_between:
        a, b = int(m_between.group(1)), int(m_between.group(2))
        filters.append({"field": "PalletCount", "op": "gte", "value": min(a, b)})
        filters.append({"field": "PalletCount", "op": "lte", "value": max(a, b)})
    elif m_lte:
        n = int(m_lte.group(1))
        filters.append({"field": "PalletCount", "op": "lte", "value": n})
    elif m_gte:
        n = int(m_gte.group(1))
        filters.append({"field": "PalletCount", "op": "gte", "value": n})
    elif m_eq:
        n = int(m_eq.group(1))
        filters.append({"field": "PalletCount", "op": "eq", "value": n})

    limit = 500
    return {"dataset": dataset, "filters": filters, "sort": [], "select": [], "limit": limit}

# ============================== FIELD RESOLUTION ==============================
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

# ============================== PLAN EXECUTION ==============================
def apply_filter(df: pd.DataFrame, field: str, op: str, value: Any) -> pd.Series:
    s = df[field]
    try:
        if op == "eq":
            return s.astype(str) == str(value) if s.dtype == "object" else s == value
        if op == "neq":
            return s.astype(str) != str(value) if s.dtype == "object" else s != value
        if op == "lt":
            return pd.to_numeric(s, errors="coerce") < float(value)
        if op == "lte":
            return pd.to_numeric(s, errors="coerce") <= float(value)
        if op == "gt":
            return pd.to_numeric(s, errors="coerce") > float(value)
        if op == "gte":
            return pd.to_numeric(s, errors="coerce") >= float(value)
        if op == "contains":
            return s.astype(str).str.contains(str(value), case=False, na=False)
        if op == "startswith":
            return s.astype(str).str.startswith(str(value))
        if op == "endswith":
            return s.astype(str).str.endswith(str(value))
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
        if not field:
            continue
        mask = apply_filter(df, field, op, val)
        df = df[mask]

    sort_fields = []
    for srt in sort:
        field = srt.get("field")
        asc = bool(srt.get("asc", True))
        resolved = resolve_field_name(df, field) if field else None
        if resolved:
            sort_fields.append((resolved, asc))
    if sort_fields:
        by = [f for f, _ in sort_fields]
        ascending = [a for _, a in sort_fields]
        try:
            df = df.sort_values(by=by, ascending=ascending)
        except Exception:
            pass
    if select:
        cols = []
        for c in select:
            rc = resolve_field_name(df, c) or c
            if rc in df.columns and rc not in cols:
                cols.append(rc)
        if cols:
            df = df[cols]

    return df.head(limit)
# ============================== SIDEBAR / SETTINGS ==============================
with st.sidebar:
    st.subheader("🔎 Ask Bin Helper (AI NLQ)")
    use_ai = st.toggle("Use AI NLQ (beta)", value=True, help="Converts your text into a safe JSON plan and runs it.")
    provider = st.selectbox("AI Provider", ["openai", "azure", "none"], index=0 if use_ai else 2)
    st.session_state["ai_provider"] = provider if use_ai else "none"
    debug_ai = st.checkbox("Show JSON Plan (debug)", value=False)

    st.divider()
    st.subheader("⚙️ Data Sources")
    inv_bal_path = st.text_input("Inventory Balances file", value=DEFAULT_INVENTORY_BALANCES)
    on_hand_path = st.text_input("On Hand Inventory file", value=DEFAULT_ON_HAND_INVENTORY)
    master_path = st.text_input("Master Locations file", value=DEFAULT_MASTER_LOCATIONS)

    st.caption("Tip: You can upload or map these to local/OneDrive paths. The app auto-reloads every 5 min.")

# ============================== DATA LOADING & PREP ==============================
df_inv = load_excel(inv_bal_path)
df_hand = load_excel(on_hand_path)
df_master = load_excel(master_path)  # placeholder; not merged in this backup

df_src = df_inv if not df_inv.empty else df_hand

if df_src.empty:
    st.warning("No data loaded. Check your file paths or sheet names.")
    st.stop()

df_src = determine_bin_types(df_src)
kpis = compute_kpis(df_src)

# ============================== HEADER & LOTTIE ==============================
col1, col2 = st.columns([1, 5])
with col1:
    st.markdown("### 🧭 Bin Helper")
    st.caption("Inventory dashboard with AI natural-language queries")
with col2:
    if LOTTIE_AVAILABLE:
        try:
            st_lottie(
                {
                    "v": "5.4.2",
                    "fr": 60,
                    "ip": 0,
                    "op": 120,
                    "w": 800,
                    "h": 200,
                    "nm": "neon-bars",
                    "ddd": 0,
                    "assets": [],
                    "layers": []
                },
                height=90,
                key="lottie_header",
            )
        except Exception:
            pass

# ============================== KPI CARDS ==============================
st.markdown('<div class="kpi-grid">', unsafe_allow_html=True)
for label, value, color in [
    ("Empty Bins", kpis["empty_bins"], PRIMARY_COLOR),
    ("Empty Partial Bins", kpis["empty_partial_bins"], PRIMARY_COLOR),
    ("Full Pallet Bins", kpis["full_pallet_bins"], ACCENT_COLOR),
    ("Partial Bins", kpis["partial_bins"], ACCENT_COLOR),
    ("Damages", kpis["damages"], "#FFA726"),
    ("Missing", kpis["missing"], "#FFEE58"),
]:
    st.markdown(
        f"""
        <div class="neon-card">
          <div class="metric-label">{label}</div>
          <div class="metric-number" style="color:{color}">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
st.markdown('</div>', unsafe_allow_html=True)

# ============================== NEW CHARTS ==============================
st.subheader("📊 Fleet Overview")

racks = df_src[(df_src["__is_rack"]) & (~df_src["__is_ignored"])]
rack_empty = int((racks["__is_empty"]).sum())
rack_full = int((racks["__is_full"]).sum())

bulk = df_src[(df_src["__is_bulk"]) & (~df_src["__is_ignored"])]
bulk_used = int((bulk["__qty"] > 0).sum())
bulk_empty = int((bulk["__qty"] <= 0).sum())

c1, c2 = st.columns(2)
with c1:
    st.caption("Racks: Empty vs Full")
    data_rack = pd.DataFrame({
        "Category": ["Empty", "Full"],
        "Count": [rack_empty, rack_full],
        "Color": [PRIMARY_COLOR, ACCENT_COLOR],
    })
    if ALTAIR_AVAILABLE:
        chart_rack = alt.Chart(data_rack).mark_bar().encode(
            x=alt.X("Category:N", sort=["Empty", "Full"]),
            y="Count:Q",
            color=alt.Color("Category:N", scale=alt.Scale(domain=["Empty", "Full"], range=[PRIMARY_COLOR, ACCENT_COLOR]), legend=None),
        ).properties(height=220)
        st.altair_chart(chart_rack, use_container_width=True)
    else:
        st.bar_chart(data_rack.set_index("Category"))

with c2:
    st.caption("Bulk: Used vs Empty")
    data_bulk = pd.DataFrame({
        "Category": ["Used", "Empty"],
        "Count": [bulk_used, bulk_empty],
        "Color": [ACCENT_COLOR, PRIMARY_COLOR],
    })
    if ALTAIR_AVAILABLE:
        chart_bulk = alt.Chart(data_bulk).mark_bar().encode(
            x=alt.X("Category:N", sort=["Used", "Empty"]),
            y="Count:Q",
            color=alt.Color("Category:N", scale=alt.Scale(domain=["Used", "Empty"], range=[ACCENT_COLOR, PRIMARY_COLOR]), legend=None),
        ).properties(height=220)
        st.altair_chart(chart_bulk, use_container_width=True)
    else:
        st.bar_chart(data_bulk.set_index("Category"))
# ============================== TABS ==============================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Empty Bins",
    "Full Pallet Bins",
    "Empty Partial Bins",
    "Partial Bins",
    "Bulk Locations",
    "Ask Bin Helper (Query)"
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
        grid_response = AgGrid(
            df, gridOptions=grid_options, height=height, theme="streamlit",
            update_mode=GridUpdateMode.SELECTION_CHANGED,
            key=key
        )
        return grid_response
    else:
        st.dataframe(df, use_container_width=True, height=height)
        return None

with tab1:
    st.subheader("Empty Bins")
    show_table(df_src[df_src["__is_empty"] & ~df_src["__is_ignored"]], key="empty")

with tab2:
    st.subheader("Full Pallet Bins")
    show_table(df_src[df_src["__is_full"] & ~df_src["__is_ignored"]], key="full")

with tab3:
    st.subheader("Empty Partial Bins")
    show_table(df_src[df_src["__is_empty_partial"] & ~df_src["__is_ignored"]], key="empty_partial")

with tab4:
    st.subheader("Partial Bins")
    show_table(df_src[df_src["__is_partial"] & ~df_src["__is_ignored"]], key="partial")

# ============================== BULK LOCATIONS (Row selection + Pallet dropdown) ==============================
with tab5:
    st.subheader("Bulk Locations")

    loc_col = df_src["__loc_col"].iloc[0] if "__loc_col" in df_src.columns and (df_src["__loc_col"] != "").any() else None
    pallet_col = df_src["__pallet_col"].iloc[0] if (df_src["__pallet_col"] != "").any() else None
    sku_col = df_src["__sku_col"].iloc[0] if (df_src["__sku_col"] != "").any() else None
    lot_col = df_src["__lot_col"].iloc[0] if (df_src["__lot_col"] != "").any() else None

    if not loc_col:
        st.warning("Cannot determine Location column. Please check your source file.")
    else:
        df_bulk_rows = df_src[(df_src["__is_bulk"]) & (~df_src["__is_ignored"])].copy()

        if pallet_col:
            agg = df_bulk_rows.groupby(loc_col)[pallet_col].nunique().rename("PalletCount").reset_index()
        else:
            agg = df_bulk_rows.groupby(loc_col)["__qty"].apply(lambda s: int((s > 0).sum())).rename("PalletCount").reset_index()

        zone_col = None
        for cand in ["zone", "area", "section"]:
            zc = _get_col(df_bulk_rows, [cand])
            if zc:
                zone_col = zc
                break

        if zone_col:
            d_zone = df_bulk_rows.groupby(loc_col)[zone_col].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else "").reset_index()
            agg = agg.merge(d_zone, on=loc_col, how="left").rename(columns={zone_col: "Zone"})
        else:
            agg["Zone"] = ""

        max_allowed_col = _get_col(df_bulk_rows, ["maxallowed", "max_allowed", "capacity"])
        if max_allowed_col:
            d_cap = df_bulk_rows.groupby(loc_col)[max_allowed_col].max(numeric_only=False).reset_index()
            d_cap = d_cap.rename(columns={max_allowed_col: "MaxAllowed"})
            agg = agg.merge(d_cap, on=loc_col, how="left")
            def _emptyslots(row):
                try:
                    ma = float(row.get("MaxAllowed", np.nan))
                    pc = float(row.get("PalletCount", 0))
                    return int(max(ma - pc, 0))
                except Exception:
                    return None
            agg["EmptySlots"] = agg.apply(_emptyslots, axis=1)
        else:
            agg["MaxAllowed"] = ""
            agg["EmptySlots"] = ""

        agg = agg.rename(columns={loc_col: "LocationName"})
        cols_order = ["LocationName", "Zone", "PalletCount", "MaxAllowed", "EmptySlots"]
        agg = agg[cols_order]

        grid_resp = show_table(agg, key="bulk_locations_grid", allow_select=True)

        selected_location = None
        if grid_resp and grid_resp.get("selected_rows"):
            selected_location = grid_resp["selected_rows"][0].get("LocationName")

        if not selected_location:
            st.caption("Select a location to view its pallets:")
            selected_location = st.selectbox("Bulk Location", options=[""] + sorted(agg["LocationName"].unique().tolist()))

        if selected_location:
            df_loc = df_bulk_rows[df_bulk_rows[loc_col] == selected_location].copy()

            if pallet_col:
                def label_row(r):
                    parts = []
                    pid = str(r.get(pallet_col, "") or "")
                    if pid: parts.append(pid)
                    if sku_col and r.get(sku_col, ""): parts.append(str(r.get(sku_col)))
                    if lot_col and r.get(lot_col, ""): parts.append(str(r.get(lot_col)))
                    return " — ".join(parts) if parts else "(unknown pallet)"
                unique_pallets = df_loc.dropna(subset=[pallet_col]).copy()
                unique_pallets["__label"] = unique_pallets.apply(label_row, axis=1)
                unique_pallets = unique_pallets.drop_duplicates(subset=[pallet_col])
                label_map = dict(zip(unique_pallets["__label"], unique_pallets[pallet_col]))
                chosen_label = st.selectbox(f"Pallets in {selected_location}", options=[""] + sorted(label_map.keys()))
                chosen_pallet_id = label_map.get(chosen_label, None)
            else:
                st.info("No Pallet ID column found; showing rows instead.")
                chosen_pallet_id = None

            st.markdown("**Details**")
            cols_to_show = []
            if sku_col: cols_to_show.append(sku_col)
            if lot_col: cols_to_show.append(lot_col)
            if pallet_col: cols_to_show.append(pallet_col)

            if chosen_pallet_id and pallet_col:
                detail_df = df_loc[df_loc[pallet_col] == chosen_pallet_id]
                if cols_to_show:
                    detail_df = detail_df[cols_to_show].drop_duplicates()
                st.dataframe(detail_df, use_container_width=True, height=220)
            else:
                if cols_to_show:
                    mini = df_loc[cols_to_show].drop_duplicates().head(50)
                    st.dataframe(mini, use_container_width=True, height=220)
                else:
                    st.dataframe(df_loc.head(50), use_container_width=True, height=220)

# ============================== ASK BIN HELPER (NLQ) ==============================
with tab6:
    st.subheader("Natural-language Query")
    q = st.text_input("Type what you need (e.g., 'show me partial pallets in bulk locations with 5 pallets or less')", key="nlq")
    run = st.button("Run Query", type="primary")

    if run and q.strip():
        plan = None
        used_fallback = False

        if get_ai_provider() != "none":
            plan = llm_parse_to_plan(q)

        if plan is None:
            st.warning("AI NLQ unavailable or failed (AI provider not configured). Falling back to regex.")
            plan = regex_fallback(q)
            used_fallback = True

        if debug_ai:
            st.code(json.dumps(plan, indent=2), language="json")

        result = execute_plan(plan, df_src)

        st.success(f"Returned {len(result)} rows (limit {plan.get('limit', 500)}).{' [regex fallback]' if used_fallback else ''}")
        show_table(result, key="nlq_result")
# ============================== DISCREPANCIES ==============================
st.subheader("🧩 Discrepancies")

loc_col = df_src["__loc_col"].iloc[0] if "__loc_col" in df_src.columns and (df_src["__loc_col"] != "").any() else None
pallet_col = df_src["__pallet_col"].iloc[0] if (df_src["__pallet_col"] != "").any() else None
sku_col = df_src["__sku_col"].iloc[0] if (df_src["__sku_col"] != "").any() else None
lot_col = df_src["__lot_col"].iloc[0] if (df_src["__lot_col"] != "").any() else None

if not loc_col:
    st.info("Location column not found; cannot compute discrepancies.")
else:
    dt1, dt2 = st.tabs(["Discrepancies (All)", "Bulk Discrepancies"])

    def hide_missing(df: pd.DataFrame) -> pd.DataFrame:
        if loc_col in df.columns:
            return df[~df[loc_col].astype(str).str.contains("MISSING", case=False, na=False)]
        return df

    with dt1:
        st.caption("Rules: Hide 'MISSING'. Rack slots must have ≤1 pallet (bulk is separate). 'Duplicate Pallets' is shown here only.")
        base = df_src.copy()
        base = hide_missing(base)

        dup_table = pd.DataFrame()
        if pallet_col:
            racks_only = base[(base["__is_rack"]) & (~base["__is_ignored"])].copy()
            grp = racks_only.groupby([loc_col])[pallet_col].nunique().reset_index(name="DistinctPallets")
            dup_table = grp[grp["DistinctPallets"] > 1].copy()
            dup_table = dup_table.rename(columns={loc_col: "Location"})
        else:
            st.info("No Pallet ID column found; cannot compute duplicate pallets on racks.")

        if not dup_table.empty:
            st.markdown("**Duplicate Pallets (Racks only)**")
            show_table(dup_table.sort_values("DistinctPallets", ascending=False), key="dup_rack", height=260)

            st.markdown("**Details**")
            for _, row in dup_table.head(250).iterrows():
                loc = row["Location"]
                with st.expander(f"Location {loc} — {int(row['DistinctPallets'])} pallets"):
                    rows = base[(base[loc_col] == loc)]
                    fields = []
                    if sku_col: fields.append(sku_col)
                    if lot_col: fields.append(lot_col)
                    if pallet_col: fields.append(pallet_col)
                    details = rows[fields].drop_duplicates() if fields else rows
                    st.dataframe(details, use_container_width=True, height=200)
        else:
            st.info("No rack duplicate-pallet discrepancies found.")

    with dt2:
        st.caption("Clickable details show only SKU, LOT Number, and Pallet ID. 'MISSING' locations are hidden.")
        base = hide_missing(df_src.copy())
        bulk_only = base[(base["__is_bulk"]) & (~base["__is_ignored"])].copy()

        if pallet_col:
            group_fields = [loc_col]
            if sku_col: group_fields.append(sku_col)
            if lot_col: group_fields.append(lot_col)
            grp = bulk_only.groupby(group_fields)[pallet_col].nunique().reset_index(name="DistinctPallets")
            bulk_disc = grp[grp["DistinctPallets"] > 1].copy()
        else:
            group_fields = [loc_col]
            if sku_col: group_fields.append(sku_col)
            if lot_col: group_fields.append(lot_col)
            grp = bulk_only.groupby(group_fields).size().reset_index(name="RowCount")
            bulk_disc = grp[grp["RowCount"] > 1].copy()
            bulk_disc = bulk_disc.rename(columns={"RowCount": "DistinctPallets"})

        if bulk_disc.empty:
            st.info("No bulk discrepancies found with the current logic.")
        else:
            renames = {}
            if loc_col: renames[loc_col] = "Location"
            if sku_col: renames[sku_col] = "SKU"
            if lot_col: renames[lot_col] = "LOT Number"
            bulk_disc = bulk_disc.rename(columns=renames)

            display_cols = [c for c in ["Location", "SKU", "LOT Number", "DistinctPallets"] if c in bulk_disc.columns]
            st.dataframe(bulk_disc[display_cols].sort_values(["Location", "DistinctPallets"], ascending=[True, False]),
                         use_container_width=True, height=320)

            st.markdown("**Details**")
            for _, r in bulk_disc.head(250).iterrows():
                loc_val = r.get("Location", r.get(loc_col))
                sku_val = r.get("SKU", r.get(sku_col, ""))
                lot_val = r.get("LOT Number", r.get(lot_col, ""))
                exp_title = f"{loc_val}"
                if sku_val not in ("", None): exp_title += f" · SKU {sku_val}"
                if lot_val not in ("", None): exp_title += f" · LOT {lot_val}"
                with st.expander(exp_title):
                    rows = bulk_only.copy()
                    if loc_val is not None:
                        rows = rows[rows[loc_col] == loc_val]
                    if sku_col and sku_val not in ("", None):
                        rows = rows[rows[sku_col] == sku_val]
                    if lot_col and lot_val not in ("", None):
                        rows = rows[rows[lot_col] == lot_val]
                    fields = []
                    if sku_col: fields.append(sku_col)
                    if lot_col: fields.append(lot_col)
                    if pallet_col: fields.append(pallet_col)
                    details = rows[fields].drop_duplicates() if fields else rows
                    st.dataframe(details, use_container_width=True, height=200)

# ============================== FOOTER ==============================
st.caption("Neon Warehouse theme enabled · Animations & Lottie ready · Auto-refresh every 5 minutes via cache TTL")