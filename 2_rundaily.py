import subprocess
import sys
import time
import os

def run_step(script_path, step_name):
    print(f"\n{'='*40}")
    print(f"🚀 步驟 {step_name}: 正在準備執行...")
    print(f"📂 目標檔案: {script_path}")
    print(f"{'='*40}")
    
    if not os.path.exists(script_path):
        print(f"\n❌ 找不到檔案！請檢查路徑: {script_path}")
        return False

    start_time = time.time()
    result = subprocess.run([sys.executable, script_path])
    duration = time.time() - start_time
    
    if result.returncode == 0:
        print(f"\n✅ {os.path.basename(script_path)} 執行成功！(耗時 {duration:.2f} 秒)")
        return True
    else:
        print(f"\n❌ {os.path.basename(script_path)} 執行失敗！")
        return False

if __name__ == "__main__":
    print("🤖 Stock AI 每日自動化作業開始...\n")
    
    # 1. 爬蟲
    if not run_step("1_update_database.py", "1/3 - 每日爬蟲"):
        sys.exit(1)

    # 2. 計算 (tool/calc_indicators.py)
    calc_path = os.path.join("tool", "calc_indicators.py")
    if not run_step(calc_path, "2/3 - 特徵工程"):
        sys.exit(1)

    # 3. 推播 (新增這段)
    if not run_step("5_push_to_line.py", "3/3 - Line 日報推播"):
        print("⚠️ 推播失敗，但資料已更新")

    print(f"\n{'='*40}")
    print("🎉 今日作業全部完成！")
    print(f"{'='*40}")