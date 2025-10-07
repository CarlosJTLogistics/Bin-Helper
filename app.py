import streamlit as st
import pandas as pd
import requests
from streamlit_lottie import st_lottie
from datetime import datetime

# --- Welcome Animation using streamlit-lottie ---
def load_lottie_url(url: str):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

if "welcome_shown" not in st.session_state:
    st.session_state.welcome_shown = True
    st.markdown("<h1 style='text-align:center;'>👋 Welcome to Bin Helper</h1>", unsafe_allow_html=True)
    lottie_json = load_lottie_url("https://assets10.lottiefiles.com/packages/lf20_qp1q7mct.json")
    if lottie_json:
        st_lottie(lottie_json, height=300)
    st.markdown("---")

# --- Page Config ---
st.set_page_config(page_title="Bin Helper", layout="wide")

# --- Load Data ---
inventory_url = "https://raw.githubusercontent.com/CarlosJTLogistics/Bin-Helper/main/ON_HAND_INVENTORY.xlsx"
master_url = "https://raw.githubusercontent.com/CarlosJTLogistics/Bin-Helper/main/Empty%20Bin%20Formula.xlsx"

inventory_df = pd.read_excel(inventory_url, engine="openpyxl")
master_df = pd.read_excel(master_url, sheet_name="Master Locations", engine="openpyxl")

# --- Clean Data ---
inventory_df["Qty"] = pd.to_numeric(inventory_df.get("Qty", 0), errors="coerce").fillna(0)
inventory_df["PalletCount"] = pd.to_numeric(inventory_df.get("PalletCount", 0), errors="coerce").fillna(0)

# --- Filter Inventory ---
exclude_locations = ["DAMAGE", "IBDAMAGE", "MISSING"]
filtered_inventory_df = inventory_df[
    ~inventory_df["LocationName"].astype(str).str.upper().isin(exclude_locations) &
    ~inventory_df["LocationName"].astype(str).str.upper().str.startswith("IB")
]

occupied_bins = set(filtered_inventory_df["LocationName"].dropna().astype(str).unique())
master_locations = set(master_df.iloc[1:, 0].dropna().astype(str).unique())

# --- Bin Logic ---
empty_bins_df = pd.DataFrame({"LocationName": [loc for loc in master_locations if loc not in occupied_bins]})
empty_partial_bins_df = pd.DataFrame({
    "LocationName": [loc for loc in master_locations
                     if loc.endswith("01") and not loc.startswith("111") and not str(loc).upper().startswith("TUN")
                     and loc not in occupied_bins]
})
partial_bins_df = filtered_inventory_df[
    filtered_inventory_df["LocationName"].astype(str).str.endswith("01") &
    ~filtered_inventory_df["LocationName"].astype(str).str.startswith("111") &
    ~filtered_inventory_df["LocationName"].astype(str).str.upper().str.startswith("TUN")
]
full_pallet_bins_df = filtered_inventory_df[
    filtered_inventory_df["LocationName"].astype(str).str.startswith("111") &
    filtered_inventory_df["Qty"].between(6, 15)
]

damages_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper().isin(["DAMAGE", "IBDAMAGE"])]
missing_df = inventory_df[inventory_df["LocationName"].astype(str).str.upper() == "MISSING"]

# --- Discrepancy Logic ---
partial_discrepancies = partial_bins_df[(partial_bins_df["Qty"] > 5) | (partial_bins_df["PalletCount"] > 1)]
full_discrepancies = filtered_inventory_df[
    filtered_inventory_df["LocationName"].astype(str).str.startswith("111") &
    ~filtered_inventory_df["Qty"].between(6, 15)
]
rack_discrepancies = filtered_inventory_df[
    filtered_inventory_df["LocationName"].astype(str).str.startswith("111") &
    (filtered_inventory_df["PalletCount"] > 1)
]

bulk_zone_limits = {"A": 5, "B": 4, "C": 4, "D": 4, "E": 4, "F": 4, "G": 4, "H": 4, "I": 4}
bulk_discrepancies = []
for zone, limit in bulk_zone_limits.items():
    zone_df = filtered_inventory_df[filtered_inventory_df["LocationName"].astype(str).str.startswith(zone)]
    zone_pallets = zone_df.groupby("LocationName")["PalletCount"].sum()
    bulk_discrepancies.extend(zone_pallets[zone_pallets > limit].index.tolist())

