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
st.set_page_config(page_title="AI Proportional Splitter v5 (Stable)", layout="wide") 

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
    try: return genai.Client(api_key=GEMINI_API_KEY)
    except: return None

gemini_client = init_gemini_client()

# --- 1. Helper Functions (Fixed for Robustness) ---

@st.cache_data(ttl=3600)
def get_live_exchange_rate(from_curr, to_curr):
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/{from_curr}/{to_curr}"
        res = requests.get(url, timeout=5).json()
        return res.get("conversion_rate") if res.get("result") == "success" else None
    except: return None

def convert_currency(amount, from_currency):
    """
    Robust currency converter that handles strings, symbols, and float errors.
    """
    # 1. Clean the amount input
    try:
        if isinstance(amount, str):
            # Remove common symbols like $, ¥, and commas
            clean_amt = re.sub(r'[^\d.]', '', amount) 
            amount = float(clean_amt) if clean_amt else 0.0
        else:
            amount = float(amount)
    except (ValueError, TypeError):
        amount = 0.0

    # 2. Handle Currency
    if not from_currency:
        from_currency = "HKD"
    from_currency = str(from_currency).upper()

    # 3. Conversion Logic
    if from_currency == BASE_CURRENCY: 
        return amount, 1.0
    
    rate = get_live_exchange_rate(from_currency, BASE_CURRENCY)
    if rate:
        return float(amount * rate), float(rate)
    
    return amount, 0.0 # Fallback if rate fails

def pdf_to_images(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.tobytes("ppm"))
    doc.close()
    return img

# --- 2. GitHub Operations ---

def save_df_to_github(df):
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        sha = file.sha
    except: sha = None

    lines = []
    # Ensure timestamp is sorted or preserved
    for _, r in df.iterrows():
        ts_str = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(r['timestamp'], pd.Timestamp) else str(r['timestamp'])
        
        # Format line strictly for Regex parsing
        line = (f"[{ts_str}] User: {r['User']}, Shop: {r['Shop']}, "
                f"Total: {float(r['Total_HKD']):.2f} HKD, Date: {r['Date']}, "
                f"Shared: {r['Shared']}, TWH_n: {r['TWH_n']}, TSH_n: {r['TSH_n']}, "
                f"Orig: {r['Original']}, Rem: {r['Remarks']}\n")
        lines.append(line)
    
    new_content = "".join(lines)
    if sha: repo.update_file(FILE_PATH, "Update/Delete via UI", new_content, sha)
    else: repo.create_file(FILE_PATH, "Init records", new_content)
    st.success("✅ GitHub records updated successfully!")

def write_to_github_file(data):
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        content = base64.b64decode(file.content).decode('utf-8')
        sha = file.sha
    except: content, sha = "", None

    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {data['user_name']}, Shop: {data['shop_name']}, "
            f"Total: {float(data['total_amount']):.2f} HKD, Date: {data['transaction_date']}, "
            f"Shared: {data['is_shared']}, TWH_n: {data['twh_n']}, TSH_n: {data['tsh_n']}, " 
            f"Orig: {data['orig_amt']} {data['orig_curr']}, Rem: {data['remarks']}\n")
    
    new_content = content + line
    if sha: repo.update_file(FILE_PATH, "add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "create file", new_content)

def read_and_parse_records_to_df():
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        content = base64.b64decode(repo.get_contents(FILE_PATH).content).decode('utf-8')
    except: return pd.DataFrame()
    
    records = []
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?) HKD, Date: (?P<d>.*?), Shared: (?P<sh>.*?), TWH_n: (?P<tn>\d+), TSH_n: (?P<sn>\d+), Orig: (?P<oa>.*?) (?P<oc>.*?), Rem: (?P<r>.*?)$', re.MULTILINE)
    
    for m in pattern.finditer(content):
        d = m.groupdict()
        try:
            records.append({
                'timestamp': pd.to_datetime(d['ts']), 
                'User': d['u'], 'Shop': d['s'], 
                'Total_HKD': float(d['t']), 'Date': d['d'], 
                'Shared': d['sh'].strip(),
                'TWH_n': int(d['tn']), 'TSH_n': int(d['sn']),
                'Original': f"{d['oa']} {d['oc']}", 'Remarks': d['r']
            })
        except:
            continue # Skip malformed lines
    
    if not records:
        return pd.DataFrame(columns=['timestamp', 'User', 'Shop', 'Total_HKD', 'Date', 'Shared', 'TWH_n', 'TSH_n', 'Original', 'Remarks'])

    return pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)

# --- 3. UI: Submission Page ---

