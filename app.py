# Bin Helper - Warehouse Inventory Dashboard
# ------------------------------------------------------------
# - Clickable KPI cards (card == button) including separate Damages & Missing
# - Theme selector in sidebar (Metallic Silver, Neutral Light, Dark Slate, Legacy)
# - Modern refresh APIs (st.query_params + st.rerun)
# - Safe file staging & resilient Excel reads (avoids OneDrive/Excel lock errors)
# - Sidebar filters for SKU / LOT Number / Pallet ID
# ------------------------------------------------------------

import os
import io
import json
import time
from datetime import datetime

import pandas as pd
import streamlit as st

# ======================
# Page configuration
# ======================
st.set_page_config(page_title="Bin Helper", layout="wide")

# ======================
# Optional: Lottie animation
# ======================
try:
    from streamlit_lottie import st_lottie

    def load_lottiefile(filepath: str):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            return None

    lottie_box = load_lottiefile("box_animation.json")
except ImportError:
    st_lottie = None
    lottie_box = None

# ======================
# THEME INJECTION
# ======================
def inject_kpi_theme(theme: str):
    """Inject CSS for KPI cards based on the selected theme.
       Styles apply to KPI buttons wrapped inside .kpi-surface containers.
    """
    if theme == "Metallic Silver (Blue Outline)":
        css = """
        <style>
          .kpi-surface .stButton>button {
            width: 100% !important;
            text-align: left !important;
            padding: 16px 18px !important;
            border-radius: 14px !important;

            /* ✨ Metallic silver with a hint of blue outline */
            background:
              linear-gradient(180deg, rgba(255,255,255,.85), rgba(255,255,255,.35)),
              linear-gradient(135deg, #f1f3f7 0%, #e3e7ee 55%, #cfd6e3 100%),
              repeating-linear-gradient(90deg, rgba(255,255,255,.10) 0px, rgba(255,255,255,.10) 4px, rgba(0,0,0,.03) 8px) !important;
            border: 1px solid #8fb0ff !important;
            box-shadow: inset 0 0 0 1px rgba(143,176,255,.25), 0 6px 16px rgba(0,0,0,.06) !important;
            color: #111827 !important;

            white-space: pre-line !important;  /* title 1st line, value 2nd line */
            font-size: 1.9rem !important;
            font-weight: 800 !important;
            line-height: 1.15 !important;
            transition: 160ms ease !important;
          }
          .kpi-surface .stButton>button::first-line {
            font-size: 0.95rem !important;
            font-weight: 600 !important;
            color: #6b7280 !important;
          }
          .kpi-surface .stButton>button:hover {
            transform: translateY(-2px) !important;
            border-color: #6a95ff !important;
            box-shadow: inset 0 0 0 1px rgba(106,149,255,.35), 0 14px 28px rgba(0,0,0,.12) !important;
          }
          .main .block-container { padding-top: 1rem; }
          .kpi-surface .stButton { margin-bottom: 0.5rem; }
        </style>
        """
    elif theme == "Neutral Light":
        css = """
        <style>
          .kpi-surface .stButton>button {
            width: 100% !important;
            text-align: left !important;
            padding: 16px 18px !important;
            border-radius: 14px !important;
            background: #ffffff !important;
            border: 1px solid #e5e7eb !important;
            box-shadow: 0 6px 16px rgba(0,0,0,.06) !important;
            color: #111827 !important;
            white-space: pre-line !important;
            font-size: 1.9rem !important;
            font-weight: 800 !important;
            line-height: 1.15 !important;
            transition: 160ms ease !important;
          }
          .kpi-surface .stButton>button::first-line {
            font-size: 0.95rem !important;
            font-weight: 600 !important;
            color: #6b7280 !important;
          }
          .kpi-surface .stButton>button:hover {
            transform: translateY(-2px) !important;
            border-color: #d1d5db !important;
            box-shadow: 0 14px 28px rgba(0,0,0,.12) !important;
          }
          .main .block-container { padding-top: 1rem; }
          .kpi-surface .stButton { margin-bottom: 0.5rem; }
        </style>
        """
    elif theme == "Dark Slate":
        css = """
        <style>
          .kpi-surface .stButton>button {
            width: 100% !important;
            text-align: left !important;
            padding: 16px 18px !important;
            border-radius: 14px !important;
            background:
              linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.02)),
              linear-gradient(135deg, #1e293b 0%, #334155 100%) !important;
            border: 1px solid rgba(147,197,253,.35) !important;
            box-shadow: inset 0 0 0 1px rgba(147,197,253,.15), 0 6px 16px rgba(0,0,0,.25) !important;
            color: #f8fafc !important;
            white-space: pre-line !important;
            font-size: 1.9rem !important;
            font-weight: 800 !important;
            line-height: 1.15 !important;
            transition: 160ms ease !important;
          }
          .kpi-surface .stButton>button::first-line {
            font-size: 0.95rem !important;
            font-weight: 600 !important;
            color: #cbd5e1 !important;
          }
          .kpi-surface .stButton>button:hover {
            transform: translateY(-2px) !important;
            border-color: rgba(147,197,253,.65) !important;
            box-shadow: inset 0 0 0 1px rgba(147,197,253,.35), 0 14px 28px rgba(0,0,0,.35) !important;
          }
          .main .block-container { padding-top: 1rem; }
          .kpi-surface .stButton { margin-bottom: 0.5rem; }
        </style>
        """
    else:  # Legacy
        css = """
        <style>
          .kpi-surface .stButton>button {
            width: 100% !important;
            text-align: left !important;
            padding: 16px 18px !important;
            border-radius: 14px !important;
            background: #ffffff !important;
            border: 1px solid #e5e7eb !important;
            box-shadow: 0 4px 12px rgba(0,0,0,.06) !important;
            color: #111827 !important;
            white-space: pre-line !important;
            font-size: 1.9rem !important;
            font-weight: 800 !important;
            line-height: 1.15 !important;
            transition: 160ms ease !important;
          }
          .kpi-surface .stButton>button::first-line {
            font-size: 0.95rem !important;
            font-weight: 600 !important;
            color: #6b7280 !important;
          }
          .kpi-surface .stButton>button:hover {
            transform: translateY(-2px) !important;
            border-color: #d1d5db !important;
            box-shadow: 0 10px 24px rgba(0,0,0,.10) !important;
          }
          .main .block-container { padding-top: 1rem; }
          .kpi-surface .stButton { margin-bottom: 0.5rem; }
        </style>
        """
    st.markdown(css, unsafe_allow_html=True)

