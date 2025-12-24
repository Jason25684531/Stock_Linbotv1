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
# 1. 引用新聞特工
try:
    from tool.news_agent import get_market_briefing
except ImportError:
    pass 

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
    'MA5', 'MA20', 'MA60', 'RSI','PEG','foreign_ratio', 'trust_ratio'
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

# --- 🟢 [新增] 資金控管建議模組 ---
def calculate_position_size(win_prob, peg, capital=1000000):
    """
    動態資金控管 (Kelly-like)
    根據 AI 信心度 與 PEG 估值，給出建議部位大小
    """
    # 基礎倉位 (單筆最大風險暴露)
    base_pos = 0.20 # 最多買 20%
    
    # 信心加成 (Prob)
    if win_prob >= 0.70:
        conf_score = 1.0 # 信心滿
    elif win_prob >= 0.60:
        conf_score = 0.6 # 信心強
    elif win_prob >= 0.53:
        conf_score = 0.3 # 信心普通
    else:
        conf_score = 0.0 # 信心不足
        
    # 估值扣分 (PEG) - 越貴買越少
    if peg < 1.0:
        val_score = 1.0 # 超便宜
    elif peg < 1.5:
        val_score = 0.8 # 合理
    else:
        val_score = 0.5 # 有點貴
        
    # 最終建議比例
    final_ratio = base_pos * conf_score * val_score
    suggested_money = int(capital * final_ratio)
    
    if final_ratio >= 0.15:
        advice = f"🔥 重倉出擊 ({final_ratio:.0%}資金)"
    elif final_ratio >= 0.05:
        advice = f"⚖️ 中度佈局 ({final_ratio:.0%}資金)"
    elif final_ratio > 0:
        advice = f"👀 輕倉嘗試 ({final_ratio:.0%}資金)"
    else:
        advice = "☕ 建議觀望"
        
    return advice, suggested_money

# 功能 1: AI 推薦 (全市場掃描)
def get_ai_recommendation():
    if daily_data.empty or model is None: return "⚠️ 系統資料準備中..."
    try:
        rename_map = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume'}
        process_df = daily_data.rename(columns=rename_map)
        
        # 確保有 PEG 欄位
        if 'PEG' not in process_df.columns:
            return "⚠️ 資料庫尚未更新 PEG 欄位，請重新執行 feature_engineering。"

        if 'prob' not in process_df.columns:
             process_df['prob'] = model.predict_proba(process_df[V12_FEATURES])[:, 1]
        
        
        # 1. AI 信心 > 50%
        # 2. PEG < 1.2 (原本是 1.5) -> 排除高估股
        # 3. PE > 0 (原本就有) -> 排除虧損股
        good_stocks = process_df[
            (process_df['prob'] >= 0.50) & 
            (process_df['PEG'] < 1.2) &  # 👈 這裡改成 1.2
            (process_df['pe_ratio'] > 0)
        ]
        
        picks = good_stocks.sort_values('prob', ascending=False).head(5)
        
        msg = f"🚀 【AI 嚴選價值股】 ({process_df['trade_date'].iloc[0].date()})\n"
        msg += f"(條件: PEG < 1.2 且 獲利中)\n" # 更新提示文字
        
        if picks.empty:
            msg += "🐢 今日無符合「高成長+低估值」之標的。"
        else:
            for _, row in picks.iterrows():
                # 加入簡單的資金建議提示
                advice, _ = calculate_position_size(row['prob'], row['PEG'])
                msg += f"🔥 {row['stock_id']} 信心:{row['prob']:.1%} (PEG:{row['PEG']:.2f})\n   👉 {advice}\n"
                
        return msg
    except Exception as e:
        return f"❌ 運算錯誤: {e}"

# 功能 2: 個股查詢 (含圖表)
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
    peg_val = 999
    if model:
        try:
            ai_prob = model.predict_proba(pd.DataFrame([row])[V12_FEATURES])[:, 1][0]
            peg_val = row.get('PEG', 999) # 取得 PEG
        except: pass

    # 戰術計算
    strat_result = calculate_pivot_strategy(
        high=float(row['high_price']),
        low=float(row['low_price']),
        close=float(row['close_price']),
        ai_prob=ai_prob
    )
    
    # 🟢 [新增] 計算資金控管建議
    pos_advice, pos_money = calculate_position_size(ai_prob, peg_val)
    
    # 產生文字報告 (這裡插入資金建議)
    base_msg = format_strategy_message(
        stock_id, row['trade_date'], row['close_price'], ai_prob, strat_result
    )
    
    # 將資金建議附加在文字訊息最後
    final_msg = base_msg + f"💰 資金控管: {pos_advice}\n(以百萬本金為例: ${pos_money:,})"
    
    messages = [TextSendMessage(text=final_msg)]
    
    # 畫圖
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