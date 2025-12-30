import pandas as pd
from sqlalchemy import create_engine, text
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
import joblib
import os
import datetime

# ==========================================
# 🔧 設定區
# ==========================================
# 使用你 Config 裡的設定
DB_URL = Config.SQLALCHEMY_DATABASE_URI
LINE_TOKEN = Config.LINE_CHANNEL_ACCESS_TOKEN
MODEL_PATH = os.path.join('stock_ai_model.pkl') # 假設模型在根目錄或 ML_Data
BOND_SYMBOL = '00679B'
MARKET_SYMBOL = '0050'

def get_market_status(engine, date_str):
    """判斷市場紅綠燈 (V27 雙重濾網)"""
    query = text(f"SELECT * FROM daily_market_data WHERE stock_id='{MARKET_SYMBOL}' AND trade_date='{date_str}'")
    with engine.connect() as conn:
        data = conn.execute(query).mappings().fetchone()
    
    if not data: return "⚪ 資料不足", 0
    
    # 確保有 ma20
    ma20 = data['ma20'] if data.get('ma20') else data['close_price']
    bias = (data['close_price'] - data['ma60']) / data['ma60'] * 100
    
    # 雙重濾網: 股價 > 月線 > 季線
    if data['close_price'] > ma20 and data['close_price'] > data['ma60']:
        return "🔴 多頭 (進攻)", bias
    elif bias < -8:
        return "🟢 恐慌 (避險)", bias
    else:
        return "🟡 空頭 (觀望)", bias

def get_ai_picks(engine, model, date_str):
    """AI 選股 (V27 隨機森林)"""
    # 1. 初步篩選 (流動性 + 趨勢)
    query = text(f"""
        SELECT * FROM daily_market_data
        WHERE trade_date = '{date_str}' 
        AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}', '00632R')
        AND volume > 2000000
        AND close_price > ma20
    """)
    with engine.connect() as conn:
        candidates = pd.read_sql(query, conn)
    
    if candidates.empty: return []

    # 2. 準備特徵 (必須跟訓練時完全一樣)
    features = ['rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width', 'volume']
    
    # 確保欄位存在，缺的補 0
    for f in features:
        if f not in candidates.columns: candidates[f] = 0
            
    X = candidates[features].fillna(0)
    
    # 3. AI 預測
    probs = model.predict_proba(X)[:, 1]
    candidates['ai_score'] = probs
    
    # 4. 選前 5 名
    top_picks = candidates.sort_values('ai_score', ascending=False).head(5)
    
    results = []
    for _, row in top_picks.iterrows():
        results.append({
            'id': row['stock_id'],
            'score': row['ai_score'],
            'price': row['close_price']
        })
    return results

def main():
    print("🚀 V27 Line 推播啟動...")
    line_bot_api = LineBotApi(LINE_TOKEN)
    engine = create_engine(DB_URL)
    
    # 1. 載入模型
    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
    elif os.path.exists(os.path.join("ML_Data", "pkl", "stock_ai_model.pkl")):
        model = joblib.load(os.path.join("ML_Data", "pkl", "stock_ai_model.pkl"))
    else:
        print("❌ 找不到模型 (stock_ai_model.pkl)，請先跑訓練！")
        return

    # 2. 取得最新日期
    with engine.connect() as conn:
        latest_date = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
    
    if not latest_date:
        print("❌ 資料庫無資料")
        return

    date_str = latest_date.strftime("%Y-%m-%d")
    print(f"📅 資料日期: {date_str}")

    # 3. 判斷市場
    status, bias = get_market_status(engine, date_str)
    
    # 4. 組合訊息
    msg = f"📅 【StockAI 日報】 {date_str}\n"
    msg += f"--------------------------\n"
    msg += f"🚦 市場狀態: {status}\n"
    msg += f"📊 大盤乖離: {bias:.2f}%\n"
    msg += f"--------------------------\n"
    
    if "多頭" in status:
        picks = get_ai_picks(engine, model, date_str)
        if picks:
            msg += "🔥 AI 嚴選飆股:\n"
            for p in picks:
                msg += f"🎫 {p['id']} (${p['price']})\n"
                msg += f"   🧠 信心: {int(p['score']*100)}%\n"
        else:
            msg += "⚠️ 無符合高分個股\n"
            
    elif "恐慌" in status:
        msg += f"🛡️ 建議避險: 買入 {BOND_SYMBOL}\n"
        
    else:
        msg += "☕ 建議空手觀望，多看少做\n"
        msg += "等待大盤站上月線再進場。"

    print(msg)
    
    # 5. 發送廣播
    try:
        line_bot_api.broadcast(TextSendMessage(text=msg))
        print("✅ Line 推播已發送！")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

if __name__ == "__main__":
    main()