def render_submission_page(def_twh, def_tsh):
    st.title("💸 Submit Expense")
    mode = st.radio("Input Mode", ["📸 AI OCR", "✍️ Manual"])
    
    with st.form("sub_form"):
        user = st.selectbox("Payer", ALLOWED_USERS)
        remarks = st.text_input("Remarks")
        
        st.markdown("---")
        st.write("🔧 **Split Settings**")
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("Split this bill?", value=True)
        twh_n = col_n1.number_input("TWH Count", min_value=1, value=def_twh)
        tsh_n = col_n2.number_input("TSH Count", min_value=1, value=def_tsh)
        st.markdown("---")

        if mode == "📸 AI OCR":
            up = st.file_uploader("Upload Receipt", type=['jpg','png','pdf'])
        else:
            s_n = st.text_input("Shop Name")
            a_n = st.number_input("Amount", step=1.0)
            c_n = st.selectbox("Currency", AVAILABLE_CURRENCIES)
            d_n = st.date_input("Date")

        if st.form_submit_button("Submit Record"):
            ocr_data = None
            
            # --- AI Logic ---
            if mode == "📸 AI OCR" and up:
                with st.spinner("Gemini 2.5 Lite Analyzing..."):
                    img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                    try:
                        # Improved prompt to ask for clean numbers
                        res = gemini_client.models.generate_content(
                            model='gemini-2.5-flash-lite',
                            contents=["Extract vendor as 'shop_name', total amount (number only) as 'total_amount', currency code (e.g. HKD, JPY), date (YYYY-MM-DD). JSON format.", img],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        ocr_data = json.loads(res.text)
                    except Exception as e:
                        st.error(f"AI Error: {e}")
                        return
            else:
                # Manual Logic
                ocr_data = {"shop_name": s_n, "total_amount": a_n, "currency": c_n, "transaction_date": str(d_n)}

            # --- Data Processing & Save ---
            if ocr_data:
                # Safe Extraction using .get() to prevent KeyErrors
                raw_amt = ocr_data.get('total_amount', 0)
                raw_curr = ocr_data.get('currency', 'HKD')
                shop = ocr_data.get('shop_name', 'Unknown Shop')
                date_str = ocr_data.get('transaction_date', str(datetime.now().date()))
                
                # Robust Conversion
                amt_hkd, rate = convert_currency(raw_amt, raw_curr)
                
                # Prepare Original Amount string safely
                try:
                    orig_amt_clean = float(re.sub(r'[^\d.]', '', str(raw_amt))) if raw_amt else 0.0
                except:
                    orig_amt_clean = 0.0

                write_to_github_file({
                    "user_name": user, 
                    "shop_name": shop, 
                    "total_amount": amt_hkd,
                    "transaction_date": date_str, 
                    "is_shared": "Yes" if is_shared else "No",
                    "twh_n": twh_n, 
                    "tsh_n": tsh_n, 
                    "orig_amt": orig_amt_clean,
                    "orig_curr": raw_curr, 
                    "remarks": remarks
                })
                st.success("✅ Saved Successfully!")

# --- 4. UI: History Page ---

def render_history_page(def_twh, def_tsh):
    st.title("📚 History & Management")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.info("No records found.")
        return

    # --- 1. Settlement Dashboard ---
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        # Calculate Owed Amount Per Row
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        shared_df['TSH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TSH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)

        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum()
        tsh_should = shared_df['TSH_Owe'].sum()
        
        balance = twh_paid - twh_should 

        st.subheader("🤝 Settlement (HKD)")
        c1, c2, c3 = st.columns(3)
        c1.metric("TWH Total Paid", f"{twh_paid:,.1f}")
        c2.metric("TWH Target Share", f"{twh_should:,.1f}")
        
        if balance > 0:
            c3.success(f"💰 **TSH owes TWH: {abs(balance):,.1f}**")
        elif balance < 0:
            c3.warning(f"💰 **TWH owes TSH: {abs(balance):,.1f}**")
        else:
            c3.info("✅ Settled")

        # --- Average Cost Calculation ---
        avg_twh = twh_should / def_twh if def_twh > 0 else 0
        avg_tsh = tsh_should / def_tsh if def_tsh > 0 else 0

        st.markdown(f"##### 📊 Cost Per Person (Based on Settings: TWH={def_twh}, TSH={def_tsh})")
        k1, k2 = st.columns(2)
        k1.metric(f"TWH (Per Person)", f"${avg_twh:,.1f} HKD")
        k2.metric(f"TSH (Per Person)", f"${avg_tsh:,.1f} HKD")

    st.markdown("---")

    # --- 2. Interactive Editor ---
    st.subheader("📝 Edit / Delete Records")
    st.caption("Instructions: Edit cells directly. Select row(s) and press Delete key to remove. Click Save to Sync.")

    edited_df = st.data_editor(
        df,
        column_config={
            "timestamp": None,
            "User": st.column_config.SelectboxColumn("Payer", options=ALLOWED_USERS, required=True),
            "Shop": st.column_config.TextColumn("Shop"),
            "Total_HKD": st.column_config.NumberColumn("Amount (HKD)", format="%.2f"),
            "Shared": st.column_config.SelectboxColumn("Shared", options=["Yes", "No"]),
            "TWH_n": st.column_config.NumberColumn("TWH #", min_value=1),
            "TSH_n": st.column_config.NumberColumn("TSH #", min_value=1),
            "Original": st.column_config.TextColumn("Original"),
        },
        num_rows="dynamic",
        use_container_width=True,
        key="data_editor"
    )

    if st.button("💾 Save Changes to GitHub", type="primary"):
        with st.spinner("Syncing to GitHub..."):
            save_df_to_github(edited_df)
            st.rerun()

# --- 5. Main Execution ---

def main():
    st.sidebar.title("⚙️ Settings")
    
    with st.sidebar.expander("👥 Headcount Defaults", expanded=True):
        def_twh = st.number_input("TWH Group Size", min_value=1, value=3)
        def_tsh = st.number_input("TSH Group Size", min_value=1, value=4)

    st.sidebar.markdown("---")
    page = st.sidebar.radio("Navigate", ["Submit Expense", "View History"])
    
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("Rate (JPY->HKD)", f"{rate:.4f}")
    
    if page == "Submit Expense":
        render_submission_page(def_twh, def_tsh)
    else:
        render_history_page(def_twh, def_tsh)

if __name__ == "__main__":
    main()