# ======================
# Files (local; OneDrive-synced) & Safe Staging
# ======================
# Use relative names if files live next to app.py. You can also set absolute paths.
inventory_file = "ON_HAND_INVENTORY.xlsx"
empty_bins_file = "Empty Bin Formula.xlsx"

def safe_mtime(path: str):
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return None
    except PermissionError:
        return os.path.getmtime(path) if os.path.exists(path) else None

STAGING_DIR = os.path.join(os.path.dirname(__file__), "_staged")
os.makedirs(STAGING_DIR, exist_ok=True)

def stage_copy(src_path: str, staged_name: str, attempts: int = 5, delay: float = 0.35) -> str:
    """
    Copy the live file into _staged for safe reading. Retries on file locks.
    Returns staged path; falls back to last staged copy if needed.
    """
    staged_path = os.path.join(STAGING_DIR, staged_name)
    last_err = None
    for i in range(attempts):
        try:
            tmp_path = staged_path + ".tmp"
            # Copy to a temp, then replace atomically
            with open(src_path, "rb") as src, open(tmp_path, "wb") as dst:
                dst.write(src.read())
            os.replace(tmp_path, staged_path)
            return staged_path
        except (PermissionError, FileNotFoundError, OSError) as e:
            last_err = e
            time.sleep(delay * (i + 1))
    if os.path.exists(staged_path):
        st.warning(
            f"Using last staged copy for **{os.path.basename(src_path)}** "
            f"(could not copy latest: {last_err})"
        )
        return staged_path
    raise RuntimeError(f"Could not stage copy of {src_path}: {last_err}")

