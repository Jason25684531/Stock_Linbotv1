import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
import joblib
import matplotlib.pyplot as plt
import warnings
from sklearn.model_selection import GridSearchCV
import os

# 忽略警告
warnings.filterwarnings('ignore')

def main():
    print("🚀 Day 3: 開始訓練 AI 模型 (Phase 2 優化版)...")

    # 1. 讀取 Day 2 產生的資料
    print("📂 讀取 training_data.csv ...")
    try:
        # ✨ 修改讀取路徑
        file_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
        df = pd.read_csv(file_path, dtype={'stock_id': str})
    except FileNotFoundError:
        print(f"❌ 找不到 {file_path}，請先執行 Day 2。")
        return

    # [資料清洗] 處理無限大 (inf) 的數值
    print("🧹 正在清洗異常數值 (inf)...")
    original_len = len(df)
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.dropna(inplace=True)
    
    if len(df) != original_len:
        print(f"⚠️ 已移除 {original_len - len(df)} 筆異常資料")

    # 2. 定義特徵 (X) 與 目標 (y)
    features = [
        'MA5', 'MA20', 'MA60', 'RSI', 'MACD', 'BB_width', 'Bias_20', 
        'trust_streak', 'institutions_ratio', 'foreign_5d_sum',
        'slowk', 'KD_diff', 'vol_ratio', 'ATR_pct'
    ]
    target = 'Target'

    # 檢查欄位是否存在
    missing_cols = [col for col in features if col not in df.columns]
    if missing_cols:
        print(f"❌ 缺少特徵欄位: {missing_cols}")
        return

    # 3. 切分訓練集與測試集 (Time Series Split)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date')
    
    # 取前 80% 當教材 (Train)，後 20% 當考卷 (Test)
    split_index = int(len(df) * 0.8)
    
    train_df = df.iloc[:split_index]
    test_df = df.iloc[split_index:]
    
    X_train = train_df[features]
    y_train = train_df[target]
    X_test = test_df[features]
    y_test = test_df[target]
    
    print(f"📊 訓練集數量: {len(X_train)} (過去)")
    print(f"📊 測試集數量: {len(X_test)} (未來)")

    # [關鍵優化] 計算正負樣本比例，解決 AI 偷懶不買的問題
    num_neg = (y_train == 0).sum()
    num_pos = (y_train == 1).sum()
    scale_pos_weight = num_neg / num_pos if num_pos > 0 else 1
    print(f"⚖️ 正負樣本比例: 1:{scale_pos_weight:.2f} (已設定 scale_pos_weight)")

    # 4. 參數最佳化 (Grid Search)
    print("🧠 AI 正在進行參數最佳化 (Grid Search)... 這可能需要幾分鐘...")
    
    # 參數池
    param_grid = {
        'max_depth': [5, 7],                 # 深度
        'learning_rate': [0.05, 0.1],        # 學習率
        'n_estimators': [100, 200],          # 樹的數量
        'subsample': [0.8],                  # 隨機抽樣
        'scale_pos_weight': [scale_pos_weight] # 強制使用平衡權重
    }

    # 基礎模型
    base_model = xgb.XGBClassifier(
        random_state=42,
        tree_method='hist',
        n_jobs=1 # 避免與 GridSearch 衝突
    )

    grid_search = GridSearchCV(
        estimator=base_model,
        param_grid=param_grid,
        cv=3,
        scoring='precision', # 我們最在乎「準確率」
        n_jobs=-1,
        verbose=1
    )
    
    try:
        grid_search.fit(X_train, y_train)
        print("✅ 最佳化訓練完成！")
        print(f"👑 冠軍參數: {grid_search.best_params_}")
        
        # 取得最佳模型
        model = grid_search.best_estimator_
        
    except Exception as e:
        print(f"❌ 訓練失敗: {e}")
        return

    # 5. 模型評估
    print("\n" + "="*30)
    print("🧐 最終模型考試結果")
    print("="*30)
    
    y_pred = model.predict(X_test)
    
    accuracy = accuracy_score(y_test, y_pred)
    print(f"準確率 (Accuracy): {accuracy:.2%}")
    print("\n詳細報告:")
    print(classification_report(y_test, y_pred))
    print("\n混淆矩陣:")
    print(confusion_matrix(y_test, y_pred))

    # 6. 特徵重要性
    print("\n" + "="*30)
    print("🔍 AI 認為最重要的選股因子")
    print("="*30)
    feature_importance = pd.DataFrame({
        'Feature': features,
        'Importance': model.feature_importances_
    }).sort_values(by='Importance', ascending=False)
    
    print(feature_importance)

    # 7. 儲存模型
    joblib.dump(model, 'stock_ai_model.pkl')
    print(f"\n💾 模型已儲存為 'stock_ai_model.pkl'")

if __name__ == "__main__":
    main()