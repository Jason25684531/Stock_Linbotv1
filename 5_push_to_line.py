import sys
import io

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
from sqlalchemy import text
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
from tool.strategy import get_v30_candidates, get_v30_params_from_db, calculate_v30_signal
from tool.db_helper import get_db_engine, get_market_trend, get_stock_data
from tool.strategy_manager import StrategyManager

# ==========================================
# 🔧 設定區 (統一使用 db_helper)
# ==========================================


def get_market_status(engine, date_str):
    """
    判斷市場紅綠燈 (整合 db_helper.get_market_trend)
    
    🔄 V33 Refactor: 使用共用的市場趨勢判斷函數
    """
    # 使用共用函數取得趨勢
    trend = get_market_trend(date_str)
    
    # 取得詳細資料計算乖離率
    df, _ = get_stock_data(Config.MARKET_SYMBOL, date_str)
    if df.empty:
        return "⚪ 資料不足", 0
    
    data = df.iloc[0]
    ma60 = data.get('ma60', data['close_price'])
    bias = (data['close_price'] - ma60) / ma60 * 100 if ma60 > 0 else 0
    
    # 根據趨勢返回狀態
    if trend == 'BULL':
        return "🔴 多頭 (進攻)", bias
    elif bias < -8:
        return "🟢 恐慌 (避險)", bias
    else:
        return "🟡 空頭 (觀望)", bias

def get_v30_picks(date_str):
    """
    V30 策略選股（40% 報酬實績）
    
    🔄 V33 Refactor: 使用 get_stock_data() 共用函數
    """
    # 1. 使用共用函數抓取資料
    df, _ = get_stock_data(date_str=date_str)
    
    if df.empty: 
        return []

    # 2. 過濾價格區間
    df = df[(df['close_price'] > 10) & (df['close_price'] < 500)]
    
    if df.empty:
        return []

    # 3. 使用 V30 統一篩選函數
    candidates = get_v30_candidates(df)
    
    if candidates.empty:
        return []
    
    # 4. 依外資買超或成交量排序，取前 5 名
    if 'foreign_buy' in candidates.columns:
        top_picks = candidates.sort_values('foreign_buy', ascending=False).head(5)
    else:
        top_picks = candidates.sort_values('volume', ascending=False).head(5)
    
    # 5. 產生結果
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
    print("🚀 V33 Line 推播啟動 (Strategy Factory)...")
    line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
    engine = get_db_engine()
    
    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategy = manager.get_active_strategy()
    print(f"📊 當前策略: {strategy.display_name}")
    
    # 2. 取得最新日期
    with engine.connect() as conn:
        latest_date = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
    
    if not latest_date:
        print("❌ 資料庫無資料")
        return

    date_str = latest_date.strftime("%Y-%m-%d")
    print(f"📅 資料日期: {date_str}")

    # 3. 判斷市場狀態
    status, bias = get_market_status(engine, date_str)
    
    # 4. 從資料庫讀取今日推薦
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT stock_id, close_price, ai_score, rsi, volume
            FROM daily_recommendations
            WHERE trade_date = :date AND strategy = :strategy
            ORDER BY ai_score DESC
            LIMIT 5
        """), {"date": date_str, "strategy": strategy.name})
        picks = result.fetchall()
    
    # 5. 組合訊息
    msg = f"📅 【StockAI 日報】 {date_str}\n"
    msg += f"--------------------------\n"
    msg += f"🛡️ 當前策略: {strategy.display_name}\n"
    msg += f"🚦 市場狀態: {status}\n"
    msg += f"📊 大盤乖離: {bias:.2f}%\n"
    msg += f"--------------------------\n"
    
    if picks:
        msg += f"🚀 {strategy.display_name}推薦:\n"
        for p in picks:
            stock_id, price, ai_score, rsi, volume = p
            msg += f"🎫 {stock_id} (${price:.2f})\n"
            
            # 動態顯示指標
            if strategy.name == 'v33_low_vol':
                # V33 顯示 NATR
                df_stock = pd.read_sql(
                    f"SELECT natr FROM daily_market_data WHERE stock_id='{stock_id}' AND trade_date='{date_str}'",
                    engine
                )
                if not df_stock.empty:
                    natr = df_stock.iloc[0]['natr']
                    msg += f"   🌊 NATR: {natr:.2f}%"
            elif strategy.name == 'v34_turbo':
                # V34 顯示營收年增
                df_stock = pd.read_sql(
                    f"SELECT revenue_yoy FROM daily_market_data WHERE stock_id='{stock_id}' AND trade_date='{date_str}'",
                    engine
                )
                if not df_stock.empty:
                    rev_yoy = df_stock.iloc[0]['revenue_yoy']
                    msg += f"   💰 營收年增: {rev_yoy:.1f}%"
            else:
                # V31 顯示 RSI
                if rsi:
                    msg += f"   📈 RSI: {rsi:.1f}"
            
            # AI分數
            if ai_score:
                msg += f" | 🤖 AI: {ai_score:.1%}\n"
            else:
                msg += "\n"
        
        msg += f"--------------------------\n"
        msg += f"🎯 目標報酬: {strategy.target_return}%\n"
        msg += f"⏰ 持有期限: {strategy.look_ahead_days}天\n"
        msg += "💡 嚴格執行停損停利"
    else:
        msg += f"🐢 今日無符合{strategy.name}條件標的\n"
        msg += "☕ 建議空手觀望"

    print(msg)
    
    # 6. 發送廣播
    try:
        line_bot_api.broadcast(TextSendMessage(text=msg))
        print("✅ Line 推播已發送！")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

if __name__ == "__main__":
    main()