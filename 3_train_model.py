import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import os

def main():
    print("🚀 Day 3: 開始訓練 AI 模型 (V11 價值動能版)...")

    # ==========================================
    # 📂 1. 讀取資料 (從 ML_Data/feature_engineering)
    # ==========================================
    input_path = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
    print(f"📂 讀取 training_data.csv: {input_path}")
    
    try:
        df = pd.read_csv(input_path, dtype={'stock_id': str})
    except FileNotFoundError:
        print(f"❌ 找不到 {input_path}，請確認是否已執行 Step 2。")
        return

    # ==========================================
    # 🧹 2. 資料清洗
    # ==========================================
    # 去除無限大 (inf) 與 空值 (NaN)
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    print(f"📊 有效訓練資料: {len(df)} 筆")

    # ==========================================
    # 🎯 3. 定義特徵 (X) 與 答案 (y)
    # ==========================================
    # 這是 V11 策略的核心特徵，必須與 Day 2 產出的欄位完全一致
    features = [
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',  # 價量
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',            # V11 基本面四大天王
        'MA5', 'MA20', 'MA60', 'RSI',                                       # 基礎技術面
        'PEG'
    ]
    
    # 檢查是否有缺欄位
    missing_cols = [col for col in features if col not in df.columns]
    if missing_cols:
        print(f"❌ 缺少特徵欄位: {missing_cols}")
        print("💡 請重新執行 2_feature_engineering.py 確保欄位正確。")
        return

    X = df[features]
    y = df['Target']

    # ==========================================
    # ✂️ 4. 切分訓練集與測試集
    # ==========================================
    # 為了不讓 AI 偷看未來，我們不隨機切分，而是依時間切分會更好
    # 但這裡為了簡單與普適性，先維持隨機切分 (80% 訓練, 20% 考試)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # ==========================================
    # 🧠 5. 建立與訓練模型 (XGBoost)
    # ==========================================
    print("⚙️  正在訓練 XGBoost 模型 (這可能需要幾分鐘)...")
    
    # 參數設定：稍微降低深度避免過擬合 (Overfitting)
    model = xgb.XGBClassifier(
        n_estimators=100,     # 樹的數量
        learning_rate=0.1,    # 學習率
        max_depth=5,          # 樹的深度
        objective='binary:logistic',
        use_label_encoder=False,
        eval_metric='logloss',
        random_state=42
    )
    
    model.fit(X_train, y_train)

    # ==========================================
    # 📝 6. 驗收成果
    # ==========================================
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    print(f"\n🏆 模型訓練完成！")
    print(f"🎯 準確率 (Accuracy): {accuracy:.2%}")
    print("\n📊 詳細報告:")
    print(classification_report(y_test, y_pred))

    # 查看特徵重要性 (看 AI 最在意什麼)
    print("🔍 AI 最重視的特徵 (Top 5):")
    feature_importances = pd.DataFrame({
        'feature': features,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    print(feature_importances.head(5))

    # ==========================================
    # 💾 7. 儲存模型 (存到 ML_Data/pkl)
    # ==========================================
    output_dir = os.path.join('ML_Data', 'pkl')
    os.makedirs(output_dir, exist_ok=True)
    
    model_output_path = os.path.join(output_dir, 'stock_ai_model.pkl')
    joblib.dump(model, model_output_path)
    
    print(f"\n💾 模型已儲存至: {model_output_path}")

if __name__ == "__main__":
    main()