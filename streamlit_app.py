import streamlit as st
import os
import json
import base64
import re
import pandas as pd
import requests 
from datetime import datetime, date

# 外部依賴
from dotenv import load_dotenv 
from google import genai
from google.genai import types
from PIL import Image
from github import Github
import fitz 

# --- 0. 環境變數與初始化 ---
load_dotenv()
st.set_page_config(page_title="AI 費用記錄系統", layout="wide") 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY") 

REPO_NAME = "iversonhang/travel-expense" # !! 請修改此處 !!
FILE_PATH = "expense_records.txt"

ALLOWED_USERS = ["TWH", "TSH", "Olivia"] 
BASE_CURRENCY = "JPY" 
TARGET_CURRENCIES = ["JPY"] 
AVAILABLE_CURRENCIES = ["HKD", "JPY"] 
API_BASE_URL = "https://v6.exchangerate-api.com/v6" 

# Session State 用於處理編輯與刪除邏輯
if 'edit_id' not in st.session_state: st.session_state.edit_id = None
if 'delete_confirm_id' not in st.session_state: st.session_state.delete_confirm_id = None
if 'df_records' not in st.session_state: st.session_state.df_records = pd.DataFrame()

@st.cache_resource
def init_gemini_client():
    if not GEMINI_API_KEY: return None
    try: return genai.Client(api_key=GEMINI_API_KEY)
    except: return None

gemini_client = init_gemini_client()

# --- 1. 輔助函數 (匯率與 PDF) ---

@st.cache_data(ttl=3600)
def get_live_exchange_rate(from_curr, to_curr):
    if not EXCHANGE_RATE_API_KEY: return None
    try:
        url = f"{API_BASE_URL}/{EXCHANGE_RATE_API_KEY}/pair/{from_curr}/{to_curr}"
        res = requests.get(url, timeout=5).json()
        return res.get("conversion_rate") if res.get("result") == "success" else None
    except: return None

def convert_currency(amount, from_currency):
    if from_currency == BASE_CURRENCY: return amount, BASE_CURRENCY, 1.0
    rate = get_live_exchange_rate(from_currency, BASE_CURRENCY)
    if rate: return float(amount * rate), BASE_CURRENCY, float(rate)
    return amount, from_currency, 0.0

def pdf_to_images(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.tobytes("ppm"))
    doc.close()
    return img

# --- 2. GitHub 核心操作 (讀取、寫入、修改、刪除) ---

def read_full_content():
    if not GITHUB_TOKEN: return None, None
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        file = repo.get_contents(FILE_PATH)
        return base64.b64decode(file.content).decode('utf-8'), file.sha
    except: return None, None

def write_to_github_file(data):
    full_content, sha = read_full_content()
    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {data['user_name']}, Shop: {data['shop_name']}, "
            f"Total: {data['total_amount']:.2f} {data['currency']}, Date: {data['transaction_date']}, "
            f"Remarks: {data['remarks']}, Shared: {data.get('is_shared','No')}, "
            f"OriginalAmount: {data.get('original_amount',0):.2f}, OriginalCurrency: {data.get('original_currency','HKD')}, "
            f"Conversion: {data.get('conversion_notes','N/A')}\n")
    new_content = (full_content or "") + line
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    if sha: repo.update_file(FILE_PATH, "feat: add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "feat: create file", new_content)
    st.session_state.df_records = pd.DataFrame() # 重置緩存

def execute_github_action(action, record_id, new_data=None):
    """執行刪除或更新操作"""
    full_content, sha = read_full_content()
    df = st.session_state.df_records
    target = df[df['Record_ID'] == record_id].iloc[0]
    target_start = f"[{target['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}] User: {target['User']}"
    
    lines = full_content.strip().split('\n')
    new_lines = []
    for l in lines:
        if l.startswith(target_start):
            if action == 'delete': continue
            if action == 'update' and new_data:
                l = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User: {new_data['user_name']}, Shop: {new_data['shop_name']}, "
                     f"Total: {new_data['total_amount']:.2f} {new_data['currency']}, Date: {new_data['transaction_date']}, "
                     f"Remarks: {new_data['remarks']}, Shared: {new_data['is_shared']}, "
                     f"OriginalAmount: {new_data['original_amount']:.2f}, OriginalCurrency: {new_data['original_currency']}, "
                     f"Conversion: {new_data['conversion_notes']}")
        new_lines.append(l)
    
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    repo.update_file(FILE_PATH, f"{action} record", "\n".join(new_lines)+"\n", sha)
    st.session_state.df_records = pd.DataFrame()
    return True

# --- 3. 數據解析 ---

def read_and_parse_records_to_df():
    content, _ = read_full_content()
    if not content: return pd.DataFrame()
    records = []
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?)\s*(?P<c>[A-Z]{3}), Date: (?P<d>.*?), Remarks: (?P<r>.*?), Shared: (?P<sh>.*?), OriginalAmount: (?P<oa>.*?), OriginalCurrency: (?P<oc>.*?), Conversion: (?P<cv>.*?)$', re.MULTILINE)
    for line in content.strip().split('\n'):
        m = pattern.match(line)
        if m:
            d = m.groupdict()
            records.append({'timestamp': pd.to_datetime(d['ts']), 'User': d['u'], 'Shop': d['s'], 'Amount Recorded': f"{d['t']} {d['c']}", 'Total_HKD_Value': float(d['t']), 'Date': d['d'], 'Remarks': d['r'], 'Shared': d['sh'], 'OriginalAmount': float(d['oa']), 'OriginalCurrency': d['oc']})
    df = pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index
    return df

