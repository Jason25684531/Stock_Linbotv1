import pandas as pd
import joblib
import os
import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
from config import Config

# --- 模組引用區 ---
# 1. 引用新聞特工 (自動偵測是 7 號還是 6 號)

from news_agent import get_market_briefing


# 2. 引用策略大腦 (由 strategy.py 負責運算)
from tool.strategy import calculate_pivot_strategy, format_strategy_message

# 3. 引用畫家 (由 plotter.py 負責畫圖)
from tool.plotter import plot_stock_chart

app = Flask(__name__)

# 初始化 Line Bot
line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# --- 🟢 全局載入資源 (啟動時跑一次) ---
print("🧠 正在載入 AI 模型與資料...")

# 1. 載入模型
model = None
try:
    model_path = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')
    if not os.path.exists(model_path): model_path = 'stock_ai_model.pkl'
    
    model = joblib.load(model_path)
    print("✅ 模型載入成功！")
except Exception as e:
    print(f"⚠️ 模型載入失敗: {e}")

# 2. 載入歷史資料 (full_df 用於畫圖，daily_data 用於預測)
full_df = pd.DataFrame()
daily_data = pd.DataFrame()
try:
    data_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
    if os.path.exists(data_path):
        # 讀取完整資料 (為了畫 K 線圖)
        full_df = pd.read_csv(data_path, dtype={'stock_id': str})
        full_df['trade_date'] = pd.to_datetime(full_df['trade_date']) # 轉成日期格式
        
        # 切出最新一天 (為了 AI 預測)
        last_date = full_df['trade_date'].max()
        daily_data = full_df[full_df['trade_date'] == last_date].copy()
        
        print(f"✅ 資料載入成功！日期: {last_date.date()}, 共 {len(daily_data)} 筆")
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
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 功能 1: AI 推薦 (全市場掃描)
def get_ai_recommendation():
    if daily_data.empty or model is None: return "⚠️ 系統資料準備中..."
    try:
        rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
        process_df = daily_data.rename(columns=rename_map)
        
        if 'prob' not in process_df.columns:
             process_df['prob'] = model.predict_proba(process_df[V12_FEATURES])[:, 1]
        
        picks = process_df[process_df['prob'] >= 0.50].sort_values('prob', ascending=False).head(5)
        
        msg = f"🚀 【AI 推薦】 ({process_df['trade_date'].iloc[0].date()})\n"
        for _, row in picks.iterrows():
            msg += f"🔥 {row['stock_id']} 信心: {row['prob']:.1%}\n"
        return msg
    except Exception as e:
        return f"❌ 運算錯誤: {e}"

# 功能 2: 個股查詢 (含圖表) - 回傳 List[SendMessage]
def query_stock(stock_id, base_url):
    if daily_data.empty: return [TextSendMessage(text="⚠️ 無資料。")]
    
    rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
    df_clean = daily_data.rename(columns=rename_map)
    
    stock = df_clean[df_clean['stock_id'] == stock_id]
    if stock.empty:
        return [TextSendMessage(text=f"🔍 找不到股票 {stock_id}")]
    
    row = stock.iloc[0]
    
    # AI 預測
    ai_prob = 0.0
    if model:
        try:
            ai_prob = model.predict_proba(pd.DataFrame([row])[V12_FEATURES])[:, 1][0]
        except: pass

    # 戰術計算 (呼叫 strategy.py)
    strat_result = calculate_pivot_strategy(
        high=float(row['high_price']),
        low=float(row['low_price']),
        close=float(row['close_price']),
        ai_prob=ai_prob
    )
    
    # 產生文字報告
    txt_msg = format_strategy_message(
        stock_id, row['trade_date'], row['close_price'], ai_prob, strat_result
    )
    
    messages = [TextSendMessage(text=txt_msg)]
    
    # 🟢 [修正後] 畫圖並產生圖片訊息
    try:
        # 1. 撈出歷史資料
        stock_hist = full_df[full_df['stock_id'] == stock_id].copy()
        
        if not stock_hist.empty:
            # ⚠️ 關鍵修正：
            # 畫圖套件 mplfinance 指定要 'Open', 'High'... (首字大寫)
            # 如果我們的資料是 'open_price' (小寫)，要轉回來；如果是 'Open' (大寫)，就保留
            
            # 定義一個「轉回大寫」的字典
            to_mpf_map = {
                'open_price': 'Open', 'high_price': 'High', 'low_price': 'Low', 
                'close_price': 'Close', 'volume': 'Volume'
            }
            stock_hist = stock_hist.rename(columns=to_mpf_map)
            
            # (注意：這裡千萬不要再用原本那個 rename_map 了，那是轉小寫用的)

            # 2. 呼叫畫家
            img_name = plot_stock_chart(stock_id, stock_hist, strat_result)
            
            if img_name:
                # 3. 組合圖片網址
                safe_base = base_url.rstrip('/')
                img_url = f"{safe_base}/static/{img_name}"
                
                messages.append(ImageSendMessage(
                    original_content_url=img_url,
                    preview_image_url=img_url
                ))
    except Exception as e:
        print(f"❌ 畫圖失敗: {e}")
    
    return messages
    
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_text = event.message.text.strip()
    
    # 取得目前的網址 (ngrok) 並強制轉 HTTPS
    # 這是為了讓 Line 能讀取到我們的圖片
    base_url = request.host_url.replace("http://", "https://")
    
    if msg_text in ["推薦", "選股"]:
        reply_text = get_ai_recommendation()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif msg_text in ["新聞", "news", "News", "報紙"]:
        # 呼叫新聞特工
        reply_text = get_market_briefing()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif msg_text.isdigit(): # 輸入數字
        reply_msgs = query_stock(msg_text, base_url)
        line_bot_api.reply_message(event.reply_token, reply_msgs)
        
    elif msg_text.startswith("查詢"):
        sid = msg_text.replace("查詢", "").strip()
        reply_msgs = query_stock(sid, base_url)
        line_bot_api.reply_message(event.reply_token, reply_msgs)
        
    else:
        reply = "🤖 指令說明：\n1. 輸入「推薦」看 AI 選股\n2. 輸入「新聞」看國際戰情\n3. 輸入代碼 (如 2330) 查詢 K 線圖"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)