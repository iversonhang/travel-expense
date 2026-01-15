import streamlit as st
import os
import json
import base64
import re
import pandas as pd
import requests 
from datetime import datetime
from dotenv import load_dotenv 
from google import genai
from google.genai import types
from PIL import Image
from github import Github
import fitz 

# --- 0. Initial Configuration ---
load_dotenv()
st.set_page_config(page_title="AI Proportional Splitter v3", layout="wide") 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY") 
REPO_NAME = "iversonhang/travel-expense" 
FILE_PATH = "expense_records.txt"

ALLOWED_USERS = ["TWH", "TSH"] 
BASE_CURRENCY = "HKD" 
AVAILABLE_CURRENCIES = ["HKD", "JPY"] 

@st.cache_resource
def init_gemini_client():
    if not GEMINI_API_KEY: return None
    try: return genai.Client(api_key=GEMINI_API_KEY)
    except: return None

gemini_client = init_gemini_client()

# --- 1. GitHub Data Sync Logic ---

def save_df_to_github(df):
    """Converts a DataFrame back into the custom log format and uploads to GitHub."""
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        sha = file.sha
    except: sha = None

    lines = []
    for _, r in df.iterrows():
        # Match the exact format we use for parsing
        line = (f"[{r['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}] User: {r['User']}, Shop: {r['Shop']}, "
                f"Total: {r['Total_HKD']:.2f} HKD, Date: {r['Date']}, "
                f"Shared: {r['Shared']}, TWH_n: {r['TWH_n']}, TSH_n: {r['TSH_n']}, "
                f"Orig: {r['Original']}, Rem: {r['Remarks']}\n")
        lines.append(line)
    
    new_content = "".join(lines)
    if sha:
        repo.update_file(FILE_PATH, "Update/Delete records via UI", new_content, sha)
    else:
        repo.create_file(FILE_PATH, "Init records via UI", new_content)
    st.success("✨ GitHub records synchronized successfully!")

def read_and_parse_records_to_df():
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        content = base64.b64decode(repo.get_contents(FILE_PATH).content).decode('utf-8')
    except: return pd.DataFrame()
    
    records = []
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?) HKD, Date: (?P<d>.*?), Shared: (?P<sh>.*?), TWH_n: (?P<tn>\d+), TSH_n: (?P<sn>\d+), Orig: (?P<oa>.*?) (?P<oc>.*?), Rem: (?P<r>.*?)$', re.MULTILINE)
    
    for m in pattern.finditer(content):
        d = m.groupdict()
        records.append({
            'timestamp': pd.to_datetime(d['ts']), 
            'User': d['u'], 
            'Shop': d['s'], 
            'Total_HKD': float(d['t']), 
            'Date': d['d'], 
            'Shared': d['sh'].strip(),
            'TWH_n': int(d['tn']), 
            'TSH_n': int(d['sn']),
            'Original': f"{d['oa']} {d['oc']}", 
            'Remarks': d['r']
        })
    return pd.DataFrame(records)

# --- 2. Page Rendering: History (Edit/Delete) ---

def render_history_page():
    st.title("📚 Detailed Logs (Edit/Delete)")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.info("No records found.")
        return

    # 1. Settlement Logic
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum()
        balance = twh_paid - twh_should

        st.subheader("🤝 Current Balance")
        if balance > 0: st.success(f"**TSH owes TWH: {abs(balance):,.1f} HKD**")
        elif balance < 0: st.warning(f"**TWH owes TSH: {abs(balance):,.1f} HKD**")
        else: st.info("Everything is settled!")

    st.markdown("---")
    
    # 2. Interactive Data Editor
    st.subheader("📝 Edit or Remove Records")
    st.caption("Instructions: Double-click cells to edit. Select a row and press 'Backspace' to delete. Click 'Save Changes' to update GitHub.")

    # Configure the columns for a better UI
    edited_df = st.data_editor(
        df,
        column_config={
            "timestamp": None,  # Hide internal timestamp
            "User": st.column_config.SelectboxColumn("Payer", options=ALLOWED_USERS, required=True),
            "Shared": st.column_config.SelectboxColumn("Split?", options=["Yes", "No"], required=True),
            "Total_HKD": st.column_config.NumberColumn("Amount (HKD)", format="%.2f"),
            "TWH_n": st.column_config.NumberColumn("TWH Count", min_value=0),
            "TSH_n": st.column_config.NumberColumn("TSH Count", min_value=0),
        },
        num_rows="dynamic", # Allows adding/deleting rows
        use_container_width=True,
        key="history_editor"
    )

    if st.button("💾 Save Changes to GitHub", type="primary"):
        with st.spinner("Syncing..."):
            save_df_to_github(edited_df)
            st.rerun()

# --- (Other Functions: render_submission_page, main remain largely the same) ---
# ... [Keeping the previous helper functions for currency, pdf, and main()]

def get_live_exchange_rate(from_curr, to_curr):
    if not EXCHANGE_RATE_API_KEY: return None
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/{from_curr}/{to_curr}"
        res = requests.get(url, timeout=5).json()
        return res.get("conversion_rate") if res.get("result") == "success" else None
    except: return None

def convert_currency(amount, from_currency):
    if from_currency == BASE_CURRENCY: return float(amount), 1.0
    rate = get_live_exchange_rate(from_currency, BASE_CURRENCY)
    return (float(amount * rate), float(rate)) if rate else (float(amount), 0.0)

def write_to_github_file(data):
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        content = base64.b64decode(file.content).decode('utf-8')
        sha = file.sha
    except: content, sha = "", None
    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {data['user_name']}, Shop: {data['shop_name']}, "
            f"Total: {data['total_amount']:.2f} HKD, Date: {data['transaction_date']}, "
            f"Shared: {data['is_shared']}, TWH_n: {data['twh_n']}, TSH_n: {data['tsh_n']}, "
            f"Orig: {data['orig_amt']:.2f} {data['orig_curr']}, Rem: {data['remarks']}\n")
    new_content = content + line
    if sha: repo.update_file(FILE_PATH, "Add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "Init", new_content)

def render_submission_page(def_twh, def_tsh):
    st.title("💸 New Expense")
    mode = st.radio("Input", ["📸 AI OCR", "✍️ Manual"], horizontal=True)
    with st.form("sub"):
        user = st.selectbox("Payer", ALLOWED_USERS)
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("Split?", value=True)
        twh_n = col_n1.number_input("TWH Members", min_value=1, value=def_twh)
        tsh_n = col_n2.number_input("TSH Members", min_value=1, value=def_tsh)
        s_n = st.text_input("Shop")
        a_n = st.number_input("Amount", format="%.2f")
        c_n = st.selectbox("Currency", AVAILABLE_CURRENCIES)
        d_n = st.date_input("Date")
        rem = st.text_input("Remarks")
        if st.form_submit_button("Submit"):
            amt_hkd, _ = convert_currency(a_n, c_n)
            write_to_github_file({"user_name": user, "shop_name": s_n, "total_amount": amt_hkd, "transaction_date": str(d_n), "is_shared": "Yes" if is_shared else "No", "twh_n": twh_n, "tsh_n": tsh_n, "orig_amt": a_n, "orig_curr": c_n, "remarks": rem})
            st.success("Added!")

def main():
    st.sidebar.title("⚙️ Settings")
    def_twh = st.sidebar.number_input("TWH Def.", value=3)
    def_tsh = st.sidebar.number_input("TSH Def.", value=4)
    page = st.sidebar.radio("Nav", ["Submit", "History"])
    if page == "Submit": render_submission_page(def_twh, def_tsh)
    else: render_history_page()

if __name__ == "__main__":
    main()