# --- 4. 編輯與刪除 UI ---

def display_edit_form(record):
    with st.form(f"edit_{record['Record_ID']}"):
        u = st.selectbox("付款人", ALLOWED_USERS, index=ALLOWED_USERS.index(record['User']) if record['User'] in ALLOWED_USERS else 0)
        s = st.text_input("商家", value=record['Shop'])
        oa = st.number_input("原始金額", value=record['OriginalAmount'])
        oc = st.selectbox("貨幣", AVAILABLE_CURRENCIES, index=AVAILABLE_CURRENCIES.index(record['OriginalCurrency']))
        dt = st.date_input("日期", value=datetime.strptime(record['Date'], '%Y-%m-%d').date())
        rem = st.text_input("備註", value=record['Remarks'])
        sh = st.checkbox("需分攤", value=record['Shared'] == 'Yes')
        
        if st.form_submit_button("保存更新"):
            amt, curr, rate = convert_currency(oa, oc)
            new_data = {"user_name": u, "shop_name": s, "total_amount": amt, "currency": curr, "transaction_date": str(dt), "remarks": rem, "is_shared": "Yes" if sh else "No", "original_amount": oa, "original_currency": oc, "conversion_notes": f"Edited. Rate: {rate}"}
            if execute_github_action('update', record['Record_ID'], new_data):
                st.session_state.edit_id = None
                st.rerun()

# --- 5. 頁面渲染 ---

def render_view_records_page():
    st.title("📚 歷史費用記錄")
    if st.session_state.df_records.empty:
        st.session_state.df_records = read_and_parse_records_to_df()
    
    df = st.session_state.df_records
    if df.empty:
        st.info("尚無記錄。")
        return

    # 顯示總結
    total = df['Total_HKD_Value'].sum()
    st.metric("💰 總支出", f"{total:,.2f} HKD")
    st.markdown("---")

    for i, row in df.iterrows():
        rid = row['Record_ID']
        c1, c2, c3 = st.columns([8, 1, 1])
        
        with c1:
            st.markdown(f"**{row['Date']}** | **{row['Shop']}** | `{row['Amount Recorded']}` ({row['User']})")
            if row['Remarks']: st.caption(f"💬 {row['Remarks']} | 分攤: {row['Shared']}")
        
        with c2:
            if st.button("✏️", key=f"btn_ed_{rid}"):
                st.session_state.edit_id = rid
                st.session_state.delete_confirm_id = None
        
        with c3:
            if st.button("🗑️", key=f"btn_de_{rid}"):
                st.session_state.delete_confirm_id = rid
                st.session_state.edit_id = None

        if st.session_state.edit_id == rid:
            display_edit_form(row)
        
        if st.session_state.delete_confirm_id == rid:
            if st.button("❌ 確認刪除", key=f"conf_{rid}", type="primary"):
                execute_github_action('delete', rid)
                st.session_state.delete_confirm_id = None
                st.rerun()
        
        st.markdown("<hr style='margin:0; border-top:1px solid #eee'>", unsafe_allow_html=True)

def render_submission_page():
    st.title("💸 提交費用")
    # (此部分與之前版本相同，包含 PDF/圖片處理)
    # ... (略，請參考之前的提交邏輯) ...
    # 這裡放簡化版的提交邏輯以確保程式碼運行
    mode = st.radio("模式", ["📸 OCR", "✍️ 手動"])
    with st.form("sub"):
        u = st.selectbox("付款人", ALLOWED_USERS)
        if mode == "📸 OCR":
            up = st.file_uploader("上傳收據", type=['jpg','png','pdf'])
        else:
            s_m = st.text_input("商家")
            a_m = st.number_input("金額")
            c_m = st.selectbox("幣種", ["HKD", "JPY"])
            d_m = st.date_input("日期")
        
        if st.form_submit_button("提交"):
            # 這裡簡化處理，實際運行時請保留您完整的 Gemini/PDF 處理邏輯
            if mode == "✍️ 手動":
                amt, curr, rate = convert_currency(a_m, c_m)
                data = {"user_name": u, "shop_name": s_m, "total_amount": amt, "currency": curr, "transaction_date": str(d_m), "remarks": "", "is_shared": "No", "original_amount": a_m, "original_currency": c_m}
                write_to_github_file(data)
                st.success("已提交！")

# --- 6. 主程序 ---

def main():
    # 側邊欄匯率
    st.sidebar.title("🧭 選單")
    page = st.sidebar.radio("跳轉頁面", ["提交費用", "歷史記錄"])
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("💱 即時匯率")
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate:
        st.sidebar.metric("1 JPY 兌 HKD", f"{rate:.4f}")
        st.sidebar.caption(f"更新時間: {datetime.now().strftime('%H:%M')}")
    
    if page == "提交費用": render_submission_page()
    else: render_view_records_page()

if __name__ == "__main__":
    main()
