import pandas as pd
import streamlit as st
from io import BytesIO

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = None
if "filters" not in st.session_state:
    st.session_state.filters = {"LocationName": "", "PalletId": "", "WarehouseSku": "", "CustomerLotReference": ""}
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "fixed_pallets" not in st.session_state:
    st.session_state.fixed_pallets = set()
if "use_filters" not in st.session_state:
    st.session_state.use_filters = True

# ---------------- AUTO REFRESH ----------------
if st.session_state.auto_refresh:
    st.rerun()

# ---------------- GITHUB FILE URLS ----------------
inventory_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://github.com/CarlosJTLogistics/Bin-Helper/raw/refs/heads/main/Empty%20Bin%20Formula.xlsx"

# ---------------- LOAD DATA ----------------
@st.cache_data
def load_data(inventory_url, master_url):
    inventory_df = pd.read_excel(inventory_url, engine="openpyxl")
    master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")
    return inventory_df, master_df

try:
    inventory_df, master_df = load_data(inventory_url, master_url)
except Exception as e:
    st.error(f"❌ Failed to load data from GitHub: {e}")
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
                        "Issue": f"⚠️ Exceeds max allowed: {count} > {max_pallets}",
                        "Action": "Fix"
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
            "Issue": issue,
            "Action": "Fix"
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
            "Issue": issue,
            "Action": "Fix"
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

# ---------------- HORIZONTAL CARD DISPLAY ----------------
def display_discrepancy_cards(df):
    if df.empty:
        st.warning("⚠️ No discrepancies found for this view.")
        return

    # Custom CSS for card styling
    st.markdown("""
        <style>
        .card {
            background-color: #1E1E1E;
            border-radius: 8px;
            padding: 15px;
            margin: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            color: #fff;
        }
        .issue-badge {
            background-color: #FFC107;
            color: #000;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)

    # Display cards in rows
    for i in range(0, len(df), 2):  # 2 cards per row
        cols = st.columns(2)
        for j in range(2):
            if i + j < len(df):
                row = df.iloc[i + j]
                with cols[j]:
                    st.markdown(f"""
                        <div class="card">
                            <h4>📍 Location: {row['LocationName']}</h4>
                            <div class="issue-badge">{row['Issue']}</div>
                            <p>SKU: {row['WarehouseSku']} | LOT: {row['CustomerLotReference']}<br>
                            Pallet: {row['PalletId']} | Qty: {row['Qty']}</p>
                            <button style="background-color:#4CAF50;color:white;border:none;padding:8px;border-radius:4px;">✅ Fix Pallet</button>
                        </div>
                    """, unsafe_allow_html=True)

# ---------------- KPI CARDS ----------------
st.markdown("<h1 style='text-align: center; color: #2E86C1;'>📊 Bin-Helper Dashboard</h1>", unsafe_allow_html=True)

kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Rack Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df), "icon": "📦"}
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
st.session_state.use_filters = st.sidebar.checkbox("Apply filters", value=True)

# ---------------- DISPLAY VIEWS ----------------
view_map = {
    "Rack Discrepancies": discrepancy_df,
    "Bulk Discrepancies": bulk_df,
    "Empty Bins": empty_bins_view_df,
    "Full Pallet Bins": full_pallet_bins_df,
    "Empty Partial Bins": empty_partial_bins_df,
    "Partial Bins": partial_bins_df,
    "Damages": damages_df,
    "Missing": missing_df
}

if st.session_state.active_view:
    raw_df = view_map.get(st.session_state.active_view, pd.DataFrame())
    active_df = apply_filters(raw_df) if st.session_state.use_filters else raw_df

    if st.session_state.active_view in ["Rack Discrepancies", "Bulk Discrepancies"]:
        st.subheader(f"📋 {st.session_state.active_view}")
        display_discrepancy_cards(active_df)
    else:
        st.dataframe(active_df)

    export_dataframe(active_df, f"{st.session_state.active_view.replace(' ', '_')}_filtered.xlsx")
else:
    st.info("👆 Select a KPI card above to view details.")