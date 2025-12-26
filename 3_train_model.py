import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report
import joblib
import os

# ============================================
# ⚙️ 設定區
# ============================================
# 資料路徑
DATA_PATH = os.path.join('ML_Data', 'feature_engineering', 'training_data.csv')
# 模型儲存路徑
MODEL_DIR = os.path.join('ML_Data', 'pkl')
MODEL_PATH = os.path.join(MODEL_DIR, 'stock_ai_model.pkl')

# 🟢 [關鍵] 特徵列表 (Feature List)
# 必須跟 2_feature_engineering.py 算出來的一模一樣
FEATURES = [
    'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    'MACD_hist', 'KD_K', 'BB_width', # 🟢 記得加這行！
    'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
    'MA5', 'MA20', 'MA60', 'RSI',
    'PEG', 
    # 👇 這次新增的王牌特徵
    'foreign_ratio', 'trust_ratio', 'trust_ma3'
]

# ============================================
# 🚀 主程式
# ============================================
def main():
    print("🚀 Day 3: 開始訓練 AI 模型 (V16 籌碼狙擊版)...")
    
    # 1. 讀取資料
    if not os.path.exists(DATA_PATH):
        print(f"❌ 找不到資料檔: {DATA_PATH}")
        print("💡 請先執行: python 2_feature_engineering.py")
        return

    print(f"📂 讀取 training_data.csv: {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    print(f"📊 有效訓練資料: {len(df)} 筆")
    
    # 2. 準備 X (特徵) 與 y (目標)
    # 檢查欄位是否存在
    missing_cols = [col for col in FEATURES if col not in df.columns]
    if missing_cols:
        print(f"❌ 資料庫缺少特徵: {missing_cols}")
        print("💡 請重新檢查 2_feature_engineering.py 是否有計算這些欄位")
        return

    X = df[FEATURES]
    
    # 🟢 [修正] 這裡要用小寫 'target'，對應 2_feature_engineering.py 的設定
    if 'target' not in df.columns:
        print("❌ 找不到目標欄位 'target'")
        return
        
    y = df['target']
    
    # 3. 切分訓練集與測試集 (80% 訓練, 20% 驗證)
    # shuffle=False 代表依時間順序切分 (因為股票有時間性，不能亂跳)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    # 4. 建立與訓練模型 (XGBoost)
    # scale_pos_weight: 處理資料不平衡 (因為 Target=1 的飆股通常比較少)
    # 你的正樣本比例約 11%，所以這裡設 8 左右可以讓 AI 更重視抓飆股
    model = xgb.XGBClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=5,
        scale_pos_weight=5, 
        random_state=42,
        eval_metric='logloss'
    )
    
    print("🧠 正在訓練大腦 (這可能需要幾分鐘)...")
    model.fit(X_train, y_train)
    
    # 5. 驗證成效
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    acc = accuracy_score(y_test, y_pred)
    auc = roc_auc_score(y_test, y_prob)
    
    print("\n===================================")
    print(f"🏆 模型評估報告 (V16 籌碼狙擊版)")
    print("===================================")
    print(f"✅ 準確率 (Accuracy): {acc:.2%}")
    print(f"🔥 AUC 分數 (鑑別力): {auc:.4f} (越高越好)")
    print("\n詳細分類報告:")
    print(classification_report(y_test, y_pred))
    
    # 6. 查看特徵重要性 (Feature Importance)
    # 讓我們看看 AI 覺得哪個指標最重要？
    importance = model.feature_importances_
    feat_importances = pd.Series(importance, index=FEATURES).sort_values(ascending=False)
    
    print("\n🔍 AI 眼中的關鍵特徵 (前 5 名):")
    print(feat_importances.head(5))
    
    # 7. 儲存模型
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        
    joblib.dump(model, MODEL_PATH)
    print(f"\n💾 模型已儲存至: {MODEL_PATH}")
    print("🎉 恭喜！你的 AI 已經學會看籌碼了！")

if __name__ == "__main__":
    main()