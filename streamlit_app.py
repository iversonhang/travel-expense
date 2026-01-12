import streamlit as st
import os
import json
import base64
import re
import pandas as pd
from datetime import datetime, date

# 外部依賴
from dotenv import load_dotenv 
from google import genai
from google.genai import types
from PIL import Image
from github import Github
from forex_python.converter import CurrencyRates 

# --- 0. 環境變數設定與初始化 ---
load_dotenv()

st.set_page_config(page_title="AI 費用記錄系統", layout="centered")

# 從環境變數或 Streamlit Secrets 獲取金鑰
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# 設置您的 GitHub 儲存庫信息
# !! 請務必替換成您自己的 GitHub 用戶名和儲存庫名稱 !!
REPO_NAME = "iversonhang/travel-expense" 
FILE_PATH = "expense_records.txt"

# 貨幣轉換設定
BASE_CURRENCY = "HKD" # <--- 基礎儲存貨幣設定為 HKD
TARGET_CURRENCIES = ["JPY"] # 只有 JPY 需要轉換為 HKD
AVAILABLE_CURRENCIES = ["HKD", "JPY"] # 用於手動輸入和編輯表單

# --- Session State 初始化 (用於編輯/刪除/緩存) ---
if 'edit_id' not in st.session_state:
    st.session_state.edit_id = None
if 'delete_confirm_id' not in st.session_state:
    st.session_state.delete_confirm_id = None
if 'df_records' not in st.session_state:
    st.session_state.df_records = pd.DataFrame()


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
        "currency": types.Schema(type=types.Type.STRING, description="The currency code (e.g., JPY, HKD)."),
        "transaction_date": types.Schema(type=types.Type.STRING, description="The date of the transaction in YYYY-MM-DD format."),
    },
    required=["shop_name", "total_amount", "currency", "transaction_date"]
)


# --- 2. 匯率轉換函數 ---
@st.cache_data(ttl=3600)
def convert_currency(amount, from_currency):
    """將金額轉換為基礎貨幣 (HKD)"""
    if from_currency == BASE_CURRENCY:
        return amount, BASE_CURRENCY, 1.0 # 如果是 HKD，不轉換

    try:
        c = CurrencyRates(force_decimal=True)
        # 獲取 JPY 到 HKD 的即時匯率
        rate = c.get_rate(from_currency, BASE_CURRENCY)
        converted_amount = amount * rate
        return float(converted_amount), BASE_CURRENCY, float(rate)
    except Exception:
        # 轉換失敗，返回原始數據，但標記轉換失敗
        return amount, from_currency, 0.0 


# --- 3. 核心 Gemini 處理函數 ---
def analyze_receipt(uploaded_file):
    """呼叫 Gemini API 進行收據 OCR 分析"""
    if not gemini_client: return None
        
    image = Image.open(uploaded_file)
    
    prompt = ("Analyze the provided receipt image. Extract the vendor name, total amount, currency, and date "
            "in YYYY-MM-DD format. Strictly output the data in the required JSON format.")
    
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash', contents=[prompt, image],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=RECEIPT_SCHEMA)
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"❌ Gemini API 處理失敗: {e}")
        return None


# --- 4. GitHub 讀取/寫入/刪除 輔助函數 ---

def read_full_content():
    """從 GitHub 讀取並返回 expense_records.txt 的原始字串和 SHA"""
    if not GITHUB_TOKEN:
        return None, None
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH)
        content = base64.b64decode(contents.content).decode('utf-8')
        return content, contents.sha 
    except Exception:
        return None, None


