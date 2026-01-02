import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from xgboost import XGBClassifier  # 🟢 XGBoost 登場
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, precision_score
import joblib
import os
from config import Config

# ============================================
# ⚙️ V31 混合策略版 - 設定區（統一使用 Config）
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
MODEL_PATH = Config.MODEL_PATH
FEATURES = Config.FEATURES  # V31: 使用 Config 統一定義

# V31: 預測參數（配合獲利目標 10-20%）
LOOK_AHEAD_DAYS = 7      # 看未來 7 天（配合 10 天持有期）
TARGET_RETURN = 0.08     # 目標漲幅 8%（中間值，提高精準度）

def train_xgboost():
    print("🚀 正在啟動 XGBoost V31 混合策略訓練引擎...")
    print("🎯 目標：V30 篩選 + ML 智慧排名，獲利 10-20%")
    engine = create_engine(DB_URL)
    
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

    # 2. 數據前處理
    df = df.sort_values(['stock_id', 'trade_date'])
    
    # 補齊缺失的籌碼欄位
    if 'foreign_buy' not in df.columns:
        df['foreign_buy'] = 0
    if 'trust_buy' not in df.columns:
        df['trust_buy'] = 0
    
    # ============================================
    # 🆕 V31: 計算比例特徵（關鍵改進）
    # ============================================
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
    
    df = df.fillna(0)

    # 3. V28 標註答案：未來 N 天內最高點漲幅 > TARGET_RETURN
    print(f"📊 計算未來 {LOOK_AHEAD_DAYS} 天最高漲幅...")
    
    def calc_future_max_return(group):
        """計算未來 N 天的最高收益"""
        close = group['close_price'].values
        high = group['high_price'].values
        max_returns = []
        
        for i in range(len(close)):
            if i + LOOK_AHEAD_DAYS >= len(close):
                max_returns.append(np.nan)
            else:
                # 取未來 N 天內的最高價
                future_max = max(high[i+1:i+LOOK_AHEAD_DAYS+1])
                ret = (future_max - close[i]) / close[i]
                max_returns.append(ret)
        
        group['future_max_return'] = max_returns
        return group
    
    df = df.groupby('stock_id', group_keys=False).apply(calc_future_max_return)
    df['target'] = (df['future_max_return'] > TARGET_RETURN).astype(int)
    
    # 清洗
    data = df.dropna(subset=['target', 'future_max_return'])
    
    print(f"📊 訓練樣本數: {len(data):,} 筆")
    print(f"📈 正樣本比例: {data['target'].mean():.2%}")
    print(f"🎯 目標：未來 {LOOK_AHEAD_DAYS} 天內漲幅 > {TARGET_RETURN*100}%")
    
    # 4. 準備特徵
    available_features = [f for f in FEATURES if f in data.columns]
    print(f"📋 使用特徵: {available_features}")
    
    X = data[available_features]
    y = data['target']
    
    # 80% 訓練，20% 測試
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=True
    )
    
    # 5. 訓練 XGBoost (優化參數)
    print("🏋️ XGBoost 正在極限訓練中...")
    
    pos_weight = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    print(f"⚖️ 正樣本權重: {pos_weight:.2f}")
    
    model = XGBClassifier(
        n_estimators=300,       # 增加樹的數量
        learning_rate=0.03,     # 降低學習率 (更穩定)
        max_depth=5,            # 降低深度 (避免過擬合)
        min_child_weight=3,     # 增加子節點最小權重
        subsample=0.7,          
        colsample_bytree=0.7,   
        scale_pos_weight=pos_weight * 0.5,  # 降低權重 (提高 precision)
        random_state=42,
        n_jobs=-1,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    
    # 6. 驗收成果
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("\n📊 模型成績單 (XGBoost V31 混合策略):")
    print("=" * 50)
    print(classification_report(y_test, y_pred))
    print(f"📈 準確率 (Accuracy): {accuracy_score(y_test, y_pred):.2%}")
    print(f"🎯 精準率 (Precision): {precision_score(y_test, y_pred):.2%}")
    print("=" * 50)
    
    # 7. 特徵重要性
    print("\n🎯 特徵重要性排行:")
    feature_importance = pd.DataFrame({
        'feature': available_features,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    for _, row in feature_importance.iterrows():
        print(f"  {row['feature']:<15} {'█' * int(row['importance'] * 100)} {row['importance']:.3f}")
    
    # 8. 存檔（包含完整元數據，確保推論時特徵順序一致）
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    # 🔥 關鍵：儲存完整的模型元數據
    model_data = {
        'model': model,
        'features': available_features,  # 特徵列表（順序關鍵）
        'version': 'V31',
        'training_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'look_ahead_days': LOOK_AHEAD_DAYS,
        'target_return': TARGET_RETURN,
        'train_samples': len(X_train),
        'test_samples': len(X_test),
        'accuracy': accuracy_score(y_test, y_pred),
        'precision': precision_score(y_test, y_pred)
    }
    joblib.dump(model_data, MODEL_PATH)
    
    print(f"\n✅ XGBoost V31 模型已儲存至: {MODEL_PATH}")
    print(f"📋 特徵列表 (順序很重要): {available_features}")
    print(f"📊 訓練日期: {model_data['training_date']}")
    print(f"🎯 模型指標: 準確率 {model_data['accuracy']:.2%} | 精準率 {model_data['precision']:.2%}")
    print("🎉 V31 混合策略訓練完成！")
    print("\n💡 下一步：執行 debug_local.py 輸入「推薦」測試混合策略")
    print("⚠️ 重要：推論時必須使用模型儲存的特徵列表，避免維度不一致錯誤")

if __name__ == "__main__":
    train_xgboost()