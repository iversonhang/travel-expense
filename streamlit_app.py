import streamlit as st
import os
import json
import base64
import re
import pandas as pd
from datetime import datetime
from io import BytesIO

# 為了在本地運行時加載 .env
from dotenv import load_dotenv 

# Gemini/AI 相關
from google import genai
from google.genai import types
from PIL import Image

# GitHub 寫入相關
from github import Github

# --- 0. 環境變數設定與初始化 ---
load_dotenv()

st.set_page_config(page_title="AI 費用記錄系統", layout="centered")

# 從環境變數或 Streamlit Secrets 獲取金鑰
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# 設置您的 GitHub 儲存庫信息
# !! 請務必替換成您自己的 GitHub 用戶名和儲存庫名稱 !!
REPO_NAME = "YOUR_USERNAME/YOUR_REPO_NAME" 
FILE_PATH = "expense_records.txt"

@st.cache_resource
def init_gemini_client():
    """初始化 Gemini 客戶端"""
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
        "currency": types.Schema(type=types.Type.STRING, description="The currency code (e.g., TWD, JPY, USD)."),
        "transaction_date": types.Schema(type=types.Type.STRING, description="The date of the transaction in YYYY-MM-DD format."),
    },
    required=["shop_name", "total_amount", "currency", "transaction_date"]
)


# --- 2. 核心 Gemini 處理函數 ---
def analyze_receipt(uploaded_file):
    """呼叫 Gemini API 進行收據 OCR 分析"""
    if not gemini_client:
        return None
        
    image = Image.open(uploaded_file)
    
    prompt = (
        "Analyze the provided receipt image. Extract the vendor name, total amount, currency, and date "
        "in YYYY-MM-DD format. Strictly output the data in the required JSON format."
    )
    
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, image],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RECEIPT_SCHEMA
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"❌ Gemini API 處理失敗: {e}")
        return None


# --- 3. GitHub 寫入函數 ---
def write_to_github_file(record_data):
    """使用 GitHub API 將記錄寫入 TXT 檔案"""
    if not GITHUB_TOKEN:
        st.error("❌ GitHub Token 缺失，無法寫入檔案。")
        return False

    try:
        # 將記錄轉換為單行文本格式
        record_text = (
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"User: {record_data['user_name']}, "
            f"Shop: {record_data['shop_name']}, "
            f"Total: {record_data['total_amount']} {record_data['currency']}, "
            f"Date: {record_data['transaction_date']}, "
            f"Remarks: {record_data['remarks']}\n"
        )
        
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        
        # 嘗試獲取現有內容
        try:
            contents = repo.get_contents(FILE_PATH)
            existing_content = base64.b64decode(contents.content).decode('utf-8')
            sha = contents.sha
        except Exception:
            # 檔案不存在，視為創建新檔案
            existing_content = ""
            sha = None
            
        updated_content = existing_content + record_text
        commit_message = f"feat: Add new expense record for {record_data['user_name']}"
        
        if sha:
            repo.update_file(FILE_PATH, commit_message, updated_content, sha)
        else:
            repo.create_file(FILE_PATH, commit_message, updated_content)
        
        st.success(f"數據已成功寫入 GitHub 檔案：[{FILE_PATH}](https://github.com/{REPO_NAME}/blob/main/{FILE_PATH})")
        return True

    except Exception as e:
        st.error(f"❌ 寫入 GitHub 失敗 (請檢查 Token 權限或 REPO_NAME)：{e}")
        return False


