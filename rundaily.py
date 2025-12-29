import subprocess
import sys
import time
import os

def run_step(script_path, step_name):
    print(f"\n{'='*40}")
    print(f"🚀 步驟 {step_name}: 正在準備執行...")
    print(f"📂 目標檔案: {script_path}")
    print(f"{'='*40}")
    
    # 檢查檔案是否存在
    if not os.path.exists(script_path):
        print(f"\n❌ 找不到檔案！請檢查路徑是否正確: {script_path}")
        return False

    start_time = time.time()
    
    # 呼叫 Python 執行該檔案
    result = subprocess.run([sys.executable, script_path])
    
    end_time = time.time()
    duration = end_time - start_time
    
    if result.returncode == 0:
        print(f"\n✅ {os.path.basename(script_path)} 執行成功！(耗時 {duration:.2f} 秒)")
        return True
    else:
        print(f"\n❌ {os.path.basename(script_path)} 執行失敗！程式終止。")
        return False

if __name__ == "__main__":
    print("🤖 Stock AI 每日自動化作業開始...\n")
    
    # ==========================================
    # 1. 執行爬蟲 (在根目錄)
    # ==========================================
    crawler_script = "1_update_database.py" 
    if not run_step(crawler_script, "1/2 - 每日爬蟲 (抓原始資料)"):
        sys.exit(1)

    # ==========================================
    # 2. 執行計算 (在 tool 資料夾)
    # ==========================================
    # 使用 os.path.join 確保路徑格式正確 (Windows/Mac 通用)
    calculator_script = os.path.join("tool", "calc_indicators.py")
    
    if not run_step(calculator_script, "2/2 - 特徵工程 (計算 MA60/RSI)"):
        sys.exit(1)

    print(f"\n{'='*40}")
    print("🎉 全部作業完成！資料庫已更新至最新狀態。")
    print("💡 現在可以去跑 4_run_backtest.py 驗證結果了！")
    print(f"{'='*40}")