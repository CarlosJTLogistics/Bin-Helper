import os
import pandas as pd
import streamlit as st
from datetime import datetime
from io import BytesIO

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Discrepancies"
if "expanded_rows" not in st.session_state:
    st.session_state.expanded_rows = set()
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

# ---------------- AUTO REFRESH ----------------
if st.session_state.auto_refresh:
    st.experimental_rerun()

# ---------------- SIDEBAR SETTINGS ----------------
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    theme = st.radio("Theme", ["Light", "Dark"], index=0)
    st.session_state.auto_refresh = st.checkbox("Auto Refresh", value=st.session_state.auto_refresh)

    st.markdown("### 📤 Upload Files")
    uploaded_inventory = st.file_uploader("Upload ON_HAND_INVENTORY.xlsx", type=["xlsx"])
    uploaded_master = st.file_uploader("Upload Empty Bin Formula.xlsx", type=["xlsx"])

# ---------------- LOAD DATA ----------------
@st.cache_data
def load_data(inventory_file, master_file):
    inventory_df = pd.read_excel(inventory_file, engine="openpyxl")
    master_df = pd.read_excel(master_file, sheet_name="Master Locations", engine="openpyxl")
    return inventory_df, master_df

if uploaded_inventory and uploaded_master:
    inventory_df, master_df = load_data(uploaded_inventory, uploaded_master)
else:
    st.error("Please upload both ON_HAND_INVENTORY.xlsx and Empty Bin Formula.xlsx to proceed.")
    st.stop()

# ---------------- DATA PREP ----------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# ---------------- BUSINESS RULES ----------------
def exclude_damage_missing(df):
    return df[
        ~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"]) &
        ~df["LocationName"].astype(str).str.upper().str.startswith("IB")
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        df["LocationName"].astype(str).str[0].str.isdigit()
    ]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
    ]

def get_empty_partial_bins(master_locs, occupied_locs):
    partial_candidates = [
        loc for loc in master_locs
        if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN") and str(loc)[0].isdigit()
    ]
    empty_partial = sorted(set(partial_candidates) - set(occupied_locs))
    return pd.DataFrame({"LocationName": empty_partial})

empty_bins_view_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_locations]})
full_pallet_bins_df = get_full_pallet_bins(filtered_inventory_df)
partial_bins_df = get_partial_bins(filtered_inventory_df)
empty_partial_bins_df = get_empty_partial_bins(master_locations, occupied_locations)

damages_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper() == "MISSING"]

# ---------------- BULK DISCREPANCY LOGIC ----------------
def analyze_bulk_locations(df):
    df = exclude_damage_missing(df)
    results = []
    for letter, max_pallets in bulk_rules.items():
        letter_df = df[df["LocationName"].astype(str).str.startswith(letter)]
        slot_counts = letter_df.groupby("LocationName").size()
        for slot, count in slot_counts.items():
            if count > max_pallets:
                details = df[df["LocationName"] == slot]
                for _, drow in details.iterrows():
                    results.append({
                        "LocationName": slot,
                        "Qty": drow.get("Qty", ""),
                        "WarehouseSku": drow.get("WarehouseSku", ""),
                        "PalletId": drow.get("PalletId", ""),
                        "CustomerLotReference": drow.get("CustomerLotReference", ""),
                        "Issue": f"⚠️ Exceeds max allowed: {count} > {max_pallets}"
                    })
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations(filtered_inventory_df)

# ---------------- DISCREPANCY LOGIC ----------------
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []

    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        issue = "⚠️ Qty too high for partial bin" if row["Qty"] > 5 else "⚠️ Multiple pallets in partial bin"
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "PalletCount": row.get("PalletCount", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", ""),
            "Issue": issue
        })

    full_df = df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric())
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        issue = "⚠️ Qty out of range for full pallet bin"
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "PalletCount": row.get("PalletCount", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", ""),
            "Issue": issue
        })

    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ---------------- EXPORT FUNCTION ----------------
def export_dataframe(df, filename):
    output = BytesIO()
    df.to_excel(output, index=False, engine="openpyxl")
    st.download_button(
        label="📥 Download Filtered Data",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ---------------- FILTER FUNCTION ----------------
def apply_filters(df):
    for key, value in st.session_state.filters.items():
        if value and key in df.columns:
            df = df[df[key].astype(str).str.contains(value, case=False, na=False)]
    return df

# ---------------- DISPLAY GROUPED ----------------
def display_grouped(df):
    grouped = df.groupby("LocationName")
    for loc, group in grouped:
        issue_text = group["Issue"].iloc[0] if "Issue" in group.columns else ""
        badge = f"<span style='background-color:#FFCC00; color:#000; padding:4px; border-radius:4px;'>{issue_text}</span>" if issue_text else ""
        st.markdown(f"---\n**📍 Location:** {loc} | {badge}", unsafe_allow_html=True)

        if loc in st.session_state.expanded_rows:
            st.dataframe(group[["WarehouseSku", "CustomerLotReference", "PalletId", "Qty"]])
            if st.button(f"Collapse {loc}", key=f"collapse_{loc}"):
                st.session_state.expanded_rows.remove(loc)
        else:
            if st.button(f"Expand {loc}", key=f"expand_{loc}"):
                st.session_state.expanded_rows.add(loc)

# ---------------- KPI CARDS ----------------
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Discrepancies", "value": len(discrepancy_df.groupby('LocationName')), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df.groupby('LocationName')), "icon": "📦"}
]

cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# ---------------- FILTERS ----------------
st.sidebar.markdown("### 🔍 Filter Options")
st.session_state.filters["LocationName"] = st.sidebar.text_input("Location", value=st.session_state.filters["LocationName"])
st.session_state.filters["PalletId"] = st.sidebar.text_input("Pallet ID", value=st.session_state.filters["PalletId"])
st.session_state.filters["WarehouseSku"] = st.sidebar.text_input("Warehouse SKU", value=st.session_state.filters["WarehouseSku"])
st.session_state.filters["CustomerLotReference"] = st.sidebar.text_input("LOT", value=st.session_state.filters["CustomerLotReference"])

# ---------------- DISPLAY VIEWS ----------------
view_map = {
    "Discrepancies": discrepancy_df,
    "Bulk Discrepancies": bulk_df,
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df
}

active_df = apply_filters(view_map.get(st.session_state.active_view, pd.DataFrame()))

if st.session_state.active_view in ["Discrepancies", "Bulk Discrepancies"]:
    display_grouped(active_df)
else:
    st.dataframe(active_df)

export_dataframe(active_df, f"{st.session_state.active_view.replace(' ', '_')}_filtered.xlsx")