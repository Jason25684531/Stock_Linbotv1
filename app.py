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
try:
    from tool.news_agent import get_market_briefing
except ImportError:
    pass 

from tool.strategy import calculate_pivot_strategy, format_strategy_message, calculate_position_size
from tool.plotter import plot_stock_chart

app = Flask(__name__)

# 初始化 Line Bot
line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# --- 全局載入資源 ---
print("🧠 正在載入 AI 模型與資料...")
model = None
FEATURES = [] # 先宣告空的

try:
    model_path = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')
    if not os.path.exists(model_path): model_path = 'stock_ai_model.pkl'
    
    model = joblib.load(model_path)
    
    # 🟢 [自動同步] 直接問模型它需要什麼特徵，永遠不會錯
    try:
        FEATURES = model.get_booster().feature_names
    except:
        # 萬一讀不到，才用手動備案 (V20)
        FEATURES = [
            'open_price', 'high_price', 'low_price', 'close_price', 'volume',
            'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
            'MA5', 'MA20', 'MA60', 'RSI',
            'MACD_hist', 'KD_K', 'BB_width',
            'PEG', 'foreign_ratio', 'trust_ratio', 'trust_ma3'
        ]
        
    print(f"✅ 模型載入成功！(特徵數: {len(FEATURES)})")

except Exception as e:
    print(f"⚠️ 模型載入失敗: {e}")

full_df = pd.DataFrame()
daily_data = pd.DataFrame()
try:
    data_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
    if os.path.exists(data_path):
        full_df = pd.read_csv(data_path, dtype={'stock_id': str})
        full_df['trade_date'] = pd.to_datetime(full_df['trade_date'])
        last_date = full_df['trade_date'].max()
        daily_data = full_df[full_df['trade_date'] == last_date].copy()
        print(f"✅ 資料載入成功！日期: {last_date.date()}, 共 {len(daily_data)} 筆")
    else:
        print(f"⚠️ 找不到資料檔: {data_path}")
except Exception as e:
    print(f"⚠️ 資料載入失敗: {e}")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 功能 1: AI 推薦
def get_ai_recommendation():
    if daily_data.empty or model is None: return "⚠️ 系統資料準備中..."
    try:
        rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
        process_df = daily_data.rename(columns=rename_map)
        
        # 檢查特徵齊全度
        missing = [c for c in FEATURES if c not in process_df.columns]
        if missing:
            return f"⚠️ 資料庫特徵不足，請重新執行 feature_engineering。\n缺: {missing}"

        if 'prob' not in process_df.columns:
             process_df['prob'] = model.predict_proba(process_df[FEATURES])[:, 1]
        
        # 篩選條件：AI > 50% 且 PEG < 1.2
        good_stocks = process_df[
            (process_df['prob'] >= 0.50) & 
            (process_df['PEG'] < 1.75) & 
            (process_df['pe_ratio'] > 0)
        ]
        
        picks = good_stocks.sort_values('prob', ascending=False).head(5)
        
        msg = f"🚀 【AI 狙擊選股】 ({process_df['trade_date'].iloc[0].date()})\n"
        
        if picks.empty:
            msg += "🐢 今日無符合標的 (市場可能過熱或過冷)。"
        else:
            for _, row in picks.iterrows():
                advice, _ = calculate_position_size(row['prob'], row['PEG'], capital=1000000)
                msg += f"🔥 {row['stock_id']} 信心:{row['prob']:.1%} (PEG:{row['PEG']:.2f})\n   👉 {advice}\n"
                
        return msg
    except Exception as e:
        return f"❌ 運算錯誤: {e}"

# 功能 2: 個股查詢
def query_stock(stock_id, base_url):
    if daily_data.empty: return [TextSendMessage(text="⚠️ 無資料。")]
    
    rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
    df_clean = daily_data.rename(columns=rename_map)
    
    stock = df_clean[df_clean['stock_id'] == stock_id]
    if stock.empty:
        return [TextSendMessage(text=f"🔍 找不到股票 {stock_id}")]
    
    row = stock.iloc[0]
    
    ai_prob = 0.0
    peg_val = 999
    
    # 檢查特徵是否存在
    if model and all(c in pd.DataFrame([row]).columns for c in FEATURES):
        try:
            ai_prob = model.predict_proba(pd.DataFrame([row])[FEATURES])[:, 1][0]
            peg_val = row.get('PEG', 999)
        except: pass

    strat_result = calculate_pivot_strategy(
        high=float(row['high_price']),
        low=float(row['low_price']),
        close=float(row['close_price']),
        ai_prob=ai_prob
    )
    
    pos_advice, pos_money = calculate_position_size(ai_prob, peg_val, capital=100000)
    
    base_msg = format_strategy_message(
        stock_id, row['trade_date'], row['close_price'], ai_prob, strat_result
    )
    final_msg = base_msg + f"💰 資金建議: {pos_advice}\n(以十萬本金為例: ${pos_money:,})"
    
    messages = [TextSendMessage(text=final_msg)]
    
    try:
        stock_hist = full_df[full_df['stock_id'] == stock_id].copy()
        if not stock_hist.empty:
            to_mpf_map = {
                'open_price': 'Open', 'high_price': 'High', 'low_price': 'Low', 
                'close_price': 'Close', 'volume': 'Volume'
            }
            stock_hist = stock_hist.rename(columns=to_mpf_map)
            img_name = plot_stock_chart(stock_id, stock_hist, strat_result)
            if img_name:
                safe_base = base_url.rstrip('/')
                img_url = f"{safe_base}/static/{img_name}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
    except Exception as e:
        print(f"❌ 畫圖失敗: {e}")
    
    return messages
    
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_text = event.message.text.strip()
    base_url = request.host_url.replace("http://", "https://")
    
    if msg_text in ["推薦", "選股"]:
        reply_text = get_ai_recommendation()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        
    elif msg_text in ["新聞", "news", "News", "報紙"]:
        reply_text = get_market_briefing()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    elif msg_text.isdigit():
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