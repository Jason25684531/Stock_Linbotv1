import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
import sys
from config import Config

# 📚 引用策略模組
from tool.strategy import (
    calculate_pivot_strategy, format_strategy_message, calculate_position_size, 
    calculate_v30_signal, get_v30_params_from_db, get_best_stocks_v31_hybrid
)
# 引用資料庫輔助模組
from tool.db_helper import get_db_engine, get_setting, update_setting, get_stock_data, get_latest_trade_date

# 嘗試載入新聞模組
try:
    from tool.news_agent import get_market_briefing
except ImportError:
    pass

# ============================================
# ⚙️ 設定區（統一使用 Config 和 db_helper）
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
MODEL_PATH = Config.MODEL_PATH
BOND_SYMBOL = Config.BOND_SYMBOL
MARKET_SYMBOL = Config.MARKET_SYMBOL

# ============================================
# 🔍 資料存取與模型
# ============================================
def get_latest_data(stock_id=None):
    engine = get_db_engine()
    if stock_id:
        # 個股：抓最近一次交易日
        sql = f"SELECT * FROM daily_market_data WHERE stock_id = '{stock_id}' ORDER BY trade_date DESC LIMIT 1"
        df = pd.read_sql(sql, engine)
        if df.empty: return pd.DataFrame(), None
        date_str = df['trade_date'].iloc[0].strftime('%Y-%m-%d')
        return df, date_str
    else:
        # 全市場：抓最新一天
        with engine.connect() as conn:
            date_str = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
        if not date_str: return pd.DataFrame(), None
        date_str = date_str.strftime('%Y-%m-%d')
        sql = f"SELECT * FROM daily_market_data WHERE trade_date = '{date_str}'"
        sql += f" AND stock_id NOT IN ('{BOND_SYMBOL}', '{MARKET_SYMBOL}', '00632R')"
        df = pd.read_sql(sql, engine)
        return df, date_str

