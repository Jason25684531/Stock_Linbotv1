import pandas as pd
import joblib
import os
import matplotlib.pyplot as plt

# ============================================
# ⚙️ 設定區
# ============================================
DATA_PATH = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
MODEL_PATH = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')

# 特徵列表 (備用)
REQUIRED_FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI',
    'MACD_hist', 'KD_K', 'BB_width',
    'PEG', 
    'foreign_ratio', 'trust_ratio', 'trust_ma3'
]

# 🟢 [設定] 避險標的：改成債券 ETF (例如 00679B 元大美債20年)
HEDGE_TARGET_ID = '00679B' 

INITIAL_CAPITAL = 1_000_000 
POSITION_SIZE = 0.2         
HOLD_DAYS = 5               
CONFIDENCE_THRESHOLD = 0.6
PEG_THRESHOLD = 1.2         
STOP_LOSS_PCT = -0.07

# 🟢 [新增] 交易成本設定 (殘酷現實版)
FEE_RATE = 0.001425 * 0.6  # 手續費 6 折 (0.0855%)
TAX_RATE_STOCK = 0.003     # 股票證交稅 0.3%
TAX_RATE_ETF = 0.001       # ETF 證交稅 0.1% (債券ETF適用)

# ============================================
# 🚀 主程式
# ============================================
def main():
    print(f"🚀 Day 4: V23 債券避險回測 (標的: {HEDGE_TARGET_ID}, 含手續費)...")
    
    if not os.path.exists(MODEL_PATH) or not os.path.exists(DATA_PATH):
        print("❌ 找不到模型或資料")
        return
        
    model = joblib.load(MODEL_PATH)
    df = pd.read_csv(DATA_PATH, dtype={'stock_id': str})
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 自動同步特徵順序
    try:
        model_features = model.get_booster().feature_names
    except:
        print("⚠️ 無法自動讀取特徵順序，使用預設列表")
        model_features = REQUIRED_FEATURES

    # 檢查特徵
    missing_features = [f for f in model_features if f not in df.columns]
    if missing_features:
        print(f"❌ 資料庫缺少特徵: {missing_features}")
        return

    # 預測
    print("🔮 AI 正在重新計算買進訊號...")
    df['prob'] = model.predict_proba(df[model_features])[:, 1]
    
    capital = INITIAL_CAPITAL
    positions = [] 
    history = []   
    
    dates = sorted(df['trade_date'].unique())
    test_dates = dates[-90:] 
    
    cooldown_counter = 0 
    
    print(f"⚔️ 開始回測: {test_dates[0].date()} ~ {test_dates[-1].date()}")

    for i, date in enumerate(test_dates):
        
        # --- 1. 計算市場寬度 (Market Breadth) ---
        daily_snapshot = df[df['trade_date'] == date]
        is_bear_market = False
        market_breadth_ratio = 1.0 
        
        if not daily_snapshot.empty and 'MA20' in daily_snapshot.columns:
            stocks_above_ma20 = daily_snapshot[daily_snapshot['close_price'] > daily_snapshot['MA20']]
            market_breadth_ratio = len(stocks_above_ma20) / len(daily_snapshot)
            
            # 門檻：市場 < 30% 股票站上月線 -> 認定為空頭
            if market_breadth_ratio < 0.3:
                is_bear_market = True
        
        # --- 2. 處理庫存 (含避險 ETF 管理) ---
        for pos in positions[:]: 
            stock_id, buy_price, shares, days_held, buy_date = pos
            
            today_data = df[(df['trade_date'] == date) & (df['stock_id'] == stock_id)]
            if today_data.empty: continue
            current_price = today_data.iloc[0]['close_price']
            
            unrealized_pnl = (current_price - buy_price) / buy_price
            
            should_sell = False
            sell_reason = ""
            
            # 🅰️ 債券避險部位 (00679B) 的賣出邏輯
            if stock_id == HEDGE_TARGET_ID:
                # 如果市場已經回穩 (不再是 Bear Market)，就把債券賣掉換現金買股
                if not is_bear_market:
                    should_sell = True
                    sell_reason = "🌤️ 市場回穩 (債券退場)"
                # 債券通常波動小，不需要太嚴格的停損，除非跌太慘
                elif unrealized_pnl <= -0.05: 
                    should_sell = True
                    sell_reason = "🛑 債券停損"
            
            # 🅱️ 一般股票的賣出邏輯
            else:
                is_stop_loss = unrealized_pnl <= STOP_LOSS_PCT
                is_time_up = days_held >= HOLD_DAYS
                is_emergency_exit = is_bear_market 
                
                if is_stop_loss: 
                    should_sell = True; sell_reason = "🛑 停損"
                elif is_time_up: 
                    should_sell = True; sell_reason = "⏰ 時間到"
                elif is_emergency_exit: 
                    should_sell = True; sell_reason = "🚨 市場恐慌逃命"
                    if is_emergency_exit: cooldown_counter = 3
            
            # --- 執行賣出 (含稅費計算) ---
            if should_sell:
                # 稅率判斷：如果是避險標的(ETF)用 0.1%，個股用 0.3%
                tax_rate = TAX_RATE_ETF if stock_id == HEDGE_TARGET_ID else TAX_RATE_STOCK
                
                revenue = shares * current_price
                cost_friction = int(revenue * (FEE_RATE + tax_rate)) # 賣出成本 (手續費+稅)
                
                net_revenue = revenue - cost_friction
                capital += net_revenue
                
                # 計算真實損益 (包含買賣雙邊手續費)
                # 買入時已扣除手續費，所以這裡的 Profit = 淨回收 - 原始股價成本
                # 但更精確的算法是：
                # 總獲利 = (賣價 * 股數 - 賣出成本) - (買價 * 股數 + 買入成本)
                # 這裡為了顯示方便，print 出來的 PnL 還是用「價差」百分比
                
                print(f"{sell_reason} 賣出 {stock_id} @ {current_price} | 帳面損益: {unrealized_pnl:.2%}")
                positions.remove(pos)
                
            else:
                positions.remove(pos)
                positions.append((stock_id, buy_price, shares, days_held + 1, buy_date))

        # --- 3. 買進邏輯 (含債券避險) ---
        if is_bear_market:
            # 🛡️ 避險模式
            has_hedge = any(p[0] == HEDGE_TARGET_ID for p in positions)
            
            if not has_hedge:
                # 嘗試買入債券 ETF
                hedge_target = df[(df['trade_date'] == date) & (df['stock_id'] == HEDGE_TARGET_ID)]
                if not hedge_target.empty:
                    price = hedge_target.iloc[0]['close_price']
                    # 投入 50% 現金停泊在債券
                    invest_amount = capital * 0.5
                    shares = int(invest_amount / price)
                    cost = shares * price
                    cost_friction = int(cost * FEE_RATE) # 買入手續費
                    
                    if capital > (cost + cost_friction) and shares > 0:
                        capital -= (cost + cost_friction)
                        positions.append((HEDGE_TARGET_ID, price, shares, 0, date))
                        print(f"🛡️ {date.date()} 資金避險：買入 {HEDGE_TARGET_ID} ({shares}張)")
                else:
                    # 如果資料庫沒有 00679B，就乖乖空手
                    pass # print(f"⚠️ 找不到 {HEDGE_TARGET_ID} 資料，維持空手")
            
            if cooldown_counter == 0:
                print(f"🐻 {date.date()} 市場恐慌 ({market_breadth_ratio:.0%}) -> 避險/觀望")
            
        elif cooldown_counter > 0:
            print(f"🧊 {date.date()} 冷靜期剩餘 {cooldown_counter} 天 -> 暫停買進")
            cooldown_counter -= 1
            
        else:
            # 正常買進
            # 防禦型濾網 V21
            is_weak_market = market_breadth_ratio < 0.5
            current_peg_limit = 1.0 if is_weak_market else PEG_THRESHOLD
            current_yield_limit = 3.0 if is_weak_market else 0.0
            
            candidates = df[
                (df['trade_date'] == date) & 
                (df['prob'] > CONFIDENCE_THRESHOLD) & 
                (df['PEG'] < current_peg_limit) &
                (df['yield_percent'] > current_yield_limit) &
                (df['pe_ratio'] > 0)
            ].sort_values('prob', ascending=False)
            
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
                cost_friction = int(cost * FEE_RATE) # 買入手續費
                
                # 確認錢夠付
                if shares > 0 and capital >= (cost + cost_friction):
                    capital -= (cost + cost_friction)
                    positions.append((stock_id, price, shares, 0, date))
                    buy_count += 1
        
        # 結算
        current_market_value = 0
        for p in positions:
            sid, buy_px, shs, _, _ = p
            today_row = df[(df['trade_date'] == date) & (df['stock_id'] == sid)]
            curr_px = today_row.iloc[0]['close_price'] if not today_row.empty else buy_px
            current_market_value += shs * curr_px
            
        total_asset = capital + current_market_value
        history.append({'date': date, 'total': total_asset})
        
        if len(positions) > 0 or is_bear_market or cooldown_counter > 0:
             pos_info = [f"{p[0]}({'債' if p[0]==HEDGE_TARGET_ID else '股'})" for p in positions]
             print(f"📅 {date.date()} 資產: {int(total_asset)} 持倉: {pos_info}")

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
        plt.title(f"V23 Bond Hedging Strategy (with Fees)")
        plt.grid(True)
        plt.savefig("backtest_result.png")
    except: pass

if __name__ == "__main__":
    main()