def read_excel_resilient(path: str, sheet_name=None, attempts: int = 3, base_delay: float = 0.35, cache_key: str | None = None):
    """
    Read Excel with retries; if locked, attempt shadow read via BytesIO.
    Caches last good DF in session_state[cache_key].
    """
    last_err = None
    for i in range(attempts):
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
            if cache_key:
                st.session_state[cache_key] = df
            return df
        except PermissionError as e:
            last_err = e
            try:
                with open(path, "rb") as f:
                    data = f.read()
                df = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, engine="openpyxl")
                if cache_key:
                    st.session_state[cache_key] = df
                return df
            except Exception as e2:
                last_err = e2
        except Exception as e:
            last_err = e
        time.sleep(base_delay * (i + 1))
    if cache_key and cache_key in st.session_state:
        st.warning(
            f"Using last loaded data for **{os.path.basename(path)}** "
            f"(could not open current file: {last_err})"
        )
        return st.session_state[cache_key]
    st.error(f"Could not open **{os.path.basename(path)}** after {attempts} attempts.\n{last_err}")
    return pd.DataFrame()

# ======================
# Auto-reload watcher
# ======================
if "file_mod_times" not in st.session_state:
    st.session_state.file_mod_times = {
        inventory_file: safe_mtime(inventory_file),
        empty_bins_file: safe_mtime(empty_bins_file),
    }

def files_updated():
    changed = False
    for file in [inventory_file, empty_bins_file]:
        new_mtime = safe_mtime(file)
        if new_mtime != st.session_state.file_mod_times[file]:
            st.session_state.file_mod_times[file] = new_mtime
            changed = True
    return changed

if files_updated():
    st.rerun()  # modern API (replaces st.experimental_rerun)

# ======================
# Sidebar
# ======================
st.sidebar.title("📦 Bin Helper")

# Theme selector (persisted)
theme_options = ["Metallic Silver (Blue Outline)", "Neutral Light", "Dark Slate", "Legacy"]
if "theme_choice" not in st.session_state:
    st.session_state.theme_choice = theme_options[0]
theme_choice = st.sidebar.radio("Theme", theme_options, index=theme_options.index(st.session_state.theme_choice))
st.session_state.theme_choice = theme_choice

# Inject theme CSS before drawing KPI cards
inject_kpi_theme(theme_choice)

# Refresh button (deprecation-safe)
if st.sidebar.button("🔄 Refresh Now"):
    st.query_params["refresh"] = str(int(datetime.now().timestamp()))
    st.rerun()

last_refresh = st.sidebar.empty()
st.sidebar.markdown("### 🔎 Filters")

# ======================
# Stage → Read data
# ======================
staged_inventory = stage_copy(inventory_file, "ON_HAND_INVENTORY.staged.xlsx")
staged_master    = stage_copy(empty_bins_file, "Empty Bin Formula.staged.xlsx")

inventory_df = read_excel_resilient(staged_inventory, cache_key="cache_inventory_df")
master_locations_df = read_excel_resilient(staged_master, sheet_name="Master Locations", cache_key="cache_master_df")

# Normalize numeric types
if "PalletCount" in inventory_df.columns:
    inventory_df["PalletCount"] = pd.to_numeric(inventory_df["PalletCount"], errors="coerce").fillna(0)
if "Qty" in inventory_df.columns:
    inventory_df["Qty"] = pd.to_numeric(inventory_df["Qty"], errors="coerce").fillna(0)

# ======================
# Business rules
# ======================
def is_valid_location(loc):
    if pd.isna(loc):
        return False
    loc_str = str(loc).upper()
    return (loc_str.startswith("TUN") or loc_str in ["DAMAGE", "MISSING", "IBDAMAGE"] or loc_str.isdigit())

filtered_inventory_df = inventory_df[inventory_df["LocationName"].apply(is_valid_location)]

occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_locations_df.iloc[1:, 0].dropna().astype(str).unique())

# Empty bins: in master but not occupied
empty_bins = sorted(master_locations - occupied_locations)
empty_bins_view_df = pd.DataFrame({"LocationName": empty_bins})

def exclude_damage_missing(df):
    return df[~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"])]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[df["PalletCount"] == 1]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01")
        & ~df["LocationName"].astype(str).str.startswith("111")
        & ~df["LocationName"].astype(str).str.upper().str.startswith("TUN")
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not loc.upper().startswith("TUN")
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

def get_damage(df):
    # DAMAGE + IBDAMAGE
    mask = df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])
    return df[mask]

def get_missing(df):
    mask = df["LocationName"].astype(str).str.upper().eq("MISSING")
    return df[mask]

columns_to_show = ["LocationName", "PalletId", "Qty", "CustomerLotReference", "WarehouseSku"]

