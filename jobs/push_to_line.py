"""
Line 推播主程式（早晚雙模式）
============================================
用法：
    python jobs/push_to_line.py                  # 預設: evening（原始行為）
    python jobs/push_to_line.py --time morning   # 早晨大局觀（新聞摘要 + 隨機策略精選）
    python jobs/push_to_line.py --time evening   # 晚間選股策劃（全策略推薦）
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import argparse
import pandas as pd
from datetime import datetime
from sqlalchemy import text
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    TextMessage, FlexMessage, FlexBubble, FlexBox, FlexText, FlexCarousel,
    FlexSeparator, BroadcastRequest
)
from config import Config
from core.db_helper import (
    format_market_fallback_notice,
    get_actual_latest_date,
    get_db_engine,
    get_daily_recommendations,
    get_market_trend,
    get_recommendations_with_market_fallback,
    get_stock_data,
    get_latest_trade_date,
    normalize_date_str,
    record_pipeline_step_finish,
    record_pipeline_step_start,
)
from core.line_message_builder import build_news_summary_bubble
from core.strategy_manager import StrategyManager

# ==========================================
# 策略顯示名稱對照表
# ==========================================
STRATEGY_DISPLAY_NAMES = {
    'v31_hybrid': '🔹 均衡型 (V31)',
    'v33_low_vol': '🛡️ 穩健型 (V33)',
    'v34_turbo': '🚀 飆股型 (V34)',
    'v35_innovation': '🧪 經營效益 (V35)',
    'v36_chip_momentum': '📊 籌碼動能 (V36)',
    'v37_mean_reversion': '🔄 均值回歸 (V37)',
    'v38_value_dividend': '💰 品質價值低波動 (V38)',
}


def get_pipeline_baseline_date() -> str | None:
    """取得推播共用的資料基準日。"""
    return normalize_date_str(get_actual_latest_date() or get_latest_trade_date())


def _current_push_request_date() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def _resolve_push_strategy_rows(strategy_name: str, requested_date: str, limit: int) -> tuple[pd.DataFrame, dict]:
    rows, meta = get_recommendations_with_market_fallback(
        date_str=requested_date,
        strategy=strategy_name,
        limit=limit,
        max_fallback_age_days=Config.RECOMMENDATION_FALLBACK_MAX_AGE_DAYS,
    )
    return rows, meta


def _build_push_resolution_notes(strategy_notes: list[tuple[str, str]]) -> str:
    if not strategy_notes:
        return ''
    return '；'.join(f'{label}: {note}' for label, note in strategy_notes[:2] if note)


def get_market_status(engine, date_str):
    """判斷市場紅綠燈"""
    trend = get_market_trend(date_str)
    df, _ = get_stock_data(Config.MARKET_SYMBOL, date_str)
    if df.empty:
        return "⚪ 資料不足", 0

    data = df.iloc[0]
    ma60 = data.get('ma60')
    close = data.get('close_price')

    if ma60 is None or close is None or ma60 == 0:
        return "⚪ 資料不足", 0

    bias = (close - ma60) / ma60 * 100

    if trend == 'BULL':
        return "🔴 多頭 (進攻)", bias
    elif bias < -8:
        return "🟢 恐慌 (避險)", bias
    else:
        return "🟡 空頭 (觀望)", bias


DEFAULT_FEATURED_STRATEGY = 'v36_chip_momentum'
SCHEDULED_PUSH_BUBBLE_SIZE = 'mega'
MORNING_NEWS_FALLBACK = '目前暫無今晨摘要，先以前一交易日盤後資訊持續追蹤。'
MORNING_PICKS_EMPTY_STATE = '今日尚無新的精選標的，先觀察既有市場訊號。'
MORNING_BASELINE_NOTE = '若今日新資料尚未落地，內容引用最近交易日快照。'


def _resolve_pipeline_run_context(step_name: str) -> tuple[str, str, str]:
    pipeline_name = str(os.getenv('STOCK_PIPELINE_NAME') or '').strip() or 'manual'
    run_date = normalize_date_str(os.getenv('STOCK_PIPELINE_RUN_DATE')) or datetime.now().strftime('%Y-%m-%d')
    return pipeline_name, step_name, run_date


def _normalize_news_summary(news_summary: str | None, fallback_text: str) -> str:
    """標準化新聞摘要，避免空字串導致卡片內容缺失。"""
    normalized = str(news_summary or '').strip()
    return normalized or fallback_text


def _build_stock_items(picks: list) -> list[FlexBox]:
    """建立精選股票列表元件。"""
    stock_items: list[FlexBox] = []
    for i, (_strategy_label, stock_id, price, ai_score) in enumerate(picks, 1):
        score_text = f"{ai_score:.0%}" if ai_score else 'N/A'
        stock_items.append(
            FlexBox(
                layout='horizontal',
                margin='sm',
                contents=[
                    FlexText(text=f'{i}.', size='sm', color='#888888',
                             flex=0, min_width='20px'),
                    FlexText(text=stock_id, weight='bold', size='md',
                             color='#ffffff', flex=1),
                    FlexText(text=f'${price:.2f}', size='sm',
                             color='#4ecca3', flex=1, align='center'),
                    FlexText(text=score_text, size='sm',
                             color='#e94560', flex=1, align='end'),
                ]
            )
        )
    return stock_items


def _build_morning_overview_bubble(date_str: str, market_status: str) -> FlexBubble:
    """建構早晨市場概況卡。"""
    return FlexBubble(
        size=SCHEDULED_PUSH_BUBBLE_SIZE,
        header=FlexBox(
            layout='vertical',
            background_color='#1a1a2e',
            padding_all='16px',
            contents=[
                FlexText(text='🌅 StockAI 早報', weight='bold',
                         size='lg', color='#e0e0e0'),
                FlexText(text=f'📅 {date_str}', size='xs',
                         color='#aaaaaa', margin='sm'),
            ]
        ),
        body=FlexBox(
            layout='vertical',
            background_color='#16213e',
            padding_all='16px',
            contents=[
                FlexText(text='📊 市場概況', weight='bold',
                         size='md', color='#4dd0e1'),
                FlexText(text=market_status or '⚪ 資料不足',
                         size='sm', color='#d0f0ff', wrap=True, margin='md'),
                FlexSeparator(margin='lg', color='#333333'),
                FlexText(text='🗂️ 資料基準', weight='bold',
                         size='md', color='#ffd54f', margin='lg'),
                FlexText(text=f'{date_str} 收盤後可用快照',
                         size='sm', color='#f6e9b2', wrap=True, margin='md'),
                FlexText(text=MORNING_BASELINE_NOTE,
                         size='xs', color='#b9c0c8', wrap=True, margin='lg'),
            ]
        ),
    )


def _build_morning_picks_bubble(picks: list, market_status: str,
                                date_str: str, picks_title: str,
                                picks_notice: str = '') -> FlexBubble:
    """建構早晨精選五股卡，無資料時維持空狀態卡片。"""
    stock_items = _build_stock_items(picks)
    body_contents = [
        FlexBox(
            layout='horizontal',
            margin='sm',
            contents=[
                FlexText(text='#', size='xs', color='#555555',
                         flex=0, min_width='20px'),
                FlexText(text='股票', size='xs', color='#555555', flex=1),
                FlexText(text='現價', size='xs', color='#555555',
                         flex=1, align='center'),
                FlexText(text='AI信心', size='xs', color='#555555',
                         flex=1, align='end'),
            ]
        ),
    ]

    if stock_items:
        body_contents.extend(stock_items)
    else:
        body_contents.append(
            FlexText(text=MORNING_PICKS_EMPTY_STATE,
                     size='sm', color='#d7f6da', wrap=True, margin='lg')
        )

    body_contents.extend([
        FlexSeparator(margin='lg', color='#333333'),
        FlexText(text='💡 以上僅供參考，請嚴格執行停損停利',
                 size='xxs', color='#666666', margin='md'),
    ])

    return FlexBubble(
        size=SCHEDULED_PUSH_BUBBLE_SIZE,
        header=FlexBox(
            layout='vertical',
            background_color='#1a1a2e',
            padding_all='16px',
            contents=[
                FlexText(text='🎯 今日精選五股', weight='bold',
                         size='lg', color='#e0e0e0'),
                FlexText(text=f'📅 {date_str}  {market_status or "⚪ 資料不足"}',
                         size='xs', color='#aaaaaa', margin='sm'),
                FlexText(text=picks_title, size='xs', color='#cccccc', margin='sm'),
                *([
                    FlexText(text=picks_notice, size='xxs', color='#f6e9b2', margin='sm', wrap=True),
                ] if picks_notice else []),
            ]
        ),
        body=FlexBox(
            layout='vertical',
            background_color='#16213e',
            padding_all='16px',
            contents=body_contents,
        ),
    )


def _pick_featured_stocks(engine, requested_date: str, strategy_names: list[str] | None = None,
                          n: int = 5) -> tuple[list, str, str]:
    """從啟用策略中選出 ai_score 最高的 N 檔股票（多樣化配置）

    多樣性規則：ETF (00開頭) 最多 2 檔，其餘為個股，確保分散。

    Returns:
        (picks, title)
        picks: [(strategy_display, stock_id, close_price, ai_score), ...]
        title: 用於 Flex 顯示的精選標題
    """
    MAX_ETF = 2  # ETF 最多佔 2 檔
    candidate_names = []
    seen_names = set()
    for name in (strategy_names or [DEFAULT_FEATURED_STRATEGY]):
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        candidate_names.append(name)

    rows = []
    resolution_notes: list[tuple[str, str]] = []
    for strategy_name in candidate_names:
        display = STRATEGY_DISPLAY_NAMES.get(strategy_name, strategy_name)
        df, meta = _resolve_push_strategy_rows(strategy_name, requested_date, n * 3)
        notice = format_market_fallback_notice(meta, display)
        if notice:
            resolution_notes.append((display, notice))
        if df.empty:
            continue
        for _, row in df.iterrows():
            rows.append((
                display,
                str(row['stock_id']).strip(),
                float(row['close_price']),
                float(row['ai_score']) if pd.notna(row.get('ai_score')) else 0.0,
            ))

    if not rows:
        return [], STRATEGY_DISPLAY_NAMES.get(candidate_names[0], candidate_names[0]) if candidate_names else '精選策略', _build_push_resolution_notes(resolution_notes)

    rows.sort(key=lambda item: item[3] if item[3] else 0, reverse=True)

    deduped_rows = []
    seen_stocks = set()
    for row in rows:
        sid = row[1]
        if sid in seen_stocks:
            continue
        seen_stocks.add(sid)
        deduped_rows.append(row)

    # 分類：個股 vs ETF
    stocks = []  # 4碼個股 (1xxx-9xxx)
    etfs = []    # 00開頭 ETF/債券ETF
    for row in deduped_rows:
        sid = str(row[1]).strip()
        if sid.startswith('00'):
            etfs.append(row)
        else:
            stocks.append(row)

    # 組合：個股優先，ETF 最多 MAX_ETF 檔
    picks = []
    etf_count = 0
    stock_count = 0
    min_stocks = n - MAX_ETF  # 個股至少要佔 n - MAX_ETF 檔

    # 先填滿個股最低數量
    for row in stocks:
        if stock_count >= min_stocks:
            break
        picks.append(row)
        stock_count += 1

    # 再從剩餘候選（個股 + ETF 混合）依 ai_score 填滿
    remaining = [r for r in deduped_rows if r not in picks]
    for row in remaining:
        if len(picks) >= n:
            break
        sid = str(row[1]).strip()
        if sid.startswith('00'):
            if etf_count >= MAX_ETF:
                continue
            etf_count += 1
        picks.append(row)

    # 按 ai_score 降序排列
    picks.sort(key=lambda r: r[3] if r[3] else 0, reverse=True)

    if len(candidate_names) == 1:
        title = STRATEGY_DISPLAY_NAMES.get(candidate_names[0], candidate_names[0])
    else:
        title = '多策略精選'

    return [(row[0], row[1], row[2], row[3]) for row in picks[:n]], title, _build_push_resolution_notes(resolution_notes)


def _build_morning_flex(news_summary: str, picks: list,
                        market_status: str, date_str: str,
                        picks_title: str, picks_notice: str = '') -> FlexMessage:
    """建構早晨大局觀 Flex Message（今日精選五股）

    Args:
        picks: list of (strategy_label, stock_id, price, ai_score)
    """
    full_news_summary = _normalize_news_summary(news_summary, MORNING_NEWS_FALLBACK)
    overview_bubble = _build_morning_overview_bubble(date_str, market_status)

    news_bubble = build_news_summary_bubble(
        news_summary=full_news_summary,
        date_str=date_str,
        title='🌅 早安總經摘要',
    )

    picks_bubble = _build_morning_picks_bubble(picks, market_status, date_str, picks_title, picks_notice)
    return FlexMessage(
        alt_text=f'🌅 StockAI 早報 {date_str}',
        contents=FlexCarousel(contents=[overview_bubble, news_bubble, picks_bubble]),
    )


def _build_evening_flex(news_summary: str, picks: list,
                        date_str: str, picks_title: str,
                        market_status: str, market_bias: float,
                        sentiment_summary: str,
                        strategy_summaries: list[str],
                        picks_notice: str = '') -> FlexMessage:
    """建構晚間整合 Flex Message（市場 + 消息面 + 策略摘要 + 精選五股）

    Args:
        news_summary: AI 新聞摘要
        picks: list of (strategy_label, stock_id, price, ai_score)
    """
    full_news = (news_summary or '').strip() or '盤後新聞摘要暫無資料'

    compact_sentiment_lines = [line.strip() for line in str(sentiment_summary or '').splitlines() if line.strip()]
    compact_sentiment_lines = compact_sentiment_lines[:3] or ['消息面暫無明顯利多利空摘要']
    preview_sentiment = '\n'.join(compact_sentiment_lines)

    strategy_preview = '\n'.join(strategy_summaries[:8]).strip()
    if len(strategy_preview) > 420:
        strategy_preview = strategy_preview[:417] + '...'

    stock_items = []
    for i, (strategy_label, stock_id, price, ai_score) in enumerate(picks, 1):
        score_text = f"{ai_score:.0%}" if ai_score else "N/A"
        stock_items.append(
            FlexBox(
                layout="horizontal",
                margin="sm",
                contents=[
                    FlexText(text=f"{i}.", size="sm", color="#888888",
                             flex=0, min_width="20px"),
                    FlexText(text=stock_id, weight="bold", size="md",
                             color="#ffffff", flex=1),
                    FlexText(text=f"${price:.2f}", size="sm",
                             color="#4ecca3", flex=1, align="center"),
                    FlexText(text=score_text, size="sm",
                             color="#e94560", flex=1, align="end"),
                ]
            )
        )

    overview_bubble = FlexBubble(
        size=SCHEDULED_PUSH_BUBBLE_SIZE,
        header=FlexBox(
            layout="vertical",
            background_color="#1a1a2e",
            padding_all="16px",
            contents=[
                FlexText(text="🌙 StockAI 明日關注", weight="bold",
                         size="lg", color="#e0e0e0"),
                FlexText(text=f"📅 {date_str}  {market_status}", size="xs",
                         color="#aaaaaa", margin="sm"),
            ]
        ),
        body=FlexBox(
            layout="vertical",
            background_color="#16213e",
            padding_all="16px",
            contents=[
                FlexText(text="📊 市場概況", weight="bold",
                         size="md", color="#4dd0e1"),
                FlexText(text=f"{market_status}｜大盤乖離 {market_bias:.2f}%",
                         size="sm", color="#d0f0ff", wrap=True, margin="md"),
                FlexSeparator(margin="lg", color="#333333"),
                FlexText(text="📰 消息面摘要", weight="bold",
                         size="md", color="#ffd54f", margin="lg"),
                FlexText(text=preview_sentiment, size="sm", color="#f6e9b2",
                         wrap=True, margin="md"),
                FlexText(text="📋 策略摘要", weight="bold",
                         size="md", color="#81c784", margin="lg"),
                FlexText(text=strategy_preview or '今日無策略摘要',
                         size="sm", color="#d7f6da", wrap=True, margin="md"),
            ]
        ),
    )

    news_bubble = build_news_summary_bubble(
        news_summary=full_news,
        date_str=date_str,
        title='🌙 盤後新聞摘要',
    )

    picks_bubble = FlexBubble(
        size=SCHEDULED_PUSH_BUBBLE_SIZE,
        header=FlexBox(
            layout="vertical",
            background_color="#1a1a2e",
            padding_all="16px",
            contents=[
                FlexText(text="🎯 明日精選五股", weight="bold",
                         size="lg", color="#e0e0e0"),
                FlexText(text=picks_title, size="xs",
                         color="#aaaaaa", margin="sm"),
                *([
                    FlexText(text=picks_notice, size="xxs",
                             color="#f6e9b2", margin="sm", wrap=True),
                ] if picks_notice else []),
            ]
        ),
        body=FlexBox(
            layout="vertical",
            background_color="#16213e",
            padding_all="16px",
            contents=[
                FlexBox(
                    layout="horizontal",
                    margin="md",
                    contents=[
                        FlexText(text="#", size="xs", color="#555555",
                                 flex=0, min_width="20px"),
                        FlexText(text="股票", size="xs", color="#555555", flex=1),
                        FlexText(text="現價", size="xs", color="#555555",
                                 flex=1, align="center"),
                        FlexText(text="AI信心", size="xs", color="#555555",
                                 flex=1, align="end"),
                    ]
                ),
                *stock_items,
                FlexSeparator(margin="lg", color="#333333"),
                FlexText(text="💡 明日開盤前請再次確認技術面與籌碼面",
                         size="xxs", color="#666666", margin="md"),
            ]
        ),
    )

    carousel = FlexCarousel(contents=[overview_bubble, news_bubble, picks_bubble])
    return FlexMessage(
        alt_text=f"🌙 StockAI 明日關注 {picks_title} {date_str}",
        contents=carousel,
    )


# ==========================================
# Morning 模式
# ==========================================

def run_morning(dry_run: bool = False):
    """早晨大局觀：市場概況 + 新聞摘要 + 精選五股。"""
    print("🌅 早晨大局觀模式啟動...")

    engine = get_db_engine()
    requested_date = _current_push_request_date()
    market_date = get_pipeline_baseline_date()
    if not market_date:
        print("❌ 資料庫無資料")
        return

    # 1. 新聞摘要
    print("📰 取得新聞摘要...")
    try:
        if dry_run:
            raise RuntimeError("[DRY-RUN] news external call skipped")
        from core.news_agent import get_morning_news_summary
        news_summary = get_morning_news_summary()
    except Exception as e:
        news_summary = f"⚠️ 新聞取得失敗: {e}"
    news_summary = _normalize_news_summary(news_summary, MORNING_NEWS_FALLBACK)
    print(f"  ✓ 新聞摘要完成 ({len(news_summary)} 字)")

    # 2. 市場狀態
    market_status, _ = get_market_status(engine, market_date)

    manager = StrategyManager()
    active_strategy_names = manager.get_active_strategy_names()

    # 3. 依目前啟用策略產生精選五股
    picks, picks_title, picks_notice = _pick_featured_stocks(
        engine,
        requested_date,
        strategy_names=active_strategy_names,
        n=5,
    )
    if not picks:
        print("⚠️ 啟用策略無推薦股票，改用精選空狀態卡片")
        picks_title = '多策略精選｜暫無新標的'
    else:
        for strategy_label, stock_id, price, ai_score in picks:
            print(f"  ✓ 精選: {stock_id} (${price:.2f}) from {strategy_label}")

    # 4. 建構 Flex Message
    flex = _build_morning_flex(
        news_summary, picks, market_status, requested_date, picks_title, picks_notice
    )

    # 5. 推播
    _broadcast_flex(flex, dry_run=dry_run)
    return {
        'target_time': 'morning',
        'message_type': 'flex',
        'recommendation_count': len(picks),
        'would_push': not dry_run,
    }


# ==========================================
# Evening 模式（原始邏輯 + 新增 Flex 精選）
# ==========================================

def run_evening(dry_run: bool = False):
    """晚間選股策劃：全策略推薦 + 明日關注精選"""
    print("🌙 晚間選股策劃模式啟動...")
    engine = get_db_engine()

    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    strategy_names = manager.get_active_strategy_names()

    print(f"📊 啟用策略數量: {len(strategies)}")
    print(f"📋 策略列表: {', '.join(strategy_names)}")

    # 2. 取得最新日期
    requested_date = _current_push_request_date()
    date_str = get_pipeline_baseline_date()
    if not date_str:
        print("❌ 資料庫無資料")
        return
    print(f"📅 資料日期: {date_str}")

    # 3. 判斷市場狀態
    status, bias = get_market_status(engine, date_str)

    # 4. 組合訊息標頭
    msg = f"📅 【StockAI 日報】 {date_str}\n"
    msg += f"--------------------------\n"
    msg += f"🚦 市場狀態: {status}\n"
    msg += f"📊 大盤乖離: {bias:.2f}%\n"
    sentiment_lines = []

    # 新增：消息面情緒摘要
    try:
        from core.db_helper import get_news_sentiment
        ns = get_news_sentiment(date_str)
        sent_emoji = {'偏多': '🟢', '偏空': '🔴', '中性': '🟡'}.get(ns['sentiment'], '🟡')
        bull_theme_map = ns.get('bull_theme_map', {})
        bear_theme_map = ns.get('bear_theme_map', {})
        sector_separator = '、'
        msg += f"--------------------------\n"
        msg += f"{sent_emoji} 消息面: {ns['sentiment']}"
        summary_line = f"{sent_emoji} 消息面: {ns['sentiment']}"
        if ns['bull_sectors']:
            bull_text = sector_separator.join(ns['bull_sectors'])
            msg += f" ｜ 利多: {bull_text}"
            summary_line += f" ｜ 利多: {bull_text}"
        if ns['bear_sectors']:
            bear_text = sector_separator.join(ns['bear_sectors'])
            msg += f" ｜ 利空: {bear_text}"
            summary_line += f" ｜ 利空: {bear_text}"
        msg += "\n"
        sentiment_lines.append(summary_line)
        if bull_theme_map:
            msg += "🟢 利多主題:\n"
            for sector, topic in list(bull_theme_map.items())[:3]:
                msg += f"• {sector}: {topic}\n"
                sentiment_lines.append(f"利多主題｜{sector}: {topic}")
        if bear_theme_map:
            msg += "🔴 利空主題:\n"
            for sector, topic in list(bear_theme_map.items())[:3]:
                msg += f"• {sector}: {topic}\n"
                sentiment_lines.append(f"利空主題｜{sector}: {topic}")
        if ns.get('bull_reasons'):
            msg += "📈 利多重點:\n"
            for item in ns['bull_reasons'][:3]:
                msg += f"• {item}\n"
                sentiment_lines.append(f"利多重點｜{item}")
        if ns.get('bear_reasons'):
            msg += "📉 利空重點:\n"
            for item in ns['bear_reasons'][:3]:
                msg += f"• {item}\n"
                sentiment_lines.append(f"利空重點｜{item}")
    except Exception:
        pass
    msg += f"--------------------------\n"
    if not sentiment_lines:
        sentiment_lines.append('消息面暫無明顯利多利空摘要')

    # 5. 遍歷所有策略，撈取推薦結果
    has_picks = False
    strategy_summary_lines = []

    for strategy in strategies:
        strategy_label = STRATEGY_DISPLAY_NAMES.get(strategy.name, strategy.display_name)

        picks_df, meta = _resolve_push_strategy_rows(strategy.name, requested_date, 5)
        strategy_notice = format_market_fallback_notice(meta, strategy_label)
        picks = list(picks_df.itertuples(index=False, name=None)) if not picks_df.empty else []

        if picks:
            has_picks = True
            msg += f"\n== {strategy_label} ==\n"
            if strategy_notice:
                msg += f"{strategy_notice}\n"
            summary_parts = []

            for _, row in picks_df.iterrows():
                stock_id = row.get('stock_id')
                price = float(row.get('close_price', 0) or 0)
                ai_score = row.get('ai_score')
                rd_expense = row.get('rd_expense')
                revenue = row.get('revenue')
                revenue_yoy = row.get('revenue_yoy')

                msg += f"🎫 {stock_id} (${price:.2f})"

                if ai_score:
                    msg += f" | 🤖 {ai_score:.0%}"

                if strategy.name == 'v34_turbo' and revenue_yoy is not None:
                    msg += f" | 🔥 YoY {revenue_yoy:.1f}%"

                if strategy.name == 'v35_innovation' and rd_expense and revenue and revenue > 0:
                    rd_ratio = (rd_expense / revenue) * 100
                    msg += f" | 🧪 R&D {rd_ratio:.1f}%"

                msg += "\n"
                if len(summary_parts) < 3:
                    part = f"{stock_id} ${price:.2f}"
                    if ai_score:
                        part += f" {ai_score:.0%}"
                    if strategy.name == 'v34_turbo' and revenue_yoy is not None:
                        part += f" YoY {revenue_yoy:.1f}%"
                    if strategy.name == 'v35_innovation' and rd_expense and revenue and revenue > 0:
                        part += f" R&D {rd_ratio:.1f}%"
                    summary_parts.append(part)

            msg += f"🎯 目標: {strategy.target_return}% / ⏰ {strategy.look_ahead_days}天\n"
            strategy_summary_lines.append(
                f"{strategy_label}｜{', '.join(summary_parts)}｜目標 {strategy.target_return}% / {strategy.look_ahead_days}天"
            )
        elif strategy_notice:
            strategy_summary_lines.append(f"{strategy_label}｜{strategy_notice}")

    if not has_picks:
        msg += f"\n🐢 今日無符合條件標的\n"
        msg += "☕ 建議空手觀望"
        strategy_summary_lines.append('今日無符合條件標的，建議空手觀望')
    else:
        msg += f"\n--------------------------\n"
        msg += "💡 嚴格執行停損停利"

    print("\n" + "=" * 40)
    print("📨 推播訊息預覽:")
    print("=" * 40)
    print(msg)
    print("=" * 40)

    # 6. 爬取盤後新聞摘要
    print("📰 取得盤後新聞摘要...")
    try:
        if dry_run:
            raise RuntimeError("[DRY-RUN] news external call skipped")
        from core.news_agent import get_morning_news_summary
        evening_news = get_morning_news_summary()
    except Exception as e:
        evening_news = f"⚠️ 新聞取得失敗: {e}"
    print(f"  ✓ 新聞摘要完成 ({len(evening_news)} 字)")

    # 7. 推播整合 Flex（日報內容 + 新聞 + 啟用策略五股）
    picks, picks_title, picks_notice = _pick_featured_stocks(
        engine,
        requested_date,
        strategy_names=strategy_names,
        n=5,
    )
    if picks:
        flex = _build_evening_flex(
            evening_news,
            picks,
            requested_date,
            picks_title,
            status,
            bias,
            '\n'.join(sentiment_lines),
            strategy_summary_lines,
            picks_notice,
        )
        _broadcast_flex(flex, dry_run=dry_run)
        stock_ids = [p[1] for p in picks]
        print(f"✅ 明日關注精選已推播 ({picks_title}): {', '.join(stock_ids)}")
    else:
        print("⚠️ 無法建立 evening Flex，因為啟用策略沒有推薦標的")


# ==========================================
# 推播工具
# ==========================================

    return _push_preview_summary('evening', picks, dry_run)


def _push_preview_summary(target_time: str, picks: list, dry_run: bool) -> dict:
    return {
        'target_time': target_time,
        'message_type': 'flex',
        'recommendation_count': len(picks or []),
        'would_push': not dry_run,
    }


def _broadcast_text(msg: str, dry_run: bool = False):
    if dry_run:
        print("[DRY-RUN] LINE push skipped")
        return
    """推播純文字訊息"""
    try:
        configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.broadcast(BroadcastRequest(
                messages=[TextMessage(text=msg)]
            ))
        print("✅ 文字推播已發送！")
    except Exception as e:
        print(f"❌ 文字推播失敗: {e}")


def _broadcast_flex(flex_msg: FlexMessage, dry_run: bool = False):
    if dry_run:
        print("[DRY-RUN] LINE push skipped")
        return
    """推播 Flex Message"""
    try:
        configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.broadcast(BroadcastRequest(
                messages=[flex_msg]
            ))
        print("✅ Flex 推播已發送！")
    except Exception as e:
        print(f"❌ Flex 推播失敗: {e}")


# ==========================================
# 主程式入口
# ==========================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="StockAI Line push")
    parser.add_argument(
        '--time', choices=['morning', 'evening'], default='evening',
        help='push mode: morning or evening'
    )
    parser.add_argument('--dry-run', action='store_true', help='Preview the LINE payload without pushing or status writes.')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    pipeline_name, step_name, run_date = _resolve_pipeline_run_context('push_to_line')
    if not args.dry_run:
        record_pipeline_step_start(
            pipeline_name=pipeline_name,
            step_name=step_name,
            run_date=run_date,
        )

    print(f"StockAI push start (mode: {args.time})")
    print(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        if args.time == 'morning':
            summary = run_morning(dry_run=args.dry_run)
        else:
            summary = run_evening(dry_run=args.dry_run)

        trade_date = get_pipeline_baseline_date()
        status = 'success' if trade_date else 'failed'
        if args.dry_run:
            summary = summary or _push_preview_summary(args.time, [], True)
            print(
                "[DRY-RUN] summary: "
                f"target_time: {summary['target_time']}, "
                f"message_type: {summary['message_type']}, "
                f"recommendation_count: {summary['recommendation_count']}, "
                "would_push: false"
            )
        else:
            record_pipeline_step_finish(
                pipeline_name=pipeline_name,
                step_name=step_name,
                run_date=run_date,
                status=status,
                source_date=run_date,
                trade_date=trade_date,
                error_summary=None if trade_date else 'no pipeline baseline date available for push payload',
            )
            print('Line push completed.')
        return 0
    except Exception as exc:
        if not args.dry_run:
            record_pipeline_step_finish(
                pipeline_name=pipeline_name,
                step_name=step_name,
                run_date=run_date,
                status='failed',
                source_date=run_date,
                trade_date=get_pipeline_baseline_date(),
                error_summary=str(exc),
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
