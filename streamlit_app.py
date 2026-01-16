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
st.set_page_config(page_title="AI 比例分帳系統 (Edit版)", layout="wide") 

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

def pdf_to_images(uploaded_file):
    """將 PDF 第一頁轉換為圖片以供 AI 分析"""
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.tobytes("ppm"))
    doc.close()
    return img

# --- 2. GitHub 讀寫操作 ---

def save_df_to_github(df):
    """將編輯後的 DataFrame 轉回文字格式並儲存到 GitHub"""
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        sha = file.sha
    except: sha = None

    lines = []
    # 確保按時間順序排序（或者保持編輯後的順序）
    for _, r in df.iterrows():
        # 格式化每一行，確保符合 Regex 解析規則
        ts_str = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(r['timestamp'], pd.Timestamp) else str(r['timestamp'])
        
        line = (f"[{ts_str}] User: {r['User']}, Shop: {r['Shop']}, "
                f"Total: {r['Total_HKD']:.2f} HKD, Date: {r['Date']}, "
                f"Shared: {r['Shared']}, TWH_n: {r['TWH_n']}, TSH_n: {r['TSH_n']}, "
                f"Orig: {r['Original']}, Rem: {r['Remarks']}\n")
        lines.append(line)
    
    new_content = "".join(lines)
    
    if sha:
        repo.update_file(FILE_PATH, "Update/Delete via UI", new_content, sha)
    else:
        repo.create_file(FILE_PATH, "Init records", new_content)
    st.success("✅ GitHub 記錄已成功更新！")

def write_to_github_file(data):
    """新增單筆記錄"""
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
    if sha: repo.update_file(FILE_PATH, "add record", new_content, sha)
    else: repo.create_file(FILE_PATH, "create file", new_content)

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
            'timestamp': pd.to_datetime(d['ts']), 
            'User': d['u'], 'Shop': d['s'], 
            'Total_HKD': float(d['t']), 'Date': d['d'], 
            'Shared': d['sh'].strip(), # 去除可能的空格
            'TWH_n': int(d['tn']), 'TSH_n': int(d['sn']),
            'Original': f"{d['oa']} {d['oc']}", 'Remarks': d['r']
        })
    
    if not records:
        return pd.DataFrame(columns=['timestamp', 'User', 'Shop', 'Total_HKD', 'Date', 'Shared', 'TWH_n', 'TSH_n', 'Original', 'Remarks'])

    return pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)

# --- 3. 頁面渲染：提交費用 ---

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
                    img = pdf_to_images(up) if up.type=="application/pdf" else Image.open(up)
                    res = gemini_client.models.generate_content(
                        model='gemini-2.5-flash-lite', # 使用更快的 Lite 模型
                        contents=["Extract vendor, amount, currency, date (YYYY-MM-DD) as JSON.", img],
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

# --- 4. 頁面渲染：歷史記錄 (含編輯功能) ---

def render_history_page():
    st.title("📚 歷史記錄與管理")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.info("尚無記錄。")
        return

    # --- 1. 結算看板 (保持不變) ---
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        
        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum()
        balance = twh_paid - twh_should # 正數代表 TWH 墊付了，TSH 欠錢

        st.subheader("🤝 即時結算 (HKD)")
        c1, c2, c3 = st.columns(3)
        c1.metric("TWH 已付總額", f"{twh_paid:,.1f}")
        c2.metric("TWH 應付份額", f"{twh_should:,.1f}")
        
        if balance > 0:
            c3.success(f"💰 **TSH 需支付給 TWH: {abs(balance):,.1f}**")
        elif balance < 0:
            c3.warning(f"💰 **TWH 需支付給 TSH: {abs(balance):,.1f}**")
        else:
            c3.info("✅ 目前已平帳")

    st.markdown("---")

    # --- 2. 互動式編輯表 ---
    st.subheader("📝 編輯或刪除記錄")
    st.caption("說明：直接點擊表格內容進行修改。若要**刪除**，請選取該行左側並按下鍵盤的 Delete 鍵。完成後請點擊下方按鈕同步。")

    # 設定欄位顯示屬性
    edited_df = st.data_editor(
        df,
        column_config={
            "timestamp": None, # 隱藏內部時間戳
            "User": st.column_config.SelectboxColumn("付款人", options=ALLOWED_USERS, required=True),
            "Shop": st.column_config.TextColumn("商家名稱"),
            "Total_HKD": st.column_config.NumberColumn("金額 (HKD)", format="%.2f"),
            "Shared": st.column_config.SelectboxColumn("是否分攤", options=["Yes", "No"]),
            "TWH_n": st.column_config.NumberColumn("TWH 人數", min_value=1),
            "TSH_n": st.column_config.NumberColumn("TSH 人數", min_value=1),
            "Original": st.column_config.TextColumn("原始金額 (參考)"),
            "Remarks": st.column_config.TextColumn("備註"),
        },
        num_rows="dynamic", # 允許新增/刪除行
        use_container_width=True,
        key="data_editor"
    )

    # 儲存按鈕
    if st.button("💾 將修改同步至 GitHub", type="primary"):
        with st.spinner("正在更新雲端記錄..."):
            save_df_to_github(edited_df)
            st.rerun() # 重新整理頁面

# --- 5. 主程序 ---

def main():
    st.sidebar.title("🧭 選單")
    page = st.sidebar.radio("頁面跳轉", ["提交費用", "歷史記錄"])
    
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("匯率參考 (JPY->HKD)", f"{rate:.4f}")
    
    if page == "提交費用":
        render_submission_page()
    else:
        render_history_page()

if __name__ == "__main__":
    main()
