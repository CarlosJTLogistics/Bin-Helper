# 📦 Bin Helper — Warehouse Inventory Dashboard (Streamlit)

**Bin Helper** is a fast, lightweight Streamlit app to monitor warehouse bin status, locate inventory, and keep an eye on damages and missing stock. It’s optimized for daily use, mobile-friendly, and designed to play nicely with OneDrive/Excel saves.

---

## ✨ Features

- **Clickable KPI cards** (the card *is* the button)  
  - Empty Bins, Full Pallet Bins, Empty Partial Bins, Partial Bins  
  - **Damages (DAMAGE & IBDAMAGE)** and **Missing** split into their own KPIs & tabs
- **Theme selector** in sidebar  
  - Metallic Silver (Blue Outline), Neutral Light, Dark Slate, Legacy
- **Resilient Excel reads**  
  - Staging layer + retries to avoid OneDrive/Excel lock errors  
  - Auto-detects file changes and reloads on next rerun
- **Modern refresh**  
  - `st.query_params` + `st.rerun()` (no deprecated APIs)
- **Filters** for SKU, LOT Number, and Pallet ID
- **Mobile‑responsive UI**

---

## 🗂️ Project structure