"""
新聞摘要代理 (News Agent)
============================================
功能：
1. 爬取鉅亨網新聞（透過 Google News RSS）
2. 優先篩選「盤前/盤後」標題文章
3. 使用 Google Gemini 篩選重大新聞 + 濃縮為台股影響摘要
4. 至少提供 5 則相關資訊

資料來源：Google News RSS (site:cnyes.com)
LLM：Google Gemini (google-genai SDK)
"""

import feedparser
import datetime
import re

from google import genai
from google.genai import types
from config import Config


# ==========================================
# RSS 來源設定
# ==========================================

RSS_SOURCES = {
    "美股": "https://news.google.com/rss/search?q=site:cnyes.com+美股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "國際政經": "https://news.google.com/rss/search?q=site:cnyes.com+國際政經&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "台股": "https://news.google.com/rss/search?q=site:cnyes.com+台股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "盤前盤後": "https://news.google.com/rss/search?q=site:cnyes.com+(%E7%9B%A4%E5%89%8D+OR+%E7%9B%A4%E5%BE%8C)&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "全球財經": "https://news.google.com/rss/search?q=site:cnyes.com+(%E5%85%A8%E7%90%83+OR+%E5%A4%AE%E8%A1%8C+OR+%E8%81%AF%E6%BA%96%E6%9C%83+OR+Fed)&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "美股焦點": "https://news.google.com/rss/search?q=site:cnyes.com+(S%26P500+OR+%E9%81%93%E7%93%8A+OR+%E7%B4%8D%E6%8C%87+OR+%E8%B2%BB%E5%8D%8A+OR+NVIDIA+OR+%E8%BC%9D%E9%81%94)&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
}

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

# 盤前/盤後關鍵字（用於優先排序）
_PRIORITY_PATTERNS = [
    r'盤前', r'盤後', r'盤勢', r'開盤',
    r'〈.*盤.*〉', r'＜.*盤.*＞', r'<.*盤.*>',
]

# ==========================================
# 新聞爬蟲
# ==========================================

def _clean_html(text: str) -> str:
    """清除 HTML 標籤"""
    return re.sub(r'<[^>]+>', '', text).strip()


def _is_priority_news(title: str) -> bool:
    """判斷是否為盤前/盤後優先新聞"""
    for pattern in _PRIORITY_PATTERNS:
        if re.search(pattern, title):
            return True
    return False


def fetch_anue_news(max_per_source: int = 8) -> tuple[str, list[str]]:
    """爬取鉅亨網新聞，優先篩選盤前/盤後標題

    Args:
        max_per_source: 每個來源最多抓取幾篇

    Returns:
        tuple: (組合後的新聞文字, 優先新聞標題列表)
    """
    print("📡 正在爬取鉅亨網新聞...")
    priority_articles = []  # 盤前/盤後文章
    other_articles = []     # 其他文章
    seen_titles: set = set()

    for label, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                if count >= max_per_source:
                    break
                title = entry.title or ""
                if len(title) < 5 or "贊助" in title:
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                summary = _clean_html(
                    getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                )
                if len(summary) > 120:
                    summary = summary[:120] + "..."

                article = {"title": title, "summary": summary, "source": label}

                if _is_priority_news(title):
                    priority_articles.append(article)
                else:
                    other_articles.append(article)
                count += 1
            print(f"  ✓ {label}: {count} 篇")
        except Exception as e:
            print(f"  ⚠️ {label} 抓取失敗: {e}")

    # 組合文字：盤前/盤後優先排在前面
    combined = ""
    priority_titles = []

    if priority_articles:
        combined += "\n【盤前/盤後重點】\n"
        for a in priority_articles:
            combined += f"- {a['title']}\n  {a['summary']}\n"
            priority_titles.append(a['title'])

    for label in ["美股", "美股焦點", "國際政經", "全球財經", "台股"]:
        label_articles = [a for a in other_articles if a['source'] == label]
        if label_articles:
            combined += f"\n【{label}】\n"
            for a in label_articles:
                combined += f"- {a['title']}\n  {a['summary']}\n"

    total = len(priority_articles) + len(other_articles)
    print(f"  📊 共 {total} 篇 (盤前/盤後: {len(priority_articles)} 篇)")

    return combined, priority_titles


# ==========================================
# Gemini LLM 摘要
# ==========================================

def _summarize_with_gemini(news_text: str) -> str:
    """使用 Gemini 篩選重大新聞並濃縮為台股影響摘要

    Returns:
        str: 至少 5 則重大新聞摘要
    """
    if not Config.GEMINI_API_KEY:
        return "⚠️ 未設定 GEMINI_KEY，無法生成 AI 摘要"

    today = datetime.datetime.now().strftime('%Y-%m-%d')

    prompt = f"""你是資深台股分析師。今天是 {today}。
以下是今日鉅亨網的新聞（含盤前盤後分析、美股、美股焦點、國際政經、全球財經、台股）：

{news_text}

請執行以下任務：

1. 從以上新聞中，篩選出「至少 5 則」對台股操作策略最有影響的重大新聞。
   優先選擇：帶有「盤前」「盤後」標題的分析文章、重大政策/經濟數據、影響特定產業族群的消息。

2. 每則新聞用以下格式：
   📌 [新聞標題摘要]
   → 影響：1 句話說明對台股的具體影響

3. 最後加一段「📊 綜合研判：」，2-3 句話給出今日操作方向（偏多/偏空/觀望）與建議關注族群。

格式要求：
- 使用繁體中文，語氣專業簡潔
- 至少列出 5 則新聞
- 總字數 300-400 字"""

    try:
        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=800,
            ),
        )
        return response.text
    except Exception as e:
        return f"⚠️ Gemini 分析失敗: {e}"


