# debug_local.py
# 這是本地測試工具，不用透過 Line 也能測

from app import query_stock, get_ai_recommendation

print("=========================================")
print("🛠️  V15.0 本地戰術模擬器 (Local Debugger)")
print("=========================================")
print("輸入股票代碼 (例如 2330) 進行分析")
print("輸入 '推薦' 查看 AI 選股")
print("輸入 'q' 離開")
print("-----------------------------------------")

while True:
    user_input = input("\n請輸入指令: ").strip()
    
    if user_input.lower() == 'q':
        print("👋 測試結束")
        break
        
    if user_input == "推薦":
        print("\n⏳ 正在計算全市場推薦...")
        print(get_ai_recommendation())
        
    elif user_input.isdigit():
        print(f"\n⏳ 正在分析 {user_input}...")
        # 直接呼叫 app.py 裡的邏輯
        result = query_stock(user_input)
        print(result)
        
    else:
        print("❌ 無效指令，請輸入代碼或 '推薦'")