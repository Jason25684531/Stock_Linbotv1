import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
from tqdm import tqdm
import warnings
import os # 記得 import os


warnings.filterwarnings('ignore')

# ==========================================
# 🔧 V11 策略參數 (價值動能混合版)
# ==========================================
PROB_THRESHOLD = 0.60      # AI 信心門檻
STOP_LOSS = -0.07          # 初始停損
TAKE_PROFIT = 0.15         # 停利目標 (會搭配移動鎖利)
HOLD_DAYS = 20             
INITIAL_CAPITAL = 1000000 
POSITION_SIZE = 0.15       

# ✨ V11 基本面濾網參數
FILTER_PE = 20.0     # 本益比 < 20
FILTER_PB = 2.5      # 股價淨值比 < 2.5
FILTER_YIELD = 4.0   # 殖利率 > 4%
FILTER_ROE = 0.08    # ROE > 8%

def main():
    print("🚀 Day 4 (V11): 啟動 基本面濾網 + AI 狙擊 回測...")
    
    try:
        # ✨ 修改讀取路徑
        file_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
        df = pd.read_csv(file_path, dtype={'stock_id': str})
        
        model = joblib.load('stock_ai_model.pkl')
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return

    df = df[df['close_price'] > 0]
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date')
    
    # 切分測試集 (取最後 20%)
    split_idx = int(len(df) * 0.8)
    test_df = df.iloc[split_idx:].copy().reset_index(drop=True)
    
    print(f"📊 回測區間: {test_df['trade_date'].min()} ~ {test_df['trade_date'].max()}")

    # AI 預測特徵 (注意：基本面特徵不用放入模型訓練，只用來過濾)
    # 我們還是用原本的技術面+籌碼面讓 AI 判斷短線爆發力
    features = [
        'MA5', 'MA20', 'MA60', 'RSI', 'MACD', 'BB_width', 'Bias_20', 
        'trust_streak', 'institutions_ratio', 'foreign_5d_sum',
        'slowk', 'KD_diff', 'vol_ratio', 'ATR_pct'
    ]
    
    print("🧠 AI 正在計算信心分數...")
    test_df['prob'] = model.predict_proba(test_df[features])[:, 1]

    capital = INITIAL_CAPITAL
    positions = [] 
    trade_history = [] 
    capital_curve = [INITIAL_CAPITAL] 
    
    blocked_by_market = 0
    blocked_by_fundamental = 0 # 統計被基本面擋掉的數量

    for date, daily_data in tqdm(test_df.groupby('trade_date')):
        
        # --- 1. 大盤多空濾網 ---
        valid_stocks = daily_data[daily_data['close_price'] > 0]
        IS_BEAR_MARKET = False
        
        if len(valid_stocks) > 10:
            stocks_above_ma20 = valid_stocks[valid_stocks['close_price'] > valid_stocks['MA20']]
            market_sentiment = len(stocks_above_ma20) / len(valid_stocks)
            if market_sentiment < 0.25:
                IS_BEAR_MARKET = True

        # --- 2. 持倉管理 (移動鎖利) ---
        remaining_positions = []
        for pos in positions:
            stock_id = pos['stock_id']
            row = daily_data[daily_data['stock_id'] == stock_id]
            
            if not row.empty:
                current_price = row.iloc[0]['close_price']
                return_rate = (current_price - pos['buy_price']) / pos['buy_price']
                
                if return_rate > pos['max_return']:
                    pos['max_return'] = return_rate
                
                pos['days_held'] += 1
                
                # 動態停損邏輯
                dynamic_stop_loss = STOP_LOSS
                if pos['max_return'] >= 0.05: dynamic_stop_loss = 0.01
                if pos['max_return'] >= 0.10: dynamic_stop_loss = 0.05

                action = None
                if return_rate <= dynamic_stop_loss: action = 'Stop/Trailing'
                elif return_rate >= TAKE_PROFIT: action = 'TakeProfit'
                elif pos['days_held'] >= HOLD_DAYS: action = 'TimeStop'
                
                if action:
                    capital += pos['amount'] * current_price
                    trade_history.append({
                        'date': date, 'stock_id': stock_id, 'action': 'SELL',
                        'price': current_price, 'return': return_rate, 'reason': action
                    })
                else:
                    remaining_positions.append(pos)
            else:
                remaining_positions.append(pos)
        positions = remaining_positions

        # --- 3. 進場篩選 (V11 核心) ---
        if IS_BEAR_MARKET:
            blocked_by_market += 1
        else:
            # Step A: 基本面濾網 (Safety Filter)
            # 必須有 PE, PB 資料 且 符合條件
            # 注意: 如果欄位是 NaN (沒抓到資料), 就不會選
            fundamental_pool = daily_data[
                (daily_data['pe_ratio'] > 0) & 
                (daily_data['pe_ratio'] < FILTER_PE) &
                (daily_data['pb_ratio'] < FILTER_PB) &
                (daily_data['yield_percent'] > FILTER_YIELD) &
                (daily_data['implied_roe'] > FILTER_ROE) &
                (daily_data['close_price'] > daily_data['MA60']) # 趨勢向上
            ]
            
            # 統計有多少支是被基本面刷掉的 (為了觀察策略嚴格度)
            raw_ai_picks = daily_data[daily_data['prob'] >= PROB_THRESHOLD]
            qualified_picks = fundamental_pool[fundamental_pool['prob'] >= PROB_THRESHOLD]
            blocked_by_fundamental += (len(raw_ai_picks) - len(qualified_picks))
            
            # Step B: AI 擇時 (AI Trigger)
            candidates = qualified_picks.sort_values('prob', ascending=False)
            
            buy_quota = 3
            for idx, row in candidates.iterrows():
                if buy_quota <= 0: break
                if capital >= INITIAL_CAPITAL * POSITION_SIZE:
                    if not any(p['stock_id'] == row['stock_id'] for p in positions):
                        cost = INITIAL_CAPITAL * POSITION_SIZE
                        amount = cost / row['close_price']
                        capital -= cost
                        positions.append({
                            'stock_id': row['stock_id'], 'buy_price': row['close_price'],
                            'amount': amount, 'days_held': 0, 'max_return': -1.0
                        })
                        trade_history.append({
                            'date': date, 'stock_id': row['stock_id'], 'action': 'BUY',
                            'price': row['close_price'], 'prob': row['prob'], 'return': 0, 'reason': 'V11_Signal'
                        })
                        buy_quota -= 1

        # 每日結算
        market_value = sum(p['amount'] * (daily_data[daily_data['stock_id'] == p['stock_id']].iloc[0]['close_price'] if not daily_data[daily_data['stock_id'] == p['stock_id']].empty else p['buy_price']) for p in positions)
        capital_curve.append(capital + market_value)

    # 報告
    print("\n" + "="*30)
    print("📊 V11 (基本面+AI) 回測報告")
    print("="*30)
    final_return = (capital_curve[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    print(f"總報酬率: {final_return:.2%}")
    print(f"最終資產: {int(capital_curve[-1])}")
    print(f"🛡️ 因基本面不佳被擋下的 AI 訊號次數: {blocked_by_fundamental} 次")
    
    if trade_history:
        trades_df = pd.DataFrame(trade_history)
        sells = trades_df[trades_df['action'] == 'SELL']
        if not sells.empty:
            win_rate = len(sells[sells['return'] > 0]) / len(sells)
            avg_return = sells['return'].mean()
            print(f"交易次數: {len(sells)}")
            print(f"勝率: {win_rate:.2%}")
            print(f"平均單筆報酬: {avg_return:.2%}")
            
            plt.figure(figsize=(10, 6))
            plt.plot(capital_curve, label='V11 Strategy')
            plt.title('V11 (Fundamental + AI) Backtest')
            plt.legend()
            plt.grid(True)
            plt.savefig('backtest_v11.png')
            print("📈 圖表已儲存: backtest_v11.png")

if __name__ == "__main__":
    main()