import pandas as pd
import joblib
import os
import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from config import Config

try:
    from tool.strategy import calculate_pivot_strategy, format_strategy_message
except Exception:
    # 在某些環境或歷史 repo 中檔名為 Strategy.py（大寫 S），退回相容匯入
    from tool.Strategy import calculate_pivot_strategy, format_strategy_message

app = Flask(__name__)

# 初始化 Line Bot
line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# --- 🟢 全局載入資源 (只在啟動時跑一次，提升速度) ---
print("🧠 正在載入 AI 模型與資料...")

# 1. 載入模型
model = None
try:
    # 嘗試標準路徑
    model_path = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')
    if not os.path.exists(model_path):
        model_path = 'stock_ai_model.pkl' # 備用路徑
    
    model = joblib.load(model_path)
    print("✅ 模型載入成功！")
except Exception as e:
    print(f"⚠️ 模型載入失敗: {e}")

# 2. 預先讀取資料 (只留最新一天的資料在記憶體)
daily_data = pd.DataFrame()
try:
    data_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
    if os.path.exists(data_path):
        full_df = pd.read_csv(data_path, dtype={'stock_id': str})
        last_date = full_df['trade_date'].max()
        daily_data = full_df[full_df['trade_date'] == last_date].copy()
        print(f"✅ 資料載入成功！日期: {last_date}, 共 {len(daily_data)} 筆")
    else:
        print(f"⚠️ 找不到資料檔: {data_path}")
except Exception as e:
    print(f"⚠️ 資料載入失敗: {e}")

# V12 模型的特徵列表
V12_FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI'
]

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 🟢 V15.0 核心: 支撐壓力計算與買賣建議 ---
def calculate_price_strategy(row, ai_prob):
    """根據今日數據計算明天的支撐壓力，並結合 AI 信心給出建議"""
    try:
        # 確保數值型態正確
        h = float(row['high_price'])
        l = float(row['low_price'])
        c = float(row['close_price'])
        
        # 1. 計算樞紐點 (Pivot Points) - 預測明日
        p = (h + l + c) / 3
        r1 = (2 * p) - l  # 第一壓力 (Target)
        s1 = (2 * p) - h  # 第一支撐 (Entry)
        
        # 2. 生成操作建議 (Strategy)
        strategy = ""
        buy_price = 0
        sell_price = 0
        note = ""
        
        if ai_prob >= 0.60:
            strategy = "🔥 強力看多"
            buy_price = c   # 強勢股直接看收盤或開盤
            sell_price = r1 # 目標價
            note = "建議: 開盤或回檔至 S1 佈局"
        elif ai_prob >= 0.50:
            strategy = "📈 偏多震盪"
            buy_price = s1  # 掛低一點買
            sell_price = r1
            note = "建議: 等回測 S1 支撐再進場"
        else:
            strategy = "😐 觀望/偏空"
            buy_price = s1
            sell_price = p  # 反彈到中軸就跑
            note = "建議: 暫時觀望，勿輕易進場"

        return {
            'p': p, 'r1': r1, 's1': s1,
            'strategy': strategy,
            'buy_suggest': buy_price,
            'sell_suggest': sell_price,
            'note': note
        }
    except Exception as e:
        return None

# 功能 1: AI 推薦 (全市場掃描)
def get_ai_recommendation():
    if daily_data.empty or model is None: return "⚠️ 系統資料準備中..."
    
    try:
        # 欄位名稱對應 (處理舊版 csv 可能用大寫開頭的問題)
        rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
        process_df = daily_data.rename(columns=rename_map)

        # 檢查欄位是否齊全
        missing = [c for c in V12_FEATURES if c not in process_df.columns]
        if missing: return f"⚠️ 資料特徵缺漏: {missing}"

        # 預測
        process_df['prob'] = model.predict_proba(process_df[V12_FEATURES])[:, 1]
        
        # 取信心前 5 名
        picks = process_df[process_df['prob'] >= 0.50].sort_values('prob', ascending=False).head(5)
        
        msg = f"🚀 【AI 推薦】 ({process_df['trade_date'].iloc[0]})\n"
        for _, row in picks.iterrows():
            msg += f"🔥 {row['stock_id']} 信心: {row['prob']:.1%}\n"
        return msg
    except Exception as e:
        return f"❌ 運算錯誤: {e}"

# 功能 2: 個股查詢 (含 V15 戰術分析)
def query_stock(stock_id):
    if daily_data.empty: return "⚠️ 無資料。"
    
    # 1. 找資料
    # (記得要用 rename map 處理欄位名稱，確保是小寫英文)
    rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
    df_clean = daily_data.rename(columns=rename_map)
    
    stock = df_clean[df_clean['stock_id'] == stock_id]
    if stock.empty:
        return f"🔍 找不到股票 {stock_id} (或今日無交易)"
    
    row = stock.iloc[0]
    
    # 2. 問 AI
    ai_prob = 0.0
    if model:
        try:
            ai_prob = model.predict_proba(pd.DataFrame([row])[V12_FEATURES])[:, 1][0]
        except:
            pass

    # 3. 🟢 [關鍵] 呼叫 strategy.py 進行計算
    strat_result = calculate_pivot_strategy(
        high=float(row['high_price']),
        low=float(row['low_price']),
        close=float(row['close_price']),
        ai_prob=ai_prob
    )
    
    # 4. 🟢 [關鍵] 呼叫 strategy.py 進行排版
    msg = format_strategy_message(
        stock_id=stock_id,
        trade_date=row['trade_date'],
        close=row['close_price'],
        ai_prob=ai_prob,
        strat_result=strat_result
    )
    
    return msg

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_text = event.message.text.strip()
    
    # 路由邏輯
    if msg_text in ["推薦", "選股"]:
        reply = get_ai_recommendation()
        
    elif msg_text.startswith("查詢"):
        sid = msg_text.replace("查詢", "").strip()
        reply = query_stock(sid)
        
    elif msg_text.isdigit(): # 直接輸入數字也可以
        reply = query_stock(msg_text)
        
    else:
        reply = "🤖 指令說明：\n1. 輸入「推薦」看 AI 選股\n2. 輸入股票代碼 (如 2330) 查詢分析"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)