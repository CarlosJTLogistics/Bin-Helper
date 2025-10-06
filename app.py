import streamlit as st
import pandas as pd

# Load real data sources
@st.cache_data
def load_data():
    # Load ON_HAND_INVENTORY.xlsx for bulk zone logic
    on_hand_df = pd.read_excel("ON_HAND_INVENTORY.xlsx", engine="openpyxl")
    # Load Empty Bin Formula.xlsx for empty bin logic
    empty_bin_df = pd.read_excel("Empty Bin Formula.xlsx", engine="openpyxl")
    return on_hand_df, empty_bin_df

# Load data
on_hand_df, empty_bin_df = load_data()

# Sidebar filter
location_options = sorted(on_hand_df['LOCATION'].dropna().unique())
selected_location = st.sidebar.selectbox("📍 Filter by Location", options=["All"] + location_options)
if selected_location != "All":
    on_hand_df = on_hand_df[on_hand_df['LOCATION'] == selected_location]

# Remove duplicates
on_hand_df = on_hand_df.drop_duplicates(subset=['BIN'])

# Full pallet logic
def get_full_pallet_bins(df):
    return df[(df['QTY'] >= df['PALLET_QTY']) & (df['PALLET_QTY'] > 0)]

# Partial pallet logic
def get_partial_pallet_bins(df):
    return df[(df['QTY'] > 0) & (df['QTY'] < df['PALLET_QTY'])]

# Bulk zone logic
def get_bulk_zone_data(df):
    bulk_df = df[df['ZONE'].str.contains("BULK", case=False, na=False)]
    bin_count = bulk_df['BIN'].nunique()
    total_qty = bulk_df['QTY'].sum()
    has_discrepancy = (bulk_df['QTY'] < 0).any()
    return bin_count, total_qty, has_discrepancy, bulk_df

# Empty bin logic
def get_empty_bin_data(df):
    bin_count = df['BIN'].nunique()
    total_qty = df['QTY'].sum() if 'QTY' in df.columns else 0
    has_discrepancy = df['BIN'].isnull().any()
    return bin_count, total_qty, has_discrepancy, df

# KPI card component
def kpi_card(title, qty, bins, warning=False):
    color = "#00f0ff" if not warning else "#ff4b4b"
    icon = "⚠️" if warning else "📦"
    st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:20px; border-radius:10px; text-align:center; box-shadow: 0 0 10px #00f0ff;">
            <div style="font-size:40px; color:{color}; font-weight:bold;">{icon} {qty} / {bins}</div>
            <div style="font-size:18px; color:white; margin-top:5px;">{title}</div>
        </div>
    """, unsafe_allow_html=True)

# Dashboard layout
st.set_page_config(layout="wide")
st.title("🚀 Bin Helper Dashboard")

# KPI Row
col1, col2 = st.columns(2)

with col1:
    bin_count1, total_qty1, warning1, bulk_df = get_bulk_zone_data(on_hand_df)
    if st.button("Go to Bulk Zone Tab"):
        st.session_state['active_tab'] = "Bulk Zone"
    kpi_card("Bulk Zone KPI", total_qty1, bin_count1, warning=warning1)

with col2:
    bin_count2, total_qty2, warning2, empty_df = get_empty_bin_data(empty_bin_df)
    if st.button("Go to Empty Bin Tab"):
        st.session_state['active_tab'] = "Empty Bin"
    kpi_card("Empty Bin KPI", total_qty2, bin_count2, warning=warning2)

# Tab logic
if 'active_tab' not in st.session_state:
    st.session_state['active_tab'] = "Bulk Zone"

st.markdown(f"### 🔍 Currently Viewing: {st.session_state['active_tab']}")

# Display filtered data for the selected tab
if st.session_state['active_tab'] == "Bulk Zone":
    st.subheader("📦 Bulk Zone Bins")
    st.dataframe(bulk_df)

    st.subheader("✅ Full Pallet Bins")
    full_pallet_bins_df = get_full_pallet_bins(bulk_df)
    st.dataframe(full_pallet_bins_df)

    st.subheader("🪙 Partial Pallet Bins")
    partial_pallet_bins_df = get_partial_pallet_bins(bulk_df)
    st.dataframe(partial_pallet_bins_df)

    if partial_pallet_bins_df.empty:
        st.success("🎉 No partial pallet discrepancies found!")
    else:
        st.warning(f"⚠️ {len(partial_pallet_bins_df)} partial pallet bins need review.")

elif st.session_state['active_tab'] == "Empty Bin":
    st.subheader("🗑️ Empty Bins")
    st.dataframe(empty_df)

# Discrepancy tab
st.markdown("---")
st.subheader("🚨 Discrepancy Overview")
discrepancy_df = on_hand_df[(on_hand_df['QTY'] < 0) | (on_hand_df['PALLET_QTY'] <= 0)]
if discrepancy_df.empty:
    st.success("✅ No discrepancies found in inventory.")
else:
    st.error(f"⚠️ Found {len(discrepancy_df)} discrepancies in inventory.")
    if st.button("Show Discrepancy Details"):
        st.dataframe(discrepancy_df)

# Git push instructions
st.markdown("""
---
### ✅ Git Push Instructions
Once you've copied the updated code into your local file, run the following commands in your terminal:


cd "C:\\Users\\carlos.pacheco.MYA-LOGISTICS\\OneDrive - JT Logistics\\bin-helper"
git add app.py
git commit -m "Integrated KPI cards, dynamic error messages, grouped bulk analysis, discrepancy tab, filters, and UI polish"
git push origin main