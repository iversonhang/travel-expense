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

# --- 0. 環境變數設定與初始化 ---
load_dotenv()

st.set_page_config(page_title="AI 費用記錄系統", layout="wide") 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY") 

REPO_NAME = "iversonhang/travel-expense" 
FILE_PATH = "expense_records.txt"

# --- 用戶和貨幣配置 ---
ALLOWED_USERS = ["TWH", "TSH", "Olivia"] 
BASE_CURRENCY = "JPY" 
TARGET_CURRENCIES = ["JPY"] 
AVAILABLE_CURRENCIES = ["HKD", "JPY"] 
API_BASE_URL = "https://v6.exchangerate-api.com/v6" 

# --- Session State 初始化 ---
if 'edit_id' not in st.session_state:
    st.session_state.edit_id = None
if 'delete_confirm_id' not in st.session_state:
    st.session_state.delete_confirm_id = None
if 'df_records' not in st.session_state:
    st.session_state.df_records = pd.DataFrame()

@st.cache_resource
def init_gemini_client():
    if not GEMINI_API_KEY:
        st.error("❌ 錯誤：GEMINI_API_KEY 環境變數缺失。")
        return None
    try:
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        st.error(f"❌ Gemini 客戶端初始化失敗: {e}")
        return None

gemini_client = init_gemini_client()

# --- 1. Gemini 輸出結構定義 ---
RECEIPT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "shop_name": types.Schema(type=types.Type.STRING, description="The official name of the shop or vendor."),
        "total_amount": types.Schema(type=types.Type.NUMBER, description="The final total amount paid, including tax."),
        "currency": types.Schema(type=types.Type.STRING, description="The currency code (e.g., JPY, HKD)."),
        "transaction_date": types.Schema(type=types.Type.STRING, description="The date of the transaction in YYYY-MM-DD format."),
    },
    required=["shop_name", "total_amount", "currency", "transaction_date"]
)

# --- 2. 匯率轉換函數 ---
@st.cache_data(ttl=3600)
def get_live_exchange_rate(from_curr, to_curr):
    """獲取單一匯率數據用於側邊欄顯示"""
    if not EXCHANGE_RATE_API_KEY:
        return None
    try:
        url = f"{API_BASE_URL}/{EXCHANGE_RATE_API_KEY}/pair/{from_curr}/{to_curr}"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("result") == "success":
            return data.get("conversion_rate")
    except:
        return None
    return None

@st.cache_data(ttl=3600)
def convert_currency(amount, from_currency):
    if not EXCHANGE_RATE_API_KEY:
        st.error("❌ 錯誤：EXCHANGE_RATE_API_KEY 缺失。")
        return amount, from_currency, 0.0
    if from_currency == BASE_CURRENCY:
        return amount, BASE_CURRENCY, 1.0
    try:
        rate = get_live_exchange_rate(from_currency, BASE_CURRENCY)
        if rate:
            return float(amount * rate), BASE_CURRENCY, float(rate)
    except Exception as e:
        st.error(f"❌ 轉換異常: {e}")
    return amount, from_currency, 0.0 

# --- 2A. PDF 轉換函數 ---
def pdf_to_images(uploaded_pdf_file):
    try:
        pdf_bytes = uploaded_pdf_file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0: return None
        page = doc.load_page(0)
        zoom = 300 / 72
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img_data = pix.tobytes("ppm")
        image = Image.frombytes("RGB", [pix.width, pix.height], img_data)
        doc.close()
        return image
    except Exception as e:
        st.error(f"❌ PDF 處理錯誤: {e}")
        return None

# --- 3. Gemini 處理函數 (使用 Flash 2.5) ---
def analyze_receipt(image_to_analyze):
    if not gemini_client: return None
    prompt = ("Analyze the provided receipt image. Extract the vendor name, total amount, currency, and date "
            "in YYYY-MM-DD format. Strictly output the data in the required JSON format.")
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, image_to_analyze],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=RECEIPT_SCHEMA)
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"❌ Gemini API 失敗: {e}")
        return None

# --- 4. GitHub 讀取/寫入 (保持不變) ---
def read_full_content():
    if not GITHUB_TOKEN: return None, None
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH)
        content = base64.b64decode(contents.content).decode('utf-8')
        return content, contents.sha 
    except: return None, None

