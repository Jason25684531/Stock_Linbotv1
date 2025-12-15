import pandas as pd
import joblib
import os
import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from config import Config

app = Flask(__name__)

line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

print("🧠 正在載入 AI 模型...")
try:
    model = joblib.load('stock_ai_model.pkl')
    print("✅ 模型載入成功！")
except Exception as e:
    model = None
    print(f"⚠️ 模型載入失敗: {e}")

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

def get_ai_recommendation():
    if model is None: return "⚠️ 系統維護中。"
    
    # ✨ 修改路徑變數
    file_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
    
    if not os.path.exists(file_path): return "⚠️ 無最新資料。"
    
    try:
        # ✨ 修改讀取路徑
        df = pd.read_csv(file_path, dtype={'stock_id': str})
        last_date = df['trade_date'].max()
        today_data = df[df['trade_date'] == last_date].copy()
        
        # V10 市場濾網邏輯
        valid_stocks = today_data[today_data['close_price'] > 0]
        market_sentiment = 0
        if len(valid_stocks) > 10:
            stocks_above_ma20 = valid_stocks[valid_stocks['close_price'] > valid_stocks['MA20']]
            market_sentiment = len(stocks_above_ma20) / len(valid_stocks)
        
        is_bear = market_sentiment < 0.25
        
        msg = f"🚀 【AI 即時分析】 ({last_date})\n"
        msg += f"多頭率: {market_sentiment:.0%}\n"
        msg += "------------------------\n"
        
        if is_bear:
            msg += "🐻 市場偏空，AI 建議觀望。\n"
        else:
            features = [
                'MA5', 'MA20', 'MA60', 'RSI', 'MACD', 'BB_width', 'Bias_20', 
                'trust_streak', 'institutions_ratio', 'foreign_5d_sum',
                'slowk', 'KD_diff', 'vol_ratio', 'ATR_pct'
            ]
            today_data['prob'] = model.predict_proba(today_data[features])[:, 1]
            
            picks = today_data[
                (today_data['prob'] >= 0.60) & 
                (today_data['close_price'] > today_data['MA20'])
            ].sort_values('prob', ascending=False).head(5)
            
            if picks.empty:
                msg += "👀 無高信心標的。\n"
            else:
                for _, row in picks.iterrows():
                    msg += f"🔥 {row['stock_id']} (信心 {row['prob']:.0%})\n"
                    msg += f"   收盤: {row['close_price']}\n"
                    msg += "------------------------\n"
                    
        msg += "⚠️ 投資請自負風險。"
        return msg

    except Exception as e:
        return f"❌ 錯誤: {e}"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    reply = ""
    if msg == "推薦" or msg == "選股":
        reply = get_ai_recommendation()
    elif msg.startswith("查詢"):
        stock_id = msg.replace("查詢", "").strip()
        reply = f"🔍 收到查詢 {stock_id} (開發中)"
    else:
        reply = "🤖 輸入「推薦」獲取分析。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)