def write_to_github_file(record_data):
    """將單條記錄追加寫入 TXT 檔案 (包含 Shared 和 OriginalCurrency)"""
    if not GITHUB_TOKEN: return False

    try:
        # 將記錄轉換為單行文本格式 (新增 Shared 和 OriginalCurrency)
        record_text = (
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"User: {record_data['user_name']}, "
            f"Shop: {record_data['shop_name']}, "
            f"Total: {record_data['total_amount']:.2f} {record_data['currency']}, "
            f"Date: {record_data['transaction_date']}, "
            f"Remarks: {record_data['remarks']}, "
            f"Shared: {record_data.get('is_shared', 'No')}, " 
            f"OriginalCurrency: {record_data.get('original_currency', BASE_CURRENCY)}, " # <--- 新增
            f"Conversion: {record_data.get('conversion_notes', 'N/A')}\n"
        )
        
        full_content, sha = read_full_content() # 讀取現有內容和 SHA
        updated_content = (full_content or "") + record_text
        commit_message = f"feat: Add new expense record for {record_data['user_name']}"
        
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)

        if sha:
            repo.update_file(FILE_PATH, commit_message, updated_content, sha)
        else:
            repo.create_file(FILE_PATH, commit_message, updated_content)
        
        st.success(f"數據已成功寫入 GitHub 檔案。")
        st.session_state.df_records = pd.DataFrame() # 清除緩存以重新加載
        return True

    except Exception as e:
        st.error(f"❌ 寫入 GitHub 失敗: {e}")
        return False

# --- 5. 數據讀取和解析函數 (用於查看頁面) ---
@st.cache_data(show_spinner=False)
def read_and_parse_records_to_df(cache_buster):
    """從 GitHub 讀取 TXT 檔案並解析為 DataFrame"""
    content, _ = read_full_content()
    if not content: return pd.DataFrame()

    records = []
    # 匹配 TXT 檔案中包含 Shared, OriginalCurrency 和 Conversion 信息的結構
    pattern = re.compile(
        r'^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] '
        r'User: (?P<User>.*?), '
        r'Shop: (?P<Shop>.*?), '
        r'Total: (?P<Total>.*?)\s*(?P<Currency>[A-Z]{3}?), '
        r'Date: (?P<Date>\d{4}-\d{2}-\d{2}), '
        r'Remarks: (?P<Remarks>.*?), '
        r'Shared: (?P<Shared>.*?),\s*' 
        r'OriginalCurrency: (?P<OriginalCurrency>[A-Z]{3}?), \s*' # <--- 匹配 OriginalCurrency
        r'Conversion: (?P<Conversion>.*?)$',
        re.MULTILINE
    )

    for line in content.strip().split('\n'):
        match = pattern.match(line)
        if match:
            data = match.groupdict()
            # Total 和 Currency 是儲存的 HKD 數據
            data['Amount Recorded'] = f"{data.pop('Total').strip()} {data.pop('Currency').strip()}"
            records.append(data)
    
    df = pd.DataFrame(records)
    if df.empty: return df
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values(by='timestamp', ascending=False).reset_index(drop=True)
    df['Record_ID'] = df.index 
    return df


# --- 6. 刪除/更新 執行函數 ---

def execute_github_action(action, record_id_to_target, new_data=None):
    """執行刪除或更新操作，並寫回整個檔案"""
    full_content, sha = read_full_content()
    
    if full_content is None or sha is None:
        st.error("❌ 無法讀取 GitHub 檔案或 SHA 缺失。")
        return False

    df = st.session_state.df_records
    
    if df.empty or record_id_to_target not in df['Record_ID'].values:
        st.error("❌ 找不到目標記錄。")
        return False

    target_row = df[df['Record_ID'] == record_id_to_target].iloc[0]
    target_line_start = f"[{target_row['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}] User: {target_row['User']}"
    
    original_lines = full_content.strip().split('\n')
    new_content_lines = []
    
    for line in original_lines:
        if line.startswith(target_line_start):
            if action == 'delete':
                continue # 刪除
            elif action == 'update' and new_data:
                # 重新創建新的記錄行 (注意：使用當前時間戳)
                new_line = (
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"User: {new_data['user_name']}, "
                    f"Shop: {new_data['shop_name']}, "
                    f"Total: {new_data['total_amount']:.2f} {new_data['currency']}, " # HKD 金額
                    f"Date: {new_data['transaction_date']}, "
                    f"Remarks: {new_data['remarks']}, "
                    f"Shared: {new_data.get('is_shared', 'No')}, " 
                    f"OriginalCurrency: {new_data.get('original_currency', BASE_CURRENCY)}, " # <--- 新增
                    f"Conversion: {new_data.get('conversion_notes', 'Manually Edited')}\n"
                )
                new_content_lines.append(new_line.strip())
                continue

        new_content_lines.append(line)

    new_content = "\n".join(new_content_lines) + "\n"
    
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        
        commit_msg = f"feat: {action.capitalize()} record ID {record_id_to_target}"
        
        repo.update_file(FILE_PATH, commit_msg, new_content, sha)
        st.session_state.df_records = pd.DataFrame() # 清除緩存以重新加載
        st.success(f"✅ {action.capitalize()} 操作成功完成！")
        return True
    except Exception as e:
        st.error(f"❌ GitHub {action.capitalize()} 失敗: {e}")
        return False