def write_to_github_file(record_data):
    if not GITHUB_TOKEN: return False
    try:
        record_text = (
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"User: {record_data['user_name']}, "
            f"Shop: {record_data['shop_name']}, "
            f"Total: {record_data['total_amount']:.2f} {record_data['currency']}, "
            f"Date: {record_data['transaction_date']}, "
            f"Remarks: {record_data['remarks']}, "
            f"Shared: {record_data.get('is_shared', 'No')}, " 
            f"OriginalAmount: {record_data.get('original_amount', 0.0):.2f}, "
            f"OriginalCurrency: {record_data.get('original_currency', BASE_CURRENCY)}, " 
            f"Conversion: {record_data.get('conversion_notes', 'N/A')}\n"
        )
        full_content, sha = read_full_content() 
        updated_content = (full_content or "") + record_text
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        if sha: repo.update_file(FILE_PATH, f"feat: Add record", updated_content, sha)
        else: repo.create_file(FILE_PATH, f"feat: Add record", updated_content)
        st.success(f"數據已寫入 GitHub。")
        st.session_state.df_records = pd.DataFrame()
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 5-7. 數據讀取/解析/編輯 (略，保持不變) ---
@st.cache_data(show_spinner=False)
def read_and_parse_records_to_df(cache_buster):
    content, _ = read_full_content()
    if not content: return pd.DataFrame()
    records = []
    pattern = re.compile(r'^\[(?P<timestamp>.*?)\] User: (?P<User>.*?), Shop: (?P<Shop>.*?), Total: (?P<Total>.*?)\s*(?P<Currency>[A-Z]{3}), Date: (?P<Date>.*?), Remarks: (?P<Remarks>.*?), Shared: (?P<Shared>.*?),\s*OriginalAmount: (?P<OriginalAmount>.*?),\s*OriginalCurrency: (?P<OriginalCurrency>.*?), \s*Conversion: (?P<Conversion>.*?)$', re.MULTILINE)
    for line in content.strip().split('\n'):
        match = pattern.match(line)
        if match:
            data = match.groupdict()
            records.append({
                'timestamp': data['timestamp'], 'User': data['User'].strip(), 'Shop': data['Shop'].strip(),
                'Amount Recorded': f"{float(data['Total']):.2f} {data['Currency']}", 'Total_HKD_Value': float(data['Total']),
                'Date': data['Date'], 'Remarks': data['Remarks'].strip(), 'Shared': data['Shared'].strip(),
                'OriginalAmount': float(data['OriginalAmount']), 'OriginalCurrency': data['OriginalCurrency'].strip(), 'Record_ID': 0
            })
    df = pd.DataFrame(records)
    if df.empty: return df
    df['User'] = df['User'].apply(lambda x: x if x in ALLOWED_USERS else 'Other')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values(by='timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index
    return df

# --- 8-9. 頁面渲染與總結 ---
def calculate_and_display_summary(df):
    st.markdown("---")
    st.subheader("📊 費用總結報告 (HKD)")
    total_expense = df['Total_HKD_Value'].sum()
    user_summary = df.groupby('User')['Total_HKD_Value'].sum().reset_index()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 **總支出**", f"{total_expense:,.2f} HKD")
    users = {"TWH": "👨‍💻", "TSH": "💼", "Olivia": "👩‍🎨"}
    cols = [c2, c3, c4]
    for i, (name, icon) in enumerate(users.items()):
        val = user_summary[user_summary['User'] == name]['Total_HKD_Value'].iloc[0] if name in user_summary['User'].values else 0.0
        cols[i].metric(f"{icon} **{name}**", f"{val:,.2f} HKD")

def render_submission_page():
    st.title("💸 提交費用")
    mode = st.radio("輸入方式：", ("📸 圖片/PDF OCR", "✍️ 手動輸入"))
    with st.form("expense_form"):
        user = st.selectbox("誰支付？", ALLOWED_USERS)
        rem = st.text_input("備註")
        shared = st.checkbox("需分攤？")
        ocr_data = None
        if mode == "📸 圖片/PDF OCR":
            up = st.file_uploader("上傳收據", type=['jpg','jpeg','png','pdf'])
        else:
            m_s = st.text_input("商家")
            m_a = st.number_input("金額", min_value=0.01)
            m_c = st.selectbox("貨幣", ["HKD", "JPY"])
            m_d = st.date_input("日期")
        if st.form_submit_button("提交"):
            if mode == "📸 圖片/PDF OCR" and up:
                img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                ocr_data = analyze_receipt(img)
            elif mode == "✍️ 手動輸入":
                ocr_data = {"shop_name": m_s, "total_amount": m_a, "currency": m_c, "transaction_date": str(m_d)}
            if ocr_data:
                amt, curr, rate = convert_currency(ocr_data['total_amount'], ocr_data['currency'])
                final = {"user_name": user, "remarks": rem, "is_shared": "Yes" if shared else "No", "original_currency": ocr_data['currency'], "original_amount": ocr_data['total_amount'], "shop_name": ocr_data['shop_name'], "total_amount": amt, "currency": curr, "transaction_date": ocr_data['transaction_date'], "conversion_notes": f"Rate: {rate}"}
                write_to_github_file(final)

def render_view_records_page():
    st.title("📚 歷史記錄")
    if st.session_state.df_records.empty:
        st.session_state.df_records = read_and_parse_records_to_df(datetime.now())
    df = st.session_state.df_records
    if not df.empty:
        calculate_and_display_summary(df)
        st.dataframe(df[['Date', 'Shop', 'Amount Recorded', 'User', 'Remarks']], use_container_width=True)

# --- 10. 主程序與側邊欄匯率 ---
def main():
    # --- 左側側邊欄匯率 ---
    st.sidebar.title("🧭 導航系統")
    page = st.sidebar.radio("選擇功能：", ("提交費用", "查看記錄"))
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("💱 今日參考匯率")
    
    # 獲取 JPY -> HKD 匯率
    rate_jpy_hkd = get_live_exchange_rate("JPY", "HKD")
    
    if rate_jpy_hkd:
        st.sidebar.metric(label="1 JPY 兌 HKD", value=f"{rate_jpy_hkd:.4f}")
        st.sidebar.caption(f"1 HKD ≈ {1/rate_jpy_hkd:.2f} JPY")
        st.sidebar.info(f"📅 最後更新: {datetime.now().strftime('%H:%M')}")
    else:
        st.sidebar.warning("無法取得即時匯率，請檢查 API Key")

    if page == "提交費用": render_submission_page()
    else: render_view_records_page()

if __name__ == "__main__":
    main()
