# debug_local.py
# 這是本地測試工具，不用透過 Line 也能測

from app import query_stock, get_ai_recommendation
# 如果有新聞功能，也可以加進來測
try:
    from app import get_market_briefing
except ImportError:
    get_market_briefing = None

print("=========================================")
print("🛠️  V15.0 本地戰術模擬器 (Local Debugger)")
print("=========================================")
print("輸入股票代碼 (例如 2330) 進行分析")
print("輸入 '推薦' 查看 AI 選股")
print("輸入 '新聞' 查看國際戰情")
print("輸入 'q' 離開")
print("-----------------------------------------")

# 模擬一個假的 base_url (因為本地測試看不到圖是正常的)
FAKE_URL = "https://localhost:5000"

while True:
    user_input = input("\n請輸入指令: ").strip()
    
    if user_input.lower() == 'q':
        print("👋 測試結束")
        break
        
    if user_input in ["推薦", "選股"]:
        print("\n⏳ 正在計算全市場推薦...")
        print(get_ai_recommendation())

    elif user_input in ["新聞", "news", "News"]:
        if get_market_briefing:
            print("\n⏳ 正在連線新聞特工...")
            print(get_market_briefing())
        else:
            print("❌ 無法呼叫新聞模組")
            
    elif user_input.isdigit():
        print(f"\n⏳ 正在分析 {user_input}...")
        
        # 🟢 [修改重點] 傳入假的 URL，並處理回傳的 List
        try:
            # query_stock 回傳的是 [TextSendMessage, ImageSendMessage...]
            messages = query_stock(user_input, FAKE_URL)
            
            # 我們只把裡面的文字印出來就好
            for msg in messages:
                if hasattr(msg, 'text'):
                    print(msg.text)
                elif hasattr(msg, 'original_content_url'):
                    print(f"🖼️ [圖片已生成] 連結: {msg.original_content_url}")
                    
        except Exception as e:
            print(f"❌ 發生錯誤: {e}")
        
    else:
        print("❌ 無效指令，請輸入代碼、'推薦' 或 '新聞'")