def load_model():
    """載入 V31 模型（包含模型和特徵列表）"""
    paths = [MODEL_PATH, os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')]
    for p in paths:
        if os.path.exists(p):
            data = joblib.load(p)
            # 簡化版，使用 db_helper
            if isinstance(data, dict) and 'model' in data:
                return data['model'], data.get('features', [])
            else:
                return data, []
    print("❌ 找不到模型檔案")
    return None, []

# ============================================
# 🔥 V30 策略推薦（純技術分析，40% 報酬實績）
# ============================================
def show_v30_recommendations():
    """V30 純技術分析選股（不使用 AI 模型）"""
    print(f"\n🚀 V30 純技術分析選股 (40% 報酬實績)")
    print(f"🎯 目標: 獲利 10-20% | 停損 5% | 持有最長 10 天")
    
    df, date_str = get_latest_data()
    if df.empty: 
        print("❌ 無資料")
        return

    print(f"📅 資料日期: {date_str} (掃描 {len(df)} 檔)")
    
    # 使用統一的 V30 篩選函數
    from tool.strategy import get_v30_candidates
    candidates = get_v30_candidates(df)
    
    if candidates.empty:
        print("🐢 今日無符合 V30 策略條件的股票")
        return
    
    # 依成交量排序取前5名
    picks = candidates.sort_values('volume', ascending=False).head(5)
    
    print("-" * 60)
    print(f"{'代號':<6} {'收盤':<8} {'RSI':<6} {'停損':<10} {'停利':<10} {'外資(張)':<10}")
    print("-" * 60)
    
    for _, row in picks.iterrows():
        v30_result = calculate_v30_signal(row)
        foreign = int(row.get('foreign_buy', 0) / 1000)
        print(f"{row['stock_id']:<6} {row['close_price']:<8.2f} {row['rsi']:<6.1f} {v30_result['stop_loss']:<10.2f} {v30_result['take_profit']:<10.2f} {foreign:,}")
    
    print("-" * 60)
    print(f"⏰ 建議持有: 最長 {V30_PARAMS['MAX_HOLD_DAYS']} 天")
    print(f"💡 提示: 這是純技術分析策略，不使用 AI 模型")


def show_recommendations(model):
    """🔥 V31 混合策略推薦（V30 篩選 + ML 智慧排名）"""
    print(f"\n🧠 V31 混合策略推薦 (V30 篩選 + ML 智慧排名)")
    print(f"🎯 目標: 獲利 10-20% | 停損 5% | 持有最長 10 天")
    
    df, date_str = get_latest_data()
    if df.empty: 
        print("❌ 無資料")
        return

    print(f"📅 資料日期: {date_str} (掃描 {len(df)} 檔)")
    
    # 使用 V31 混合策略選股
    picks = get_best_stocks_v31_hybrid(df, top_n=5)
    
    if picks.empty:
        print("🐢 今日無符合 V31 混合策略條件的股票")
        print("💡 提示: V30 條件 = 均線多頭排列 + 量能 > 300萬 + 40 < RSI < 70")
        return
    
    print(f"✅ V30 篩選通過，ML 排名完成")
    print("-" * 75)
    print(f"{'排名':<4} {'代號':<6} {'收盤':<8} {'RSI':<6} {'AI評分':<10} {'停損':<10} {'停利':<10}")
    print("-" * 75)
    
    for idx, (_, row) in enumerate(picks.iterrows(), 1):
        stop_loss = row['close_price'] * (1 - V30_PARAMS['STOP_LOSS'])
        take_profit = row['close_price'] * (1 + V30_PARAMS['TAKE_PROFIT'])
        ai_score = row.get('ai_score', 0)
        
        # AI 評分視覺化
        score_bar = '█' * int(ai_score * 10)
        
        print(f"{idx:<4} {row['stock_id']:<6} {row['close_price']:<8.2f} {row['rsi']:<6.1f} "
              f"{ai_score:.1%} {score_bar:<5} {stop_loss:<10.2f} {take_profit:<10.2f}")
    
    print("-" * 75)
    print(f"⏰ 建議持有: 最長 {V30_PARAMS['MAX_HOLD_DAYS']} 天")
    print(f"🛡️ 停損: -{int(V30_PARAMS['STOP_LOSS']*100)}% | 🎯 停利: +{int(V30_PARAMS['TAKE_PROFIT']*100)}%")
    print("💡 說明: AI評分 = XGBoost 預測未來 7 天漲 8% 以上的機率")
    print("⚠️ 風險提示: AI 僅供參考，請嚴格執行停損停利規則")

def analyze_stock(stock_id, model, features):
    """分析單一股票 - 使用 V31 模型特徵"""
    print(f"\n⏳ 正在分析 {stock_id}...")
    df, date_str = get_stock_data(stock_id=stock_id)
    if df.empty:
        print(f"⚠️ 無資料。")
        return
    row = df.iloc[0]
    
    df_feat = pd.DataFrame([row])
    for f in features:
        if f not in df_feat.columns: df_feat[f] = 0
    prob = model.predict_proba(df_feat[features].fillna(0))[:, 1][0]

    strat_res = calculate_pivot_strategy(
        high=float(row['high_price']), low=float(row['low_price']),
        close=float(row['close_price']), ai_prob=prob
    )
    msg = format_strategy_message(stock_id, row['trade_date'], row['close_price'], prob, strat_res, extra_data=row)
    print(msg)
    
    # 這裡也要傳入 PEG (如果有的話)
    peg = row.get('pe_ratio', 0) # 暫用 PE 代替
    advice, money = calculate_position_size(prob, peg, capital=100000)
    print(f"💰 資金建議: {advice} (約 ${money:,})")

# ============================================
# 🚀 主程式 (V31 混合策略版)
# ============================================
if __name__ == "__main__":
    print("=========================================")
    print("🛠️  V31 本地戰術模擬器 (混合策略版)")
    print("=========================================")
    
    model, features = load_model()
    if not model: exit()
    print(f"📊 模型特徵: {len(features)} 個")

    print("\n📋 指令說明:")
    print("1. 推薦     → 🔥 V31 混合策略 (V30篩選 + ML排名) ⭐推薦")
    print("2. V30     → 純技術分析選股 (40%報酬實績)")
    print("3. 代碼     → 個股診斷 (如: 2330)")
    print("")
    print("✨ 策略參數:")
    print(f"  • 停損: {int(V30_PARAMS['STOP_LOSS']*100)}% | 停利: {int(V30_PARAMS['TAKE_PROFIT']*100)}% | 持有: {V30_PARAMS['MAX_HOLD_DAYS']}天")
    print("  • 設定停損 5   → 修改停損為5%")
    print("  • 設定停利 20  → 修改停利為20%")
    print("  • 查看設定     → 顯示當前參數")
    print("")
    print("4. 設定信心 → 調整AI門檻 (如: 設定信心 60)")
    print("5. q       → 離開")
    print("-" * 40)
    print("💡 建議: 使用「推薦」指令獲取 V31 混合策略選股")
    
    while True:
        user_input = input("\n請輸入指令: ").strip()
        
        if user_input.lower() == 'q':
            break 
        
        # 🔥 V30 策略選股（40% 報酬實績）
        elif user_input.lower() in ['v30', '策略']:
            show_v30_recommendations()
            
        elif user_input == '推薦':
            show_recommendations(model)
        
        elif user_input.startswith('設定停損'):
            try:
                val = float(user_input.replace("設定停損", "").strip()) / 100
                if 0.01 <= val <= 0.20:
                    update_setting('v30_stop_loss', str(val))
                    print(f"🛡️ V30 停損已設定為 {int(val*100)}%")
                else:
                    print("❌ 停損需在 1%-20% 之間")
            except:
                print("❌ 格式錯誤，請輸入例如：設定停損 5")
        
        elif user_input.startswith('設定停利'):
            try:
                val_str = user_input.replace("設定停利", "").strip()
                if val_str == '0':
                    update_setting('v30_take_profit', '0')
                    print(f"🎯 V30 停利已取消，將持有至停損或到期")
                else:
                    val = float(val_str) / 100
                    if 0.05 <= val <= 0.50:
                        update_setting('v30_take_profit', str(val))
                        print(f"🎯 V30 停利已設定為 {int(val*100)}%")
                    else:
                        print("❌ 停利需在 5%-50% 之間")
            except:
                print("❌ 格式錯誤，例如：設定停利 20 或 設定停利 0")
        
        elif user_input == '查看設定':
            from tool.strategy import get_v30_params_from_db
            params = get_v30_params_from_db()
            ai_conf = float(get_setting('ai_threshold', '0.5'))
            print("\n⚙️ 當前設定:")
            print("-" * 40)
            print(f"🚀 V30 策略:")
            print(f"  🛡️ 停損: {int(params['STOP_LOSS']*100)}%")
            if params['TAKE_PROFIT'] > 0:
                print(f"  🎯 停利: {int(params['TAKE_PROFIT']*100)}%")
            else:
                print(f"  🎯 停利: 不停利（持有至到期）")
            print(f"  ⏰ 最長持有: {params['MAX_HOLD_DAYS']}天")
            print(f"\n🧠 AI 參數:")
            print(f"  AI門檻: {int(ai_conf*100)}%")
            print("-" * 40)
            
        elif user_input.startswith('設定信心'):
            try:
                val = float(user_input.replace("設定信心", "").strip()) / 100
                update_setting('ai_threshold', str(val))
                print(f"🧠 AI 信心門檻已設定為 {int(val*100)}%")
            except:
                print("❌ 格式錯誤，請輸入例如：設定信心 60")

        elif user_input.isdigit():
            analyze_stock(user_input, model, features)
            
        elif user_input == '新聞':
            try: print(get_market_briefing())
            except: pass
            
        else:
            print(f"❌ 看不懂指令: {user_input}")
            print("👉 請輸入: V30 / 推薦 / 股票代號 / 設定停損 / 設定停利")