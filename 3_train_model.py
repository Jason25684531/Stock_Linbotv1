import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from xgboost import XGBClassifier  # 🟢 XGBoost 登場
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import joblib
import os
from config import Config

# ============================================
# ⚙️ 設定區 (統一使用 Config)
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI
MODEL_PATH = Config.MODEL_PATH
FEATURES = Config.FEATURES

def train_xgboost():
    print("🚀 正在啟動 XGBoost 訓練引擎...")
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
    # 填補空值 (XGBoost 可以處理空值，但填補更保險)
    df = df.fillna(0)

    # 3. 標註答案 (Labeling)
    # 目標：預測明天 (t+1) 收盤價漲幅 > 2%
    df['next_return'] = df.groupby('stock_id')['close_price'].shift(-1) / df['close_price'] - 1
    df['target'] = (df['next_return'] > 0.02).astype(int)
    
    # 清洗：去除沒有明天數據的最後一天
    data = df.dropna(subset=['target'])
    
    print(f"📊 訓練樣本數: {len(data):,} 筆")
    print(f"📈 正樣本比例: {data['target'].mean():.2%}")
    
    # 4. 分割訓練集與測試集
    X = data[FEATURES]
    y = data['target']
    
    # 80% 訓練，20% 測試
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=True
    )
    
    # 5. 訓練 XGBoost (參數調校)
    print("🏋️ XGBoost 正在極限訓練中...")
    
    # scale_pos_weight: 解決「暴漲股很少」的資料不平衡問題
    # 如果正樣本(會漲)只有 10%，這個值設 9 可以讓 AI 更重視會漲的股票
    pos_weight = (len(y_train) - sum(y_train)) / sum(y_train)
    print(f"⚖️ 正樣本權重: {pos_weight:.2f}")
    
    model = XGBClassifier(
        n_estimators=200,       # 樹的數量 (比 RandomForest 多)
        learning_rate=0.05,     # 學習率 (慢工出細活)
        max_depth=6,            # 樹的深度
        subsample=0.8,          # 隨機抽 80% 資料 (避免過擬合)
        colsample_bytree=0.8,   # 隨機抽 80% 特徵 (增加多樣性)
        scale_pos_weight=pos_weight,  # 🟢 關鍵：平衡正負樣本
        random_state=42,
        n_jobs=-1,              # 用盡所有 CPU 核心
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    
    # 6. 驗收成果
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("\n📊 模型成績單 (XGBoost V2.0):")
    print("=" * 50)
    print(classification_report(y_test, y_pred))
    print(f"📈 準確率 (Accuracy): {accuracy_score(y_test, y_pred):.2%}")
    print("=" * 50)
    
    # 7. 特徵重要性
    print("\n🎯 特徵重要性排行:")
    feature_importance = pd.DataFrame({
        'feature': FEATURES,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    for _, row in feature_importance.iterrows():
        print(f"  {row['feature']:<15} {'█' * int(row['importance'] * 100)} {row['importance']:.3f}")
    
    # 8. 存檔
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    
    print(f"\n✅ XGBoost 模型已儲存至: {MODEL_PATH}")
    print("🎉 AI 智商升級完成！")

if __name__ == "__main__":
    train_xgboost()