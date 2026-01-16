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
st.set_page_config(page_title="AI 比例分帳系統 v4", layout="wide") 

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
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.tobytes("ppm"))
    doc.close()
    return img

# --- 2. GitHub 讀寫操作 ---

def save_df_to_github(df):
    repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
    try:
        file = repo.get_contents(FILE_PATH)
        sha = file.sha
    except: sha = None

    lines = []
    # 確保按時間順序排序
    for _, r in df.iterrows():
        ts_str = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(r['timestamp'], pd.Timestamp) else str(r['timestamp'])
        line = (f"[{ts_str}] User: {r['User']}, Shop: {r['Shop']}, "
                f"Total: {r['Total_HKD']:.2f} HKD, Date: {r['Date']}, "
                f"Shared: {r['Shared']}, TWH_n: {r['TWH_n']}, TSH_n: {r['TSH_n']}, "
                f"Orig: {r['Original']}, Rem: {r['Remarks']}\n")
        lines.append(line)
    
    new_content = "".join(lines)
    if sha: repo.update_file(FILE_PATH, "Update/Delete via UI", new_content, sha)
    else: repo.create_file(FILE_PATH, "Init records", new_content)
    st.success("✅ GitHub 記錄已成功更新！")

def write_to_github_file(data):
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
            'Shared': d['sh'].strip(),
            'TWH_n': int(d['tn']), 'TSH_n': int(d['sn']),
            'Original': f"{d['oa']} {d['oc']}", 'Remarks': d['r']
        })
    
    if not records:
        return pd.DataFrame(columns=['timestamp', 'User', 'Shop', 'Total_HKD', 'Date', 'Shared', 'TWH_n', 'TSH_n', 'Original', 'Remarks'])

    return pd.DataFrame(records).sort_values('timestamp', ascending=False).reset_index(drop=True)

# --- 3. 頁面渲染：提交費用 ---

def render_submission_page(def_twh, def_tsh):
    st.title("💸 提交費用")
    mode = st.radio("模式", ["📸 OCR 收據", "✍️ 手動輸入"])
    
    with st.form("sub_form"):
        user = st.selectbox("付款人", ALLOWED_USERS)
        remarks = st.text_input("備註 (例如：幫媽媽買藥)")
        
        st.markdown("---")
        st.write("🔧 **分攤工具箱**")
        col_sh, col_n1, col_n2 = st.columns([2, 2, 2])
        is_shared = col_sh.checkbox("此筆需按人數分攤？", value=True)
        # 使用傳入的預設值
        twh_n = col_n1.number_input("TWH 分攤人數", min_value=1, value=def_twh)
        tsh_n = col_n2.number_input("TSH 分攤人數", min_value=1, value=def_tsh)
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
                    try:
                        res = gemini_client.models.generate_content(
                            model='gemini-2.5-flash-lite',
                            contents=["Extract vendor, amount, currency, date (YYYY-MM-DD) as JSON.", img],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        ocr_data = json.loads(res.text)
                    except Exception as e:
                        st.error(f"AI 錯誤: {e}")
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

# --- 4. 頁面渲染：歷史記錄 (含人均計算) ---

def render_history_page(def_twh, def_tsh):
    st.title("📚 歷史記錄與管理")
    df = read_and_parse_records_to_df()
    
    if df.empty:
        st.info("尚無記錄。")
        return

    # --- 1. 結算看板 ---
    shared_df = df[df['Shared'] == 'Yes'].copy()
    if not shared_df.empty:
        # 計算每筆記錄的應付額
        shared_df['TWH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TWH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)
        shared_df['TSH_Owe'] = shared_df.apply(lambda r: r['Total_HKD'] * (r['TSH_n'] / (r['TWH_n'] + r['TSH_n'])), axis=1)

        # 總和統計
        twh_paid = shared_df[shared_df['User'] == 'TWH']['Total_HKD'].sum()
        twh_should = shared_df['TWH_Owe'].sum() # TWH 整組應付總額
        tsh_should = shared_df['TSH_Owe'].sum() # TSH 整組應付總額
        
        balance = twh_paid - twh_should 

        # --- A. 總結算 ---
        st.subheader("🤝 結算看板 (HKD)")
        c1, c2, c3 = st.columns(3)
        c1.metric("TWH 已先墊付", f"{twh_paid:,.1f}")
        c2.metric("TWH 應付份額", f"{twh_should:,.1f}")
        
        if balance > 0:
            c3.success(f"💰 **TSH 需支付給 TWH: {abs(balance):,.1f}**")
        elif balance < 0:
            c3.warning(f"💰 **TWH 需支付給 TSH: {abs(balance):,.1f}**")
        else:
            c3.info("✅ 目前已平帳")

        # --- B. 人均花費 (新增功能) ---
        # 使用側邊欄設定的 "預設人數" 作為分母來計算人均
        avg_twh = twh_should / def_twh if def_twh > 0 else 0
        avg_tsh = tsh_should / def_tsh if def_tsh > 0 else 0

        st.markdown(f"##### 📊 平均每人花費 (基於設定：TWH {def_twh}人 / TSH {def_tsh}人)")
        k1, k2 = st.columns(2)
        k1.metric(f"TWH 每人平均", f"${avg_twh:,.1f} HKD")
        k2.metric(f"TSH 每人平均", f"${avg_tsh:,.1f} HKD")

    st.markdown("---")

    # --- 2. 互動式編輯表 ---
    st.subheader("📝 編輯或刪除記錄")
    st.caption("說明：修改後請點擊下方「同步」按鈕。刪除請選取行並按 Delete。")

    edited_df = st.data_editor(
        df,
        column_config={
            "timestamp": None,
            "User": st.column_config.SelectboxColumn("付款人", options=ALLOWED_USERS, required=True),
            "Shop": st.column_config.TextColumn("商家名稱"),
            "Total_HKD": st.column_config.NumberColumn("金額 (HKD)", format="%.2f"),
            "Shared": st.column_config.SelectboxColumn("是否分攤", options=["Yes", "No"]),
            "TWH_n": st.column_config.NumberColumn("TWH 人數", min_value=1),
            "TSH_n": st.column_config.NumberColumn("TSH 人數", min_value=1),
        },
        num_rows="dynamic",
        use_container_width=True,
        key="data_editor"
    )

    if st.button("💾 將修改同步至 GitHub", type="primary"):
        with st.spinner("正在更新..."):
            save_df_to_github(edited_df)
            st.rerun()

# --- 5. 主程序 ---

def main():
    st.sidebar.title("⚙️ 設定")
    
    with st.sidebar.expander("👥 人數設定 (用於計算人均)", expanded=True):
        # 這裡設定的值會直接影響「提交頁面預設值」和「歷史頁面的人均計算」
        def_twh = st.number_input("TWH 組人數", min_value=1, value=3)
        def_tsh = st.number_input("TSH 組人數", min_value=1, value=4)

    st.sidebar.markdown("---")
    page = st.sidebar.radio("頁面", ["提交費用", "歷史記錄"])
    
    rate = get_live_exchange_rate("JPY", "HKD")
    if rate: st.sidebar.metric("匯率 (JPY->HKD)", f"{rate:.4f}")
    
    if page == "提交費用":
        render_submission_page(def_twh, def_tsh)
    else:
        render_history_page(def_twh, def_tsh)

if __name__ == "__main__":
    main()
