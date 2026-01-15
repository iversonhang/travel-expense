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

# --- 0. 初始化 ---
load_dotenv()
st.set_page_config(page_title="AI 比例分帳系統", layout="wide") 

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

# --- 1. 核心計算函數 ---

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

# --- 2. GitHub 讀寫 (新增人數欄位支援) ---

def write_to_github_file(data):
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        content = base64.b64decode(file.content).decode('utf-8')
        sha = file.sha
    except: content, sha = "", None

    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {data['user_name']}, Shop: {data['shop_name']}, "
            f"Total: {data['total_amount']:.2f} HKD, Date: {data['transaction_date']}, "
            f"Shared: {data['is_shared']}, TWH_n: {data['twh_n']}, TSH_n: {data['tsh_n']}, " # 存儲人數
            f"Orig: {data['orig_amt']:.2f} {data['orig_curr']}, Rem: {data['remarks']}\n")
    
    new_content = content + line
    if sha: repo.update_file(FILE_PATH, "add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "create file", new_content)
    st.session_state.df_records = pd.DataFrame()

# --- 3. 數據解析與按比例結算 ---

def read_and_parse_records_to_df():
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        content = base64.b64decode(repo.get_contents(FILE_PATH).content).decode('utf-8')
    except: return pd.DataFrame()
    
    records = []
    # 更新正則表達式以匹配人數 TWH_n 和 TSH_n
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?) HKD, Date: (?P<d>.*?), Shared: (?P<sh>.*?), TWH_n: (?P<tn>\d+), TSH_n: (?P<sn>\d+), Orig: (?P<oa>.*?) (?P<oc>.*?), Rem: (?P<r>.*?)$', re.MULTILINE)
    for m in pattern.finditer(content):
        d = m.groupdict()
        records.append({
            'timestamp': pd.to_datetime(d['ts']), 'User': d['u'], 'Shop': d['s'], 
            'Total_HKD': float(d['t']), 'Date': d['d'], 'Shared': d['sh'],
            'TWH_n': int(d['tn']), 'TSH_n': int(d['sn']),
            'Original': f"{d['oa']} {d['oc']}", 'Remarks': d['r']
        })
    df = pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index
    return df

def display_settlement(df):
    st.subheader("🤝 比例分帳工具箱 (Proportional Settlement)")
    
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if shared_df.empty:
        st.info("尚無分攤記錄。")
        return

    # 計算每筆記錄中各方應付的比例金額
    shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
    shared_df['TSH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TSH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
    
    # 實際支付統計
    twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
    tsh_paid = shared_df[shared_df['User'] == 'TSH']['Total_HKD'].sum()
    
    # 應支付統計 (目標)
    twh_should_pay = shared_df['TWH_Owe'].sum()
    tsh_should_pay = shared_df['TSH_Owe'].sum()
    
    # 差額 = 實際支付 - 應支付
    # 如果為正，代表墊付了；如果為負，代表欠錢
    balance = twh_paid - twh_should_pay

    c1, c2, c3 = st.columns(3)
    c1.metric("👨‍💻 TWH 實際墊付", f"{twh_paid:,.1f}")
    c2.metric("💼 TSH 實際墊付", f"{tsh_paid:,.1f}")
    
    if balance > 0:
        c3.success(f"💰 TSH 應給 TWH: **{abs(balance):,.1f} HKD**")
    elif balance < 0:
        c3.warning(f"💰 TWH 應給 TSH: **{abs(balance):,.1f} HKD**")
    else:
        c3.info("✅ 已平帳")

    with st.expander("查看分攤明細表"):
        st.dataframe(shared_df[['Date', 'Shop', 'Total_HKD', 'TWH_n', 'TSH_n', 'TWH_Owe', 'TSH_Owe']], use_container_width=True)

# --- 4. 提交頁面 (新增工具箱 UI) ---

def render_submission_page():
    st.title("💸 提交費用")
    mode = st.radio("模式", ["📸 OCR 收據", "✍️ 手動輸入"])
    
    with st.form("sub_form"):
        user = st.selectbox("付款人", ALLOWED_USERS)
        remarks = st.text_input("備註 (例如：幫媽媽買藥)")
        
        st.markdown("---")
        st.write("🔧 **分攤工具箱**")
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("此筆需按人數分攤？", value=True)
        twh_n = col_n1.number_input("TWH 分攤人數", min_value=1, value=3)
        tsh_n = col_n2.number_input("TSH 分攤人數", min_value=1, value=4)
        st.markdown("---")

        if mode == "📸 OCR 收據":
            up = st.file_uploader("上傳收據", type=['jpg','png','pdf'])
        else:
            s_n = st.text_input("商家")
            a_n = st.number_input("金額")
            c_n = st.selectbox("幣種", AVAILABLE_CURRENCIES)
            d_n = st.date_input("日期")

        if st.form_submit_button("確認提交"):
            ocr_data = None
            if mode == "📸 OCR 收據" and up:
                with st.spinner("Gemini Lite 分析中..."):
                    # 此處省略之前的 PDF 轉換函數，邏輯相同
                    res = gemini_client.models.generate_content(
                        model='gemini-3-flash-preview', # 已更新至 Gemini 3
                        contents=["Extract vendor, amount, currency, date as JSON.", Image.open(up)],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    ocr_data = json.loads(res.text)
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
                st.success("記錄已儲存！")

# --- 5. 主程序 ---

def main():
    st.sidebar.title("🧭 選單")
    page = st.sidebar.radio("頁面跳轉", ["提交費用", "歷史記錄"])
    
    # 側邊欄匯率顯示
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("1 JPY 兌 HKD", f"{rate:.4f}")
    
    if page == "提交費用":
        render_submission_page()
    else:
        st.title("📚 歷史記錄與比例分帳")
        df = read_and_parse_records_to_df()
        if not df.empty:
            display_settlement(df)
            # 列表顯示
            for _, r in df.iterrows():
                st.write(f"**{r['Date']}** | {r['Shop']} | {r['Total_HKD']:.1f} HKD ({r['User']})")
                if r['Shared'] == 'Yes':
                    st.caption(f"👥 分攤比例 (TWH:{r['TWH_n']} 人 / TSH:{r['TSH_n']} 人)")
                st.markdown("---")

if __name__ == "__main__":
    main()
