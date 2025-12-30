import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
import sys

# 📚 引用策略模組
from tool.strategy import calculate_pivot_strategy, format_strategy_message, calculate_position_size

# 嘗試載入新聞模組
try:
    from tool.news_agent import get_market_briefing
except ImportError:
    pass

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
MODEL_PATH = "stock_ai_model.pkl"
BOND_SYMBOL = '00679B'
MARKET_SYMBOL = '0050'

FEATURES = ['rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width', 'volume']

def get_db_engine():
    return create_engine(DB_URL)

# ============================================
# 🧠 設定讀寫功能 (新增!)
# ============================================
def get_setting(key, default_value):
    """從資料庫讀取設定"""
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            val = conn.execute(text(f"SELECT setting_value FROM user_settings WHERE setting_key='{key}'")).scalar()
        return val if val else default_value
    except:
        return default_value

def update_setting(key, value):
    """寫入設定到資料庫"""
    engine = get_db_engine()
    with engine.connect() as conn:
        sql = f"""
        INSERT INTO user_settings (setting_key, setting_value) VALUES ('{key}', '{value}')
        ON DUPLICATE KEY UPDATE setting_value='{value}'
        """
        conn.execute(text(sql))
        conn.commit()
    print(f"✅ 設定已更新: {key} -> {value}")

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
    paths = [MODEL_PATH, os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')]
    for p in paths:
        if os.path.exists(p):
            print(f"✅ 模型載入成功: {p}")
            return joblib.load(p)
    print("❌ 找不到模型！請先跑 3_train_model.py")
    return None

# ============================================
# 📊 核心功能
# ============================================
def show_recommendations(model):
    # 1. 讀取目前的策略設定
    mode = get_setting('mode', 'conservative')
    ai_threshold = float(get_setting('ai_threshold', '0.5'))
    
    print(f"\n🔍 正在執行 V27 AI 選股 ({mode} 模式)...")
    print(f"⚙️ 信心門檻: {int(ai_threshold*100)}%")
    
    df, date_str = get_latest_data()
    if df.empty: return

    print(f"📅 資料日期: {date_str} (掃描 {len(df)} 檔)")
    
    # 🛡️ 防呆檢查：確保技術指標已計算
    required_cols = ['ma20', 'close_price', 'volume', 'rsi']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"❌ 缺少必要欄位: {', '.join(missing_cols)}")
        print("💡 請先執行: python tool/calc_indicators.py")
        return
    
    # 檢查是否全是空值
    if df['ma20'].isnull().all() or df['rsi'].isnull().all():
        print("❌ 技術指標未計算（全是空值）")
        print("💡 請執行: python tool/calc_indicators.py")
        return
    
    # 過濾掉技術指標為空的資料
    df = df.dropna(subset=['ma20', 'rsi', 'close_price', 'volume'])
    
    if df.empty:
        print("❌ 過濾空值後無可用資料")
        return
    
    # 2. 動態濾網
    if mode == 'conservative':
        # 穩健模式：量大 + 站上月線 (嚴格)
        candidates = df[(df['volume'] > 2000000) & (df['close_price'] > df['ma20'])].copy()
    else:
        # 積極模式：量縮一點也可以，不看均線 (搶反彈)
        candidates = df[(df['volume'] > 1000000)].copy()

    if candidates.empty:
        print(f"🐢 ({mode}) 今日無符合濾網股票。")
        return

    # 3. 預測
    for f in FEATURES:
        if f not in candidates.columns: candidates[f] = 0
    X = candidates[FEATURES].fillna(0)
    candidates['prob'] = model.predict_proba(X)[:, 1]
    
    # 4. 根據信心門檻篩選並排序
    picks = candidates[candidates['prob'] >= ai_threshold].sort_values('prob', ascending=False).head(5)
    
    if picks.empty:
        print(f"⚠️ 無股票信心 > {int(ai_threshold*100)}%")
        return

    print("-" * 50)
    print(f"{'代號':<6} {'信心':<6} {'收盤':<8} {'RSI':<6} {'外資':<8}")
    print("-" * 50)
    for _, row in picks.iterrows():
        foreign = int(row.get('foreign_buy', 0))
        print(f"{row['stock_id']:<6} {row['prob']:.1%}   {row['close_price']:<8} {row['rsi']:.1f}   {foreign:,}")
    print("-" * 50)

def analyze_stock(stock_id, model):
    print(f"\n⏳ 正在分析 {stock_id}...")
    df, date_str = get_latest_data(stock_id)
    if df.empty:
        print(f"⚠️ 無資料。")
        return
    row = df.iloc[0]
    
    df_feat = pd.DataFrame([row])
    for f in FEATURES:
        if f not in df_feat.columns: df_feat[f] = 0
    prob = model.predict_proba(df_feat[FEATURES].fillna(0))[:, 1][0]

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
# 🚀 主程式 (支援簡寫指令版)
# ============================================
if __name__ == "__main__":
    print("=========================================")
    print("🛠️  V27 本地戰術模擬器 (動態策略版)")
    print("=========================================")
    
    model = load_model()
    if not model: exit()

    print("\n指令說明:")
    print("1. 推薦 / 代碼 (如 2330)")
    print("2. 積極 / 穩健 (調整濾網)")
    print("3. 設定信心 60 (調整 AI 門檻)")
    print("4. 查看設定")
    print("5. 新聞 / q")
    
    while True:
        user_input = input("\n請輸入指令: ").strip()
        
        if user_input.lower() == 'q':
            break 
            
        elif user_input == '推薦':
            show_recommendations(model)
            
        # 🟢 [修改] 支援 "積極" 或 "切換積極"
        elif user_input in ['切換積極', '積極']:
            update_setting('mode', 'aggressive')
            print("😈 已切換為【積極模式】(放寬濾網)")
            
        # 🟢 [修改] 支援 "穩健" 或 "切換穩健"
        elif user_input in ['切換穩健', '穩健']:
            update_setting('mode', 'conservative')
            print("🛡️ 已切換為【穩健模式】(嚴格濾網)")
            
        elif user_input.startswith('設定信心'):
            try:
                val = float(user_input.replace("設定信心", "").strip()) / 100
                update_setting('ai_threshold', str(val))
                print(f"🧠 AI 信心門檻已設定為 {int(val*100)}%")
            except:
                print("❌ 格式錯誤，請輸入例如：設定信心 60")
                
        elif user_input == '查看設定':
            mode = get_setting('mode', 'conservative')
            conf = float(get_setting('ai_threshold', '0.5'))
            print(f"⚙️ 目前設定: 模式=[{mode}] | AI門檻=[{int(conf*100)}%]")

        elif user_input.isdigit():
            analyze_stock(user_input, model)
            
        elif user_input == '新聞':
            try: print(get_market_briefing())
            except: pass
            
        # 🟢 [新增] 防止輸入錯誤沒反應
        else:
            print(f"❌ 看不懂指令: {user_input}")
            print("👉 請輸入: 推薦, 積極, 穩健, 或股票代號")