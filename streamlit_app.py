# 在 streamlit_app.py 中

import streamlit as st
import os  # <--- 請確保加上這一行！
import json
from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv

# --- 環境變數 ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = "iversonhang/travel-expense" # 例如: "myuser/TravelExpenseStreamlit"
FILE_PATH = "expense_records.txt"

def write_to_github_file(new_record_text):
    if not GITHUB_TOKEN:
        st.error("GitHub Token 缺失，無法寫入檔案。")
        return False

    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        
        # 1. 讀取現有內容
        try:
            contents = repo.get_contents(FILE_PATH)
            # 解碼現有內容
            existing_content = base64.b64decode(contents.content).decode('utf-8')
        except Exception:
            # 如果檔案不存在，則從空字串開始
            existing_content = ""
            contents = None
            
        # 2. 組合新內容
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_line = f"[{timestamp}] {new_record_text}\n"
        updated_content = existing_content + new_line
        
        # 3. 提交更新到 GitHub
        if contents:
            # 更新現有檔案
            repo.update_file(
                FILE_PATH,
                f"feat: Add new expense record for {timestamp}",
                updated_content,
                contents.sha
            )
        else:
            # 創建新檔案
            repo.create_file(
                FILE_PATH,
                f"feat: Initial expense record file created",
                updated_content
            )
        
        st.success(f"數據已成功寫入 GitHub 檔案：{FILE_PATH}")
        return True

    except Exception as e:
        st.error(f"寫入 GitHub 失敗 (請檢查 Token 權限)：{e}")
        return False

# --- 在主應用程式提交邏輯中呼叫 ---
if submitted and uploaded_file is not None:
    # ... (OCR 分析完成後) ...
    if ocr_data:
        # 將數據轉換為 TXT 格式
        record_text = f"User: {user_name}, Shop: {ocr_data['shop_name']}, Total: {ocr_data['total_amount']} {ocr_data['currency']}, Remarks: {remarks}"
        
        # 寫入 GitHub
        write_to_github_file(record_text)
