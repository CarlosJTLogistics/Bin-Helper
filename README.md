# Bin Helper Dashboard

This is a Streamlit app for warehouse inventory management.

## How to Deploy on Streamlit Community Cloud

1. Push these files to a **public GitHub repository**:
   - `app.py`
   - `requirements.txt`
   - `ON_HAND_INVENTORY.xlsx`
   - `Empty Bin Formula.xlsx`
   - (Optional) `box_animation.json`

2. Go to Streamlit Cloud and sign in with GitHub.

3. Click **New app**, select your repo, branch, and `app.py`.

4. Click **Deploy**. Streamlit will install dependencies from `requirements.txt`.

## Local Run

To run locally:

pip install -r requirements.txt
streamlit run app.py