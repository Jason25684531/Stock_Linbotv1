"""
股票診斷報告工具 (Stock Diagnosis Report Helper)
============================================
提供個股 AI 健康診斷書查詢功能

功能：
1. 從 daily_market_data 取得最新價格與 MA 趨勢
2. 從 daily_recommendations 取得最新 AI Score
3. 從 financial_statements / monthly_revenue 取得基本面指標
4. 整合為結構化字典 + 格式化 Line 訊息

作者: Stock AI Bot Team
最後更新: 2026-02-12
"""

from typing import Optional, Dict, Any
from sqlalchemy import text
from core.db_helper import get_db_engine


def get_stock_report(stock_id: str) -> Optional[Dict[str, Any]]:
    """取得個股 AI 健康診斷報告

    查詢三大面向：
    1. 技術面：收盤價、MA20/MA60 趨勢、RSI
    2. AI 面：最新 AI Score（從 daily_recommendations 或回測推薦）
    3. 基本面：OpMg（營業利益率）、Revenue YoY（營收年增率）

    Args:
        stock_id: 股票代碼（如 '2330'）

    Returns:
        dict: 診斷報告，包含以下鍵值：
            - stock_id, close_price, ma20, ma60, ma_trend
            - rsi, ai_score, strategy_name
            - op_margin, revenue_yoy
        若查無資料返回 None
    """
    engine = get_db_engine()
    report: Dict[str, Any] = {
        'stock_id': stock_id,
        'close_price': None,
        'ma20': None,
        'ma60': None,
        'ma_trend': '無資料',
        'rsi': None,
        'ai_score': None,
        'strategy_name': None,
        'op_margin': None,
        'revenue_yoy': None,
    }

    try:
        with engine.connect() as conn:
            # ============================================
            # 1. 技術面：最新價格 + MA + RSI
            # ============================================
            row = conn.execute(
                text("""
                    SELECT close_price, ma20, ma60, rsi, volume,
                           trade_date
                    FROM daily_market_data
                    WHERE stock_id = :sid
                    ORDER BY trade_date DESC
                    LIMIT 1
                """),
                {'sid': stock_id}
            ).mappings().fetchone()

            if row is None:
                return None  # 完全查無此股

            report['close_price'] = float(row['close_price']) if row['close_price'] else None
            report['ma20'] = float(row['ma20']) if row.get('ma20') else None
            report['ma60'] = float(row['ma60']) if row.get('ma60') else None
            report['rsi'] = float(row['rsi']) if row.get('rsi') else None
            report['trade_date'] = str(row['trade_date'])

            # 判斷 MA 趨勢
            cp = report['close_price']
            ma20 = report['ma20']
            ma60 = report['ma60']
            if cp and ma20 and ma60:
                if cp > ma20 > ma60:
                    report['ma_trend'] = '多頭排列 📈'
                elif cp < ma20 < ma60:
                    report['ma_trend'] = '空頭排列 📉'
                else:
                    report['ma_trend'] = '盤整 ⚪'
            elif cp and ma60:
                report['ma_trend'] = '多頭 📈' if cp > ma60 else '空頭 📉'

            # ============================================
            # 2. AI 面：最新 AI Score
            # ============================================
            try:
                ai_row = conn.execute(
                    text("""
                        SELECT ai_score, strategy
                        FROM daily_recommendations
                        WHERE stock_id = :sid
                        ORDER BY trade_date DESC
                        LIMIT 1
                    """),
                    {'sid': stock_id}
                ).mappings().fetchone()

                if ai_row:
                    report['ai_score'] = float(ai_row['ai_score']) if ai_row.get('ai_score') else None
                    report['strategy_name'] = ai_row.get('strategy', 'V31')
            except Exception:
                # daily_recommendations 表可能不存在
                pass

            # ============================================
            # 3. 基本面：營業利益率 + 營收年增率
            # ============================================
            try:
                fin_row = conn.execute(
                    text("""
                        SELECT op_profit_margin
                        FROM financial_statements
                        WHERE stock_id = :sid
                        ORDER BY year DESC, quarter DESC
                        LIMIT 1
                    """),
                    {'sid': stock_id}
                ).mappings().fetchone()

                if fin_row and fin_row.get('op_profit_margin') is not None:
                    report['op_margin'] = float(fin_row['op_profit_margin'])
            except Exception:
                pass

            try:
                rev_row = conn.execute(
                    text("""
                        SELECT revenue_yoy
                        FROM monthly_revenue
                        WHERE stock_id = :sid
                        ORDER BY year DESC, month DESC
                        LIMIT 1
                    """),
                    {'sid': stock_id}
                ).mappings().fetchone()

                if rev_row and rev_row.get('revenue_yoy') is not None:
                    report['revenue_yoy'] = float(rev_row['revenue_yoy'])
            except Exception:
                pass

    except Exception as e:
        print(f"❌ get_stock_report 失敗 ({stock_id}): {e}")
        return None

    return report