# ==========================================
# 公開介面
# ==========================================

def get_morning_news_summary() -> str:
    """早晨新聞摘要（供 5_push_to_line.py morning 模式調用）

    流程：爬取鉅亨網 → 優先篩選盤前/盤後 → Gemini 篩選重大新聞 → 至少 5 則摘要

    Returns:
        str: 新聞摘要文字
    """
    news_text, priority_titles = fetch_anue_news(max_per_source=8)
    if not news_text.strip():
        return "⚠️ 目前無法取得新聞資料"

    summary = _summarize_with_gemini(news_text)
    return summary


def get_market_briefing() -> str:
    """完整市場快報（供 app.py Line Bot 調用，向後相容）

    Returns:
        str: 帶標題的完整分析報告
    """
    try:
        news_text, _ = fetch_anue_news(max_per_source=8)
        if not news_text.strip():
            return "⚠️ 目前抓不到新聞資料，請稍後再試。"

        today = datetime.datetime.now().strftime('%Y-%m-%d')

        prompt = f"""你是華爾街頂尖的投資策略師。今天是 {today}。
以下是鉅亨網最新新聞：

{news_text}

請用繁體中文撰寫一份專業的台股早報：
- 🌤️ **今日氣象**：一句話定調台股氛圍
- 🌍 **國際脈動**：昨晚美股重點與對台股影響
- 🎯 **台股焦點**：2-3 個熱門族群/個股
- ⚖️ **多空判讀**：操作建議
總字數 400-500 字，適度使用 emoji。"""

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        return f"📰 【AI 財經早報】\n(來源: 鉅亨網)\n\n{response.text}"

    except Exception as e:
        return f"❌ 報告生成失敗: {e}"


# ==========================================
# 新聞族群加分萃取
# ==========================================

# Gemini 可用的產業標籤（對應 stock_sector_map.json 的產業名稱）
_VALID_SECTORS = [
    '半導體', '電子零組件', '光電', '通信網路', '電腦及週邊', '其他電子',
    '資訊服務', '電子通路', '數位雲端', '生技醫療', '金融保險', '航運',
    '鋼鐵', '塑膠', '化學', '食品', '紡織纖維', '建材營造', '汽車',
    '電機機械', '觀光餐旅', '油電燃氣', '綠能環保', '貿易百貨',
]


def get_news_sector_boost() -> dict:
    """從今日新聞萃取利多產業族群（供選股加分用）

    流程：爬取新聞 → Gemini 分析 → 萃取 2~4 個利多產業標籤

    Returns:
        dict: {"sectors": ["半導體", "航運"], "sentiment": "偏多"}
              失敗時回傳 {"sectors": [], "sentiment": "中性"}
    """
    default = {"sectors": [], "sentiment": "中性"}

    if not Config.GEMINI_API_KEY:
        print("⚠️ 未設定 GEMINI_KEY，跳過新聞族群分析")
        return default

    try:
        news_text, _ = fetch_anue_news(max_per_source=6)
        if not news_text.strip():
            return default

        today = datetime.datetime.now().strftime('%Y-%m-%d')
        sectors_str = '、'.join(_VALID_SECTORS)

        prompt = f"""你是資深台股分析師。今天是 {today}。
以下是今日鉅亨網新聞：

{news_text}

請分析這些新聞，從以下產業標籤中選出 2~4 個「今日最受利多影響」的產業：
{sectors_str}

回傳格式必須是純 JSON（不要 markdown 包裹）：
{{"sectors": ["產業1", "產業2"], "sentiment": "偏多"}}

規則：
- sectors 只能使用上面列出的標籤名稱，不要自創
- sectors 選 2~4 個，代表今日新聞最利多的產業
- sentiment 填「偏多」「偏空」或「中性」
- 只回傳 JSON，不要其他文字"""

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=200,
            ),
        )

        import json
        raw = response.text.strip()
        # 清除可能的 markdown 包裹
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

        result = json.loads(raw)

        # 驗證 sectors 只包含合法標籤
        valid = [s for s in result.get('sectors', []) if s in _VALID_SECTORS]
        sentiment = result.get('sentiment', '中性')
        if sentiment not in ('偏多', '偏空', '中性'):
            sentiment = '中性'

        print(f"  📰 新聞族群分析: {valid} | 情緒: {sentiment}")
        return {"sectors": valid, "sentiment": sentiment}

    except Exception as e:
        print(f"  ⚠️ 新聞族群萃取失敗: {e}")
        return default


# ==========================================
# 獨立執行
# ==========================================

if __name__ == "__main__":
    print("=" * 50)
    print("📰 早晨新聞摘要測試")
    print("=" * 50)
    result = get_morning_news_summary()
    print(result)
