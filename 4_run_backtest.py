import pandas as pd
import joblib
import os
import matplotlib.pyplot as plt

# ============================================
# ⚙️ 設定區
# ============================================
DATA_PATH = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
MODEL_PATH = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')

# 這裡定義列表只是為了檢查「有沒有缺欄位」
# 真正的順序我們會直接問模型 (Dynamic Ordering)
REQUIRED_FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI',
    'MACD_hist', 'KD_K', 'BB_width', # V20 新特徵
    'PEG', 
    'foreign_ratio', 'trust_ratio', 'trust_ma3'
]

INITIAL_CAPITAL = 1_000_000 
POSITION_SIZE = 0.2         
HOLD_DAYS = 5               
CONFIDENCE_THRESHOLD = 0.53 
PEG_THRESHOLD = 1.2         
STOP_LOSS_PCT = -0.07  

# ============================================
# 🚀 主程式
# ============================================
def main():
    print("🚀 Day 4: V20 全能特徵回測 (戰壕防禦版)...")
    
    if not os.path.exists(MODEL_PATH) or not os.path.exists(DATA_PATH):
        print("❌ 找不到模型或資料")
        return
        
    model = joblib.load(MODEL_PATH)
    df = pd.read_csv(DATA_PATH)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 🟢 [關鍵修復] 直接問模型它當初訓練時是用什麼順序
    # 這樣永遠不會再發生 mismatch 錯誤
    try:
        model_features = model.get_booster().feature_names
    except:
        # 如果讀不到(舊版xgboost)，就用我們手動定義的，但要確保順序一致
        print("⚠️ 無法自動讀取特徵順序，使用預設列表")
        model_features = REQUIRED_FEATURES

    # 檢查資料庫有沒有缺欄位
    missing_features = [f for f in model_features if f not in df.columns]
    if missing_features:
        print(f"❌ 資料庫缺少特徵: {missing_features}")
        return

    # 預測 (使用模型指定的順序 model_features)
    print("🔮 AI 正在重新計算買進訊號 (使用正確的特徵順序)...")
    df['prob'] = model.predict_proba(df[model_features])[:, 1]
    
    capital = INITIAL_CAPITAL
    positions = [] 
    history = []   
    
    dates = sorted(df['trade_date'].unique())
    test_dates = dates[-90:] 
    
    cooldown_counter = 0 # 冷靜期計數器
    
    print(f"⚔️ 開始回測: {test_dates[0].date()} ~ {test_dates[-1].date()}")

    for i, date in enumerate(test_dates):
        
        # --- 1. 計算市場寬度 (Market Breadth) ---
        daily_snapshot = df[df['trade_date'] == date]
        is_bear_market = False
        market_breadth_ratio = 1.0 
        
        if not daily_snapshot.empty and 'MA20' in daily_snapshot.columns:
            stocks_above_ma20 = daily_snapshot[daily_snapshot['close_price'] > daily_snapshot['MA20']]
            market_breadth_ratio = len(stocks_above_ma20) / len(daily_snapshot)
            
            # 門檻：市場只有不到 30% 的股票站上月線 -> 認定為空頭
            if market_breadth_ratio < 0.3:
                is_bear_market = True
        
        # --- 2. 處理庫存 (含強制清倉) ---
        for pos in positions[:]: 
            stock_id, buy_price, shares, days_held, buy_date = pos
            
            today_data = df[(df['trade_date'] == date) & (df['stock_id'] == stock_id)]
            if today_data.empty: continue
            current_price = today_data.iloc[0]['close_price']
            
            unrealized_pnl = (current_price - buy_price) / buy_price
            
            is_stop_loss = unrealized_pnl <= STOP_LOSS_PCT
            is_time_up = days_held >= HOLD_DAYS
            is_emergency_exit = is_bear_market 
            
            if is_stop_loss or is_time_up or is_emergency_exit:
                revenue = shares * current_price
                capital += revenue
                profit = revenue - (shares * buy_price)
                positions.remove(pos)
                
                # 若因市場恐慌而賣出，觸發冷靜期 3 天
                if is_emergency_exit:
                    cooldown_counter = 3 
                    print(f"🚨 {date.date()} 市場恐慌! 強制清倉並冷靜 3 天")
            else:
                positions.remove(pos)
                positions.append((stock_id, buy_price, shares, days_held + 1, buy_date))

        # --- 3. 買進邏輯 ---
        if is_bear_market:
            if cooldown_counter == 0:
                print(f"🐻 {date.date()} 市場恐慌 ({market_breadth_ratio:.0%}) -> 觀望")
            pass
            
        elif cooldown_counter > 0:
            print(f"🧊 {date.date()} 冷靜期剩餘 {cooldown_counter} 天 -> 暫停買進")
            cooldown_counter -= 1
            
        else:
            # 正常買進
            candidates = df[
                (df['trade_date'] == date) & 
                (df['prob'] > CONFIDENCE_THRESHOLD) & 
                (df['PEG'] < PEG_THRESHOLD) &
                (df['pe_ratio'] > 0)
            ].sort_values('prob', ascending=False)
            
            # 剛復甦時每天只買 1 檔
            buy_limit = 1 
            buy_count = 0
            
            for _, row in candidates.iterrows():
                if buy_count >= buy_limit: break
                if capital < 10000: break
                
                target_amount = INITIAL_CAPITAL * POSITION_SIZE
                if target_amount > capital: target_amount = capital
                
                stock_id = row['stock_id']
                price = row['close_price']
                if price <= 0: continue
                
                shares = int(target_amount / price)
                cost = shares * price
                
                if shares > 0:
                    capital -= cost
                    positions.append((stock_id, price, shares, 0, date))
                    buy_count += 1
        
        # 結算
        market_value = sum([p[2] * (df[(df['trade_date'] == date) & (df['stock_id'] == p[0])].iloc[0]['close_price'] if not df[(df['trade_date'] == date) & (df['stock_id'] == p[0])].empty else p[1]) for p in positions])
        total_asset = capital + market_value
        history.append({'date': date, 'total': total_asset})
        
        if len(positions) > 0 or is_bear_market or cooldown_counter > 0:
             print(f"📅 {date.date()} 資產: {int(total_asset)} (庫存: {len(positions)})")

    # 輸出
    history_df = pd.DataFrame(history)
    print("\n===========================")
    print(f"💰 初始本金: {INITIAL_CAPITAL}")
    print(f"💰 最終本金: {int(history_df.iloc[-1]['total'])}")
    ret = (history_df.iloc[-1]['total'] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    print(f"📈 總報酬率: {ret:.2%}")
    print("===========================")

    try:
        plt.figure(figsize=(10, 5))
        plt.plot(history_df['date'], history_df['total'])
        plt.title(f"V20 Full Feature Defense")
        plt.grid(True)
        plt.savefig("backtest_result.png")
    except: pass

if __name__ == "__main__":
    main()