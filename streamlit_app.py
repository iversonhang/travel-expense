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
st.set_page_config(page_title="AI 雙幣分帳系統", layout="wide") 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY") 

REPO_NAME = "iversonhang/travel-expense" 
FILE_PATH = "expense_records.txt"

ALLOWED_USERS = ["TWH", "TSH"] 
BASE_CURRENCY = "HKD" 
AVAILABLE_CURRENCIES = ["HKD", "JPY"] 
API_BASE_URL = "https://v6.exchangerate-api.com/v6" 

if 'edit_id' not in st.session_state: st.session_state.edit_id = None
if 'delete_confirm_id' not in st.session_state: st.session_state.delete_confirm_id = None
if 'df_records' not in st.session_state: st.session_state.df_records = pd.DataFrame()

@st.cache_resource
def init_gemini_client():
    if not GEMINI_API_KEY: return None
    try: return genai.Client(api_key=GEMINI_API_KEY)
    except: return None

gemini_client = init_gemini_client()

# --- 1. 核心輔助功能 ---

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

# --- 2. GitHub 檔案操作 ---

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
    st.session_state.df_records = pd.DataFrame() 

def execute_github_action(action, record_id, new_data=None):
    full_content, sha = read_full_content()
    df = st.session_state.df_records
    target = df[df['Record_ID'] == record_id].iloc[0]
    target_start = f"[{target['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}] User: {target['User']}"
    
    lines = (full_content or "").strip().split('\n')
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

# --- 3. 數據處理與雙幣渲染 ---

def read_and_parse_records_to_df():
    content, _ = read_full_content()
    if not content: return pd.DataFrame()
    records = []
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?)\s*(?P<c>[A-Z]{3}), Date: (?P<d>.*?), Remarks: (?P<r>.*?), Shared: (?P<sh>.*?), OriginalAmount: (?P<oa>.*?), OriginalCurrency: (?P<oc>.*?), Conversion: (?P<cv>.*?)$', re.MULTILINE)
    for line in content.strip().split('\n'):
        m = pattern.match(line)
        if m:
            d = m.groupdict()
            records.append({
                'timestamp': pd.to_datetime(d['ts']), 'User': d['u'], 'Shop': d['s'], 
                'Total_HKD_Value': float(d['t']), 'Date': d['d'], 'Remarks': d['r'], 
                'Shared': d['sh'].strip().capitalize(), 'OriginalAmount': float(d['oa']), 
                'OriginalCurrency': d['oc'].strip()
            })
    df = pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index
    return df

