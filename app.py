import os
import pandas as pd
import streamlit as st
from datetime import datetime

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bin Helper", layout="wide")

# ---------------- SIDEBAR ----------------
st.sidebar.title("📦 Bin Helper")
st.sidebar.markdown("### 🔍 Search Filters")
search_location = st.sidebar.text_input("Location Name")
search_pallet = st.sidebar.text_input("Pallet ID")
search_lot = st.sidebar.text_input("Customer Lot Reference")
search_sku = st.sidebar.text_input("Warehouse SKU")

# ---------------- CORRECTION LOG VIEWER ----------------
st.sidebar.markdown("### 📋 Correction Log")
log_file = "correction_log.csv"
if os.path.exists(log_file):
    log_df = pd.read_csv(log_file)
    st.sidebar.dataframe(log_df, use_container_width=True)
    st.sidebar.download_button("⬇️ Download Correction Log", data=log_df.to_csv(index=False), file_name="correction_log.csv", mime="text/csv")
else:
    st.sidebar.info("No correction log found yet.")

# ---------------- SAMPLE DATA (Replace with actual logic) ----------------
discrepancy_df = pd.DataFrame([
    {"LocationName": "12303402", "Qty": 30, "Issue": "Multiple pallets in same location (2 pallets)"},
    {"LocationName": "12303402", "Qty": 4, "Issue": "Partial pallet needs to be moved to partial location"},
    {"LocationName": "12305303", "Qty": 10, "Issue": "Inventory found in future bulk location"}
])

bulk_df = pd.DataFrame([
    {"Location": "I032", "Current Pallets": 7, "Max Allowed": 5, "Issue": "Too many pallets (7 > 5)"}
])

inventory_df = pd.DataFrame([
    {"LocationName": "12303402", "WarehouseSku": "10001617", "PalletId": "JTL08933", "CustomerLotReference": "9062258"},
    {"LocationName": "12303402", "WarehouseSku": "G1HCS000625", "PalletId": "JTL09972", "CustomerLotReference": "9062099"}
])

# ---------------- LOGGING FUNCTION ----------------
def log_correction(location, issue, sku, pallet_id, lot, notes):
    log_entry = {
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "LocationName": location,
        "Issue": issue,
        "Correction": "Marked Corrected",
        "WarehouseSku": sku,
        "PalletId": pallet_id,
        "CustomerLotReference": lot,
        "Notes": notes
    }
    log_df = pd.DataFrame([log_entry])
    if os.path.exists("correction_log.csv"):
        log_df.to_csv("correction_log.csv", mode="a", header=False, index=False)
    else:
        log_df.to_csv("correction_log.csv", index=False)

# ---------------- UI ----------------
st.title("⚠️ Discrepancies")

filtered_df = discrepancy_df.copy()
if search_location:
    filtered_df = filtered_df[filtered_df["LocationName"].astype(str).str.contains(search_location, case=False, na=False)]

for loc in filtered_df["LocationName"].unique():
    loc_issues = filtered_df[filtered_df["LocationName"] == loc]
    with st.expander(f"📍 Location: {loc} — {len(loc_issues)} issue(s)"):
        st.write("### Issues")
        st.dataframe(loc_issues[["Issue", "Qty"]], use_container_width=True)
        details = inventory_df[inventory_df["LocationName"] == loc]
        st.write("### Inventory Details")
        st.dataframe(details[["WarehouseSku", "PalletId", "CustomerLotReference"]], use_container_width=True)

        for idx, row in loc_issues.iterrows():
            issue = row["Issue"]
            qty = row["Qty"]
            notes = st.text_input(f"📝 Notes for {loc} - {issue}", key=f"note_{loc}_{idx}")
            if st.button(f"✔ Mark as Corrected: {issue}", key=f"btn_{loc}_{idx}"):
                if details.empty:
                    log_correction(loc, issue, "", "", "", notes)
                else:
                    for _, drow in details.iterrows():
                        log_correction(loc, issue, drow.get("WarehouseSku", ""), drow.get("PalletId", ""), drow.get("CustomerLotReference", ""), notes)
                st.success(f"✅ Correction logged for {loc} — {issue}")

# ---------------- BULK DISCREPANCIES ----------------
st.title("📦 Bulk Discrepancies")

filtered_bulk_df = bulk_df[bulk_df["Issue"] != ""].copy()
if search_location:
    filtered_bulk_df = filtered_bulk_df[filtered_bulk_df["Location"].astype(str).str.contains(search_location, case=False, na=False)]

for loc in filtered_bulk_df["Location"].unique():
    loc_issues = filtered_bulk_df[filtered_bulk_df["Location"] == loc]
    with st.expander(f"📍 Bulk Location: {loc} — {len(loc_issues)} issue(s)"):
        st.write("### Issues")
        st.dataframe(loc_issues[["Issue", "Current Pallets", "Max Allowed"]], use_container_width=True)
        details = inventory_df[inventory_df["LocationName"] == loc]
        st.write("### Inventory Details")
        st.dataframe(details[["WarehouseSku", "PalletId", "CustomerLotReference"]], use_container_width=True)

        for idx, row in loc_issues.iterrows():
            issue = row["Issue"]
            notes = st.text_input(f"📝 Notes for {loc} - {issue}", key=f"bulk_note_{loc}_{idx}")
            if st.button(f"✔ Mark as Corrected: {issue}", key=f"bulk_btn_{loc}_{idx}"):
                if details.empty:
                    log_correction(loc, issue, "", "", "", notes)
                else:
                    for _, drow in details.iterrows():
                        log_correction(loc, issue, drow.get("WarehouseSku", ""), drow.get("PalletId", ""), drow.get("CustomerLotReference", ""), notes)
                st.success(f"✅ Correction logged for {loc} — {issue}")