"""每日選股執行腳本 (V33 Strategy Factory)"""
import sys
import os
import io

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
from sqlalchemy import text
from datetime import datetime

from tool.db_helper import get_db_engine, get_stock_data
from tool.strategy_manager import StrategyManager
from tool.calc_indicators import calculate_ratio_features
from config import Config
import joblib

def main():
    print("\n" + "="*50)
    print("🤖 Stock AI 每日選股執行中...")
    print("="*50 + "\n")
    
    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategy = manager.get_active_strategy()
    print(f"📊 當前策略: {strategy.display_name}")
    print(f"🎯 目標報酬: {strategy.target_return}%")
    print(f"⏰ 持有天數: {strategy.look_ahead_days}天\n")
    
    # 2. 載入資料
    print("📂 載入股市資料...")
    df, date_str = get_stock_data()
    if df.empty:
        print("❌ 無資料可用")
        return
    print(f"✅ 資料日期: {date_str}")
    print(f"✅ 總計 {len(df)} 檔股票\n")
    
    # 3. 計算比率特徵 (V34需要volume_ratio)
    print("🔧 計算比率特徵...")
    df = calculate_ratio_features(df)
    print("✅ 特徵計算完成\n")
    
    # 4. 策略篩選
    print(f"🎲 執行 {strategy.name} 篩選...")
    candidates = strategy.filter_candidates(df)
    print(f"✅ 篩選出 {len(candidates)} 檔候選股票\n")
    
    if candidates.empty:
        print("💤 今日無符合條件的股票")
        return
    
    # 5. AI預測 (選填)
    model = None
    try:
        if os.path.exists(Config.MODEL_PATH):
            model = joblib.load(Config.MODEL_PATH)
            if hasattr(model, 'predict_proba'):
                print("✅ AI模型已載入\n")
            else:
                print("⚠️ 模型格式錯誤，跳過AI預測\n")
                model = None
    except Exception as e:
        print(f"⚠️ AI模型載入失敗: {e}\n")
    
    if model and hasattr(model, 'predict_proba'):
        print("🤖 執行AI預測...")
        features = strategy.features
        for f in features:
            if f not in candidates.columns:
                candidates[f] = 0
        
        X = candidates[features].fillna(0)
        candidates['ai_score'] = model.predict_proba(X)[:, 1]
        candidates = candidates.sort_values('ai_score', ascending=False)
        print("✅ AI評分完成\n")
    
    # 6. 存入資料庫
    print("💾 儲存選股結果...")
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            # 清除舊資料
            conn.execute(text("DELETE FROM daily_recommendations WHERE trade_date = :date"), 
                        {"date": date_str})
            conn.commit()
            
            # 插入新資料
            for _, row in candidates.head(10).iterrows():
                ai_score = row.get('ai_score', None)
                conn.execute(text("""
                    INSERT INTO daily_recommendations 
                    (stock_id, trade_date, strategy, close_price, ai_score, rsi, volume)
                    VALUES (:stock_id, :date, :strategy, :price, :score, :rsi, :volume)
                """), {
                    "stock_id": row['stock_id'],
                    "date": date_str,
                    "strategy": strategy.name,
                    "price": row['close_price'],
                    "score": float(ai_score) if ai_score is not None else None,
                    "rsi": row.get('rsi', None),
                    "volume": row.get('volume', None)
                })
            conn.commit()
            print("✅ 資料已儲存\n")
    except Exception as e:
        print(f"⚠️ 資料庫寫入失敗: {e}\n")
    
    # 7. 顯示結果
    print("="*50)
    print("🎯 今日推薦 (Top 5):")
    print("="*50)
    for i, (_, row) in enumerate(candidates.head(5).iterrows(), 1):
        score_str = f"AI: {row['ai_score']:.2%}" if 'ai_score' in row else "N/A"
        print(f"{i}. {row['stock_id']} (${row['close_price']:.2f}) - {score_str}")
    
    print("\n" + "="*50)
    print("✅ 今日選股作業完成！")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()