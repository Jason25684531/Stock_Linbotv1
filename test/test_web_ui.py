"""測試 Web UI 策略切換功能"""
import webbrowser
import time
import subprocess
import sys

print("""
╔════════════════════════════════════════╗
║   Web UI 策略切換測試                  ║
╚════════════════════════════════════════╝

📋 測試步驟:
1. 啟動 Flask 伺服器
2. 自動開啟瀏覽器至 http://localhost:5000
3. 在頁面頂部找到「策略指揮中心」
4. 使用下拉選單切換策略 (V31/V33/V34)
5. 點擊「切換策略」按鈕
6. 檢查是否顯示成功訊息

⚠️  注意事項:
- 請確保已執行 'python 2_rundaily.py' 產生選股資料
- 測試完成後按 Ctrl+C 停止伺服器
""")

input("按 Enter 開始測試...")

print("\n🚀 啟動 Flask 伺服器...")

# 啟動 Flask (背景執行)
flask_process = subprocess.Popen(
    [sys.executable, "app.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

# 等待伺服器啟動
time.sleep(3)

print("✅ 伺服器已啟動\n")
print("🌐 開啟瀏覽器...")

# 自動開啟瀏覽器
webbrowser.open("http://localhost:5000")

print("""
✅ 瀏覽器已開啟！

📝 測試檢查表:
□ 頁面能正常載入
□ 看到「策略指揮中心」區塊
□ 下拉選單有 3 個策略選項
□ 當前策略有標記 (selected)
□ 切換策略後顯示成功訊息
□ strategy_settings.json 更新正確

按 Ctrl+C 停止測試...
""")

try:
    # 持續輸出伺服器日誌
    for line in flask_process.stdout:
        print(line, end='')
except KeyboardInterrupt:
    print("\n\n⏹️  停止伺服器...")
    flask_process.terminate()
    flask_process.wait()
    print("✅ 測試完成！")