# --- Tabs ---
tab1, tab2 = st.tabs(["Dashboard Home", "Zone Summary"])

with tab1:
    st.markdown("<h2 style='text-align:center;'>📊 Bin-Helper Dashboard</h2>", unsafe_allow_html=True)

    # --- Interactive KPI Cards ---
    kpi_data = {
        "Empty Bins": {"count": len(empty_bins_df), "icon": "📦", "df": empty_bins_df},
        "Full Pallet Bins": {"count": len(full_pallet_bins_df), "icon": "🟩", "df": full_pallet_bins_df},
        "Empty Partial Bins": {"count": len(empty_partial_bins_df), "icon": "🟨", "df": empty_partial_bins_df},
        "Partial Bins": {"count": len(partial_bins_df), "icon": "🟥", "df": partial_bins_df},
        "Damages": {"count": len(damages_df), "icon": "🛠️", "df": damages_df},
        "Missing": {"count": len(missing_df), "icon": "❌", "df": missing_df},
        "Rack Discrepancies": {"count": len(rack_discrepancies), "icon": "⚠️", "df": rack_discrepancies},
        "Bulk Discrepancies": {"count": len(bulk_discrepancies), "icon": "📚", "df": pd.DataFrame({"LocationName": bulk_discrepancies})}
    }

    selected_kpi = st.selectbox("🔍 Click a KPI to view details", list(kpi_data.keys()))
    st.markdown(f"### {kpi_data[selected_kpi]['icon']} {selected_kpi}: {kpi_data[selected_kpi]['count']}")
    st.dataframe(kpi_data[selected_kpi]["df"])

    # --- Fix Button and Logging ---
    if selected_kpi in ["Rack Discrepancies", "Bulk Discrepancies", "Partial Bins", "Full Pallet Bins"]:
        st.markdown("#### 🛠 Resolve Discrepancy")
        selected_rows = st.multiselect("Select locations to resolve", kpi_data[selected_kpi]["df"]["LocationName"].astype(str).tolist())
        note = st.text_input("Add resolution note")
        if st.button("Fix Selected"):
            if selected_rows and note:
                log_df = pd.DataFrame({
                    "Timestamp": [datetime.now().strftime("%Y-%m-%d %H:%M:%S")] * len(selected_rows),
                    "LocationName": selected_rows,
                    "Note": [note] * len(selected_rows),
                    "Type": [selected_kpi] * len(selected_rows)
                })
                try:
                    existing_log = pd.read_csv("resolved_discrepancies.csv")
                    log_df = pd.concat([existing_log, log_df], ignore_index=True)
                except FileNotFoundError:
                    pass
                log_df.to_csv("resolved_discrepancies.csv", index=False)
                st.success("Discrepancy resolved and logged.")
            else:
                st.warning("Please select locations and enter a note.")

    # --- Summary Insights ---
    st.markdown("### 📋 Summary Insights")
    total_bins = len(master_locations)
    occupied_bins_count = len(occupied_bins)
    empty_bins_count = total_bins - occupied_bins_count
    st.write(f"**Total Bin Locations:** {total_bins}")
    st.write(f"**Occupied Bins:** {occupied_bins_count}")
    st.write(f"**Empty Bins:** {empty_bins_count}")

    discrepancy_counts = {
        "Partial Bin Discrepancies": len(partial_discrepancies),
        "Full Bin Discrepancies": len(full_discrepancies),
        "Rack Discrepancies": len(rack_discrepancies)
    }
    st.write("**Top 3 Discrepancy Types:**")
    for issue, count in sorted(discrepancy_counts.items(), key=lambda x: x[1], reverse=True)[:3]:
        st.write(f"- {issue}: {count}")

with tab2:
    st.markdown("### 🏷 Zone Summary (Qty & PalletCount)")
    zone_summary = (
        filtered_inventory_df[filtered_inventory_df["LocationName"].astype(str).str[0].isin(list("ABCDEFGHI"))]
        .assign(Zone=lambda df: df["LocationName"].astype(str).str[0])
        .groupby("Zone")[["Qty", "PalletCount"]]
        .sum()
        .reset_index()
        .sort_values("Zone")
    )

    zone_cols = st.columns(len(zone_summary))
    for i, row in zone_summary.iterrows():
        with zone_cols[i]:
            st.metric(label=f"Zone {row['Zone']}", value=f"Qty: {int(row['Qty'])}", delta=f"Pallets: {int(row['PalletCount'])}")