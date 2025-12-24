# debug_local.py
import pandas as pd
import joblib
import os
import sys

# 引用工具包
from tool.strategy import calculate_pivot_strategy, format_strategy_message, calculate_position_size
try:
    from tool.news_agent import get_market_briefing
except ImportError:
    pass

# 設定資料路徑
DATA_PATH = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
MODEL_PATH = os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')

print("🧠 正在載入 AI 模型與資料...")

# 1. 載入模型
if os.path.exists(MODEL_PATH):
    model = joblib.load(MODEL_PATH)
    print("✅ 模型載入成功！")
else:
    print(f"⚠️ 找不到模型: {MODEL_PATH}")
    exit()

# 2. 載入資料 (只取最新一天)
if os.path.exists(DATA_PATH):
    full_df = pd.read_csv(DATA_PATH, dtype={'stock_id': str})
    full_df['trade_date'] = pd.to_datetime(full_df['trade_date'])
    last_date = full_df['trade_date'].max()
    daily_data = full_df[full_df['trade_date'] == last_date].copy()
    print(f"✅ 資料載入成功！日期: {last_date.date()}, 共 {len(daily_data)} 筆")
else:
    print(f"⚠️ 找不到資料: {DATA_PATH}")
    exit()

# 特徵列表 (含 PEG)
V12_FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI', 'PEG'
]

print("=========================================")
print("🛠️  V15.5 本地戰術模擬器 (Local Debugger)")
print("=========================================")
print("輸入股票代碼 (例如 2330) 進行分析")
print("輸入 '推薦' 查看 AI 選股")
print("輸入 '新聞' 查看國際戰情")
print("輸入 'q' 離開")
print("-----------------------------------------")

while True:
    user_input = input("\n請輸入指令: ").strip()
    
    if user_input.lower() == 'q':
        print("👋 Bye!")
        break
        
    elif user_input == '新聞':
        try:
            print("\n📰 正在抓取新聞...")
            print(get_market_briefing())
        except:
            print("⚠️ 新聞模組未啟用")
            
    elif user_input == '推薦':
        print("\n🔍 正在掃描全市場 (AI > 50% & PEG < 1.5)...")
        # 預測
        if 'prob' not in daily_data.columns:
            daily_data['prob'] = model.predict_proba(daily_data[V12_FEATURES])[:, 1]
            
        # 篩選
        picks = daily_data[
            (daily_data['prob'] >= 0.50) & 
            (daily_data['PEG'] < 0.8) & 
            (daily_data['pe_ratio'] > 0)
        ].sort_values('prob', ascending=False).head(5)
        
        if picks.empty:
            print("🐢 今日無符合條件之標的。")
        else:
            for _, row in picks.iterrows():
                advice, money = calculate_position_size(row['prob'], row['PEG'])
                print(f"🔥 {row['stock_id']} 信心:{row['prob']:.1%} (PEG:{row['PEG']:.2f}) -> {advice}")

    elif user_input.isdigit():
        stock_id = user_input
        print(f"\n⏳ 正在分析 {stock_id}...")
        
        row = daily_data[daily_data['stock_id'] == stock_id]
        if row.empty:
            print("⚠️ 無此股票資料，或今日未交易。")
            continue
            
        row = row.iloc[0]
        
        # AI 預測
        ai_prob = model.predict_proba(pd.DataFrame([row])[V12_FEATURES])[:, 1][0]
        peg_val = row.get('PEG', 999)
        
        # 戰術計算
        try:
            strat_res = calculate_pivot_strategy(
                high=float(row['high_price']),
                low=float(row['low_price']),
                close=float(row['close_price']),
                ai_prob=ai_prob
            )
            
            # 產生報告
            msg = format_strategy_message(stock_id, row['trade_date'], row['close_price'], ai_prob, strat_res)
            print(msg)
            
            # 顯示資金建議
            advice, money = calculate_position_size(ai_prob, peg_val)
            print(f"💰 資金控管建議: {advice}")
            print(f"   (以十萬本金為例: ${money:,})")
            
        except Exception as e:
            print(f"❌ 發生錯誤: {e}")