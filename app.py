import os
import pandas as pd
import streamlit as st

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SESSION STATE ----------------
if "active_view" not in st.session_state:
    st.session_state.active_view = "Empty Bins"

# ---------------- FILE PATHS ----------------
inventory_file_path = "persisted_inventory.xlsx"
master_file_path = "persisted_master.xlsx"

# ---------------- LOAD DATA ----------------
if not os.path.exists(inventory_file_path) or not os.path.exists(master_file_path):
    st.error("Please upload both ON_HAND_INVENTORY.xlsx and Empty Bin Formula.xlsx to proceed.")
    st.stop()

inventory_df = pd.read_excel(inventory_file_path, engine="openpyxl")
master_df = pd.read_excel(master_file_path, sheet_name="Master Locations", engine="openpyxl")

# ---------------- DATA PREP ----------------
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

bulk_rules = {"A": 5, "B": 4, "C": 5, "D": 4, "E": 5, "F": 4, "G": 5, "H": 4, "I": 4}

# Filter out DAMAGE, IBDAMAGE, MISSING, and IB-prefixed locations for main logic
filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE", "MISSING"]) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

# ---------------- BUSINESS RULES ----------------
occupied_locations = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

def exclude_damage_missing(df):
    return df[
        ~df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "MISSING", "IBDAMAGE"]) &
        ~df["LocationName"].astype(str).str.upper().str.startswith("IB")
    ]

def get_full_pallet_bins(df):
    df = exclude_damage_missing(df)
    return df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric()) &
        (df["Qty"].between(6, 15))
    ]

def get_partial_bins(df):
    df = exclude_damage_missing(df)
    return df[
        df["LocationName"].astype(str).str.endswith("01") &
        ~df["LocationName"].astype(str).str.startswith("111") &
        ~df["LocationName"].astype(str).str.upper().str.startswith("TUN") &
        df["LocationName"].astype(str).str[0].str.isdigit()
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

# Damages and Missing
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
                        "CustomerLotReference": drow.get("CustomerLotReference", "")
                    })
    return pd.DataFrame(results)

bulk_df = analyze_bulk_locations(filtered_inventory_df)

# ---------------- DISCREPANCY LOGIC ----------------
def analyze_discrepancies(df):
    df = exclude_damage_missing(df)
    results = []

    # Partial bin errors: Qty > 5 or PalletCount > 1
    partial_df = get_partial_bins(df)
    partial_errors = partial_df[(partial_df["Qty"] > 5) | (partial_df["PalletCount"] > 1)]
    for _, row in partial_errors.iterrows():
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", "")
        })

    # Full pallet bin errors: Qty not between 6–15
    full_df = df[
        ((~df["LocationName"].astype(str).str.endswith("01")) | (df["LocationName"].astype(str).str.startswith("111"))) &
        (df["LocationName"].astype(str).str.isnumeric())
    ]
    full_errors = full_df[~full_df["Qty"].between(6, 15)]
    for _, row in full_errors.iterrows():
        results.append({
            "LocationName": row.get("LocationName", ""),
            "Qty": row.get("Qty", ""),
            "WarehouseSku": row.get("WarehouseSku", ""),
            "PalletId": row.get("PalletId", ""),
            "CustomerLotReference": row.get("CustomerLotReference", "")
        })

    return pd.DataFrame(results)

discrepancy_df = analyze_discrepancies(filtered_inventory_df)

# ---------------- KPI CARDS ----------------
kpi_data = [
    {"title": "Empty Bins", "value": len(empty_bins_view_df), "icon": "📦"},
    {"title": "Full Pallet Bins", "value": len(full_pallet_bins_df), "icon": "🟩"},
    {"title": "Empty Partial Bins", "value": len(empty_partial_bins_df), "icon": "🟨"},
    {"title": "Partial Bins", "value": len(partial_bins_df), "icon": "🟥"},
    {"title": "Damages", "value": len(damages_df), "icon": "🛠️"},
    {"title": "Missing", "value": len(missing_df), "icon": "❓"},
    {"title": "Discrepancies", "value": len(discrepancy_df), "icon": "⚠️"},
    {"title": "Bulk Discrepancies", "value": len(bulk_df), "icon": "📦"}
]

cols = st.columns(len(kpi_data))
for i, item in enumerate(kpi_data):
    with cols[i]:
        if st.button(f"{item['icon']} {item['title']} | {item['value']}", key=item['title']):
            st.session_state.active_view = item['title']

# ---------------- UI ----------------
st.markdown(f"### 🔍 Viewing: {st.session_state.active_view}")
search_location = st.text_input("🔍 Filter by Location")

def display_table(df, default_columns=None):
    if default_columns is None:
        default_columns = ["WarehouseSku", "CustomerLotReference", "LocationName", "PalletId", "Qty"]
    available_columns = [col for col in default_columns if col in df.columns]
    renamed_cols = {
        "WarehouseSku": "SKU",
        "CustomerLotReference": "LOT",
        "LocationName": "Location",
        "PalletId": "Pallet ID",
        "Qty": "Quantity"
    }
    df = df[available_columns].rename(columns=renamed_cols)
    if "Location" in df.columns and search_location:
        df = df[df["Location"].str.contains(search_location, case=False, na=False)]
    st.dataframe(df, use_container_width=True)

if st.session_state.active_view == "Bulk Discrepancies":
    display_table(bulk_df)

elif st.session_state.active_view == "Discrepancies":
    display_table(discrepancy_df)

elif st.session_state.active_view == "Empty Bins":
    display_table(empty_bins_view_df, default_columns=["LocationName"])

elif st.session_state.active_view == "Full Pallet Bins":
    display_table(full_pallet_bins_df)

elif st.session_state.active_view == "Empty Partial Bins":
    display_table(empty_partial_bins_df, default_columns=["LocationName"])

elif st.session_state.active_view == "Partial Bins":
    display_table(partial_bins_df)

elif st.session_state.active_view == "Damages":
    display_table(damages_df)

elif st.session_state.active_view == "Missing":
    display_table(missing_df)