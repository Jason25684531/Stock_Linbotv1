import feedparser
import google.generativeai as genai
import datetime
import time
from linebot.v3.messaging import MessagingApi, ApiClient, Configuration, TextMessage, BroadcastRequest
from config import Config

# ==========================================
# 🔧 設定區：鎖定鉅亨網與雅虎
# ==========================================
RSS_SOURCES = {
    # 技巧：用 site:cnyes.com 讓 Google 只抓鉅亨網的新聞
    "🇹🇼 鉅亨網 (台股)": "https://news.google.com/rss/search?q=site:cnyes.com+台股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    
    # 技巧：用 site:cnyes.com 抓美股
    "🇺🇸 鉅亨網 (美股)": "https://news.google.com/rss/search?q=site:cnyes.com+美股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    
    # Yahoo 奇摩股市 (Yahoo 的 RSS 目前通常還健在，如果壞了也可以改用 Google 代理)
    "📢 Yahoo奇摩 (焦點)": "https://tw.stock.yahoo.com/rss?category=trends"
}

# ==========================================
# 🚀 程式主體 (原有功能保持不變)
# ==========================================

def fetch_rss_news():
    """
    抓取新聞標題與摘要 (包含清洗 HTML 標籤的簡單邏輯)
    """
    print("📡 正在連線 鉅亨網 & Yahoo奇摩...")
    combined_text = ""
    
    for source_name, url in RSS_SOURCES.items():
        try:
            print(f"   - 抓取: {source_name}")
            feed = feedparser.parse(url)
            combined_text += f"\n【來源：{source_name}】\n"
            
            # 每個來源只抓前 6 則最新的 (太多會太雜)
            count = 0
            for entry in feed.entries:
                if count >= 10: break
                
                title = entry.title
                # 嘗試抓取摘要 (有些 RSS 會放在 summary 或 description)
                summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                
                # 簡單過濾掉太短或廣告雜訊
                if "贊助" in title or len(title) < 5: continue
                
                # 清洗 HTML 標籤 (RSS 摘要有時會有 <p> 等標籤)
                summary = summary.replace('<p>', '').replace('</p>', '').replace('<br />', '')
                # 截斷摘要，避免太長
                summary = summary[:100] + "..." if len(summary) > 100 else summary
                
                combined_text += f"● 標題: {title}\n"
                combined_text += f"  摘要: {summary}\n"
                count += 1
                
        except Exception as e:
            print(f"⚠️ 無法抓取 {source_name}: {e}")
            
    return combined_text

def analyze_with_gemini(news_text):
    """
    呼叫 Google Gemini 進行綜合評估
    """
    print("🧠 Gemini 正在閱讀並撰寫報告...")
    
    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-3-flash-preview')
    
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 📝 針對鉅亨/雅虎優化的 Prompt
    prompt = f"""
    你是華爾街頂尖的投資策略師。今天是 {today}。
    我蒐集了「鉅亨網」與「Yahoo奇摩股市」的最新新聞，請幫我做一份綜合評估報告。

    【蒐集到的新聞素材】：
    {news_text}

    【報告撰寫要求】：
    1. **請用繁體中文**，語氣專業、自信，像寫給 VIP 客戶的早報。
    2. 請忽略新聞中的廣告或重複資訊，只提煉精華。
    3. **報告結構** (請嚴格遵守)：
       - 🌤️ **今日氣象**：用一句話定調今天台股氣氛 (例如：多頭排列、震盪整理、空方施壓)。
       - 🌍 **國際脈動**：綜合鉅亨網美股資訊，摘要昨晚美股重點與對台股的影響。
       - 🎯 **台股焦點**：從 Yahoo 與鉅亨台股新聞中，找出今日最熱門的 2-3 個族群或個股 (如：台積電法說、AI 伺服器、航運報價)。
       - ⚖️ **多空判讀**：根據新聞摘要中的利多與利空，給出今日操作建議 (偏多操作 / 拉回買進 / 保守觀望)。
    4. 總字數約 500-600 字，排版要清晰，適度使用 emoji。
    """
    
    try:
        # 這裡可以調整 temperature，0.2 代表比較嚴謹，0.7 代表比較有創意
        response = model.generate_content(prompt, generation_config={"temperature": 0.3})
        return response.text
    except Exception as e:
        return f"❌ AI 分析失敗: {e}"
# 7_news_agent.py 的新增/確認部分

def get_market_briefing():
    """
    這是給 app.py 呼叫的專用接口
    回傳值：分析報告的文字 (String)
    """
    try:
        # 1. 抓新聞
        raw_news = fetch_rss_news()
        if not raw_news:
            return "⚠️ 目前抓不到新聞資料，請稍後再試。"
            
        # 2. AI 分析
        report = analyze_with_gemini(raw_news)
        
        # 3. 加上標題
        final_msg = f"📰 【AI 財經即時快報】\n(來源: Google News)\n\n{report}"
        return final_msg
        
    except Exception as e:
        return f"❌ 報告生成失敗: {e}"

def main():
    start_time = time.time()
    
    # 1. 抓新聞
    raw_news = fetch_rss_news()
    if not raw_news:
        print("❌ 抓不到任何新聞，任務終止。")
        return

    # 2. AI 分析
    report = analyze_with_gemini(raw_news)
    
    # 3. 發送 Line
    print("🚀 正在發送報告...")
    try:
        configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
        
            # 加上 Header
            final_msg = f"📰 【AI 財經早報】\n(來源: 鉅亨網/Yahoo)\n\n{report}"
        
            messaging_api.broadcast(BroadcastRequest(
                messages=[TextMessage(text=final_msg)]
            ))
        print(f"✅ 發送成功！耗時 {time.time() - start_time:.1f} 秒")
        
        # 本地端也印出來檢查
        print("\n" + "="*30)
        print(final_msg)
        print("="*30)
        
    except Exception as e:
        print(f"❌ Line 發送失敗: {e}")

if __name__ == "__main__":
    main()