def format_stock_diagnosis(report: Dict[str, Any]) -> str:
    """將診斷報告格式化為 Line Bot 回覆訊息

    Args:
        report: get_stock_report() 返回的字典

    Returns:
        str: 格式化的診斷訊息
    """
    if report is None:
        return "❌ 查無此股票資料"

    sid = report['stock_id']
    price = report.get('close_price')
    ma_trend = report.get('ma_trend', '無資料')
    rsi = report.get('rsi')
    ai_score = report.get('ai_score')
    strategy = report.get('strategy_name', 'V35')
    op_margin = report.get('op_margin')
    rev_yoy = report.get('revenue_yoy')

    # 價格顯示
    price_str = f"${price:.2f}" if price else "N/A"

    # AI 信心顯示
    if ai_score is not None:
        ai_pct = int(ai_score * 100)
        ai_str = f"{ai_pct}% ({strategy}策略)"
    else:
        ai_str = "尚無評分"

    # 基本面顯示
    fundamentals = []
    if op_margin is not None:
        fundamentals.append(f"OpMg {op_margin*100:.1f}%")
    else:
        fundamentals.append("OpMg N/A")

    if rev_yoy is not None:
        sign = '+' if rev_yoy > 0 else ''
        fundamentals.append(f"YoY {sign}{rev_yoy:.1f}%")
    else:
        fundamentals.append("YoY N/A")

    fund_str = " | ".join(fundamentals)

    # RSI 顯示
    rsi_str = f"{rsi:.1f}" if rsi else "N/A"

    # 綜合評語
    comment = _generate_comment(report)

    msg = (
        f"📊 {sid} 診斷報告\n"
        f"-----------------------\n"
        f"💰 股價: {price_str} (MA趨勢: {ma_trend})\n"
        f"📊 RSI: {rsi_str}\n"
        f"🤖 AI信心: {ai_str}\n"
        f"💎 基本面: {fund_str}\n"
        f"-----------------------\n"
        f"💡 綜合評語: {comment}"
    )
    return msg


def _generate_comment(report: Dict[str, Any]) -> str:
    """根據綜合指標生成評語"""
    score = 0
    reasons = []

    # MA 趨勢加分
    ma_trend = report.get('ma_trend', '')
    if '多頭' in ma_trend:
        score += 2
        reasons.append('趨勢偏多')
    elif '空頭' in ma_trend:
        score -= 2
        reasons.append('趨勢偏空')

    # AI 信心加分
    ai = report.get('ai_score')
    if ai is not None:
        if ai >= 0.7:
            score += 2
            reasons.append('AI高信心')
        elif ai >= 0.5:
            score += 1
        elif ai < 0.4:
            score -= 1
            reasons.append('AI低信心')

    # 基本面加分
    op = report.get('op_margin')
    if op is not None and op > 0.10:
        score += 1
        reasons.append('高利潤率')

    rev = report.get('revenue_yoy')
    if rev is not None and rev > 10:
        score += 1
        reasons.append('營收成長')
    elif rev is not None and rev < -10:
        score -= 1
        reasons.append('營收衰退')

    # RSI
    rsi = report.get('rsi')
    if rsi is not None:
        if rsi > 70:
            score -= 1
            reasons.append('RSI過熱')
        elif rsi < 30:
            reasons.append('RSI超賣')

    # 生成評語
    if score >= 4:
        return "⭐ 多指標共振，強勢股！" + (f" ({', '.join(reasons)})" if reasons else "")
    elif score >= 2:
        return "👍 偏正面，可留意" + (f" ({', '.join(reasons)})" if reasons else "")
    elif score >= 0:
        return "😐 中性觀望" + (f" ({', '.join(reasons)})" if reasons else "")
    else:
        return "⚠️ 偏弱勢，謹慎操作" + (f" ({', '.join(reasons)})" if reasons else "")
