import os
import json
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from io import BytesIO
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
app = Flask(__name__)

# --- 環境變數設定 (從 .env 讀取) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 初始化客戶端
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# --- Gemini 輸出結構 ---
RECEIPT_SCHEMA = types.Schema(...) # 結構與前次提供的相同，請自行複製貼上

@app.route('/analyze-receipt', methods=['POST'])
def analyze_receipt():
    # ... (接收檔案和 User/Remarks 的邏輯與前次相同，請自行複製貼上) ...
    
    # 呼叫 Gemini API (與前次提供的邏輯相同)
    try:
        response = gemini_client.models.generate_content(
            # ... (Gemini 呼叫配置) ...
        )
        ocr_data = json.loads(response.text)
        
        # --- 數據庫寫入 ---
        if not supabase:
            return jsonify({"error": "Database connection not established"}), 500

        data_to_insert = {
            "user_name": request.form.get('user', 'Unknown'),
            "remarks": request.form.get('remarks', ''),
            "shop_name": ocr_data.get("shop_name"),
            "total_amount": ocr_data.get("total_amount"),
            "currency": ocr_data.get("currency"),
            "transaction_date": ocr_data.get("transaction_date") 
        }

        # 寫入 Supabase
        data, count = supabase.table('expenses').insert(data_to_insert).execute()

        return jsonify({
            "message": "Record saved successfully", 
            "db_record": data[1][0] # 獲取返回的數據
        }), 200

    except Exception as e:
        return jsonify({"error": f"Processing failed: {e}"}), 500

if __name__ == '__main__':
    app.run(port=5000)