# --- 7. 編輯和刪除 UI 輔助函數 ---

def display_delete_confirmation(record):
    """顯示刪除確認框"""
    st.error(f"⚠️ 確認刪除記錄 (ID: {record['Record_ID']})：{record['Shop']} - {record['Amount Recorded']}？")
    
    col_confirm, col_cancel = st.columns(2)
    
    with col_confirm:
        if st.button("確定刪除", key=f"confirm_delete_{record['Record_ID']}"):
            if execute_github_action('delete', record['Record_ID']):
                st.session_state.delete_confirm_id = None
                st.rerun()

    with col_cancel:
        if st.button("取消刪除", key=f"cancel_delete_{record['Record_ID']}"):
            st.session_state.delete_confirm_id = None
            st.rerun()


def display_edit_form(record):
    """顯示編輯選定記錄的表單"""
    st.subheader(f"✏️ 編輯記錄 (ID: {record['Record_ID']})")
    
    # 記錄的金額和貨幣始終是 HKD (BASE_CURRENCY)
    amount_parts = record['Amount Recorded'].split()
    current_amount = float(amount_parts[0])
    current_currency = amount_parts[-1] # 應該是 HKD
    
    try:
        current_date = datetime.strptime(record['Date'], '%Y-%m-%d').date()
    except:
        current_date = date.today()
        
    current_shared_status = record['Shared'].upper() == 'YES'
    
    # 獲取原始貨幣，用於在編輯表單中顯示預設選項
    current_original_currency = record.get('OriginalCurrency', BASE_CURRENCY)


    with st.form(key=f"edit_form_{record['Record_ID']}"):
        edited_shop = st.text_input("商家名稱", value=record['Shop'])
        
        # 允許用戶輸入原始幣種的金額，以便進行轉換
        edited_original_amount = st.number_input(
            f"原始/HKD 金額 ({current_original_currency})", 
            value=current_amount if current_original_currency == BASE_CURRENCY else current_amount, 
            format="%.2f",
            help="請輸入您希望記錄的原始金額。如果選擇 JPY，將自動轉換為 HKD。"
        )

        edited_currency = st.selectbox(
            "原始/轉換貨幣", 
            options=AVAILABLE_CURRENCIES, 
            index=AVAILABLE_CURRENCIES.index(current_original_currency)
        )
        
        edited_date = st.date_input("交易日期", value=current_date)
        edited_remarks = st.text_input("備註", value=record['Remarks'])
        edited_is_shared = st.checkbox("費用是否需要分攤 (Shared)?", value=current_shared_status) 
        
        st.markdown("---")

        col_save, col_cancel = st.columns(2)
        
        if col_save.form_submit_button("✅ 保存更改"):
            
            # 1. 執行轉換 (edited_currency -> HKD)
            converted_amount, final_currency, _ = convert_currency(edited_original_amount, edited_currency)
            conversion_notes = f"Manually edited. Converted from {edited_original_amount} {edited_currency} to {converted_amount:.2f} {final_currency}"

            # 2. 準備新數據
            updated_data = {
                "user_name": record['User'], 
                "remarks": edited_remarks,
                "is_shared": "Yes" if edited_is_shared else "No", 
                "original_currency": edited_currency,         # <-- 記錄用戶選擇的貨幣
                "shop_name": edited_shop,
                "total_amount": converted_amount,             # <-- 儲存 HKD 金額
                "currency": final_currency,                   # <-- 儲存 HKD 幣種
                "transaction_date": edited_date.strftime("%Y-%m-%d"),
                "conversion_notes": conversion_notes
            }
            
            if execute_github_action('update', record['Record_ID'], updated_data):
                st.session_state.edit_id = None
                st.rerun()
            
        if col_cancel.form_submit_button("❌ 取消"):
            st.session_state.edit_id = None
            st.rerun()


