"""
多策略 AI 模型批次訓練腳本 (Multi-Model Batch Training)
============================================
為每個啟用的策略訓練獨立的 XGBoost 模型，
各模型使用策略專屬特徵，存檔以策略名稱區分。

輸出範例:
  ML_Data/pkl/stock_ai_model_v33_low_vol.pkl
  ML_Data/pkl/stock_ai_model_v34_turbo.pkl
  ML_Data/pkl/stock_ai_model_v35_innovation.pkl
"""

import pandas as pd
import numpy as np
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, accuracy_score, precision_score
import joblib
import os
from config import Config
from tool.news_agent import NewsSentimentAgent
from tool.db_helper import get_db_engine
from tool.calc_indicators import calculate_ratio_features
from tool.strategy_manager import StrategyManager

# 時間序列拆分參數
TRAIN_RATIO = 0.8        # 前 80% 數據用於訓練

# 模型存放目錄
MODEL_DIR = os.path.dirname(Config.MODEL_PATH) or 'ML_Data/pkl'


def calculate_future_target(df, look_ahead_days, target_return):
    """
    計算未來收益目標（向量化極速版 - 修復 KeyError 與除以零錯誤）
    
    Args:
        df: DataFrame，包含股票資料
        look_ahead_days: 向前看的天數
        target_return: 目標收益率閾值
    
    Returns:
        DataFrame: 添加了 future_max_return 和 target 欄位
    """
    print(f"📊 計算未來 {look_ahead_days} 天最高漲幅 (Vectorized)...")
    
    # 1. 確保價格大於 0 (修復 RuntimeWarning: divide by zero)
    df = df[df['close_price'] > 0].copy()

    # 2. 排序：股票分組，日期【降序】 (為了做反向 rolling)
    df = df.sort_values(['stock_id', 'trade_date'], ascending=[True, False])

    # 3. 計算未來 N 天最高價 (Vectorized)
    # 原理：反向排列後，用 rolling(N) 取最大值，相當於取「未來 N 天」的最大值
    # shift(1) 是為了讓視窗從「明天」開始算，不包含「今天」
    df['future_max_price'] = df.groupby('stock_id')['high_price'].transform(
        lambda x: x.rolling(look_ahead_days, min_periods=1).max().shift(1)
    )

    # 4. 轉回正常順序 (日期升序)
    df = df.sort_values(['stock_id', 'trade_date'], ascending=[True, True])

    # 5. 計算報酬率
    # ret = (future_max - close) / close
    df['future_max_return'] = (df['future_max_price'] - df['close_price']) / df['close_price']

    # 6. 標記 Target (填補 NaN 為 0)
    df['future_max_return'] = df['future_max_return'].fillna(0)
    df['target'] = (df['future_max_return'] > target_return).astype(int)

    # 清理暫存欄位
    df = df.drop(columns=['future_max_price'])
    
    return df


def merge_sentiment_features(df):
    """
    整合市場情緒特徵到訓練數據 (V33 Phase 2+)
    """
    print("📰 整合市場情緒特徵...")
    
    try:
        # 初始化情緒分析代理（使用 Mock Mode）
        sentiment_agent = NewsSentimentAgent(mock_mode=True)
        
        # 取得所有唯一日期
        unique_dates = df['trade_date'].unique()
        sentiment_map = {}
        
        print(f"   正在計算 {len(unique_dates)} 個交易日的情緒分數...")
        
        # 批次計算所有日期的情緒分數
        for date in unique_dates:
            date_str = pd.to_datetime(date).strftime('%Y-%m-%d')
            sentiment_result = sentiment_agent.get_daily_sentiment(date_str)
            sentiment_map[date] = sentiment_result['score']
        
        # 映射到原始數據
        df['sentiment_score'] = df['trade_date'].map(sentiment_map)
        
        # 填充缺失值為 0（中性）
        df['sentiment_score'] = df['sentiment_score'].fillna(0)
        
        print(f"   ✅ 情緒特徵整合完成")
        
    except Exception as e:
        print(f"   ⚠️ 情緒特徵整合失敗: {e}")
        df['sentiment_score'] = 0
    
    return df


