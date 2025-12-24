# tool/simple_backtest.py

import pandas as pd
import joblib
import os
import matplotlib.pyplot as plt
import sys

# 🟢 [關鍵修正] 設定路徑
# 1. 抓出這支程式的位置 (D:\...\Stock_Linbotv1\tool)
current_dir = os.path.dirname(os.path.abspath(__file__))

# 2. 抓出專案根目錄 (D:\...\Stock_Linbotv1)
project_root = os.path.dirname(current_dir)

# 3. 將專案根目錄加入系統路徑 (確保可以 import 其他模組)
sys.path.append(project_root)

# ==========================================
# 1. 載入資料與模型 (使用絕對路徑)
# ==========================================
print("⏳ 載入資料中...")

data_path = os.path.join(project_root, 'ML_Data', 'feature_engineering', 'training_data.csv')
model_path = os.path.join(project_root, 'ML_Data', 'pkl', 'stock_ai_model.pkl')

# Debug 用：印出路徑確保正確
print(f"📂 資料路徑: {data_path}")
print(f"🤖 模型路徑: {model_path}")

# 檢查檔案是否存在
if not os.path.exists(data_path):
    print(f"❌ 找不到資料檔: {data_path}")
    exit()
if not os.path.exists(model_path):
    print(f"❌ 找不到模型檔: {model_path}")
    exit()

# 載入 CSV
full_df = pd.read_csv(data_path, dtype={'stock_id': str})
full_df['trade_date'] = pd.to_datetime(full_df['trade_date'])

# 載入模型
try:
    model = joblib.load(model_path)
    print("✅ 模型載入成功！")
except Exception as e:
    print(f"❌ 模型載入失敗: {e}")
    exit()

# 定義特徵 (需與訓練時一致)
V12_FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI', 'PEG'
]

# ==========================================
# 2. 設定回測參數
# ==========================================
BACKTEST_DAYS = 30  # 回測過去 30 個交易日
HOLD_DAYS = 5       # 買進後持有 5 天

# 取得最近的交易日列表
all_dates = sorted(full_df['trade_date'].unique())

# 防呆：如果資料不足 35 天，就用全部資料
if len(all_dates) < (BACKTEST_DAYS + HOLD_DAYS):
    print("⚠️ 資料不足以進行完整回測，將縮短回測天數。")
    test_dates = all_dates[:-HOLD_DAYS]
else:
    test_dates = all_dates[-BACKTEST_DAYS-HOLD_DAYS : -HOLD_DAYS]

capital = 100000 # 初始本金 100 萬
history_capital = []
trade_log = []

print(f"🚀 開始回測 (模擬 {len(test_dates)} 個交易日)...")
    
# ==========================================
# 3. 開始模擬交易
# ==========================================
for date in test_dates:
    # 1. 當天選股
    daily_data = full_df[full_df['trade_date'] == date].copy()
    
    # 確保有需要的欄位
    if 'prob' not in daily_data.columns:
        try:
             daily_data['prob'] = model.predict_proba(daily_data[V12_FEATURES])[:, 1]
        except Exception as e:
            print(f"⚠️ 跳過 {date.date()} (特徵不足): {e}")
            history_capital.append(capital)
            continue

    # 模擬選股邏輯 (AI > 50% 且 PEG < 1.5)
    # 這裡加入防呆，如果沒有 PEG 欄位就不濾 PEG
    if 'PEG' in daily_data.columns:
        picks = daily_data[
            (daily_data['prob'] >= 0.50) & 
            (daily_data['PEG'] < 1.5) & 
            (daily_data['pe_ratio'] > 0)
        ].sort_values('prob', ascending=False).head(5)
    else:
        picks = daily_data[
            (daily_data['prob'] >= 0.50) & 
            (daily_data['pe_ratio'] > 0)
        ].sort_values('prob', ascending=False).head(5)
    
    if picks.empty:
        history_capital.append(capital)
        continue
        
    # 2. 模擬交易：將資金平分買入這 5 檔
    fund_per_stock = capital / len(picks)
    daily_profit = 0
    
    for _, row in picks.iterrows():
        buy_price = row['close_price']
        stock_id = row['stock_id']
        
        # 找 N 天後的賣出價格
        future_data = full_df[
            (full_df['stock_id'] == stock_id) & 
            (full_df['trade_date'] > date)
        ].head(HOLD_DAYS)
        
        if len(future_data) < HOLD_DAYS:
            # 如果還沒過 5 天 (例如是昨天的資料)，假設用最後一天的收盤價平倉
            if not future_data.empty:
                sell_price = future_data.iloc[-1]['close_price']
            else:
                sell_price = buy_price
        else:
            sell_price = future_data.iloc[-1]['close_price']
            
        # 計算獲利
        roi = (sell_price - buy_price) / buy_price
        profit = fund_per_stock * roi
        daily_profit += profit
        
        # 紀錄
        trade_log.append({
            'date': date.date(),
            'stock': stock_id,
            'buy': buy_price,
            'sell': sell_price,
            'roi': f"{roi:.2%}"
        })
    
    # 更新本金
    capital += daily_profit
    history_capital.append(capital)
    
    print(f"📅 {date.date()} 結算: 本金 {int(capital)} (當日損益: {int(daily_profit)})")

# ==========================================
# 4. 輸出結果與畫圖
# ==========================================
print("\n===========================")
print(f"💰 初始本金: 1,000,000")
print(f"💰 最終本金: {int(capital)}")
print(f"📈 總報酬率: {(capital - 1000000)/1000000:.2%}")
print("===========================")

# 存檔路徑也要注意 (存在專案根目錄，不然會跑去 tool 資料夾)
output_img = os.path.join(project_root, "backtest_result.png")

try:
    plt.figure(figsize=(10, 5))
    plt.plot(history_capital, marker='o')
    plt.title("AI Strategy Backtest (Last 30 Days)")
    plt.xlabel("Days")
    plt.ylabel("Capital")
    plt.grid(True)
    plt.savefig(output_img)
    print(f"🖼️  資金曲線圖已存為: {output_img}")
except Exception as e:
    print(f"⚠️ 畫圖失敗: {e}")