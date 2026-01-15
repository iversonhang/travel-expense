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
st.set_page_config(page_title="AI 費用分帳系統", layout="wide") 

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

# --- 1. 輔助功能 (匯率/PDF) ---

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

# --- 2. GitHub 核心操作 ---

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

# --- 3. 數據解析與分帳邏輯 ---

def read_and_parse_records_to_df():
    content, _ = read_full_content()
    if not content: return pd.DataFrame()
    records = []
    # 正則表達式解析每一行
    pattern = re.compile(r'^\[(?P<ts>.*?)\] User: (?P<u>.*?), Shop: (?P<s>.*?), Total: (?P<t>.*?)\s*(?P<c>[A-Z]{3}), Date: (?P<d>.*?), Remarks: (?P<r>.*?), Shared: (?P<sh>.*?), OriginalAmount: (?P<oa>.*?), OriginalCurrency: (?P<oc>.*?), Conversion: (?P<cv>.*?)$', re.MULTILINE)
    for line in content.strip().split('\n'):
        m = pattern.match(line)
        if m:
            d = m.groupdict()
            records.append({
                'timestamp': pd.to_datetime(d['ts']), 'User': d['u'], 'Shop': d['s'], 
                'Amount Recorded': f"{d['t']} {d['c']}", 'Total_HKD_Value': float(d['t']), 
                'Date': d['d'], 'Remarks': d['r'], 'Shared': d['sh'].strip().capitalize(), 
                'OriginalAmount': float(d['oa']), 'OriginalCurrency': d['oc']
            })
    df = pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index
    return df

def calculate_and_display_summary(df):
    """計算兩人支出與分帳結算"""
    st.subheader("📊 財務概覽 (HKD)")
    
    # 1. 總支出統計
    total_val = df['Total_HKD_Value'].sum()
    user_sum = df.groupby('User')['Total_HKD_Value'].sum().to_dict()

    col_total, col_twh, col_tsh = st.columns([1, 1, 1])
    col_total.metric("💰 累計總支出", f"{total_val:,.2f}")
    col_twh.metric("👨‍💻 TWH 總支出", f"{user_sum.get('TWH', 0):,.2f}")
    col_tsh.metric("💼 TSH 總支出", f"{user_sum.get('TSH', 0):,.2f}")

    st.markdown("---")
    
    # 2. 結算邏輯 (分帳核心)
    st.subheader("🤝 兩人結算 (僅限 Shared 項目)")
    
    shared_df = df[df['Shared'] == 'Yes']
    if shared_df.empty:
        st.info("目前沒有需要分攤 (Shared) 的費用項目。")
    else:
        # TWH 墊付的共有金額
        twh_shared_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD_Value'].sum()
        # TSH 墊付的共有金額
        tsh_shared_paid = shared_df[shared_df['User'] == 'TSH']['Total_HKD_Value'].sum()
        
        total_shared = twh_shared_paid + tsh_shared_paid
        fair_share = total_shared / 2 # 每人應付一半
        
        c1, c2, c3 = st.columns(3)
        c1.write(f"**共有費用總計:** {total_shared:,.2f} HKD")
        c2.write(f"**每人應負擔:** {fair_share:,.2f} HKD")
        
        # 結算結果
        # 如果 TWH 付的比應負擔的多，說明 TSH 欠 TWH
        balance = twh_shared_paid - fair_share
        
        with c3:
            if balance > 0:
                st.success(f"👉 **TSH 應支付給 TWH: {abs(balance):,.2f} HKD**")
            elif balance < 0:
                st.warning(f"👉 **TWH 應支付給 TSH: {abs(balance):,.2f} HKD**")
            else:
                st.write("✅ 雙方金額已平衡，無需支付。")

    st.markdown("---")

# --- 4. 頁面渲染 ---

