import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, accuracy_score, precision_score
import joblib
import os
from config import Config

# ============================================
# ⚙️ V31 混合策略版 - 設定區（統一使用 Config）
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
MODEL_PATH = Config.MODEL_PATH
FEATURES = Config.FEATURES

# V31: 預測參數（配合獲利目標 10-20%）
LOOK_AHEAD_DAYS = 7      # 看未來 7 天（配合 10 天持有期）
TARGET_RETURN = 0.08     # 目標漲幅 8%（中間值，提高精準度）

# 時間序列拆分參數
TRAIN_RATIO = 0.8        # 前 80% 數據用於訓練


def calculate_ratio_features(df):
    """
    計算比例特徵（籌碼面標準化）
    
    Args:
        df: DataFrame，包含股票資料
    
    Returns:
        DataFrame: 添加了比例特徵的數據
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
    計算未來收益目標（按股票分組計算）
    
    Args:
        df: DataFrame，包含股票資料
        look_ahead_days: 向前看的天數
        target_return: 目標收益率閾值
    
    Returns:
        DataFrame: 添加了 future_max_return 和 target 欄位
    """
    print(f"📊 計算未來 {look_ahead_days} 天最高漲幅...")
    
    def calc_future_max_return(group):
        """計算單個股票的未來收益"""
        close = group['close_price'].values
        high = group['high_price'].values
        max_returns = []
        
        for i in range(len(close)):
            if i + look_ahead_days >= len(close):
                max_returns.append(np.nan)
            else:
                # 取未來 N 天內的最高價
                future_max = max(high[i+1:i+look_ahead_days+1])
                ret = (future_max - close[i]) / close[i]
                max_returns.append(ret)
        
        group['future_max_return'] = max_returns
        return group
    
    df = df.groupby('stock_id', group_keys=False).apply(calc_future_max_return)
    df['target'] = (df['future_max_return'] > target_return).astype(int)
    
    return df


def time_series_split(df, train_ratio=0.8):
    """
    時間序列拆分（無數據洩露）
    
    🔥 關鍵：按日期順序拆分，前 80% 訓練，後 20% 測試
    不打亂順序，避免未來數據混入訓練集
    
    Args:
        df: DataFrame，必須包含 'trade_date' 欄位
        train_ratio: 訓練集比例（預設 0.8）
    
    Returns:
        (train_df, test_df): 訓練集和測試集
    """
    # 1. 確保按日期嚴格排序
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 2. 找出所有唯一日期並排序
    unique_dates = sorted(df['trade_date'].unique())
    n_dates = len(unique_dates)
    
    # 3. 計算拆分點（按日期數量）
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
    print(f"📊 訓練樣本: {len(train_df):,} 筆 ({len(train_df)/len(df)*100:.1f}%)")
    print(f"📊 測試樣本: {len(test_df):,} 筆 ({len(test_df)/len(df)*100:.1f}%)")
    print(f"✅ 數據無洩露：測試集所有日期都晚於訓練集")
    print("=" * 60 + "\n")
    
    return train_df, test_df

