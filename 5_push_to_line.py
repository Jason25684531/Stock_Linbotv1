"""
Line 推播主程式（早晚雙模式）
============================================
用法：
  python 5_push_to_line.py                  # 預設: evening（原始行為）
  python 5_push_to_line.py --time morning   # 早晨大局觀（新聞摘要 + 隨機策略精選）
  python 5_push_to_line.py --time evening   # 晚間選股策劃（全策略推薦）
"""
import sys

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
    TextMessage, FlexMessage, FlexBubble, FlexBox, FlexText,
    FlexSeparator, BroadcastRequest
)
from config import Config
from tool.db_helper import get_db_engine, get_market_trend, get_stock_data
from tool.strategy_manager import StrategyManager

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
    'v38_value_dividend': '💰 高殖利率 (V38)',
}


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


def _get_latest_date(engine) -> str:
    """取得資料庫最新交易日期"""
    with engine.connect() as conn:
        latest = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
    if not latest:
        return None
    return latest.strftime("%Y-%m-%d") if hasattr(latest, 'strftime') else str(latest)


PICK_STRATEGY = 'v36_chip_momentum'  # 早晨/晚間精選統一使用 V36 籌碼動能


def _pick_v36_stocks(engine, date_str: str, n: int = 5) -> list:
    """從 V36 籌碼動能策略選出 ai_score 最高的 N 檔股票（多樣化配置）

    多樣性規則：ETF (00開頭) 最多 2 檔，其餘為個股，確保分散。

    Returns:
        list of tuples: [(strategy_display, stock_id, close_price, ai_score), ...]
    """
    display = STRATEGY_DISPLAY_NAMES.get(PICK_STRATEGY, PICK_STRATEGY)
    MAX_ETF = 2  # ETF 最多佔 2 檔

    with engine.connect() as conn:
        # 多拉一些候選，方便做多樣性篩選
        rows = conn.execute(text("""
            SELECT stock_id, close_price, ai_score
            FROM daily_recommendations
            WHERE trade_date = :date AND strategy = :strategy
            ORDER BY ai_score DESC
            LIMIT :limit
        """), {"date": date_str, "strategy": PICK_STRATEGY, "limit": n * 3}).fetchall()

    # 分類：個股 vs ETF
    stocks = []  # 4碼個股 (1xxx-9xxx)
    etfs = []    # 00開頭 ETF/債券ETF
    for row in rows:
        sid = str(row[0]).strip()
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
    remaining = [r for r in rows if r not in picks]
    for row in remaining:
        if len(picks) >= n:
            break
        sid = str(row[0]).strip()
        if sid.startswith('00'):
            if etf_count >= MAX_ETF:
                continue
            etf_count += 1
        picks.append(row)

    # 按 ai_score 降序排列
    picks.sort(key=lambda r: r[2] if r[2] else 0, reverse=True)

    return [(display, row[0], row[1], row[2]) for row in picks[:n]]


