import pandas as pd
import joblib
import os
import datetime
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
import talib # 需要計算大盤均線

def main():
    print("🚀 Day 5: 開始執行 Line 主動推播 (V10 同步版)...")

    # 1. 載入設定與模型
    try:
        line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
        model = joblib.load('stock_ai_model.pkl')
        print("✅ Line Bot 與模型載入成功")
    except Exception as e:
        print(f"❌ 初始化失敗: {e}")
        return

    # 2. 讀取資料
    if not os.path.exists('training_data.csv'):
        print("❌ 找不到 training_data.csv")
        return

    try:
        df = pd.read_csv('training_data.csv', dtype={'stock_id': str})
        last_date = df['trade_date'].max()
        
        # 取得最新一天的所有資料 (用來算大盤氣氛)
        today_data = df[df['trade_date'] == last_date].copy()
        print(f"📅 資料日期: {last_date}")
        
        # ==========================================
        # 🛡️ 1. 執行 V10 市場情緒濾網
        # ==========================================
        # 檢查有多少股票站上月線
        valid_stocks = today_data[today_data['close_price'] > 0]
        market_status = "中性"
        is_bear_market = False
        
        if len(valid_stocks) > 10:
            stocks_above_ma20 = valid_stocks[valid_stocks['close_price'] > valid_stocks['MA20']]
            market_sentiment = len(stocks_above_ma20) / len(valid_stocks)
            
            if market_sentiment < 0.25:
                market_status = f"🐻 空頭 (多頭率 {market_sentiment:.0%})"
                is_bear_market = True
            elif market_sentiment > 0.50:
                market_status = f"🐂 多頭 (多頭率 {market_sentiment:.0%})"
            else:
                market_status = f"⚖️ 盤整 (多頭率 {market_sentiment:.0%})"
        
        print(f"📊 市場狀態: {market_status}")

        # 3. AI 預測
        features = [
            'MA5', 'MA20', 'MA60', 'RSI', 'MACD', 'BB_width', 'Bias_20', 
            'trust_streak', 'institutions_ratio', 'foreign_5d_sum',
            'slowk', 'KD_diff', 'vol_ratio', 'ATR_pct'
        ]
        
        today_data['prob'] = model.predict_proba(today_data[features])[:, 1]
        
        # 4. 組合訊息
        msg = f"🔔 【AI 收盤快報】 ({last_date})\n"
        msg += f"盤勢判斷: {market_status}\n"
        msg += "------------------------\n"

        if is_bear_market:
            msg += "⚠️ 偵測到市場風險過高 (<25% 站上月線)。\n"
            msg += "🛡️ AI 建議：今日空手觀望，保護本金。\n"
        else:
            # 正常篩選 (V10 邏輯: 信心>0.6 + 站上月線)
            picks = today_data[
                (today_data['prob'] >= 0.60) & 
                (today_data['close_price'] > today_data['MA20'])
            ].sort_values('prob', ascending=False).head(5)
            
            if picks.empty:
                msg += "👀 今日無高信心且趨勢向上的標的。\n"
            else:
                for _, row in picks.iterrows():
                    # 判斷 KD 狀態
                    kd_state = "金叉" if row['slowk'] > row['slowd'] else "死叉"
                    msg += f"🔥 {row['stock_id']} (信心 {row['prob']:.0%})\n"
                    msg += f"   收盤: {row['close_price']} | KD: {kd_state}\n"
                    msg += f"   籌碼連買: {int(row['trust_streak'])}天\n"
                    msg += "------------------------\n"
        
        msg += "⚠️ AI 僅供參考，建議設 7% 停損。"

        # 5. 發送
        line_bot_api.broadcast(TextSendMessage(text=msg))
        print("✅ 推播完成！內容如下：")
        print(msg)

    except Exception as e:
        print(f"❌ 執行失敗: {e}")

if __name__ == "__main__":
    main()