# --- 8. 頁面渲染函數 A：提交費用 ---

def render_submission_page():
    """渲染費用提交頁面 (OCR/手動)"""
    st.title("💸 提交費用")
    st.markdown("---")

    submission_mode = st.radio(
        "選擇數據輸入方式：",
        ("📸 圖片 OCR 分析", "✍️ 手動輸入"),
        key="submission_mode"
    )

    with st.form("expense_form"):
        st.subheader("基本信息")
        user_name = st.selectbox("誰支付了？", options=['TWH', 'TSH', 'Olivia'])
        remarks = st.text_input("備註 (可選)", key="remarks_input")
        
        is_shared = st.checkbox("費用是否需要分攤 (Shared)?", value=False) 

        st.markdown("---")

        ocr_data = None
        uploaded_file = None
        
        if submission_mode == "📸 圖片 OCR 分析":
            st.subheader("圖片上傳與 AI 分析")
            uploaded_file = st.file_uploader(
                "上傳收據圖片 (JPEG/PNG)", 
                type=['jpg', 'jpeg', 'png', 'JPG', 'JPEG', 'PNG']
            )

        elif submission_mode == "✍️ 手動輸入":
            st.subheader("手動輸入費用細節")
            manual_shop = st.text_input("商家名稱 (Shop Name)")
            manual_amount = st.number_input("總金額 (Total Amount)", min_value=0.01, format="%.2f")
            
            # 設置 HKD 為預設貨幣，且僅允許 HKD/JPY
            manual_currency = st.selectbox(
                "貨幣 (Currency)", 
                options=["HKD", "JPY"], 
                index=0, # HKD
                key="manual_currency_select"
            )
            
            manual_date = st.date_input("交易日期 (Date)", value="today")

        submitted = st.form_submit_button("執行並提交記錄")

        if submitted:
            # 1. 獲取 OCR/手動 輸入數據
            if submission_mode == "📸 圖片 OCR 分析":
                if uploaded_file is None:
                    st.warning("請上傳收據圖片才能進行分析。")
                    return
                with st.spinner('AI 正在分析收據...'):
                    ocr_data = analyze_receipt(uploaded_file)
            
            elif submission_mode == "✍️ 手動輸入":
                if manual_shop and manual_amount and manual_currency:
                    ocr_data = {
                        "shop_name": manual_shop,
                        "total_amount": float(manual_amount),
                        "currency": manual_currency.upper(),
                        "transaction_date": manual_date.strftime("%Y-%m-%d")
                    }
                else:
                    st.error("請填寫商家名稱、金額和貨幣。")
                    return
            
            if ocr_data:
                
                original_currency = ocr_data.get("currency", "N/A").upper()
                original_amount = ocr_data.get("total_amount", 0.0)
                
                converted_amount = original_amount
                final_currency = original_currency
                
                if original_currency in TARGET_CURRENCIES: # 如果是 JPY
                    # 執行 JPY -> HKD 轉換
                    converted_amount, final_currency, rate = convert_currency(original_amount, original_currency)
                    
                    if rate > 0.0:
                        conversion_info = (
                            f"Original: {original_amount} {original_currency}. "
                            f"Converted to {converted_amount:.2f} {BASE_CURRENCY} (Rate: 1:{rate:.4f})"
                        )
                        final_currency = BASE_CURRENCY # 最終幣種為 HKD
                    else:
                         # 轉換失敗，使用原始 JPY/錯誤幣種，但應記錄
                         st.warning(f"⚠️ 匯率轉換失敗。將使用原始值記錄：{original_amount} {original_currency}。")
                         conversion_info = f"Original: {original_amount} {original_currency}. 轉換失敗，使用原始值記錄。"
                else:
                    # 假設 OCR 識別出 HKD 或其他非 JPY 幣種，我們仍將其視為 HKD 基礎金額
                    final_currency = BASE_CURRENCY
                    conversion_info = f"Original: {original_amount} {original_currency}. Stored as {BASE_CURRENCY}. No conversion needed."

                st.info(conversion_info)
                
                # 組合最終記錄數據
                final_record = {
                    "user_name": user_name,
                    "remarks": remarks,
                    "is_shared": "Yes" if is_shared else "No", 
                    "original_currency": original_currency,      # <-- 儲存原始幣種
                    "shop_name": ocr_data.get("shop_name", "N/A"),
                    "total_amount": converted_amount,            # <-- 儲存轉換後 (HKD) 金額
                    "currency": final_currency,                  # <-- 儲存 HKD
                    "transaction_date": ocr_data.get("transaction_date", datetime.now().strftime("%Y-%m-%d")),
                    "conversion_notes": conversion_info
                }

                st.subheader("📝 提取和確認記錄:")
                st.json(final_record)
                
                with st.spinner('正在寫入 GitHub 儲存庫...'):
                    write_to_github_file(final_record)
            else:
                if submission_mode == "📸 圖片 OCR 分析":
                     st.error("分析失敗，請檢查圖片或嘗試手動輸入。")