def render_view_records_page():
    st.title("📚 歷史費用與結算")
    if st.session_state.df_records.empty:
        st.session_state.df_records = read_and_parse_records_to_df()
    
    df = st.session_state.df_records
    if df.empty:
        st.info("尚未發現任何記錄。")
        return

    calculate_and_display_summary(df)

    st.subheader("📝 詳細流水帳")
    for i, row in df.iterrows():
        rid = row['Record_ID']
        shared_label = "👥 共有" if row['Shared'] == 'Yes' else "🔒 私有"
        c1, c2, c3 = st.columns([8, 1, 1])
        with c1:
            st.markdown(f"**{row['Date']}** | **{row['Shop']}** | `{row['Amount Recorded']}` ({row['User']}) | {shared_label}")
            if row['Remarks']: st.caption(f"💬 {row['Remarks']}")
        with c2:
            if st.button("✏️", key=f"ed_{rid}"): st.session_state.edit_id = rid
        with c3:
            if st.button("🗑️", key=f"de_{rid}"): st.session_state.delete_confirm_id = rid

        if st.session_state.edit_id == rid:
            with st.form(f"f_ed_{rid}"):
                u = st.selectbox("付款人", ALLOWED_USERS, index=ALLOWED_USERS.index(row['User']))
                s = st.text_input("商家", value=row['Shop'])
                oa = st.number_input("金額", value=row['OriginalAmount'])
                sh = st.checkbox("費用需分攤？", value=row['Shared'] == 'Yes')
                if st.form_submit_button("保存"):
                    amt, curr, rate = convert_currency(oa, row['OriginalCurrency'])
                    nd = {"user_name": u, "shop_name": s, "total_amount": amt, "currency": curr, "transaction_date": row['Date'], "remarks": row['Remarks'], "is_shared": "Yes" if sh else "No", "original_amount": oa, "original_currency": row['OriginalCurrency'], "conversion_notes": f"Updated. Rate: {rate}"}
                    execute_github_action('update', rid, nd)
                    st.rerun()

        if st.session_state.delete_confirm_id == rid:
            if st.button("❌ 確認刪除", key=f"cf_{rid}", type="primary"):
                execute_github_action('delete', rid)
                st.session_state.delete_confirm_id = None
                st.rerun()
        st.markdown("<hr style='margin:0; border-top:1px solid #f0f2f6'>", unsafe_allow_html=True)

def render_submission_page():
    st.title("💸 提交費用")
    mode = st.radio("模式", ["📸 圖片/PDF OCR", "✍️ 手動輸入"])
    with st.form("sub_form"):
        user = st.selectbox("付款人", ALLOWED_USERS)
        remarks = st.text_input("備註")
        shared = st.checkbox("費用是否需要兩人分攤 (Shared)?", value=True)
        ocr_data = None
        
        if mode == "📸 圖片/PDF OCR":
            up = st.file_uploader("上傳收據", type=['jpg','png','pdf'])
        else:
            s_n = st.text_input("商家名稱")
            a_n = st.number_input("金額", min_value=0.0)
            c_n = st.selectbox("幣種", AVAILABLE_CURRENCIES)
            d_n = st.date_input("日期")

        if st.form_submit_button("提交並記錄"):
            if mode == "📸 圖片/PDF OCR" and up:
                with st.spinner("Gemini Lite 分析中..."):
                    img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                    prompt = "Analyze receipt: vendor, total amount, currency, date (YYYY-MM-DD). Output JSON."
                    try:
                        res = gemini_client.models.generate_content(
                            model='gemini-2.5-flash-lite', 
                            contents=[prompt, img],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        ocr_data = json.loads(res.text)
                    except: st.error("AI 分析失敗")
            elif mode == "✍️ 手動輸入":
                ocr_data = {"shop_name": s_n, "total_amount": a_n, "currency": c_n, "transaction_date": str(d_n)}
            
            if ocr_data:
                amt, curr, rate = convert_currency(ocr_data['total_amount'], ocr_data['currency'])
                final = {"user_name": user, "shop_name": ocr_data['shop_name'], "total_amount": amt, "currency": curr, "transaction_date": ocr_data['transaction_date'], "remarks": remarks, "is_shared": "Yes" if shared else "No", "original_amount": ocr_data['total_amount'], "original_currency": ocr_data['currency'], "conversion_notes": f"Rate: {rate}"}
                write_to_github_file(final)
                st.success(f"✅ 記錄成功！已{'列入分帳' if shared else '計入私有支出'}。")

# --- 5. 主程序 ---

def main():
    st.sidebar.title("🧭 系統導航")
    page = st.sidebar.radio("切換頁面", ["提交費用", "歷史記錄"])
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("💱 即時匯率")
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate:
        st.sidebar.metric("1 JPY 兌 HKD", f"{rate:.4f}")
    
    st.sidebar.caption("⚡ Powered by Gemini Lite")
    
    if page == "提交費用": render_submission_page()
    else: render_view_records_page()

if __name__ == "__main__":
    main()
