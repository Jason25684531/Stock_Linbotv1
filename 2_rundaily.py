"""每日選股執行腳本 (V33 Strategy Factory - Multi-Strategy Support)"""
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


def merge_financial_data(df: pd.DataFrame, engine) -> pd.DataFrame:
    """
    合併財報數據到日線數據
    
    財報是季度數據（稀疏），日線是每日數據（密集）
    使用 forward fill 方法：每檔股票使用最新的季報數據
    
    Args:
        df: 日線資料 DataFrame
        engine: 資料庫引擎
    
    Returns:
        合併後的 DataFrame (新增 rd_ratio, eps 欄位)
    """
    try:
        # 從資料庫讀取最新的財報數據（每檔股票取最新一季）
        query = text("""
            SELECT 
                fs1.stock_id,
                fs1.year,
                fs1.quarter,
                fs1.revenue,
                fs1.rd_expense,
                fs1.eps,
                CASE 
                    WHEN fs1.revenue > 0 THEN fs1.rd_expense / fs1.revenue 
                    ELSE 0 
                END as rd_ratio
            FROM financial_statements fs1
            INNER JOIN (
                SELECT stock_id, MAX(year * 10 + quarter) as max_period
                FROM financial_statements
                GROUP BY stock_id
            ) fs2 ON fs1.stock_id = fs2.stock_id 
                 AND (fs1.year * 10 + fs1.quarter) = fs2.max_period
        """)
        
        with engine.connect() as conn:
            financial_df = pd.read_sql(query, conn)
        
        if financial_df.empty:
            print("  ⚠️ 資料庫中無財報數據，使用預設值 (rd_ratio=0, eps=0)")
            df['rd_ratio'] = 0.0
            df['eps'] = 0.0
            return df
        
        print(f"  ✓ 從資料庫載入 {len(financial_df)} 檔股票的財報數據")
        
        # 合併數據（left join，保留所有日線數據）
        df = df.merge(
            financial_df[['stock_id', 'rd_ratio', 'eps', 'year', 'quarter']],
            on='stock_id',
            how='left'
        )
        
        # 填補缺失值（無財報數據的股票）
        df['rd_ratio'] = df['rd_ratio'].fillna(0.0)
        df['eps'] = df['eps'].fillna(0.0)
        
        # 統計
        has_rd = (df['rd_ratio'] > 0).sum()
        has_eps = (df['eps'] != 0).sum()
        print(f"  ✓ 有研發費用：{has_rd} 檔 ({has_rd/len(df)*100:.1f}%)")
        print(f"  ✓ 有 EPS 數據：{has_eps} 檔 ({has_eps/len(df)*100:.1f}%)")
        
        return df
        
    except Exception as e:
        print(f"  ❌ 合併財報數據失敗: {e}")
        # 失敗時填補預設值
        df['rd_ratio'] = 0.0
        df['eps'] = 0.0
        return df


def run_strategy(strategy, df, date_str, model, engine):
    """執行單一策略的選股流程
    
    Args:
        strategy: 策略物件
        df: 股市資料 DataFrame
        date_str: 日期字串
        model: AI 模型
        engine: 資料庫引擎
    
    Returns:
        candidates: 選股結果 DataFrame
    """
    print(f"\n{'='*40}")
    print(f"📊 執行策略: {strategy.display_name} ({strategy.name})")
    print(f"🎯 目標報酬: {strategy.target_return}%")
    print(f"⏰ 持有天數: {strategy.look_ahead_days}天")
    print(f"{'='*40}")
    
    # 策略篩選
    candidates = strategy.filter_candidates(df.copy())
    print(f"✅ 篩選出 {len(candidates)} 檔候選股票")
    
    if candidates.empty:
        print(f"💤 {strategy.name}: 今日無符合條件的股票")
        return candidates
    
    # AI 預測
    if model and hasattr(model, 'predict_proba'):
        features = strategy.features
        for f in features:
            if f not in candidates.columns:
                candidates[f] = 0
        
        X = candidates[features].fillna(0)
        candidates['ai_score'] = model.predict_proba(X)[:, 1]
        candidates = candidates.sort_values('ai_score', ascending=False)
        print(f"✅ AI 評分完成")
    
    # 存入資料庫
    try:
        with engine.connect() as conn:
            # 清除此策略的舊資料
            conn.execute(text("""
                DELETE FROM daily_recommendations 
                WHERE trade_date = :date AND strategy = :strategy
            """), {"date": date_str, "strategy": strategy.name})
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
            print(f"✅ {strategy.name}: 資料已儲存")
    except Exception as e:
        print(f"⚠️ {strategy.name}: 資料庫寫入失敗: {e}")
    
    # 顯示結果
    print(f"\n🎯 {strategy.display_name} 推薦 (Top 5):")
    for i, (_, row) in enumerate(candidates.head(5).iterrows(), 1):
        score_str = f"AI: {row['ai_score']:.2%}" if 'ai_score' in row else "N/A"
        print(f"  {i}. {row['stock_id']} (${row['close_price']:.2f}) - {score_str}")
    
    return candidates


def main():
    print("\n" + "="*50)
    print("🤖 Stock AI 每日選股執行中...")
    print("="*50 + "\n")
    
    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    strategy_names = manager.get_active_strategy_names()
    
    print(f"📊 啟用策略數量: {len(strategies)}")
    print(f"📋 策略列表: {', '.join(strategy_names)}\n")
    
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
    
    # 🧪 V35: 合併財報數據（季報：EPS、研發費用）
    print("🧪 合併財報數據（V35 需要）...")
    df = merge_financial_data(df, engine)
    print("✅ 財報數據合併完成\n")
    
    # 4. 載入 AI 模型
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
    
    # 5. 取得資料庫引擎
    engine = get_db_engine()
    
    # 6. 遍歷所有策略執行選股
    all_results = {}
    for strategy in strategies:
        candidates = run_strategy(strategy, df, date_str, model, engine)
        all_results[strategy.name] = candidates
    
    # 7. 總結
    print("\n" + "="*50)
    print("📊 選股結果總覽:")
    print("="*50)
    for name, candidates in all_results.items():
        count = len(candidates) if not candidates.empty else 0
        print(f"  • {name}: {count} 檔候選股票")
    
    print("\n" + "="*50)
    print("✅ 今日選股作業完成！")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()