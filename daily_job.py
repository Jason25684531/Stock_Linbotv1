import subprocess
import time
import datetime
import sys

def run_script(script_name):
    print(f"\n▶️ 正在執行: {script_name}...")
    start_time = time.time()
    
    # 使用當前 Python 直譯器執行外部腳本
    result = subprocess.run([sys.executable, script_name], capture_output=False)
    
    end_time = time.time()
    duration = end_time - start_time
    
    if result.returncode == 0:
        print(f"✅ {script_name} 執行成功 (耗時 {duration:.2f} 秒)")
        return True
    else:
        print(f"❌ {script_name} 執行失敗！")
        return False

def main():
    print(f"[{datetime.datetime.now()}] ⏰ 自動化排程開始...")
    
    # 1. 爬蟲 (更新資料庫)
    if not run_script('1_update_database.py'):
        print("⚠️ 爬蟲失敗，終止流程。")
        return

    # 2. 特徵工程 (更新 CSV)
    if not run_script('2_feature_engineering.py'):
        print("⚠️ 特徵計算失敗，終止流程。")
        return

    # 3. 訓練模型 (可選，每天重訓保持最新)
    if not run_script('3_train_model.py'):
        print("⚠️ 模型訓練失敗，但嘗試繼續推播...")
    
    # 4. 推播訊息
    run_script('5_push_to_line.py')

    print(f"\n[{datetime.datetime.now()}] 🎉 所有任務完成！")

if __name__ == "__main__":
    main()