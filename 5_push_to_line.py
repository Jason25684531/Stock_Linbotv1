import os
import datetime
import pandas as pd
import numpy as np
import joblib
from sqlalchemy import create_engine
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
from crawler.twse_fetcher import TwseFetcher

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

# ==========================================
# 🔧 設定區
# ==========================================
TEST_MODE = True       # 🟢 True: 測試模式 (只印不發 Line) | 🔴 False: 正式發送
AI_THRESHOLD = 0.50    # 信心門檻

# ==========================================
# 🚀 程式主體
# ==========================================

def prepare_all_history_data(engine):
    print("⏳ 載入歷史資料...")
    start_date = (datetime.datetime.now() - datetime.timedelta(days=200)).strftime('%Y-%m-%d')
    query = f"""
    SELECT stock_id, trade_date, open_price, high_price, low_price, close_price, volume, pe_ratio, pb_ratio, yield_percent
    FROM daily_market_data 
    WHERE trade_date >= '{start_date}'
    ORDER BY trade_date ASC
    """
    try:
        df = pd.read_sql(query, engine)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df['stock_id'] = df['stock_id'].astype(str).str.strip()
        cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio', 'pb_ratio', 'yield_percent']
        for col in cols: df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.fillna(0)
        print(f"✅ 載入 {len(df)} 筆歷史資料。")
        return df
    except Exception as e:
        print(f"❌ 歷史載入失敗: {e}")
        return pd.DataFrame()

def calculate_features_in_memory(stock_id, current_row, all_history_df):
    str_stock_id = str(stock_id).strip()
    history_df = all_history_df[all_history_df['stock_id'] == str_stock_id].copy()
    
    current_df = pd.DataFrame([current_row])
    current_df['trade_date'] = pd.to_datetime(current_df['trade_date'])
    current_df['stock_id'] = current_df['stock_id'].astype(str).str.strip()

    # 欄位翻譯機
    rename_map = {
        'open': 'open_price', 'high': 'high_price', 'low': 'low_price', 'close': 'close_price', 'volume': 'volume',
        'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'volume',
        'OpeningPrice': 'open_price', 'HighestPrice': 'high_price', 'LowestPrice': 'low_price', 'ClosingPrice': 'close_price', 'TradeVolume': 'volume'
    }
    current_df = current_df.rename(columns=rename_map)

    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
    for col in cols:
        if col in current_df.columns: current_df[col] = pd.to_numeric(current_df[col], errors='coerce')

    try:
        full_df = pd.concat([history_df, current_df], ignore_index=True)
        full_df = full_df.drop_duplicates(subset=['trade_date'], keep='last')
        full_df = full_df.sort_values('trade_date')
    except: return None

    if len(full_df) < 60: return None

    full_df['implied_roe'] = full_df.apply(lambda row: (row['pb_ratio'] / row['pe_ratio']) if row['pe_ratio'] > 0 else 0, axis=1)
    close = full_df['close_price']
    full_df['MA5'] = close.rolling(window=5).mean()
    full_df['MA20'] = close.rolling(window=20).mean()
    full_df['MA60'] = close.rolling(window=60).mean()
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    full_df['RSI'] = 100 - (100 / (1 + rs))
    
    today_features = full_df.iloc[-1:].copy()
    if pd.isna(today_features['MA60'].values[0]): return None
        
    feature_cols = [
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
        'MA5', 'MA20', 'MA60', 'RSI'
    ]
    missing = [c for c in feature_cols if c not in today_features.columns]
    if missing: return None

    return today_features[feature_cols]

def main():
    print(f"🚀 V13.9 Line 推播 (潔淨美化版) 啟動...")
    try:
        if not TEST_MODE: line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        fetcher = TwseFetcher()
        model = joblib.load(os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl'))
        print("✅ 模型載入成功")
    except Exception as e:
        print(f"❌ 初始化失敗: {e}"); return

    target_date = datetime.datetime.now().strftime('%Y%m%d')
    # target_date = '20251216' 
    print(f"📅 目標日期: {target_date}")
    
    daily_df = fetcher.fetch_daily_data(target_date)
    if daily_df is None or daily_df.empty: return
    daily_df['stock_id'] = daily_df['stock_id'].astype(str).str.strip()

    all_history_df = prepare_all_history_data(engine)
    if all_history_df.empty: return

    print(f"📊 開始 AI 運算 (共 {len(daily_df)} 檔)...")
    candidates = []
    
    for index, row in daily_df.iterrows():
        stock_id = row['stock_id']
        try:
            # 排除 DR 股
            if stock_id.startswith('91'): continue
            
            # 基本面濾網 (PE 允許是 nan/0，讓 ETF 通過)
            pe = float(row['pe_ratio']) if row['pe_ratio'] else 0
            vol = float(row['volume'])
            
            # 濾掉沒量 (<500張) 或 PE 太高 (>30，但 0 可以)
            if vol < 500: continue
            if pe > 30: continue
            
            features = calculate_features_in_memory(stock_id, row, all_history_df)
            if features is None: continue
            
            # AI 預測
            prob = model.predict_proba(features)[:, 1][0]
            
            # 🔇 這裡把 print 拿掉了，還你清靜
            
            if prob >= AI_THRESHOLD:
                final_price = features['close_price'].values[0]
                candidates.append({
                    'id': stock_id, 'name': row['stock_name'], 
                    'prob': prob, 'price': final_price,
                    'roe': features['implied_roe'].values[0],
                    'pe': pe
                })

        except Exception as e:
            continue

    if candidates:
        msg = f"🔥 【V12 價值動能選股】\n命中: {len(candidates)}檔\n" + "-"*20 + "\n"
        candidates.sort(key=lambda x: x['prob'], reverse=True)
        
        # 只取前 5 名
        for stock in candidates[:5]:
            # 美化顯示: 如果 PE 是 0，顯示 "ETF/無"
            pe_str = f"{stock['pe']}" if stock['pe'] > 0 else "ETF/無"
            
            msg += f"🎫 {stock['id']} {stock['name']}\n"
            msg += f"💲 {stock['price']} | 🧠 {stock['prob']:.1%}\n"
            msg += f"💎 PE: {pe_str} | ROE: {stock['roe']:.1%}\n"
            msg += "-"*20 + "\n"
        
        print("\n" + "="*20); print(msg); print("="*20)
        
        if not TEST_MODE:
            line_bot_api.broadcast(TextSendMessage(text=msg))
            print("✅ Line 推播已發送！")
    else:
        print("💤 今日無符合標準的股票。")

if __name__ == "__main__":
    main()
    