def time_series_split(df, train_ratio=0.8):
    """
    時間序列拆分（無數據洩露）
    """
    # 1. 確保按日期嚴格排序
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 2. 找出所有唯一日期並排序
    unique_dates = sorted(df['trade_date'].unique())
    n_dates = len(unique_dates)
    
    # 3. 計算拆分點
    split_idx = int(n_dates * train_ratio)
    train_end_date = unique_dates[split_idx - 1]
    
    # 4. 拆分數據
    train_df = df[df['trade_date'] <= train_end_date]
    test_df = df[df['trade_date'] > train_end_date]
    
    print("\n" + "=" * 60)
    print("🔒 時間序列拆分（防止數據洩露）")
    print("=" * 60)
    print(f"📅 訓練期間: {train_df['trade_date'].min()} ~ {train_df['trade_date'].max()}")
    print(f"📅 測試期間: {test_df['trade_date'].min()} ~ {test_df['trade_date'].max()}")
    print(f"📊 訓練樣本: {len(train_df):,} 筆")
    print(f"📊 測試樣本: {len(test_df):,} 筆")
    print("=" * 60 + "\n")
    
    return train_df, test_df


def get_model_path(strategy_name: str) -> str:
    """取得策略專屬模型檔案路徑
    
    Args:
        strategy_name: 策略名稱，例如 'v33_low_vol'
    
    Returns:
        模型檔案完整路徑
    """
    return os.path.join(MODEL_DIR, f'stock_ai_model_{strategy_name}.pkl')


def load_and_prepare_data(engine) -> pd.DataFrame:
    """載入並準備共用訓練資料（所有策略共用，只需讀取一次）
    
    Args:
        engine: 資料庫引擎
    
    Returns:
        預處理完成的 DataFrame
    """
    print("📥 從資料庫讀取訓練資料...")
    try:
        df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    except Exception as e:
        print(f"❌ 資料庫讀取失敗: {e}")
        return pd.DataFrame()

    if df.empty:
        print("❌ 資料庫是空的！請先跑 1_update_database.py")
        return pd.DataFrame()

    print(f"📦 原始數據: {len(df):,} 筆")
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date']).reset_index(drop=True)
    
    # 補齊缺失的籌碼欄位
    if 'foreign_buy' not in df.columns: df['foreign_buy'] = 0
    if 'trust_buy' not in df.columns: df['trust_buy'] = 0
    
    # 特徵工程（共用）
    df = calculate_ratio_features(df)
    df = merge_sentiment_features(df)
    
    return df


