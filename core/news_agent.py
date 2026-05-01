"""新聞摘要代理 (News Agent).

功能：
1. 爬取鉅亨網新聞（透過 Google News RSS）
2. 優先篩選「盤前/盤後」標題文章
3. 使用 Google Gemini 篩選重大新聞 + 濃縮為台股影響摘要
4. 透過 LangChain `BaseTool` 包裝 MCP-backed 市場/財報摘要
5. 保留既有公開介面：`get_morning_news_summary()`、
    `get_news_sector_boost()`、`get_stock_news_mentions()`

資料來源：Google News RSS (site:cnyes.com)
LLM：Google Gemini (google-genai SDK)
"""

import asyncio
import json
import feedparser
import datetime
import re
import threading
import time
from typing import ClassVar
from urllib.parse import quote_plus

from google import genai
from google.genai import types
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from config import Config
from core.mcp_client import TWSEMCPClient as MCPClient


# ==========================================
# RSS 來源設定
# ==========================================


def _google_news_rss(query: str) -> str:
    encoded_query = quote_plus(f"site:cnyes.com {query}")
    return (
        'https://news.google.com/rss/search'
        f'?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
    )


RSS_SOURCES = {
    '美股': _google_news_rss('美股'),
    '國際政經': _google_news_rss('國際政經'),
    '台股': _google_news_rss('台股'),
    '盤前盤後': _google_news_rss('(盤前 OR 盤後)'),
    '全球財經': _google_news_rss('(全球 OR 央行 OR 聯準會 OR Fed)'),
    '美股焦點': _google_news_rss(
        '(S&P500 OR 道瓊 OR 納指 OR 費半 OR NVIDIA OR 輝達)'
    ),
}

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

# 盤前/盤後關鍵字（用於優先排序）
_PRIORITY_PATTERNS = [
    r'盤前', r'盤後', r'盤勢', r'開盤',
    r'〈.*盤.*〉', r'＜.*盤.*＞', r'<.*盤.*>',
]


def _default_trade_date() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d')


def _latest_financial_period() -> tuple[int, int]:
    now = datetime.datetime.now()
    if now.month >= 11:
        return now.year, 3
    if now.month >= 8:
        return now.year, 2
    if now.month >= 5:
        return now.year, 1
    if now.month >= 3:
        return now.year - 1, 4
    return now.year - 1, 3


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), 10))


