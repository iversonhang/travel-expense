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
st.set_page_config(page_title="AI Proportional Splitter", layout="wide") 

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

# --- 1. Core Logic: Currency & PDF ---

@st.cache_data(ttl=3600)
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
    if rate: return float(amount * rate), float(rate)
    return float(amount), 0.0

def pdf_to_images(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.tobytes("ppm"))
    doc.close()
    return img

# --- 2. GitHub Data Operations ---

def write_to_github_file(data):
    if not GITHUB_TOKEN: 
        st.error("GitHub Token Missing")
        return
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        content = base64.b64decode(file.content).decode('utf-8')
        sha = file.sha
    except: content, sha = "", None

    # New Unified Log Format
    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {data['user_name']}, Shop: {data['shop_name']}, "
            f"Total: {data['total_amount']:.2f} HKD, Date: {data['transaction_date']}, "
            f"Shared: {data['is_shared']}, TWH_n: {data['twh_n']}, TSH_n: {data['tsh_n']}, "
            f"Orig: {data['orig_amt']:.2f} {data['orig_curr']}, Rem: {data['remarks']}\n")
    
    new_content = content + line
    if sha: repo.update_file(FILE_PATH, "feat: add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "init: create log", new_content)
    st.session_state.df_records = pd.DataFrame() 

def read_and_parse_records_to_df():
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        content = base64.b64decode(repo.get_contents(FILE_PATH).content).decode('utf-8')
    except: return pd.DataFrame()
    
    records = []
    # Enhanced Regex to match the new proportional fields
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
    
    if not records:
        return pd.DataFrame(columns=['timestamp', 'User', 'Shop', 'Total_HKD', 'Date', 'Shared', 'TWH_n', 'TSH_n', 'Original', 'Remarks'])
    
    return pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)

# --- 3. Page Rendering: Submission ---

def render_submission_page(def_twh, def_tsh):
    st.title("💸 Expense Submission")
    mode = st.radio("Input Method", ["📸 OCR (Gemini Lite)", "✍️ Manual"], horizontal=True)
    
    with st.form("main_form"):
        user = st.selectbox("Who Paid?", ALLOWED_USERS)
        remarks = st.text_input("Remarks (optional)")
        
        st.markdown("---")
        st.write("🔧 **Proportional Splitting Toolbox**")
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("Split this expense?", value=True)
        twh_n = col_n1.number_input("TWH Members", min_value=1, value=def_twh)
        tsh_n = col_n2.number_input("TSH Members", min_value=1, value=def_tsh)
        st.markdown("---")

        if mode == "📸 OCR (Gemini Lite)":
            up = st.file_uploader("Upload Receipt (Image/PDF)", type=['jpg','png','pdf'])
        else:
            c1, c2, c3 = st.columns(3)
            s_n = c1.text_input("Shop Name")
            a_n = c2.number_input("Amount", format="%.2f")
            c_n = c3.selectbox("Currency", AVAILABLE_CURRENCIES)
            d_n = st.date_input("Date")

        if st.form_submit_button("Submit Record"):
            ocr_data = None
            if mode == "📸 OCR (Gemini Lite)" and up:
                with st.spinner("Gemini Flash-Lite is analyzing..."):
                    img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                    prompt = "Extract from receipt: vendor as 'shop_name', total amount as 'total_amount', currency (3-letter), date (YYYY-MM-DD). JSON format only."
                    try:
                        res = gemini_client.models.generate_content(
                            model='gemini-2.5-flash-lite', 
                            contents=[prompt, img],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        ocr_data = json.loads(res.text)
                    except Exception as e:
                        st.error(f"AI Error: {e}")
            else:
                ocr_data = {"shop_name": s_n, "total_amount": a_n, "currency": c_n, "transaction_date": str(d_n)}

            if ocr_data:
                amt_hkd, rate = convert_currency(ocr_data['total_amount'], ocr_data['currency'])
                write_to_github_file({
                    "user_name": user, "shop_name": ocr_data['shop_name'], "total_amount": amt_hkd,
                    "transaction_date": ocr_data['transaction_date'], "is_shared": "Yes" if is_shared else "No",
                    "twh_n": twh_n, "tsh_n": tsh_n, "orig_amt": ocr_data['total_amount'],
                    "orig_curr": ocr_data['currency'], "remarks": remarks
                })
                st.success("✅ Recorded successfully!")

# --- 4. Page Rendering: History & Settlement ---

def render_history_page():
    st.title("📚 History & Settlement")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.info("No records found yet.")
        return

    # Proportional Calculation logic
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        # Calculate how much TWH and TSH OWE for each specific transaction
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        shared_df['TSH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TSH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        
        # Calculate how much each party PAID
        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum()
        
        balance = twh_paid - twh_should # If positive, TWH is owed money.
        
        st.subheader("🤝 Settlement Summary (HKD)")
        c1, c2, c3 = st.columns(3)
        c1.metric("TWH Total Paid", f"{twh_paid:,.1f}")
        c2.metric("TWH Target Share", f"{twh_should:,.1f}")
        
        if balance > 0:
            c3.success(f"👉 **TSH pays TWH: {abs(balance):,.1f} HKD**")
        elif balance < 0:
            c3.warning(f"👉 **TWH pays TSH: {abs(balance):,.1f} HKD**")
        else:
            c3.info("✅ All settled up!")

    st.markdown("---")
    st.subheader("📋 Detailed Logs")
    
    # Optional JPY Reference Rate for display
    rate_jpy_hkd = get_live_exchange_rate("JPY", "HKD")

    for _, r in df.iterrows():
        # Display dual currency for visual clarity
        val_jpy = (r['Total_HKD'] / rate_jpy_hkd) if rate_jpy_hkd else 0
        
        with st.container():
            col_a, col_b = st.columns([7, 3])
            with col_a:
                st.markdown(f"**{r['Date']}** | **{r['Shop']}**")
                st.caption(f"Paid by {r['User']} | Orig: {r['Original']} | {r['Remarks']}")
            with col_b:
                st.markdown(f"**{r['Total_HKD']:,.1f} HKD**")
                st.caption(f"(≈ ¥{val_jpy:,.0f} JPY)")
                if r['Shared'] == 'Yes':
                    st.markdown(f"<small>👥 Split Ratio {r['TWH_n']}:{r['TSH_n']}</small>", unsafe_allow_html=True)
            st.divider()

# --- 5. Main Loop with Sidebar Settings ---

def main():
    st.sidebar.title("⚙️ Travel Settings")
    
    with st.sidebar.expander("👥 Default Headcounts", expanded=True):
        def_twh = st.number_input("TWH Members", min_value=1, value=3)
        def_tsh = st.number_input("TSH Members", min_value=1, value=4)
        st.caption("These will be pre-filled in your submission form.")

    st.sidebar.markdown("---")
    page = st.sidebar.radio("Navigation", ["Submit Expense", "View History"])
    
    # Global Rate display
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("Live Rate: 1 JPY", f"{rate:.4f} HKD")
    
    if page == "Submit Expense":
        render_submission_page(def_twh, def_tsh)
    else:
        render_history_page()

if __name__ == "__main__":
    main()