# --- 4. 數據讀取和解析函數 (用於查看頁面) ---
def read_and_parse_records():
    """從 GitHub 讀取 TXT 檔案並解析為 DataFrame"""
    if not GITHUB_TOKEN:
        return pd.DataFrame()

    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH)
        content = base64.b64decode(contents.content).decode('utf-8')
    except Exception:
        # 檔案不存在或讀取失敗
        return pd.DataFrame()

    records = []
    # 正則表達式來匹配每行的結構
    pattern = re.compile(
        r'^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] '
        r'User: (?P<User>.*?), '
        r'Shop: (?P<Shop>.*?), '
        r'Total: (?P<Total>.*?)\s*(?P<Currency>[A-Z]{3}?), '
        r'Date: (?P<Date>\d{4}-\d{2}-\d{2}), '
        r'Remarks: (?P<Remarks>.*?)$',
        re.MULTILINE
    )

    for line in content.strip().split('\n'):
        match = pattern.match(line)
        if match:
            data = match.groupdict()
            # 調整欄位名稱
            data['Amount'] = f"{data.pop('Total').strip()} {data.pop('Currency').strip()}"
            records.append(data)
    
    return pd.DataFrame(records)


# --- 5. 頁面渲染函數 ---

def render_submission_page():
    """渲染費用提交頁面 (主頁面)"""
    st.title("💸 提交費用 (OCR)")
    st.markdown("使用 Gemini AI 分析收據，並將數據記錄到 GitHub TXT 檔案。")
    st.markdown("---")

    with st.form("expense_form"):
        st.subheader("輸入費用信息")
        user_name = st.selectbox("誰支付了？", options=['Mary', 'John', 'Other'])
        remarks = st.text_input("備註 (可選)", key="remarks_input")
        
        st.markdown("---")
        
        uploaded_file = st.file_uploader("上傳收據圖片 (JPEG/PNG)", type=['jpg', 'jpeg', 'png'])
        
        submitted = st.form_submit_button("執行分析並提交到 GitHub")
        
        if submitted and uploaded_file is not None:
            # --- 流程開始 ---
            with st.spinner('AI 正在分析收據...'):
                ocr_data = analyze_receipt(uploaded_file)
            
            if ocr_data:
                st.success("收據分析完成！")
                
                final_record = {
                    "user_name": user_name,
                    "remarks": remarks,
                    "shop_name": ocr_data.get("shop_name", "N/A"),
                    "total_amount": ocr_data.get("total_amount", 0),
                    "currency": ocr_data.get("currency", "N/A"),
                    "transaction_date": ocr_data.get("transaction_date", datetime.now().strftime("%Y-%m-%d")) 
                }

                st.subheader("📝 提取和確認記錄:")
                st.json(final_record)
                
                with st.spinner('正在寫入 GitHub 儲存庫...'):
                    write_to_github_file(final_record)
            else:
                st.error("分析失敗，請檢查圖片或 Gemini API 狀態。")
        
        elif submitted and uploaded_file is None:
            st.warning("請上傳收據圖片才能進行分析。")


def render_view_records_page():
    """渲染查看記錄頁面"""
    st.title("📚 歷史費用記錄")
    st.info(f"正在從 GitHub 儲存庫 `{REPO_NAME}` 讀取檔案 `{FILE_PATH}`...")
    
    with st.spinner("從 GitHub 下載並解析數據中..."):
        df = read_and_parse_records()

    if not df.empty:
        st.subheader(f"找到 {len(df)} 條記錄")
        # 重新排序，讓最新的記錄在最上方
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values(by='timestamp', ascending=False)
        st.dataframe(df, use_container_width=True)
    else:
        st.warning("當前檔案中沒有可解析的費用記錄。")
        st.code(f"請在提交頁面提交一條記錄，檔案會自動創建於 GitHub：{FILE_PATH}")


# --- 6. 應用程式主運行流程 (切換頁面) ---

# 側邊欄導航 (模擬多頁面)
st.sidebar.title("導航")
page = st.sidebar.radio(
    "選擇功能頁面：",
    ("提交費用 (OCR)", "查看記錄"),
    key="page_selection"
)

# 根據選擇渲染對應的頁面
if page == "提交費用 (OCR)":
    render_submission_page()
elif page == "查看記錄":
    render_view_records_page()

st.markdown("---")