def _extract_error_status_code(exc: Exception) -> int | None:
    for attr_name in ("status_code", "status", "code"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    response = getattr(exc, 'response', None)
    response_status = getattr(response, 'status_code', None)
    if isinstance(response_status, int):
        return response_status

    match = re.search(r'\b(429|502|503|504)\b', str(exc))
    if match:
        return int(match.group(1))

    return None


_STOCK_NEWS_GUARD_LOCK = threading.Lock()
_STOCK_NEWS_GUARD_STATE = {
    'consecutive_failures': 0,
    'breaker_open_until': 0.0,
    'last_error': '',
    'last_status_code': None,
    'last_timeout_seconds': None,
}


def _stock_news_timeout_seconds() -> float:
    try:
        return max(0.1, float(Config.DASHBOARD_NEWS_TIMEOUT_SECONDS))
    except Exception:
        return 3.0


def _stock_news_failure_threshold() -> int:
    try:
        return max(1, int(Config.DASHBOARD_NEWS_FAILURE_THRESHOLD))
    except Exception:
        return 2


def _stock_news_breaker_cooldown_seconds() -> float:
    try:
        return max(1.0, float(Config.DASHBOARD_NEWS_BREAKER_COOLDOWN_SECONDS))
    except Exception:
        return 60.0


def _normalize_timeout_seconds(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None

    try:
        normalized = float(timeout_seconds)
    except (TypeError, ValueError):
        return None

    if normalized <= 0:
        return 0.0
    return normalized


def _resolve_stock_news_timeout(deadline_monotonic: float | None = None) -> float:
    timeout_seconds = _stock_news_timeout_seconds()
    if deadline_monotonic is None:
        return timeout_seconds

    remaining = float(deadline_monotonic) - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(timeout_seconds, remaining)


def _expire_stock_news_breaker_if_needed(now_monotonic: float | None = None) -> None:
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    with _STOCK_NEWS_GUARD_LOCK:
        breaker_open_until = float(_STOCK_NEWS_GUARD_STATE['breaker_open_until'] or 0.0)
        if breaker_open_until and breaker_open_until <= now_monotonic:
            _STOCK_NEWS_GUARD_STATE['consecutive_failures'] = 0
            _STOCK_NEWS_GUARD_STATE['breaker_open_until'] = 0.0


def reset_stock_news_guard_state() -> None:
    with _STOCK_NEWS_GUARD_LOCK:
        _STOCK_NEWS_GUARD_STATE['consecutive_failures'] = 0
        _STOCK_NEWS_GUARD_STATE['breaker_open_until'] = 0.0
        _STOCK_NEWS_GUARD_STATE['last_error'] = ''
        _STOCK_NEWS_GUARD_STATE['last_status_code'] = None
        _STOCK_NEWS_GUARD_STATE['last_timeout_seconds'] = None


def get_stock_news_guard_snapshot(now_monotonic: float | None = None) -> dict:
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    _expire_stock_news_breaker_if_needed(now_monotonic)
    with _STOCK_NEWS_GUARD_LOCK:
        breaker_open_until = float(_STOCK_NEWS_GUARD_STATE['breaker_open_until'] or 0.0)
        cooldown_remaining_seconds = max(0.0, breaker_open_until - now_monotonic)
        return {
            'breaker_open': cooldown_remaining_seconds > 0,
            'cooldown_remaining_seconds': cooldown_remaining_seconds,
            'consecutive_failures': int(_STOCK_NEWS_GUARD_STATE['consecutive_failures'] or 0),
            'last_error': str(_STOCK_NEWS_GUARD_STATE['last_error'] or ''),
            'last_status_code': _STOCK_NEWS_GUARD_STATE['last_status_code'],
            'last_timeout_seconds': _STOCK_NEWS_GUARD_STATE['last_timeout_seconds'],
        }


def _record_stock_news_success() -> None:
    with _STOCK_NEWS_GUARD_LOCK:
        _STOCK_NEWS_GUARD_STATE['consecutive_failures'] = 0
        _STOCK_NEWS_GUARD_STATE['last_error'] = ''
        _STOCK_NEWS_GUARD_STATE['last_status_code'] = None
        _STOCK_NEWS_GUARD_STATE['last_timeout_seconds'] = None


def _record_stock_news_failure(
    reason: str,
    *,
    status_code: int | None = None,
    timeout_seconds: float | None = None,
) -> dict:
    now_monotonic = time.monotonic()
    _expire_stock_news_breaker_if_needed(now_monotonic)

    with _STOCK_NEWS_GUARD_LOCK:
        _STOCK_NEWS_GUARD_STATE['consecutive_failures'] += 1
        _STOCK_NEWS_GUARD_STATE['last_error'] = str(reason or '').strip()
        _STOCK_NEWS_GUARD_STATE['last_status_code'] = status_code
        _STOCK_NEWS_GUARD_STATE['last_timeout_seconds'] = _normalize_timeout_seconds(timeout_seconds)

        if _STOCK_NEWS_GUARD_STATE['consecutive_failures'] >= _stock_news_failure_threshold():
            _STOCK_NEWS_GUARD_STATE['breaker_open_until'] = max(
                float(_STOCK_NEWS_GUARD_STATE['breaker_open_until'] or 0.0),
                now_monotonic + _stock_news_breaker_cooldown_seconds(),
            )

    return get_stock_news_guard_snapshot(now_monotonic)


def _is_timeout_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__} {exc}".lower()
    timeout_markers = (
        'timeout',
        'timed out',
        'deadline exceeded',
        'readtimeout',
    )
    return any(marker in message for marker in timeout_markers)


def _build_stock_news_client(timeout_seconds: float | None = None):
    normalized_timeout = _normalize_timeout_seconds(timeout_seconds)
    http_options = None
    if normalized_timeout is not None and normalized_timeout > 0:
        http_options = types.HttpOptions(timeout=max(1, int(normalized_timeout * 1000)))
    return genai.Client(api_key=Config.GEMINI_API_KEY, http_options=http_options)


def _records_to_json(records: list[dict]) -> str:
    return json.dumps(records, ensure_ascii=False)


def _tool_unavailable_response(
    tool_name: str,
    **extra: object,
) -> str:
    payload = {
        'tool_name': tool_name,
        'available': False,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class CompanyBasicInfoToolInput(BaseModel):
    stock_id: str = Field(..., min_length=1)
    market: str = Field(default=Config.MCP_DEFAULT_MARKET)
    trade_date: str = Field(default_factory=_default_trade_date)


class MarketSnapshotToolInput(BaseModel):
    market: str = Field(default=Config.MCP_DEFAULT_MARKET)
    trade_date: str = Field(default_factory=_default_trade_date)
    include_etfs: bool = Field(default=False)
    limit: int = Field(default=5, ge=1, le=10)


class ForeignInvestorFlowToolInput(BaseModel):
    market: str = Field(default=Config.MCP_DEFAULT_MARKET)
    trade_date: str = Field(default_factory=_default_trade_date)
    limit: int = Field(default=5, ge=1, le=10)


class FinancialStatementsToolInput(BaseModel):
    market: str = Field(default=Config.MCP_DEFAULT_MARKET)
    year: int = Field(default_factory=lambda: _latest_financial_period()[0])
    quarter: int = Field(default_factory=lambda: _latest_financial_period()[1])
    limit: int = Field(default=5, ge=1, le=10)


class MCPCompanyBasicInfoTool(BaseTool):
    tool_name: ClassVar[str] = 'mcp_company_basic_info'
    name: str = tool_name
    description: str = (
        'Get MCP-backed company basic info for a specific stock.'
    )
    args_schema: type[BaseModel] = CompanyBasicInfoToolInput

    def _run(
        self,
        stock_id: str,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
    ) -> str:
        payload = MCPClient().get_company_basic_info_sync(
            stock_id=stock_id,
            trade_date=trade_date or _default_trade_date(),
            market=market,
        )
        if payload is None:
            return _tool_unavailable_response(
                self.tool_name,
                stock_id=stock_id,
                trade_date=trade_date or _default_trade_date(),
            )

        record = MCPClient.company_basic_info_to_record(payload)
        return json.dumps({
            'tool_name': self.tool_name,
            'available': True,
            'stock_id': stock_id,
            'trade_date': payload.get('as_of_date', trade_date or _default_trade_date()),
            'company': record,
        }, ensure_ascii=False)

    async def _arun(
        self,
        stock_id: str,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._run,
            stock_id=stock_id,
            market=market,
            trade_date=trade_date,
        )


class MCPMarketSnapshotTool(BaseTool):
    tool_name: ClassVar[str] = 'mcp_market_snapshot'
    name: str = tool_name
    description: str = (
        'Get MCP-backed market snapshot summary for news context.'
    )
    args_schema: type[BaseModel] = MarketSnapshotToolInput

    def _run(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
        include_etfs: bool = False,
        limit: int = 5,
    ) -> str:
        payload = MCPClient().get_market_statistics_sync(
            trade_date or _default_trade_date(),
            market=market,
            include_etfs=include_etfs, 
        )
        if payload is None:
            return _tool_unavailable_response(
                self.tool_name,
                trade_date=trade_date or _default_trade_date(),
            )
        frame = MCPClient.stock_basic_snapshot_to_frame(payload)
        top_volume = []
        if not frame.empty and 'volume' in frame.columns:
            top_volume = json.loads(
                frame.sort_values('volume', ascending=False)
                .head(_clamp_limit(limit))
                [['stock_id', 'close_price', 'volume', 'security_type']]
                .to_json(orient='records')
            )
        return json.dumps({
            'tool_name': self.tool_name,
            'trade_date': trade_date or _default_trade_date(),
            'record_count': int(
                payload.get('meta', {}).get('record_count', len(frame))
            ),
            'top_volume': top_volume,
        }, ensure_ascii=False)

    async def _arun(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
        include_etfs: bool = False,
        limit: int = 5,
    ) -> str:
        return await asyncio.to_thread(
            self._run,
            market=market,
            trade_date=trade_date,
            include_etfs=include_etfs,
            limit=limit,
        )


class MCPForeignInvestorFlowTool(BaseTool):
    tool_name: ClassVar[str] = 'mcp_foreign_investor_flow'
    name: str = tool_name
    description: str = (
        'Get MCP-backed foreign flow summary for news context.'
    )
    args_schema: type[BaseModel] = ForeignInvestorFlowToolInput

    def _run(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
        limit: int = 5,
    ) -> str:
        payload = MCPClient().get_foreign_investment_sync(
            trade_date or _default_trade_date(),
            market=market,
        )
        if payload is None:
            return _tool_unavailable_response(
                self.tool_name,
                trade_date=trade_date or _default_trade_date(),
            )
        frame = MCPClient.foreign_investor_flow_to_frame(payload)
        top_buy = []
        top_sell = []
        if not frame.empty and 'foreign_buy' in frame.columns:
            limit_value = _clamp_limit(limit)
            top_buy = json.loads(
                frame.sort_values('foreign_buy', ascending=False)
                .head(limit_value)
                [['stock_id', 'foreign_buy', 'trust_buy', 'dealer_buy']]
                .to_json(orient='records')
            )
            top_sell = json.loads(
                frame.sort_values('foreign_buy', ascending=True)
                .head(limit_value)
                [['stock_id', 'foreign_buy', 'trust_buy', 'dealer_buy']]
                .to_json(orient='records')
            )
        return json.dumps({
            'tool_name': self.tool_name,
            'trade_date': trade_date or _default_trade_date(),
            'record_count': int(
                payload.get('meta', {}).get('record_count', len(frame))
            ),
            'top_buy': top_buy,
            'top_sell': top_sell,
        }, ensure_ascii=False)

    async def _arun(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        trade_date: str | None = None,
        limit: int = 5,
    ) -> str:
        return await asyncio.to_thread(
            self._run,
            market=market,
            trade_date=trade_date,
            limit=limit,
        )


class MCPFinancialStatementsTool(BaseTool):
    tool_name: ClassVar[str] = 'mcp_financial_statements'
    name: str = tool_name
    description: str = (
        'Get MCP-backed financial statement summary for news context.'
    )
    args_schema: type[BaseModel] = FinancialStatementsToolInput

    def _run(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        year: int | None = None,
        quarter: int | None = None,
        limit: int = 5,
    ) -> str:
        target_year, target_quarter = _latest_financial_period()
        payload = MCPClient().get_historical_financial_statements_sync(
            year or target_year,
            quarter or target_quarter,
            market=market,
        )
        if payload is None:
            return _tool_unavailable_response(
                self.tool_name,
                year=year or target_year,
                quarter=quarter or target_quarter,
            )
        frame = MCPClient.historical_financial_statements_to_frame(payload)
        top_operating_profit = []
        if not frame.empty and 'operating_profit' in frame.columns:
            top_operating_profit = json.loads(
                frame.sort_values('operating_profit', ascending=False)
                .head(_clamp_limit(limit))
                [['stock_id', 'revenue', 'operating_profit', 'eps']]
                .to_json(orient='records')
            )
        return json.dumps({
            'tool_name': self.tool_name,
            'year': year or target_year,
            'quarter': quarter or target_quarter,
            'record_count': int(
                payload.get('meta', {}).get('record_count', len(frame))
            ),
            'top_operating_profit': top_operating_profit,
        }, ensure_ascii=False)

    async def _arun(
        self,
        market: str = Config.MCP_DEFAULT_MARKET,
        year: int | None = None,
        quarter: int | None = None,
        limit: int = 5,
    ) -> str:
        return await asyncio.to_thread(
            self._run,
            market=market,
            year=year,
            quarter=quarter,
            limit=limit,
        )


def get_mcp_context_tools() -> list[BaseTool]:
    """提供新聞代理使用的 MCP-backed LangChain tools。"""
    return [
        MCPCompanyBasicInfoTool(),
        MCPMarketSnapshotTool(),
        MCPForeignInvestorFlowTool(),
        MCPFinancialStatementsTool(),
    ]


def build_mcp_prompt_context() -> str:
    """使用 MCP-backed tools 建立 Gemini prompt 的市場/財務上下文。"""
    trade_date = _default_trade_date()
    year, quarter = _latest_financial_period()
    tools = {tool.tool_name: tool for tool in get_mcp_context_tools()}
    context_lines: list[str] = []

    try:
        snapshot_result = json.loads(
            tools['mcp_market_snapshot'].invoke({
                'trade_date': trade_date,
                'market': Config.MCP_DEFAULT_MARKET,
                'include_etfs': False,
                'limit': 5,
            })
        )
        top_volume = '、'.join(
            item['stock_id']
            for item in snapshot_result.get('top_volume', [])
        ) or '無'
        context_lines.append(
            f"- MCP 市場快照筆數: {snapshot_result.get('record_count', 0)}"
        )
        context_lines.append(f"- MCP 成交量前列: {top_volume}")
    except Exception as e:
        context_lines.append(f"- MCP 市場快照暫不可用: {e}")

    try:
        flow_result = json.loads(
            tools['mcp_foreign_investor_flow'].invoke({
                'trade_date': trade_date,
                'market': Config.MCP_DEFAULT_MARKET,
                'limit': 5,
            })
        )
        top_buy = '、'.join(
            item['stock_id']
            for item in flow_result.get('top_buy', [])
        ) or '無'
        top_sell = '、'.join(
            item['stock_id']
            for item in flow_result.get('top_sell', [])
        ) or '無'
        context_lines.append(f"- MCP 外資買超前列: {top_buy}")
        context_lines.append(f"- MCP 外資賣超前列: {top_sell}")
    except Exception as e:
        context_lines.append(f"- MCP 外資籌碼暫不可用: {e}")

    try:
        financial_result = json.loads(
            tools['mcp_financial_statements'].invoke({
                'year': year,
                'quarter': quarter,
                'market': Config.MCP_DEFAULT_MARKET,
                'limit': 5,
            })
        )
        top_profit = '、'.join(
            item['stock_id']
            for item in financial_result.get('top_operating_profit', [])
        ) or '無'
        context_lines.append(f"- 最新可用財報季度: {year}Q{quarter}")
        context_lines.append(f"- MCP 營業利益前列: {top_profit}")
    except Exception as e:
        context_lines.append(f"- MCP 財報上下文暫不可用: {e}")

    return '\n'.join(context_lines) if context_lines else '- MCP 上下文暫不可用'


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
                    getattr(entry, 'summary', '')
                    or getattr(entry, 'description', '')
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
    mcp_context = build_mcp_prompt_context()

    prompt = f"""你是資深台股分析師。今天是 {today}。
以下是由 MCP-backed tools 取得的市場與財務上下文，請優先納入判讀：

{mcp_context}

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
    """從今日新聞萃取利多/利空產業族群（供選股雙向加/減分用）

    流程：爬取新聞 → Gemini 分析 → 同時萃取 2~4 個利多與 0~2 個利空產業標籤

    Returns:
        dict: {
            "bull_sectors": ["半導體", "航運"],   # 利多族群（用於加分）
            "bear_sectors": ["塑膠"],              # 利空族群（用於減分）
            "bull_reasons": ["AI 伺服器拉貨升溫"],  # 利多重點條列
            "bear_reasons": ["成熟製程報價承壓"],  # 利空重點條列
            "bull_theme_map": {"半導體": "AI 伺服器拉貨"},
            "bear_theme_map": {"塑膠": "油價飆升壓縮利差"},
            "sectors": ["半導體", "航運"],         # 向後相容：等同 bull_sectors
            "sentiment": "偏多"                   # 整體市場情緒
        }
        失敗時回傳：
        {
            "bull_sectors": [], "bear_sectors": [],
            "bull_reasons": [], "bear_reasons": [],
            "bull_theme_map": {}, "bear_theme_map": {},
            "sectors": [], "sentiment": "中性"
        }
    """
    default = {
        "bull_sectors": [],
        "bear_sectors": [],
        "bull_reasons": [],
        "bear_reasons": [],
        "bull_theme_map": {},
        "bear_theme_map": {},
        "sectors": [],
        "sentiment": "中性",
    }

    if not Config.GEMINI_API_KEY:
        print("⚠️ 未設定 GEMINI_KEY，跳過新聞族群分析")
        return default

    try:
        news_text, _ = fetch_anue_news(max_per_source=6)
        if not news_text.strip():
            return default

        today = datetime.datetime.now().strftime('%Y-%m-%d')
        sectors_str = '、'.join(_VALID_SECTORS)
        mcp_context = build_mcp_prompt_context()

        prompt = f"""你是資深台股分析師。今天是 {today}。
以下是由 MCP-backed tools 取得的市場與財務上下文，請先參考：

{mcp_context}

以下是今日鉅亨網新聞：

{news_text}

請分析這些新聞，從以下產業標籤中：
{sectors_str}

選出：
1. bull_sectors：2~4 個「今日最受利多影響」的產業（有正面消息、法說超預期、題材發酵）
2. bear_sectors：0~2 個「今日受利空影響」的產業（有負面消息、衰退、跌法人調降等），無則留空陣列

回傳格式必須是純 JSON（不要 markdown 包裹）：
{{
    "bull_sectors": ["產業1", "產業2"],
    "bear_sectors": ["產業3"],
    "bull_reasons": ["利多重點1", "利多重點2"],
    "bear_reasons": ["利空重點1"],
    "bull_theme_map": {{"產業1": "主題1", "產業2": "主題2"}},
    "bear_theme_map": {{"產業3": "主題3"}},
    "sentiment": "偏多"
}}

規則：
- 所有產業只能使用上面列出的標籤名稱，不要自創
- bull_sectors 選 2~4 個，bear_sectors 選 0~2 個
- bull_reasons / bear_reasons 各列 0~3 條，用繁體中文短句條列，15 字內，直接說重點
- bull_theme_map / bear_theme_map 要把「族群」對應到「關鍵主題」，例如：
    半導體: 關稅風險升溫
    油電燃氣: 中東戰事推升油價
    電子零組件: AI 伺服器拉貨
- theme 需直接寫出主題，不要只寫「新聞利多」或「消息偏空」
- sentiment 填「偏多」「偏空」或「中性」
- 只回傳 JSON，不要其他文字"""

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=300,
            ),
        )

        import json
        raw = response.text.strip()
        # 清除可能的 markdown 包裹
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

        result = json.loads(raw)

        # 驗證標籤只包含合法產業
        bull = [
            sector for sector in result.get('bull_sectors', [])
            if sector in _VALID_SECTORS
        ]
        bear = [
            sector for sector in result.get('bear_sectors', [])
            if sector in _VALID_SECTORS
        ]
        bull_reasons = [
            str(item).strip()[:20]
            for item in result.get('bull_reasons', [])
            if str(item).strip()
        ][:3]
        bear_reasons = [
            str(item).strip()[:20]
            for item in result.get('bear_reasons', [])
            if str(item).strip()
        ][:3]
        raw_bull_theme_map = result.get('bull_theme_map', {}) or {}
        raw_bear_theme_map = result.get('bear_theme_map', {}) or {}
        bull_theme_map = {
            str(sector).strip(): str(topic).strip()[:24]
            for sector, topic in raw_bull_theme_map.items()
            if str(sector).strip() in bull and str(topic).strip()
        }
        bear_theme_map = {
            str(sector).strip(): str(topic).strip()[:24]
            for sector, topic in raw_bear_theme_map.items()
            if str(sector).strip() in bear and str(topic).strip()
        }
        sentiment = str(result.get('sentiment', '中性')).strip()
        if sentiment not in ('偏多', '偏空', '中性'):
            sentiment = '中性'

        print(
            f"  📈 利多族群: {bull} | 📉 利空族群: {bear} | "
            f"利多主題: {bull_theme_map} | 利空主題: {bear_theme_map} | "
            f"利多重點: {bull_reasons} | 利空重點: {bear_reasons} | 情緒: {sentiment}"
        )
        return {
            "bull_sectors": bull,
            "bear_sectors": bear,
            "bull_reasons": bull_reasons,
            "bear_reasons": bear_reasons,
            "bull_theme_map": bull_theme_map,
            "bear_theme_map": bear_theme_map,
            "sectors": bull,       # 向後相容別名
            "sentiment": sentiment,
        }

    except Exception as e:
        print(f"  ⚠️ 新聞族群萃取失敗: {e}")
        return default


def get_stock_news_mentions(stock_ids: list, deadline_monotonic: float | None = None) -> dict:
    """個股層級新聞偵測（Yahoo 奇摩股市 RSS + Gemini 情緒判斷）

    只對「已通過策略篩選的候選股」呼叫，控制 API 成本。
    每支股票撈最新 5 則 RSS，送 Gemini 判斷整體情緒。

    Args:
        stock_ids: 候選股票代號列表（如 ['2330', '2317']）

    Returns:
        dict: {
            "2330": {"score": 1, "reason": "法說會超預期"},
            "2317": {"score": -1, "reason": "接單下滑警訊"},
        }
        score: 1=正面, 0=中性/無資料, -1=負面
        信心度 < 0.7 的結果不回傳（避免誤判）
    """
    if not Config.GEMINI_API_KEY or not stock_ids:
        return {}

    guard_state = get_stock_news_guard_snapshot()
    if guard_state['breaker_open']:
        print(
            '  ⚠️ 個股新聞 breaker 開啟中，略過本輪請求 '
            f"({guard_state['cooldown_remaining_seconds']:.1f}s)"
        )
        return {}

    if deadline_monotonic is not None and _resolve_stock_news_timeout(deadline_monotonic) <= 0:
        print('  ⚠️ 個股新聞逾時預算已耗盡，略過本輪請求')
        return {}

    results = {}
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        mcp_context = build_mcp_prompt_context()
    except Exception as exc:
        print(f'  ⚠️ 個股新聞上下文取得失敗: {exc}')
        mcp_context = '- MCP 上下文暫不可用'

    service_unavailable_errors = 0
    failure_threshold = _stock_news_failure_threshold()

    for sid in stock_ids[:8]:  # 最多處理 8 支，保留配額給其他操作
        call_timeout_seconds = _resolve_stock_news_timeout(deadline_monotonic)
        if deadline_monotonic is not None and call_timeout_seconds <= 0:
            print('    ⚠️ 個股新聞逾時，略過剩餘個股新聞判讀')
            break

        try:
            # Yahoo 奇摩股市 RSS（個股新聞）
            rss_url = f"https://tw.stock.yahoo.com/rss?s={sid}"
            headlines = []
            try:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries[:5]:
                    title = _clean_html(entry.get('title', ''))
                    if title and len(title) > 5:
                        headlines.append(title)
            except Exception:
                pass

            if not headlines:
                continue  # 無新聞，略過

            headline_text = '\n'.join(f'- {h}' for h in headlines)
            prompt = f"""你是台股分析師。今天是 {today}，針對股票代號 {sid} 的以下最新新聞標題：

以下是由 MCP-backed tools 取得的市場與財務上下文，請先納入判讀：

{mcp_context}

{headline_text}

請判斷這些新聞對 {sid} 股價的短期影響（1-5天內），並回傳 JSON：
{{"score": 1, "reason": "法說會營收強勁", "confidence": 0.85}}

規則：
- score: 1=正面利多, 0=中性/影響不明, -1=負面利空
- reason: 10字以內，說明主要原因
- confidence: 0-1，研判信心程度
- 只回傳 JSON，不要其他文字"""

            client = _build_stock_news_client(call_timeout_seconds)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=120,
                ),
            )
            raw = resp.text.strip()
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

            item = json.loads(raw)
            score = item.get('score', 0)
            confidence = float(item.get('confidence', 0))
            reason = item.get('reason', '')

            _record_stock_news_success()

            if confidence >= 0.7 and score != 0:
                results[str(sid)] = {"score": score, "reason": reason}

        except Exception as e:
            status_code = _extract_error_status_code(e)
            if _is_timeout_error(e):
                _record_stock_news_failure(
                    'timeout',
                    timeout_seconds=call_timeout_seconds,
                )
                print(
                    f'    ⚠️ {sid} 個股新聞偵測逾時 '
                    f'({call_timeout_seconds:.2f}s)，略過剩餘請求'
                )
                break

            failure_state = _record_stock_news_failure(
                str(e),
                status_code=status_code,
                timeout_seconds=call_timeout_seconds,
            )
            if status_code == 429:
                print(f"    ⚠️ {sid} 個股新聞偵測收到 429 Quota，提前結束本輪請求")
                break
            if status_code in (502, 503, 504):
                service_unavailable_errors += 1
                print(
                    f'    ⚠️ {sid} 個股新聞偵測收到 {status_code} 異常 '
                    f'({service_unavailable_errors}/{failure_threshold})'
                )
                if results or failure_state['breaker_open']:
                    break
                continue
            print(f"    ⚠️ {sid} 個股新聞偵測失敗: {e}")
            if failure_state['breaker_open']:
                print('    ⚠️ 個股新聞 breaker 已開啟，停止後續新聞判讀')
                break

    if results:
        pos = [k for k, v in results.items() if v['score'] > 0]
        neg = [k for k, v in results.items() if v['score'] < 0]
        print(f"  📰 個股新聞: 利多 {pos} | 利空 {neg}")

    return results


# ==========================================
# 獨立執行
# ==========================================

if __name__ == "__main__":
    print("=" * 50)
    print("📰 早晨新聞摘要測試")
    print("=" * 50)
    result = get_morning_news_summary()
    print(result)
