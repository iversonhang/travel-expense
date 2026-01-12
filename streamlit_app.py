import streamlit as st
import os
import json
import base64
from datetime import datetime
from io import BytesIO

# 為了在本地運行時加載 .env，但在 Streamlit Cloud 上會使用 Secrets
from dotenv import load_dotenv

# Gemini/AI 相關
from google import genai
from google.genai import types
from PIL import Image

# GitHub 寫入相關
from github import Github

# --- 0. 環境變數設定與初始化 ---
# 僅在本地環境運行時加載 .env
load_dotenv()

st.set_page_config(page_title="AI 旅行費用記錄器", layout="centered")

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
        st.error("❌ 錯誤：GEMINI_API_KEY 環境變數缺失。請在 Streamlit Secrets 或 .env 中設定。")
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
        st.error("❌ GitHub Token 缺失，無法寫入檔案。請在 Streamlit Secrets 中設定 GITHUB_TOKEN。")
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
            # 解碼現有內容 (GitHub API 返回 Base64)
            existing_content = base64.b64decode(contents.content).decode('utf-8')
            sha = contents.sha
        except Exception:
            # 檔案不存在，視為創建新檔案
            existing_content = ""
            sha = None
            
        updated_content = existing_content + record_text
        commit_message = f"feat: Add new expense record for {record_data['user_name']}"
        
        # 執行創建或更新檔案操作
        if sha:
            repo.update_file(FILE_PATH, commit_message, updated_content, sha)
        else:
            repo.create_file(FILE_PATH, commit_message, updated_content)
        
        st.success(f"數據已成功寫入 GitHub 檔案：[{FILE_PATH}](https://github.com/{REPO_NAME}/blob/main/{FILE_PATH})")
        return True

    except Exception as e:
        st.error(f"❌ 寫入 GitHub 失敗 (請檢查 Token 權限或 REPO_NAME)：{e}")
        return False


# --- 4. Streamlit UI 介面 ---

st.title("💸 AI 旅行費用記錄器")
st.markdown("---")

# 這裡使用 st.form 來確保在提交按鈕按下之前，程式碼不會執行後續的數據處理
with st.form("expense_form"):
    st.subheader("輸入費用信息")
    user_name = st.selectbox("誰支付了？", options=['Mary', 'John', 'Other'])
    remarks = st.text_input("備註 (可選)", key="remarks_input")
    
    st.markdown("---")
    
    uploaded_file = st.file_uploader("上傳收據圖片 (JPEG/PNG)", type=['jpg', 'jpeg', 'png'])
    
    # !!! 這裡定義了 submitted 變數 !!!
    submitted = st.form_submit_button("執行分析並提交到 GitHub")
    
    # !!! 依賴 submitted 的邏輯必須在 form 塊內且在 submitted 定義之後 !!!
    if submitted and uploaded_file is not None:
        
        # --- 流程開始 ---
        with st.spinner('AI 正在分析收據...'):
            ocr_data = analyze_receipt(uploaded_file)
        
        if ocr_data:
            st.success("收據分析完成！")
            
            # 組合最終記錄數據
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
            
            # 寫入 GitHub TXT 檔案
            with st.spinner('正在寫入 GitHub 儲存庫...'):
                write_to_github_file(final_record)
        else:
            st.error("分析失敗，請檢查圖片或 Gemini API 狀態。")
    
    elif submitted and uploaded_file is None:
        st.warning("請上傳收據圖片才能進行分析。")

st.markdown("---")
st.info(f"當前運行環境的 `REPO_NAME` 為：`{REPO_NAME}`")