full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)[columns_to_show]
partial_bins_df = get_partial_bins(filtered_inventory_df)[columns_to_show]
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)
damage_df = get_damage(filtered_inventory_df)[columns_to_show]
missing_df = get_missing(filtered_inventory_df)[columns_to_show]

# KPI values
damage_qty = int(damage_df["Qty"].sum()) if not damage_df.empty else 0
missing_qty = int(missing_df["Qty"].sum()) if not missing_df.empty else 0

# ======================
# Filters
# ======================
sku_list = ["All"] + sorted(filtered_inventory_df["WarehouseSku"].dropna().astype(str).unique().tolist())
lot_list = ["All"] + sorted(filtered_inventory_df["CustomerLotReference"].dropna().astype(str).unique().tolist())
pallet_list = ["All"] + sorted(filtered_inventory_df["PalletId"].dropna().astype(str).unique().tolist())

sku_filter = st.sidebar.selectbox("SKU", sku_list)
lot_filter = st.sidebar.selectbox("LOT Number", lot_list)
pallet_filter = st.sidebar.selectbox("Pallet ID", pallet_list)

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df
    if sku_filter != "All":
        out = out[out["WarehouseSku"].astype(str) == sku_filter]
    if lot_filter != "All":
        out = out[out["CustomerLotReference"].astype(str) == lot_filter]
    if pallet_filter != "All":
        out = out[out["PalletId"].astype(str) == pallet_filter]
    return out

# ======================
# Navigation state
# ======================
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = "Empty Bins"

def kpi_card(title: str, value: int, tab_name: str, icon: str = "", key: str | None = None):
    """Button == card (entire surface is clickable).
       Newline splits title (first line) and value (second line).
    """
    label = f"{(icon + ' ') if icon else ''}{title}\n{value:,}"
    if st.button(label, key=key or f"kpi_{tab_name}", use_container_width=True, help=f"Open {tab_name}"):
        st.session_state.selected_tab = tab_name

# ======================
# KPI Area (Bin Helper)
# ======================
st.markdown("## 📦 Bin Helper")

st.markdown('<div class="kpi-surface">', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
with c1:
    kpi_card("Empty Bins", len(empty_bins_view_df), "Empty Bins", icon="📦")
with c2:
    kpi_card("Full Pallet Bins", len(full_pallet_bins_df), "Full Pallet Bins", icon="🟩")
with c3:
    kpi_card("Empty Partial Bins", len(empty_partial_bins_df), "Empty Partial Bins", icon="🟨")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="kpi-surface">', unsafe_allow_html=True)
c4, c5, c6 = st.columns(3)
with c4:
    kpi_card("Partial Bins", len(partial_bins_df), "Partial Bins", icon="🟥")
with c5:
    kpi_card("Damages (QTY)", damage_qty, "Damages", icon="🛠️")
with c6:
    kpi_card("Missing (QTY)", missing_qty, "Missing", icon="❓")
st.markdown("</div>", unsafe_allow_html=True)

# ======================
# Central View
# ======================
tab = st.session_state.selected_tab
st.markdown(f"### 🔍 Viewing: {tab}")

if tab == "Empty Bins":
    st.subheader("📦 Empty Bins")
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(empty_bins_view_df)

elif tab == "Full Pallet Bins":
    st.subheader("🟩 Full Pallet Bins")
    df = apply_filters(full_pallet_bins_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(df)

elif tab == "Empty Partial Bins":
    st.subheader("🟨 Empty Partial Bins")
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(empty_partial_bins_df)

elif tab == "Partial Bins":
    st.subheader("🟥 Partial Bins")
    df = apply_filters(partial_bins_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.dataframe(df)

elif tab == "Damages":
    st.subheader("🛠️ Damages (DAMAGE & IBDAMAGE)")
    df = apply_filters(damage_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.metric(label="Total Damaged Qty", value=f"{damage_qty:,}")
    st.dataframe(df)

elif tab == "Missing":
    st.subheader("❓ Missing")
    df = apply_filters(missing_df)
    if st_lottie and lottie_box:
        st_lottie(lottie_box, height=150)
    st.metric(label="Total Missing Qty", value=f"{missing_qty:,}")
    st.dataframe(df)

# ======================
# Footer
# ======================
last_refresh.markdown(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")