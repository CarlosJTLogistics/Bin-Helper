import streamlit as st
import pandas as pd
import streamlit.components.v1 as components

# --- Welcome Animation ---
if "welcome_shown" not in st.session_state:
    st.session_state.welcome_shown = True
    st.markdown("<h1 style='text-align:center;'>👋 Welcome to Bin Helper</h1>", unsafe_allow_html=True)
    components.html("""
    https://unpkg.com/@lottiefiles/lottie-player@latest/dist/lottie-player.js</script>
    https://assets10.lottiefiles.com/packages/lf20_qp1q7mct.json
    </lottie-player>
    """, height=300)
    st.stop()

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

    # KPI Cards
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Empty Bins", len(empty_bins_df))
    kpi_cols[1].metric("Full Pallet Bins", len(full_pallet_bins_df))
    kpi_cols[2].metric("Empty Partial Bins", len(empty_partial_bins_df))
    kpi_cols[3].metric("Partial Bins", len(partial_bins_df))

    kpi_cols2 = st.columns(4)
    kpi_cols2[0].metric("Damages", len(damages_df))
    kpi_cols2[1].metric("Missing", len(missing_df))
    kpi_cols2[2].metric("Rack Discrepancies", len(rack_discrepancies))
    kpi_cols2[3].metric("Bulk Discrepancies", len(bulk_discrepancies))

    # Summary Insights
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