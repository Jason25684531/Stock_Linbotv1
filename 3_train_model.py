import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, accuracy_score, precision_score
import joblib
import os
from config import Config
from tool.news_agent import NewsSentimentAgent
from tool.db_helper import get_db_engine

# ============================================
# ⚙️ V31 混合策略版 - 設定區（統一使用 Config）
# ============================================

# V31: 預測參數（配合獲利目標 10-20%）
LOOK_AHEAD_DAYS = 7      # 看未來 7 天（配合 10 天持有期）
TARGET_RETURN = 0.08     # 目標漲幅 8%（中間值，提高精準度）

# 時間序列拆分參數
TRAIN_RATIO = 0.8        # 前 80% 數據用於訓練

# V33 Phase 2+: 擴展特徵清單（加入情緒分數）
FEATURES = Config.FEATURES + ['sentiment_score']


def calculate_ratio_features(df):
    """
    計算比例特徵（籌碼面標準化）
    """
    print("📊 計算比例特徵（籌碼面標準化）...")
    
    # 避免除以零
    df['volume'] = df['volume'].replace(0, 1)
    
    # 計算成交量相對於 20 日均量的比例（量能強度）
    df['volume_ma20'] = df.groupby('stock_id')['volume'].transform(
        lambda x: x.rolling(20, min_periods=1).mean()
    )
    df['volume_ratio'] = df['volume'] / df['volume_ma20'].replace(0, 1)
    
    # 籌碼面比例（外資/投信 參與度）
    df['foreign_ratio'] = df['foreign_buy'] / df['volume']
    df['trust_ratio'] = df['trust_buy'] / df['volume']
    
    # 限制極端值（避免異常數據影響模型）
    df['foreign_ratio'] = df['foreign_ratio'].clip(-0.5, 0.5)
    df['trust_ratio'] = df['trust_ratio'].clip(-0.5, 0.5)
    df['volume_ratio'] = df['volume_ratio'].clip(0, 5)
    
    return df


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


def train_xgboost():
    """
    XGBoost V31 混合策略訓練主函數
    """
    print("🚀 正在啟動 XGBoost V31 混合策略訓練引擎...")
    
    engine = get_db_engine()
    
    # 1. 讀取數據
    print("📥 從資料庫讀取訓練資料...")
    try:
        df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    except Exception as e:
        print(f"❌ 資料庫讀取失敗: {e}")
        return

    if df.empty:
        print("❌ 資料庫是空的！請先跑 1_update_database.py")
        return

    print(f"📦 原始數據: {len(df):,} 筆")
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date']).reset_index(drop=True)
    
    # 補齊缺失的籌碼欄位
    if 'foreign_buy' not in df.columns: df['foreign_buy'] = 0
    if 'trust_buy' not in df.columns: df['trust_buy'] = 0
    
    # 2. 特徵工程
    df = calculate_ratio_features(df)
    df = merge_sentiment_features(df)
    
    # 3. 計算未來收益目標 (✅ 這裡使用了修復後的函數)
    df = calculate_future_target(df, LOOK_AHEAD_DAYS, TARGET_RETURN)
    
    # 清洗：移除無法計算目標的樣本
    data = df.dropna(subset=['target', 'future_max_return'])
    
    print(f"📊 有效樣本數: {len(data):,} 筆")
    print(f"📈 正樣本比例: {data['target'].mean():.2%}")
    
    # 4. 時間序列拆分
    train_df, test_df = time_series_split(data, train_ratio=TRAIN_RATIO)
    
    # 準備特徵
    available_features = [f for f in FEATURES if f in data.columns]
    print(f"📋 使用特徵: {available_features}\n")
    
    X_train = train_df[available_features]
    y_train = train_df['target']
    X_test = test_df[available_features]
    y_test = test_df['target']
    
    # 5. 訓練 XGBoost
    print("🏋️ XGBoost 正在極限訓練中...")
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
    
    # 6. 評估模型
    y_pred = model.predict(X_test)
    
    print("\n" + "=" * 60)
    print("📊 模型成績單 (XGBoost V31 - 時間序列驗證)")
    print("=" * 60)
    print(classification_report(y_test, y_pred, zero_division=0))
    print(f"📈 準確率 (Accuracy): {accuracy_score(y_test, y_pred):.2%}")
    print(f"🎯 精準率 (Precision): {precision_score(y_test, y_pred, zero_division=0):.2%}")
    print("=" * 60)
    
    # 7. 保存模型
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    model_data = {
        'model': model,
        'features': available_features,
        'version': 'V31-TimeSeries',
        'training_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'look_ahead_days': LOOK_AHEAD_DAYS,
        'target_return': TARGET_RETURN,
        'accuracy': accuracy_score(y_test, y_pred),
    }
    joblib.dump(model_data, MODEL_PATH)
    
    print(f"\n✅ 模型已儲存至: {MODEL_PATH}")
    print("\n🎉 V31 混合策略訓練完成！")

if __name__ == "__main__":
    train_xgboost()