def _build_morning_flex(news_summary: str, picks: list,
                        market_status: str, date_str: str) -> FlexMessage:
    """建構早晨大局觀 Flex Message（今日精選五股）

    Args:
        picks: list of (strategy_label, stock_id, price, ai_score)
    """
    # 建立精選股票列表元件
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

    bubble = FlexBubble(
        size="giga",
        header=FlexBox(
            layout="vertical",
            background_color="#1a1a2e",
            padding_all="16px",
            contents=[
                FlexText(text="🌅 早安！StockAI 大局觀", weight="bold",
                         size="lg", color="#e0e0e0"),
                FlexText(text=f"📅 {date_str}  {market_status}",
                         size="xs", color="#aaaaaa", margin="sm"),
            ]
        ),
        body=FlexBox(
            layout="vertical",
            background_color="#16213e",
            padding_all="16px",
            contents=[
                FlexText(text="📰 國際新聞摘要", weight="bold",
                         size="md", color="#e94560"),
                FlexText(text=news_summary, size="sm", color="#cccccc",
                         wrap=True, margin="md"),
                FlexSeparator(margin="lg", color="#333333"),
                FlexText(text="🎯 今日精選五股 — 📊 籌碼動能 (V36)", weight="bold",
                         size="md", color="#e94560", margin="lg"),
                FlexBox(
                    layout="horizontal",
                    margin="sm",
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
                FlexText(text="💡 以上僅供參考，請嚴格執行停損停利",
                         size="xxs", color="#666666", margin="md"),
            ]
        ),
    )
    return FlexMessage(alt_text=f"🌅 StockAI 早安大局觀 {date_str}", contents=bubble)


def _build_evening_flex(news_summary: str, picks: list,
                        date_str: str) -> FlexMessage:
    """建構晚間明日關注 Flex Message（新聞摘要 + V36 籌碼動能五股）

    Args:
        news_summary: AI 新聞摘要
        picks: list of (strategy_label, stock_id, price, ai_score)
    """
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

    bubble = FlexBubble(
        size="giga",
        header=FlexBox(
            layout="vertical",
            background_color="#1a1a2e",
            padding_all="16px",
            contents=[
                FlexText(text="🌙 StockAI 明日關注", weight="bold",
                         size="lg", color="#e0e0e0"),
                FlexText(text=f"📅 {date_str}", size="xs",
                         color="#aaaaaa", margin="sm"),
            ]
        ),
        body=FlexBox(
            layout="vertical",
            background_color="#16213e",
            padding_all="16px",
            contents=[
                FlexText(text="📰 盤後新聞摘要", weight="bold",
                         size="md", color="#e94560"),
                FlexText(text=news_summary, size="sm", color="#cccccc",
                         wrap=True, margin="md"),
                FlexSeparator(margin="lg", color="#333333"),
                FlexText(text="🎯 明日精選五股 — 📊 籌碼動能 (V36)", weight="bold",
                         size="md", color="#e94560", margin="lg"),
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
    return FlexMessage(alt_text=f"🌙 StockAI 明日關注 V36 精選 {date_str}", contents=bubble)


# ==========================================
# Morning 模式
# ==========================================

def run_morning():
    """早晨大局觀：新聞摘要 + 隨機策略精選一股"""
    print("🌅 早晨大局觀模式啟動...")

    engine = get_db_engine()
    date_str = _get_latest_date(engine)
    if not date_str:
        print("❌ 資料庫無資料")
        return

    # 1. 新聞摘要
    print("📰 取得新聞摘要...")
    try:
        from tool.news_agent import get_morning_news_summary
        news_summary = get_morning_news_summary()
    except Exception as e:
        news_summary = f"⚠️ 新聞取得失敗: {e}"
    print(f"  ✓ 新聞摘要完成 ({len(news_summary)} 字)")

    # 2. 市場狀態
    market_status, _ = get_market_status(engine, date_str)

    # 3. V36 籌碼動能精選五股
    picks = _pick_v36_stocks(engine, date_str, n=5)
    if not picks:
        print("⚠️ 無推薦股票，僅推播新聞摘要")
        msg = f"🌅 【StockAI 早安大局觀】{date_str}\n{market_status}\n\n📰 國際新聞摘要：\n{news_summary}\n\n🐢 今日無精選標的"
        _broadcast_text(msg)
        return

    for strategy_label, stock_id, price, ai_score in picks:
        print(f"  ✓ 精選: {stock_id} (${price:.2f}) from {strategy_label}")

    # 4. 建構 Flex Message
    flex = _build_morning_flex(
        news_summary, picks, market_status, date_str
    )

    # 5. 推播
    _broadcast_flex(flex)


# ==========================================
# Evening 模式（原始邏輯 + 新增 Flex 精選）
# ==========================================

def run_evening():
    """晚間選股策劃：全策略推薦 + 明日關注精選"""
    print("🌙 晚間選股策劃模式啟動...")
    configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
    engine = get_db_engine()

    # 1. 初始化策略管理器
    manager = StrategyManager()
    strategies = manager.get_active_strategies()
    strategy_names = manager.get_active_strategy_names()

    print(f"📊 啟用策略數量: {len(strategies)}")
    print(f"📋 策略列表: {', '.join(strategy_names)}")

    # 2. 取得最新日期
    date_str = _get_latest_date(engine)
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

    # 消息面情緒摘要（利多 + 利空條列）
    try:
        from tool.db_helper import get_news_sentiment
        ns = get_news_sentiment(date_str)
        sent_emoji = {'偏多': '🟢', '偏空': '🔴', '中性': '🟡'}.get(ns['sentiment'], '🟡')
        msg += f"--------------------------\n"
        msg += f"{sent_emoji} 消息面情緒: {ns['sentiment']}\n"
        if ns['bull_sectors']:
            msg += f"  📈 利多族群: {'、'.join(ns['bull_sectors'])}\n"
        else:
            msg += f"  📈 利多族群: 無明顯利多\n"
        if ns['bear_sectors']:
            msg += f"  📉 利空族群: {'、'.join(ns['bear_sectors'])}\n"
        else:
            msg += f"  📉 利空族群: 無明顯利空\n"
    except Exception:
        pass
    msg += f"--------------------------\n"

    # 5. 遍歷所有策略，撈取推薦結果
    has_picks = False

    for strategy in strategies:
        strategy_label = STRATEGY_DISPLAY_NAMES.get(strategy.name, strategy.display_name)

        with engine.connect() as conn:
            if strategy.name == 'v35_innovation':
                result = conn.execute(text("""
                    SELECT
                        dr.stock_id,
                        dr.close_price,
                        dr.ai_score,
                        dr.rsi,
                        dr.volume,
                        fs.rd_expense,
                        fs.revenue,
                        NULL as revenue_yoy
                    FROM daily_recommendations dr
                    LEFT JOIN (
                        SELECT stock_id, rd_expense, revenue
                        FROM financial_statements fs1
                        WHERE fs1.year >= 1911
                          AND (year * 10 + quarter) = (
                            SELECT MAX(year * 10 + quarter)
                            FROM financial_statements fs2
                            WHERE fs2.stock_id = fs1.stock_id
                              AND fs2.year >= 1911
                        )
                    ) fs ON dr.stock_id = fs.stock_id
                    WHERE dr.trade_date = :date AND dr.strategy = :strategy
                    ORDER BY dr.ai_score DESC
                    LIMIT 5
                """), {"date": date_str, "strategy": strategy.name})
            elif strategy.name == 'v34_turbo':
                result = conn.execute(text("""
                    SELECT
                        dr.stock_id,
                        dr.close_price,
                        dr.ai_score,
                        dr.rsi,
                        dr.volume,
                        NULL as rd_expense,
                        NULL as revenue,
                        mr.revenue_yoy
                    FROM daily_recommendations dr
                    LEFT JOIN (
                        SELECT stock_id, revenue_yoy
                        FROM monthly_revenue mr1
                        WHERE (year * 100 + month) = (
                            SELECT MAX(year * 100 + month)
                            FROM monthly_revenue mr2
                            WHERE mr2.stock_id = mr1.stock_id
                        )
                    ) mr ON dr.stock_id = mr.stock_id
                    WHERE dr.trade_date = :date AND dr.strategy = :strategy
                    ORDER BY dr.ai_score DESC
                    LIMIT 5
                """), {"date": date_str, "strategy": strategy.name})
            else:
                result = conn.execute(text("""
                    SELECT stock_id, close_price, ai_score, rsi, volume,
                           NULL as rd_expense, NULL as revenue, NULL as revenue_yoy
                    FROM daily_recommendations
                    WHERE trade_date = :date AND strategy = :strategy
                    ORDER BY ai_score DESC
                    LIMIT 5
                """), {"date": date_str, "strategy": strategy.name})

            picks = result.fetchall()

        if picks:
            has_picks = True
            msg += f"\n== {strategy_label} ==\n"

            for p in picks:
                stock_id = p[0]
                price = p[1]
                ai_score = p[2]
                rd_expense = p[5] if len(p) > 5 else None
                revenue = p[6] if len(p) > 6 else None
                revenue_yoy = p[7] if len(p) > 7 else None

                msg += f"🎫 {stock_id} (${price:.2f})"

                if ai_score:
                    msg += f" | 🤖 {ai_score:.0%}"

                if strategy.name == 'v34_turbo' and revenue_yoy is not None:
                    msg += f" | 🔥 YoY {revenue_yoy:.1f}%"

                if strategy.name == 'v35_innovation' and rd_expense and revenue and revenue > 0:
                    rd_ratio = (rd_expense / revenue) * 100
                    msg += f" | 🧪 R&D {rd_ratio:.1f}%"

                msg += "\n"

            msg += f"🎯 目標: {strategy.target_return}% / ⏰ {strategy.look_ahead_days}天\n"

    if not has_picks:
        msg += f"\n🐢 今日無符合條件標的\n"
        msg += "☕ 建議空手觀望"
    else:
        msg += f"\n--------------------------\n"
        msg += "💡 嚴格執行停損停利"

    print("\n" + "=" * 40)
    print("📨 推播訊息預覽:")
    print("=" * 40)
    print(msg)
    print("=" * 40)

    # 6. 推播全策略日報（文字）
    _broadcast_text(msg)

    # 7. 爬取盤後新聞摘要
    print("📰 取得盤後新聞摘要...")
    try:
        from tool.news_agent import get_morning_news_summary
        evening_news = get_morning_news_summary()
    except Exception as e:
        evening_news = f"⚠️ 新聞取得失敗: {e}"
    print(f"  ✓ 新聞摘要完成 ({len(evening_news)} 字)")

    # 8. 額外推播明日關注精選（新聞 + V36 五股 Flex）
    picks = _pick_v36_stocks(engine, date_str, n=5)
    if picks:
        flex = _build_evening_flex(evening_news, picks, date_str)
        _broadcast_flex(flex)
        stock_ids = [p[1] for p in picks]
        print(f"✅ 明日關注精選已推播 (V36): {', '.join(stock_ids)}")


# ==========================================
# 推播工具
# ==========================================

def _broadcast_text(msg: str):
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


def _broadcast_flex(flex_msg: FlexMessage):
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

def main():
    parser = argparse.ArgumentParser(description="StockAI Line 推播")
    parser.add_argument(
        '--time', choices=['morning', 'evening'], default='evening',
        help='推播模式: morning=早晨大局觀, evening=晚間選股策劃 (預設: evening)'
    )
    args = parser.parse_args()

    print(f"🚀 StockAI 推播啟動 (模式: {args.time})")
    print(f"📅 執行時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.time == 'morning':
        run_morning()
    else:
        run_evening()

    print("🎉 推播流程完成！")


if __name__ == "__main__":
    main()