def train_xgboost():
    """
    XGBoost V31 混合策略訓練主函數
    
    改進：
    1. 移除 train_test_split 的 shuffle=True
    2. 實現時間序列拆分（前 80% 訓練，後 20% 測試）
    3. 封裝特徵工程和目標計算邏輯
    4. 保存完整的模型元數據
    """
    print("🚀 正在啟動 XGBoost V31 混合策略訓練引擎...")
    print("🎯 目標：V30 篩選 + ML 智慧排名，獲利 10-20%")
    print("🔒 防止數據洩露：使用時間序列拆分\n")
    
    engine = create_engine(DB_URL)
    
    # ============================================
    # 1. 讀取數據
    # ============================================
    print("📥 從資料庫讀取訓練資料...")
    try:
        df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    except Exception as e:
        print(f"❌ 資料庫讀取失敗: {e}")
        return

    if df.empty:
        print("❌ 資料庫是空的！請先跑 1_update_database.py")
        return

    # ============================================
    # 2. 數據前處理
    # ============================================
    print(f"📦 原始數據: {len(df):,} 筆")
    
    # 確保日期格式正確
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    # 按股票和日期排序（關鍵）
    df = df.sort_values(['stock_id', 'trade_date']).reset_index(drop=True)
    
    # 補齊缺失的籌碼欄位
    if 'foreign_buy' not in df.columns:
        df['foreign_buy'] = 0
    if 'trust_buy' not in df.columns:
        df['trust_buy'] = 0
    
    # ============================================
    # 3. 特徵工程
    # ============================================
    df = calculate_ratio_features(df)
    df = df.fillna(0)
    
    # ============================================
    # 4. 計算目標變量
    # ============================================
    df = calculate_future_target(df, LOOK_AHEAD_DAYS, TARGET_RETURN)
    
    # 清洗：移除無法計算目標的樣本
    data = df.dropna(subset=['target', 'future_max_return'])
    
    print(f"📊 有效樣本數: {len(data):,} 筆")
    print(f"📈 正樣本比例: {data['target'].mean():.2%}")
    print(f"🎯 目標：未來 {LOOK_AHEAD_DAYS} 天內漲幅 > {TARGET_RETURN*100}%")
    
    # ============================================
    # 5. 時間序列拆分（關鍵改進）
    # ============================================
    train_df, test_df = time_series_split(data, train_ratio=TRAIN_RATIO)
    
    # 準備特徵
    available_features = [f for f in FEATURES if f in data.columns]
    print(f"📋 使用特徵: {available_features}\n")
    
    X_train = train_df[available_features]
    y_train = train_df['target']
    X_test = test_df[available_features]
    y_test = test_df['target']
    
    # ============================================
    # 6. 訓練 XGBoost
    # ============================================
    print("🏋️ XGBoost 正在極限訓練中...")
    
    pos_weight = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    print(f"⚖️ 正樣本權重: {pos_weight:.2f}")
    
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
    
    # ============================================
    # 7. 評估模型
    # ============================================
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("\n" + "=" * 60)
    print("📊 模型成績單 (XGBoost V31 - 時間序列驗證)")
    print("=" * 60)
    print(classification_report(y_test, y_pred, zero_division=0))
    print(f"📈 準確率 (Accuracy): {accuracy_score(y_test, y_pred):.2%}")
    print(f"🎯 精準率 (Precision): {precision_score(y_test, y_pred, zero_division=0):.2%}")
    print("=" * 60)
    
    # ============================================
    # 8. 特徵重要性
    # ============================================
    print("\n🎯 特徵重要性排行:")
    feature_importance = pd.DataFrame({
        'feature': available_features,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    for _, row in feature_importance.iterrows():
        print(f"  {row['feature']:<15} {'█' * int(row['importance'] * 100)} {row['importance']:.3f}")
    
    # ============================================
    # 9. 保存模型與元數據
    # ============================================
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    model_data = {
        'model': model,
        'features': available_features,
        'version': 'V31-TimeSeries',
        'training_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'look_ahead_days': LOOK_AHEAD_DAYS,
        'target_return': TARGET_RETURN,
        'train_samples': len(X_train),
        'test_samples': len(X_test),
        'train_period': f"{train_df['trade_date'].min()} ~ {train_df['trade_date'].max()}",
        'test_period': f"{test_df['trade_date'].min()} ~ {test_df['trade_date'].max()}",
        'accuracy': accuracy_score(y_test, y_pred),
        'precision': precision_score(y_test, y_pred, zero_division=0),
        'time_series_split': True  # 標記使用時間序列拆分
    }
    joblib.dump(model_data, MODEL_PATH)
    
    print(f"\n✅ XGBoost V31 模型已儲存至: {MODEL_PATH}")
    print(f"📋 特徵列表: {available_features}")
    print(f"📊 訓練日期: {model_data['training_date']}")
    print(f"🎯 模型指標: 準確率 {model_data['accuracy']:.2%} | 精準率 {model_data['precision']:.2%}")
    print(f"🔒 時間序列拆分: ✅ (無數據洩露)")
    print("\n🎉 V31 混合策略訓練完成！")
    print("\n💡 下一步：")
    print("   1. 執行 debug_local.py 輸入「推薦」測試混合策略")
    print("   2. 執行 4_run_backtest.py 進行回測驗證")
    print("\n⚠️ 重要：推論時必須使用模型儲存的特徵列表，避免維度不一致錯誤")

if __name__ == "__main__":
    train_xgboost()