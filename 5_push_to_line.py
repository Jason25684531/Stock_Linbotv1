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
# 策略顯示名稱對照表
# ==========================================
STRATEGY_DISPLAY_NAMES = {
    'v31_hybrid': '🔹 均衡型 (V31)',
    'v33_low_vol': '🛡️ 穩健型 (V33)',
    'v34_turbo': '🚀 飆股型 (V34)',
}


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


def main():
    print("🚀 V33 Line 推播啟動 (Multi-Strategy Support)...")
    line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
    engine = get_db_engine()
    
    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    strategy_names = manager.get_active_strategy_names()
    
    print(f"📊 啟用策略數量: {len(strategies)}")
    print(f"📋 策略列表: {', '.join(strategy_names)}")
    
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
    
    # 4. 組合訊息標頭
    msg = f"📅 【StockAI 日報】 {date_str}\n"
    msg += f"--------------------------\n"
    msg += f"🚦 市場狀態: {status}\n"
    msg += f"📊 大盤乖離: {bias:.2f}%\n"
    msg += f"--------------------------\n"
    
    # 5. 遍歷所有策略，撈取推薦結果
    has_picks = False
    
    for strategy in strategies:
        strategy_label = STRATEGY_DISPLAY_NAMES.get(strategy.name, strategy.display_name)
        
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT stock_id, close_price, ai_score, rsi, volume
                FROM daily_recommendations
                WHERE trade_date = :date AND strategy = :strategy
                ORDER BY ai_score DESC
                LIMIT 5
            """), {"date": date_str, "strategy": strategy.name})
            picks = result.fetchall()
        
        if picks:
            has_picks = True
            msg += f"\n== {strategy_label} ==\n"
            
            for p in picks:
                stock_id, price, ai_score, rsi, volume = p
                msg += f"🎫 {stock_id} (${price:.2f})"
                
                # AI分數
                if ai_score:
                    msg += f" | 🤖 {ai_score:.0%}"
                msg += "\n"
            
            msg += f"🎯 目標: {strategy.target_return}% / ⏰ {strategy.look_ahead_days}天\n"
    
    # 6. 無推薦時的訊息
    if not has_picks:
        msg += f"\n🐢 今日無符合條件標的\n"
        msg += "☕ 建議空手觀望"
    else:
        msg += f"\n--------------------------\n"
        msg += "💡 嚴格執行停損停利"

    print("\n" + "="*40)
    print("📨 推播訊息預覽:")
    print("="*40)
    print(msg)
    print("="*40)
    
    # 7. 發送廣播
    try:
        line_bot_api.broadcast(TextSendMessage(text=msg))
        print("✅ Line 推播已發送！")
    except Exception as e:
        print(f"❌ 推播失敗: {e}")

if __name__ == "__main__":
    main()