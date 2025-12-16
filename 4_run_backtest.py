import pandas as pd
import joblib
import os
import numpy as np
from datetime import timedelta

def calculate_trade_return(entry_date, stock_id, entry_price, full_df, hold_days=20):
    """
    計算單筆交易的損益
    策略：持有 hold_days 天後，以當天收盤價賣出
    """
    # 找到該股票的所有資料
    stock_data = full_df[full_df['stock_id'] == stock_id].sort_values('trade_date')
    
    # 找到進場的那一天在第幾行
    try:
        entry_idx = stock_data[stock_data['trade_date'] == entry_date].index[0]
        # 取得該股票的 index 列表
        stock_indices = stock_data.index.tolist()
        current_pos = stock_indices.index(entry_idx)
        
        # 往後找 20 天 (如果沒資料了，就用最後一天算)
        exit_pos = min(current_pos + hold_days, len(stock_indices) - 1)
        exit_idx = stock_indices[exit_pos]
        
        exit_row = stock_data.loc[exit_idx]
        exit_price = exit_row['close_price']
        exit_date = exit_row['trade_date']
        
        # 計算報酬率
        ret = (exit_price - entry_price) / entry_price
        return ret, exit_price, exit_date
        
    except IndexError:
        return 0.0, entry_price, entry_date # 找無資料，當作沒賠沒賺

def main():
    print("🚀 Day 4: V12 策略回測 (含損益驗證 - 修正版)...")

    # ==========================================
    # 📂 1. 讀取資料與模型
    # ==========================================
    try:
        csv_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
        df = pd.read_csv(csv_path, dtype={'stock_id': str})
        
        model_path = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')
        model = joblib.load(model_path)
    except Exception as e:
        print(f"❌ 檔案讀取失敗: {e}")
        return

    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date')
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    
    # ==========================================
    # 🔮 2. AI 預測
    # ==========================================
    feature_cols = [
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
        'MA5', 'MA20', 'MA60', 'RSI'
    ]
    
    print("🔮 AI 正在計算買進訊號 (Confidence > 53%)...")
    try:
        # 確保資料格式正確再預測
        df['prob'] = model.predict_proba(df[feature_cols])[:, 1]
    except Exception as e:
        print(f"❌ AI 預測失敗: {e}")
        return
    
    # ==========================================
    # ⚔️ 3. 模擬交易
    # ==========================================
    history = []
    
    # [關鍵修正] 使用 numpy.sort 來排序日期，避開 DatetimeArray 錯誤
    dates = np.sort(df['trade_date'].unique())
    
    skipped_by_market = 0
    
    print(f"📅 回測區間: {pd.to_datetime(dates[0]).date()} ~ {pd.to_datetime(dates[-1]).date()}")
    
    for date in dates:
        # 轉換 date 為 pandas Timestamp 比較安全
        ts_date = pd.to_datetime(date)
        daily_data = df[df['trade_date'] == ts_date]
        
        if daily_data.empty: continue

        # --- 大盤濾網 ---
        total_stocks = len(daily_data)
        bullish_stocks = len(daily_data[daily_data['close_price'] > daily_data['MA60']])
        market_sentiment = bullish_stocks / total_stocks if total_stocks > 0 else 0
        
        if market_sentiment < 0.25:
            skipped_by_market += 1
            continue

        # --- 選股邏輯 ---
        # 1. 基本面
        fundamental_pool = daily_data[
            (daily_data['pe_ratio'] < 25) &
            (daily_data['pe_ratio'] > 0) &
            (daily_data['yield_percent'] > 3) &
            (daily_data['implied_roe'] > 0.06) &
            (daily_data['volume'] > 500)
        ]
        
        if fundamental_pool.empty: continue

        # 2. AI 訊號 (門檻 53%)
        buy_signals = fundamental_pool[fundamental_pool['prob'] >= 0.53]
        
        if not buy_signals.empty:
            # 每天只買信心最高的一檔
            top_pick = buy_signals.sort_values('prob', ascending=False).iloc[0]
            
            history.append({
                'entry_date': ts_date,
                'stock_id': top_pick['stock_id'],
                'entry_price': top_pick['close_price'],
                'prob': top_pick['prob'],
                'roe': top_pick['implied_roe']
            })

    # ==========================================
    # 💰 4. 計算損益 (P&L)
    # ==========================================
    print(f"\n⚙️ 正在結算 {len(history)} 筆交易的獲利 (持有 20 天)...")
    
    results = []
    total_ret = 0
    wins = 0
    
    for trade in history:
        ret, exit_price, exit_date = calculate_trade_return(
            trade['entry_date'], 
            trade['stock_id'], 
            trade['entry_price'], 
            df, 
            hold_days=20 # 設定持有 20 天賣出
        )
        
        trade['return'] = ret
        trade['exit_price'] = exit_price
        trade['exit_date'] = exit_date
        results.append(trade)
        
        total_ret += ret
        if ret > 0: wins += 1

    # ==========================================
    # 📊 5. 最終報告
    # ==========================================
    if len(results) > 0:
        avg_ret = total_ret / len(results)
        win_rate = wins / len(results)
        
        print(f"\n" + "="*30)
        print(f"📊 V12 策略績效報告")
        print(f"="*30)
        print(f"🔹 總交易次數: {len(results)} 次")
        print(f"🏆 勝率 (Win Rate): {win_rate:.2%}")
        print(f"💰 平均單筆報酬: {avg_ret:.2%}")
        print(f"📈 累積報酬估算: {(1+avg_ret)**len(results):.2f} 倍 (複利粗估)")
        print(f"🛡️ 避開空頭天數: {skipped_by_market} 天")
        print("-" * 30)
        
        # 儲存詳細結果
        res_df = pd.DataFrame(results)
        res_df.to_csv('backtest_profit_report.csv', index=False)
        print(f"✅ 詳細損益表已儲存為 backtest_profit_report.csv")
    else:
        print("⚠️ 無交易產生。")

if __name__ == "__main__":
    main()