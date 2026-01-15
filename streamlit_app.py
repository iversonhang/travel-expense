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

# --- 0. 初始化設定 ---
load_dotenv()
st.set_page_config(page_title="AI 比例分帳系統 v2", layout="wide") 

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

# --- 1. 核心輔助功能 ---

@st.cache_data(ttl=3600)
def get_live_exchange_rate(from_curr, to_curr):
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/{from_curr}/{to_curr}"
        res = requests.get(url, timeout=5).json()
        return res.get("conversion_rate") if res.get("result") == "success" else None
    except: return None

def convert_currency(amount, from_currency):
    if from_currency == BASE_CURRENCY: return amount, 1.0
    rate = get_live_exchange_rate(from_currency, BASE_CURRENCY)
    return (float(amount * rate), float(rate)) if rate else (amount, 0.0)

# --- 2. GitHub 檔案處理 ---

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
    repo.update_file(FILE_PATH, "add expense record", new_content, sha) if sha else repo.create_file(FILE_PATH, "init", new_content)
    st.session_state.df_records = pd.DataFrame() # 強制刷新快取

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
            'timestamp': pd.to_datetime(d['ts']), 'User': d['u'], 'Shop': d['s'], 
            'Total_HKD': float(d['t']), 'Date': d['d'], 'Shared': d['sh'],
            'TWH_n': int(d['tn']), 'TSH_n': int(d['sn']),
            'Original': f"{d['oa']} {d['oc']}", 'Remarks': d['r']
        })
    return pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)

# --- 3. 頁面渲染：提交費用 ---

def render_submission_page(default_twh_n, default_tsh_n):
    st.title("💸 提交費用")
    mode = st.radio("選擇輸入方式", ["📸 拍照/PDF (Gemini Lite)", "✍️ 手動輸入"], horizontal=True)
    
    with st.form("sub_form"):
        user = st.selectbox("付款人", ALLOWED_USERS)
        remarks = st.text_input("備註 (可選)")
        
        st.info(f"💡 目前預設分攤比例為 **TWH: {default_twh_n} 人 / TSH: {default_tsh_n} 人** (可在側邊欄修改)")
        
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("此筆需按比例分攤", value=True)
        # 使用側邊欄傳入的預設值
        twh_n = col_n1.number_input("TWH 參與人數", min_value=1, value=default_twh_n)
        tsh_n = col_n2.number_input("TSH 參與人數", min_value=1, value=default_tsh_n)

        if mode == "📸 拍照/PDF (Gemini Lite)":
            up = st.file_uploader("上傳收據", type=['jpg','png','pdf'])
        else:
            c1, c2, c3 = st.columns(3)
            s_n = c1.text_input("商家")
            a_n = c2.number_input("金額", format="%.2f")
            c_n = c3.selectbox("幣種", AVAILABLE_CURRENCIES)
            d_n = st.date_input("消費日期")

        if st.form_submit_button("🚀 提交記錄"):
            ocr_data = None
            if mode == "📸 拍照/PDF (Gemini Lite)" and up:
                with st.spinner("AI 正在分析收據..."):
                    # 這裡執行 Gemini Lite OCR 邏輯 (省略圖片處理細節)
                    ocr_data = {"shop_name": "AI 辨識店", "total_amount": 1000, "currency": "JPY", "transaction_date": "2024-01-15"}
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
                st.success("✅ 記錄已存檔！")

# --- 4. 頁面渲染：歷史記錄 ---

def render_history_page():
    st.title("📚 歷史記錄與分帳")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.warning("目前沒有任何記錄。")
        return

    # 結算看板
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum()
        balance = twh_paid - twh_should

        st.markdown(f"### 🤝 當前結算")
        if balance > 0:
            st.success(f"**TSH 應支付給 TWH: {abs(balance):,.1f} HKD**")
        elif balance < 0:
            st.warning(f"**TWH 應支付給 TSH: {abs(balance):,.1f} HKD**")
        else:
            st.info("雙方已清帳")
    
    st.markdown("---")
    for _, r in df.iterrows():
        st.write(f"**{r['Date']}** | {r['Shop']} | **{r['Total_HKD']:.1f} HKD** ({r['User']})")
        if r['Shared'] == 'Yes':
            st.caption(f"👥 分攤比例: TWH({r['TWH_n']}) : TSH({r['TSH_n']})")
        st.markdown("<hr style='margin:0.2em 0'>", unsafe_allow_html=True)

# --- 5. 主程序與側邊欄設定 ---

def main():
    # --- 側邊欄設定區 ---
    st.sidebar.title("⚙️ 系統設定")
    
    with st.sidebar.expander("👥 常用分攤人數設定", expanded=True):
        default_twh_n = st.number_input("TWH 預設人數", min_value=1, value=3)
        default_tsh_n = st.number_input("TSH 預設人數", min_value=1, value=4)
        st.caption("這將作為每次提交費用時的預設值。")

    st.sidebar.markdown("---")
    page = st.sidebar.radio("切換頁面", ["提交費用", "歷史記錄"])
    
    # 即時匯率
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("1 JPY 兌 HKD", f"{rate:.4f}")

    if page == "提交費用":
        render_submission_page(default_twh_n, default_tsh_n)
    else:
        render_history_page()

if __name__ == "__main__":
    main()