def render_view_records_page():
    st.title("📚 歷史費用 (HKD / JPY)")
    
    # 獲取最新匯率用於反向換算
    rate_jpy_hkd = get_live_exchange_rate("JPY", "HKD")
    
    if st.session_state.df_records.empty:
        st.session_state.df_records = read_and_parse_records_to_df()
    
    df = st.session_state.df_records
    if df.empty:
        st.info("尚無記錄。")
        return

    # --- 頂部總結儀表板 ---
    total_hkd = df['Total_HKD_Value'].sum()
    user_sum = df.groupby('User')['Total_HKD_Value'].sum().to_dict()
    
    c_tot, c_twh, c_tsh = st.columns(3)
    c_tot.metric("💰 總支出 (HKD)", f"${total_hkd:,.2f}")
    c_twh.metric("👨‍💻 TWH (HKD)", f"${user_sum.get('TWH', 0):,.2f}")
    c_tsh.metric("💼 TSH (HKD)", f"${user_sum.get('TSH', 0):,.2f}")

    # --- 兩人分帳結算 ---
    shared_df = df[df['Shared'] == 'Yes']
    if not shared_df.empty:
        twh_shared = shared_df[shared_df['User'] == 'TWH']['Total_HKD_Value'].sum()
        tsh_shared = shared_df[shared_df['User'] == 'TSH']['Total_HKD_Value'].sum()
        balance = twh_shared - (twh_shared + tsh_shared) / 2
        
        st.markdown("---")
        if balance > 0:
            st.success(f"🤝 **結算提示：TSH 應支付給 TWH {abs(balance):,.2f} HKD**")
        elif balance < 0:
            st.warning(f"🤝 **結算提示：TWH 應支付給 TSH {abs(balance):,.2f} HKD**")
        else:
            st.info("🤝 雙方共有費用已平帳。")
    
    st.markdown("---")
    st.subheader("📝 詳細清單")

    # --- 雙幣流水帳顯示 ---
    for i, row in df.iterrows():
        rid = row['Record_ID']
        
        # 計算雙幣數值
        val_hkd = row['Total_HKD_Value']
        if row['OriginalCurrency'] == 'JPY':
            val_jpy = row['OriginalAmount']
        else:
            # 如果原始是 HKD，且有匯率，則算出日幣參考
            val_jpy = val_hkd / rate_jpy_hkd if rate_jpy_hkd else 0

        c1, c2, c3 = st.columns([8, 1, 1])
        
        with c1:
            # 強調顯示雙幣
            st.markdown(
                f"**{row['Date']}** | **{row['Shop']}** | `{row['User']}` "
                f"<span style='color:#007bff; font-weight:bold; font-size:1.1em;'> {val_hkd:,.2f} HKD </span> "
                f"<span style='color:#6c757d;'> (¥{val_jpy:,.0f} JPY) </span>", 
                unsafe_allow_html=True
            )
            
            # 顯示備註與標籤
            tags = f" {'👥 共有' if row['Shared'] == 'Yes' else '🔒 私有'}"
            if row['Remarks']:
                st.caption(f"💬 {row['Remarks']} | {tags}")
            else:
                st.caption(tags)
        
        with c2:
            if st.button("✏️", key=f"e_{rid}"): st.session_state.edit_id = rid
        with c3:
            if st.button("🗑️", key=f"d_{rid}"): st.session_state.delete_confirm_id = rid

        # 編輯與刪除邏輯 (略，與之前相同)
        if st.session_state.edit_id == rid:
            with st.form(f"form_ed_{rid}"):
                u = st.selectbox("付款人", ALLOWED_USERS, index=ALLOWED_USERS.index(row['User']))
                s = st.text_input("商家", value=row['Shop'])
                oa = st.number_input("原始金額", value=row['OriginalAmount'])
                oc = st.selectbox("貨幣", AVAILABLE_CURRENCIES, index=AVAILABLE_CURRENCIES.index(row['OriginalCurrency']))
                sh = st.checkbox("需分攤", value=row['Shared'] == 'Yes')
                if st.form_submit_button("更新"):
                    amt, curr, rate = convert_currency(oa, oc)
                    nd = {"user_name": u, "shop_name": s, "total_amount": amt, "currency": curr, "transaction_date": row['Date'], "remarks": row['Remarks'], "is_shared": "Yes" if sh else "No", "original_amount": oa, "original_currency": oc, "conversion_notes": f"Rate: {rate}"}
                    execute_github_action('update', rid, nd)
                    st.rerun()

        if st.session_state.delete_confirm_id == rid:
            if st.button("確認刪除", key=f"conf_{rid}", type="primary"):
                execute_github_action('delete', rid)
                st.session_state.delete_confirm_id = None
                st.rerun()
        
        st.markdown("<hr style='margin:0.5em 0; border-top:1px solid #eee'>", unsafe_allow_html=True)

# (其餘 render_submission_page 與 main 保持不變，但確保使用 gemini-2.5-flash-lite)
def render_submission_page():
    st.title("💸 提交費用")
    mode = st.radio("模式", ["📸 圖片/PDF OCR", "✍️ 手動輸入"])
    with st.form("sub"):
        user = st.selectbox("付款人", ALLOWED_USERS)
        rem = st.text_input("備註")
        sh = st.checkbox("需分攤？", value=True)
        ocr_data = None
        if mode == "📸 圖片/PDF OCR":
            up = st.file_uploader("上傳收據", type=['jpg','png','pdf'])
        else:
            s_n = st.text_input("商家")
            a_n = st.number_input("金額", min_value=0.0)
            c_n = st.selectbox("幣種", AVAILABLE_CURRENCIES)
            d_n = st.date_input("日期")

        if st.form_submit_button("記錄"):
            if mode == "📸 圖片/PDF OCR" and up:
                with st.spinner("Gemini Lite 分析中..."):
                    img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                    prompt = "Analyze receipt: vendor, total amount, currency, date (YYYY-MM-DD). Output JSON."
                    res = gemini_client.models.generate_content(
                        model='gemini-2.5-flash-lite', contents=[prompt, img],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    ocr_data = json.loads(res.text)
            else:
                ocr_data = {"shop_name": s_n, "total_amount": a_n, "currency": c_n, "transaction_date": str(d_n)}
            
            if ocr_data:
                amt, curr, rate = convert_currency(ocr_data['total_amount'], ocr_data['currency'])
                write_to_github_file({"user_name": user, "shop_name": ocr_data['shop_name'], "total_amount": amt, "currency": curr, "transaction_date": ocr_data['transaction_date'], "remarks": rem, "is_shared": "Yes" if sh else "No", "original_amount": ocr_data['total_amount'], "original_currency": ocr_data['currency'], "conversion_notes": f"Rate: {rate}"})
                st.success("成功記錄")

def main():
    st.sidebar.title("🧭 系統導航")
    page = st.sidebar.radio("切換頁面", ["提交費用", "歷史記錄"])
    st.sidebar.markdown("---")
    st.sidebar.subheader("💱 即時匯率")
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("1 JPY 兌 HKD", f"{rate:.4f}")
    if page == "提交費用": render_submission_page()
    else: render_view_records_page()

if __name__ == "__main__":
    main()
