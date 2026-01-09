import pandas as pd
from sqlalchemy import create_engine, text
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
from tool.strategy import get_v30_candidates, get_v30_params_from_db, calculate_v30_signal
import os
import datetime

# ==========================================
# 🔧 設定區
# ==========================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
LINE_TOKEN = Config.LINE_CHANNEL_ACCESS_TOKEN
BOND_SYMBOL = Config.BOND_SYMBOL
MARKET_SYMBOL = Config.MARKET_SYMBOL

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

def get_v30_picks(engine, date_str):
    """V30 策略選股（40% 報酬實績）"""
    # 1. 抓取當日全市場資料
    query = text(f"""
        SELECT * FROM daily_market_data
        WHERE trade_date = '{date_str}' 
        AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}', '00632R')
        AND close_price > 10
        AND close_price < 500
    """)
    
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    
    if df.empty: 
        return []

    # 2. 使用 V30 統一篩選函數
    candidates = get_v30_candidates(df)
    
    if candidates.empty:
        return []
    
    # 3. 依外資買超或成交量排序，取前 5 名
    if 'foreign_buy' in candidates.columns:
        top_picks = candidates.sort_values('foreign_buy', ascending=False).head(5)
    else:
        top_picks = candidates.sort_values('volume', ascending=False).head(5)
    
    # 4. 產生結果
    results = []
    for _, row in top_picks.iterrows():
        v30_result = calculate_v30_signal(row)
        results.append({
            'id': row['stock_id'],
            'price': row['close_price'],
            'rsi': row.get('rsi', 0),
            'stop_loss': v30_result['stop_loss'],
            'take_profit': v30_result['take_profit'],
            'foreign': int(row.get('foreign_buy', 0) / 1000)  # 轉張
        })
    
    return results

def main():
    print("🚀 V3.0 Line 推播啟動 (V30策略)...")
    line_bot_api = LineBotApi(LINE_TOKEN)
    engine = create_engine(DB_URL)
    
    # 1. 取得最新日期
    with engine.connect() as conn:
        latest_date = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
    
    if not latest_date:
        print("❌ 資料庫無資料")
        return

    date_str = latest_date.strftime("%Y-%m-%d")
    print(f"📅 資料日期: {date_str}")

    # 2. 判斷市場狀態
    status, bias = get_market_status(engine, date_str)
    
    # 3. V30 策略選股
    picks = get_v30_picks(engine, date_str)
    
    # 4. 組合訊息
    msg = f"📅 【StockAI 日報】 {date_str}\n"
    msg += f"--------------------------\n"
    msg += f"🚦 市場狀態: {status}\n"
    msg += f"📊 大盤乖離: {bias:.2f}%\n"
    msg += f"--------------------------\n"
    
    if picks:
        msg += "🚀 V30 策略推薦 (40%實績):\n"
        for p in picks:
            msg += f"🎫 {p['id']} (${p['price']:.2f})\n"
            msg += f"   RSI: {p['rsi']:.1f} | 外資: {p['foreign']:+,}張\n"
            msg += f"   🛡️ 停損: ${p['stop_loss']:.2f} | 🎯 停利: ${p['take_profit']:.2f}\n"
        msg += f"--------------------------\n"
        msg += f"⏰ 持有期限: 最長{V30_PARAMS['MAX_HOLD_DAYS']}天\n"
        msg += "💡 嚴格執行停損停利"
    else:
        msg += "🐢 今日無符合V30條件標的\n"
        msg += "☕ 建議空手觀望"

    print(msg)
    
    # 5. 發送廣播
    try:
        line_bot_api.broadcast(TextSendMessage(text=msg))
        print("✅ Line 推播已發送！")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

if __name__ == "__main__":
    main()