# --- 9. 頁面渲染函數 B：查看記錄 ---

def render_view_records_page():
    """渲染查看記錄頁面，包含編輯和刪除按鈕"""
    st.title("📚 歷史費用記錄")
    
    if st.session_state.df_records.empty:
        with st.spinner("從 GitHub 下載並解析數據中..."):
            st.session_state.df_records = read_and_parse_records_to_df(datetime.now()) 

    df = st.session_state.df_records

    if df.empty:
        st.warning("當前檔案中沒有可解析的費用記錄。")
        return

    st.subheader(f"找到 {len(df)} 條記錄")
    st.markdown("---")

    # 手動渲染每條記錄並添加按鈕
    for index, row in df.iterrows():
        record_id = row['Record_ID']
        
        col_data, col_edit, col_delete = st.columns([10, 1, 1])

        # 顯示數據摘要 (包含 Shared 和 OriginalCurrency)
        shared_icon = "👥" if row['Shared'].upper() == 'YES' else "👤"
        
        # 顯示最終金額 (HKD) 和原始幣種
        original_curr_display = f" (原: {row['OriginalCurrency']})" if row['OriginalCurrency'] != BASE_CURRENCY else ""

        record_summary = (
            f"**日期:** {row['Date']} | "
            f"**商家:** {row['Shop']} | "
            f"**金額:** {row['Amount Recorded']}{original_curr_display} | " # <-- 顯示原始貨幣
            f"**用戶:** {row['User']} | "
            f"{shared_icon} **共享:** {row['Shared']} | " 
            f"**備註:** {row['Remarks']}"
        )
        col_data.markdown(record_summary)

        # 編輯按鈕
        if col_edit.button("✏️ 編輯", key=f'edit_{record_id}'):
            st.session_state.edit_id = record_id
            st.session_state.delete_confirm_id = None
            st.rerun()

        # 刪除按鈕
        if col_delete.button("🗑️ 刪除", key=f'delete_{record_id}'):
            st.session_state.delete_confirm_id = record_id
            st.session_state.edit_id = None
            st.rerun()

        st.markdown("---")
        
        # 處理交互式 UI
        if st.session_state.edit_id == record_id:
            display_edit_form(row)
            
        if st.session_state.delete_confirm_id == record_id:
            display_delete_confirmation(row)


# --- 10. 應用程式主運行流程 (切換頁面) ---

st.sidebar.title("導航")
page = st.sidebar.radio(
    "選擇功能頁面：",
    ("提交費用 (OCR/手動)", "查看記錄"),
    key="page_selection"
)

if page == "提交費用 (OCR/手動)":
    render_submission_page()
elif page == "查看記錄":
    render_view_records_page()

st.markdown("---")