def train_single_strategy(strategy, base_df: pd.DataFrame) -> dict:
    """為單一策略訓練 XGBoost 模型
    
    Args:
        strategy: 策略物件
        base_df: 已完成基礎特徵工程的 DataFrame
    
    Returns:
        訓練結果摘要 dict，失敗時返回含 error 的 dict
    """
    strategy_name = strategy.name
    features = strategy.features + ['sentiment_score']
    look_ahead_days = strategy.look_ahead_days
    target_return = strategy.target_return
    
    print(f"\n{'='*60}")
    print(f"🧠 訓練策略: {strategy.display_name} ({strategy_name})")
    print(f"   預測天數: {look_ahead_days} 天 | 目標報酬: {target_return*100:.1f}%")
    print(f"   特徵數量: {len(features)} 個")
    print(f"{'='*60}")
    
    # 1. 計算此策略專用的目標變數
    df = calculate_future_target(base_df.copy(), look_ahead_days, target_return)
    
    # 清洗：移除無法計算目標的樣本
    data = df.dropna(subset=['target', 'future_max_return'])
    
    print(f"📊 有效樣本數: {len(data):,} 筆")
    print(f"📈 正樣本比例: {data['target'].mean():.2%}")
    
    # 2. 時間序列拆分
    train_df, test_df = time_series_split(data, train_ratio=TRAIN_RATIO)
    
    # 3. 準備特徵（只使用當前策略定義的特徵）
    available_features = [f for f in features if f in data.columns]
    missing_features = [f for f in features if f not in data.columns]
    if missing_features:
        print(f"⚠️ 缺少特徵（已跳過）: {missing_features}")
    
    print(f"📋 使用特徵: {available_features}")
    
    X_train = train_df[available_features].fillna(0)
    y_train = train_df['target']
    X_test = test_df[available_features].fillna(0)
    y_test = test_df['target']
    
    # 4. 訓練 XGBoost
    print("🏋️ XGBoost 訓練中...")
    pos_weight = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    
    model = XGBClassifier(
        n_estimators=300,
        learning_rate=0.03,
        max_depth=5,
        min_child_weight=3,
        subsample=0.7,
        colsample_bytree=0.7,
        scale_pos_weight=pos_weight * 0.5,
        random_state=42,
        n_jobs=-1,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    
    # 5. 評估
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    
    print(f"\n📊 {strategy.display_name} 模型成績單")
    print("-" * 40)
    print(classification_report(y_test, y_pred, zero_division=0))
    print(f"📈 準確率: {acc:.2%} | 🎯 精準率: {prec:.2%}")
    
    # 6. 存檔
    model_path = get_model_path(strategy_name)
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    
    model_data = {
        'model': model,
        'features': available_features,
        'strategy': strategy_name,
        'version': f'{strategy_name}-TimeSeries',
        'training_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'look_ahead_days': look_ahead_days,
        'target_return': target_return,
        'accuracy': acc,
        'precision': prec,
    }
    joblib.dump(model_data, model_path)
    
    print(f"✅ 模型已儲存: {model_path}")
    
    return {
        'strategy': strategy_name,
        'display_name': strategy.display_name,
        'accuracy': acc,
        'precision': prec,
        'features_count': len(available_features),
        'train_samples': len(X_train),
        'test_samples': len(X_test),
        'model_path': model_path,
        'error': None,
    }


def train_all_strategies():
    """
    多策略批次訓練主函數
    逐一為所有啟用策略訓練獨立模型，任一策略失敗不影響其他策略。
    """
    print("\n" + "=" * 60)
    print("🚀 多策略 AI 模型批次訓練引擎")
    print("=" * 60)
    
    # 1. 取得所有啟用策略
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    
    print(f"📊 啟用策略數量: {len(strategies)}")
    for s in strategies:
        print(f"   • {s.display_name} ({s.name})")
    
    # 2. 載入共用資料（只讀一次 DB）
    engine = get_db_engine()
    base_df = load_and_prepare_data(engine)
    if base_df.empty:
        return
    
    # 3. 依序訓練每個策略的模型
    results = []
    for strategy in strategies:
        try:
            result = train_single_strategy(strategy, base_df)
            results.append(result)
        except Exception as e:
            print(f"\n❌ {strategy.name} 訓練失敗: {e}")
            results.append({
                'strategy': strategy.name,
                'display_name': strategy.display_name,
                'error': str(e),
            })
    
    # 4. 列印總結
    print("\n" + "=" * 60)
    print("📊 多策略模型訓練報告")
    print("=" * 60)
    print(f"{'策略':<25} {'準確率':>8} {'精準率':>8} {'特徵數':>6} {'狀態':>6}")
    print("-" * 60)
    
    success_count = 0
    for r in results:
        if r.get('error'):
            print(f"{r['display_name']:<25} {'—':>8} {'—':>8} {'—':>6} {'❌ 失敗':>6}")
        else:
            success_count += 1
            print(f"{r['display_name']:<25} {r['accuracy']:>7.2%} {r['precision']:>7.2%} {r['features_count']:>6} {'✅':>6}")
    
    print("-" * 60)
    print(f"成功: {success_count}/{len(results)} | 模型目錄: {MODEL_DIR}/")
    print("=" * 60)
    print("\n🎉 多策略批次訓練完成！")


if __name__ == "__main__":
    train_all_strategies()