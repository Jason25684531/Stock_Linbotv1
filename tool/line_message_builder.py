"""
Line Bot Flex Message 建構器
============================================
將個股診斷資料轉換為 Line Flex Message 卡片

功能：
1. create_stock_flex_message(): 建構個股診斷 Flex 卡片
2. create_recommendation_carousel(): 建構推薦清單 Flex Carousel（多張卡片）
3. create_backtest_summary_flex(): 建構回測績效摘要 Flex 卡片
4. create_holdings_flex(): 建構 AI 持股狀態 Flex 卡片
5. 內部 helper: _color_by_value(), _format_pct() 等

🔄 V36 Upgrade:
- 新增 Flex Carousel 推薦清單（最多 10 張卡片）
- 新增回測摘要 & 持股狀態 Flex 卡片
- 支援 V31~V38 策略顏色標籤
- 使用 Line Bot SDK v3 FlexMessage / FlexCarousel

作者: Stock AI Bot Team
最後更新: 2026-02-15
"""

from typing import Dict, Any, Optional, List
from linebot.v3.messaging import (
    FlexMessage,
    FlexBubble,
    FlexCarousel,
    FlexBox,
    FlexText,
    FlexButton,
    FlexSeparator,
    FlexIcon,
    FlexFiller,
    URIAction,
)


# ============================================
# 🎨 Helper 函式
# ============================================

def _color_by_value(value: float, positive_color: str = '#1DB446',
                    negative_color: str = '#DD2222',
                    neutral_color: str = '#888888') -> str:
    """根據數值正負回傳顏色代碼"""
    if value is None:
        return neutral_color
    if value > 0:
        return positive_color
    elif value < 0:
        return negative_color
    return neutral_color


def _format_pct(value: Optional[float], suffix: str = '%', na: str = 'N/A') -> str:
    """將數值格式化為百分比字串"""
    if value is None:
        return na
    return f'{value:.1f}{suffix}'


def _ai_score_label(score: Optional[float]) -> str:
    """AI 分數轉等級文字"""
    if score is None:
        return '尚無評分'
    pct = int(score * 100)
    if pct >= 70:
        return f'{pct}% 🔥'
    elif pct >= 50:
        return f'{pct}% 👍'
    else:
        return f'{pct}% ⚠️'


def _trend_badge(trend: str) -> str:
    """趨勢轉為標籤文字"""
    if '多頭' in trend:
        return '📈 多頭'
    elif '空頭' in trend:
        return '📉 空頭'
    return '⚪ 盤整'


# ============================================
# 📲 Flex Message 建構器
# ============================================

def create_stock_flex_message(stock_id: str, data: Dict[str, Any]) -> FlexMessage:
    """建構個股 AI 健康診斷 Flex Message 卡片

    Args:
        stock_id: 股票代碼 (如 '2330')
        data: get_stock_report() 回傳的 dict, 包含:
            - close_price, ma_trend, rsi
            - ai_score, strategy_name
            - op_margin, revenue_yoy

    Returns:
        FlexMessage: Line Flex Message 物件，可直接用於 reply_message
    """
    price = data.get('close_price')
    ma_trend = data.get('ma_trend', '無資料')
    rsi = data.get('rsi')
    ai_score = data.get('ai_score')
    strategy = data.get('strategy_name', 'V35')
    op_margin = data.get('op_margin')
    revenue_yoy = data.get('revenue_yoy')

    # 價格顏色（收盤 > MA20 → 綠, 否則紅）
    ma20 = data.get('ma20')
    price_color = '#1DB446' if (price and ma20 and price > ma20) else '#DD2222'

    # ============================================
    # Header: 股票代號 + 趨勢標籤
    # ============================================
    header = FlexBox(
        layout='horizontal',
        contents=[
            FlexText(
                text=f'📊 {stock_id}',
                weight='bold',
                size='xl',
                color='#FFFFFF',
            ),
            FlexText(
                text=_trend_badge(ma_trend),
                size='sm',
                color='#FFFFFFCC',
                align='end',
            ),
        ],
        padding_all='16px',
        background_color='#1A1A2E',
    )

    # ============================================
    # Hero: 大字體股價顯示
    # ============================================
    price_str = f'${price:.2f}' if price else 'N/A'
    hero = FlexBox(
        layout='vertical',
        contents=[
            FlexText(
                text=price_str,
                weight='bold',
                size='3xl',
                color=price_color,
                align='center',
            ),
            FlexText(
                text=f'MA趨勢: {ma_trend}',
                size='xs',
                color='#AAAAAA',
                align='center',
                margin='sm',
            ),
        ],
        padding_all='16px',
        background_color='#16213E',
    )

    # ============================================
    # Body: AI Score + 基本面指標
    # ============================================
    rows = []

    # AI Score 行
    ai_label = _ai_score_label(ai_score)
    rows.append(_make_data_row('🤖 AI 信心', ai_label,
                               _color_by_value(ai_score - 0.5 if ai_score else None)))

    rows.append(FlexSeparator(margin='md'))

    # RSI 行
    rsi_str = f'{rsi:.1f}' if rsi else 'N/A'
    rsi_color = '#DD2222' if (rsi and rsi > 70) else ('#1DB446' if (rsi and rsi < 30) else '#FFFFFF')
    rows.append(_make_data_row('📊 RSI', rsi_str, rsi_color))

    rows.append(FlexSeparator(margin='md'))

    # 營業利益率
    if op_margin is not None:
        op_str = f'{op_margin * 100:.1f}%'
    else:
        op_str = 'N/A'
    rows.append(_make_data_row('💼 營業利益率', op_str,
                               _color_by_value(op_margin)))

    rows.append(FlexSeparator(margin='md'))

    # 營收年增率
    if revenue_yoy is not None:
        sign = '+' if revenue_yoy > 0 else ''
        yoy_str = f'{sign}{revenue_yoy:.1f}%'
    else:
        yoy_str = 'N/A'
    rows.append(_make_data_row('📈 營收 YoY', yoy_str,
                               _color_by_value(revenue_yoy)))

    rows.append(FlexSeparator(margin='md'))

    # 策略名稱
    rows.append(_make_data_row('🎯 策略', strategy or 'V35', '#AAAAAA'))

    body = FlexBox(
        layout='vertical',
        contents=rows,
        padding_all='16px',
        background_color='#0F3460',
    )

    # ============================================
    # Footer: Goodinfo 外部連結按鈕
    # ============================================
    footer = FlexBox(
        layout='vertical',
        contents=[
            FlexButton(
                action=URIAction(
                    label='Goodinfo 詳細資料',
                    uri=f'https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={stock_id}',
                ),
                style='primary',
                color='#E94560',
            ),
        ],
        padding_all='12px',
        background_color='#1A1A2E',
    )

    # ============================================
    # 組裝 Bubble
    # ============================================
    bubble = FlexBubble(
        header=header,
        hero=hero,
        body=body,
        footer=footer,
    )

    return FlexMessage(
        alt_text=f'{stock_id} 診斷報告 - {price_str}',
        contents=bubble,
    )


def _make_data_row(label: str, value: str, value_color: str = '#FFFFFF') -> FlexBox:
    """建構資料行 (Label + Value)"""
    return FlexBox(
        layout='horizontal',
        contents=[
            FlexText(
                text=label,
                size='sm',
                color='#AAAAAA',
                flex=3,
            ),
            FlexText(
                text=value,
                size='sm',
                color=value_color,
                align='end',
                weight='bold',
                flex=2,
            ),
        ],
        margin='md',
    )


# ============================================
# 🎨 策略配色表
# ============================================

STRATEGY_COLORS: Dict[str, Dict[str, str]] = {
    'v31': {'bg': '#1A1A2E', 'accent': '#4FC3F7', 'label': '🔷 V31 混合'},
    'v33': {'bg': '#1A2E1A', 'accent': '#81C784', 'label': '🟢 V33 低波'},
    'v34': {'bg': '#2E1A1A', 'accent': '#E57373', 'label': '🔴 V34 渦輪'},
    'v35': {'bg': '#2E2E1A', 'accent': '#FFD54F', 'label': '🟡 V35 創新'},
    'v36': {'bg': '#1A2E2E', 'accent': '#4DD0E1', 'label': '📊 V36 籌碼'},
    'v37': {'bg': '#2E1A2E', 'accent': '#CE93D8', 'label': '🔄 V37 均值回歸'},
    'v38': {'bg': '#1A1A1A', 'accent': '#FFB74D', 'label': '💰 V38 高殖利'},
}

_MEDAL = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']


def _get_strategy_style(strategy_key: str) -> Dict[str, str]:
    """取得策略配色，未知策略使用預設灰"""
    key = strategy_key.lower().replace('_', '').replace('-', '')
    for k, v in STRATEGY_COLORS.items():
        if k in key:
            return v
    return {'bg': '#1A1A2E', 'accent': '#90A4AE', 'label': f'📋 {strategy_key}'}


# ============================================
# 🎰 Flex Carousel: 推薦清單
# ============================================

def create_recommendation_carousel(
    picks: List[Dict[str, Any]],
    strategy_name: str = 'V31',
    date_str: str = '',
) -> FlexMessage:
    """建構推薦股票 Flex Carousel（每檔一張卡片，最多 10 張）

    Args:
        picks: list of dict, 每筆至少包含:
            - stock_id: str (e.g. '2330')
            - close_price: float
            - ai_score: float (0-1)
            - rsi: float
            - volume: float (成交量)
            - stop_loss_price: float (停損價)
            - take_profit_price: float (停利價)
        strategy_name: 策略顯示名稱
        date_str: 日期字串

    Returns:
        FlexMessage: Carousel 包含最多 10 張 Bubble
    """
    style = _get_strategy_style(strategy_name)
    bubbles: List[FlexBubble] = []

    for i, pick in enumerate(picks[:10]):
        stock_id = str(pick.get('stock_id', '????'))
        sector = pick.get('sector', '')
        close = pick.get('close_price', 0)
        ai_score = pick.get('ai_score')
        rsi = pick.get('rsi')
        volume = pick.get('volume', 0)
        sl = pick.get('stop_loss_price', 0)
        tp = pick.get('take_profit_price', 0)
        medal = _MEDAL[i] if i < len(_MEDAL) else f'{i+1}.'

        ai_pct = int(ai_score * 100) if ai_score else 0
        ai_bar = '🟩' * (ai_pct // 20) + '⬜' * (5 - ai_pct // 20)

        # Header — 股號+族群放同一個 vertical box，策略標籤靠右
        title_text = f'{medal} {stock_id}'
        header = FlexBox(
            layout='vertical',
            contents=[
                FlexBox(
                    layout='horizontal',
                    contents=[
                        FlexText(text=title_text, weight='bold',
                                 size='xl', color='#FFFFFF', flex=3),
                        FlexText(text=style['label'], size='xxs',
                                 color=style['accent'], align='end',
                                 gravity='center', flex=0),
                    ],
                ),
            ] + ([
                FlexText(text=sector, size='xs',
                         color='#AAAAAA', margin='xs'),
            ] if sector else []),
            padding_all='14px',
            background_color=style['bg'],
        )

        # Price hero
        price_str = f'${close:.2f}' if close else 'N/A'
        hero = FlexBox(
            layout='vertical',
            contents=[
                FlexText(text=price_str, weight='bold', size='3xl',
                         color=style['accent'], align='center'),
                FlexText(text=f'📅 {date_str}' if date_str else '',
                         size='xxs', color='#999999', align='center',
                         margin='sm'),
            ],
            padding_all='12px',
            background_color='#16213E',
        )

        # Body rows
        rows = []
        rows.append(_make_data_row(
            '🤖 AI 信心',
            f'{ai_pct}分 {ai_bar}',
            style['accent'] if ai_pct >= 60 else '#FFFFFF',
        ))
        rows.append(FlexSeparator(margin='sm'))

        rsi_str = f'{rsi:.1f}' if rsi else 'N/A'
        rsi_color = '#DD2222' if (rsi and rsi > 70) else (
            '#1DB446' if (rsi and rsi < 30) else '#FFFFFF')
        rows.append(_make_data_row('📊 RSI', rsi_str, rsi_color))
        rows.append(FlexSeparator(margin='sm'))

        vol_str = f'{volume/10000:.0f}萬' if volume else 'N/A'
        rows.append(_make_data_row('📈 成交量', vol_str, '#FFFFFF'))
        rows.append(FlexSeparator(margin='sm'))

        sl_str = f'${sl:.2f}' if sl else '-'
        tp_str = f'${tp:.2f}' if tp else '-'
        rows.append(_make_data_row('🛡️ 停損', sl_str, '#E57373'))
        rows.append(FlexSeparator(margin='sm'))
        rows.append(_make_data_row('🎯 停利', tp_str, '#81C784'))

        body = FlexBox(
            layout='vertical',
            contents=rows,
            padding_all='14px',
            background_color='#0F3460',
        )

        # Footer with Goodinfo link
        footer = FlexBox(
            layout='vertical',
            contents=[
                FlexButton(
                    action=URIAction(
                        label='📋 Goodinfo 詳情',
                        uri=f'https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={stock_id}',
                    ),
                    style='primary',
                    color='#E94560',
                    height='sm',
                ),
            ],
            padding_all='10px',
            background_color=style['bg'],
        )

        bubbles.append(FlexBubble(
            size='kilo',
            header=header,
            hero=hero,
            body=body,
            footer=footer,
        ))

    carousel = FlexCarousel(contents=bubbles)
    return FlexMessage(
        alt_text=f'🎯 {strategy_name} 推薦 {len(picks)} 檔',
        contents=carousel,
    )


# ============================================
# 📊 Flex Bubble: 回測績效摘要
# ============================================

def create_backtest_summary_flex(
    metrics: Dict[str, Any],
    strategy_name: str = '',
) -> FlexMessage:
    """建構回測績效摘要 Flex Bubble

    Args:
        metrics: dict with keys like:
            - total_return: float (e.g. 0.25 = 25%)
            - max_drawdown: float (e.g. -0.08 = -8%)
            - sharpe_ratio: float
            - win_rate: float (e.g. 0.65 = 65%)
            - total_trades: int
            - period: str (e.g. '2025-01 ~ 2026-01')
        strategy_name: 策略名稱

    Returns:
        FlexMessage: 回測摘要 Bubble
    """
    style = _get_strategy_style(strategy_name)

    total_ret = metrics.get('total_return', 0)
    mdd = metrics.get('max_drawdown', 0)
    sharpe = metrics.get('sharpe_ratio', 0)
    win_rate = metrics.get('win_rate', 0)
    trades = metrics.get('total_trades', 0)
    period = metrics.get('period', '最近回測')

    ret_pct = total_ret * 100 if abs(total_ret) < 10 else total_ret
    mdd_pct = mdd * 100 if abs(mdd) < 10 else mdd
    wr_pct = win_rate * 100 if abs(win_rate) <= 1 else win_rate

    header = FlexBox(
        layout='vertical',
        contents=[
            FlexText(text='📊 回測績效報告', weight='bold',
                     size='lg', color='#FFFFFF'),
            FlexText(text=f'{style["label"]} | {period}',
                     size='xs', color='#AAAAAA', margin='sm'),
        ],
        padding_all='16px',
        background_color=style['bg'],
    )

    # Big return number
    ret_color = '#1DB446' if ret_pct >= 0 else '#DD2222'
    sign = '+' if ret_pct >= 0 else ''
    hero = FlexBox(
        layout='vertical',
        contents=[
            FlexText(text='總報酬率', size='xs', color='#AAAAAA',
                     align='center'),
            FlexText(text=f'{sign}{ret_pct:.1f}%', weight='bold',
                     size='3xl', color=ret_color, align='center'),
        ],
        padding_all='16px',
        background_color='#16213E',
    )

    rows = [
        _make_data_row('📉 最大回撤', f'{mdd_pct:.1f}%',
                       _color_by_value(mdd_pct)),
        FlexSeparator(margin='md'),
        _make_data_row('📐 夏普比率', f'{sharpe:.2f}',
                       _color_by_value(sharpe)),
        FlexSeparator(margin='md'),
        _make_data_row('🎯 勝率', f'{wr_pct:.1f}%',
                       '#1DB446' if wr_pct >= 50 else '#DD2222'),
        FlexSeparator(margin='md'),
        _make_data_row('📝 交易次數', str(trades), '#FFFFFF'),
    ]

    body = FlexBox(
        layout='vertical',
        contents=rows,
        padding_all='16px',
        background_color='#0F3460',
    )

    bubble = FlexBubble(
        header=header,
        hero=hero,
        body=body,
    )

    return FlexMessage(
        alt_text=f'📊 回測摘要 {strategy_name} {sign}{ret_pct:.1f}%',
        contents=bubble,
    )


# ============================================
# 💼 Flex Bubble: AI 持股狀態
# ============================================

def create_holdings_flex(
    holdings: List[Dict[str, Any]],
    strategy_name: str = '',
    date_str: str = '',
) -> FlexMessage:
    """建構 AI 持股狀態 Flex Bubble（單一 Bubble 列出所有持股）

    Args:
        holdings: list of dict, 每筆包含:
            - stock_id: str
            - entry_price: float
            - current_price: float (最新收盤)
            - pnl_pct: float (損益百分比, e.g. 0.05 = +5%)
            - hold_days: int (持有天數)
            - strategy: str (來源策略)
        strategy_name: 總覽策略名
        date_str: 日期字串

    Returns:
        FlexMessage: 持股狀態 Bubble, 空持股時顯示「目前無持股」
    """
    style = _get_strategy_style(strategy_name)

    header = FlexBox(
        layout='vertical',
        contents=[
            FlexText(text='💼 AI 模擬持股', weight='bold',
                     size='lg', color='#FFFFFF'),
            FlexText(text=f'📅 {date_str}' if date_str else '最新狀態',
                     size='xs', color='#AAAAAA', margin='sm'),
        ],
        padding_all='16px',
        background_color=style['bg'],
    )

    if not holdings:
        body = FlexBox(
            layout='vertical',
            contents=[
                FlexText(text='📭 目前無持股', size='md',
                         color='#AAAAAA', align='center',
                         margin='xl'),
                FlexText(text='輸入「推薦」取得今日選股',
                         size='xs', color='#666666', align='center',
                         margin='md'),
            ],
            padding_all='20px',
            background_color='#16213E',
        )
        bubble = FlexBubble(header=header, body=body)
        return FlexMessage(alt_text='💼 目前無持股', contents=bubble)

    # Build holding rows
    rows: List[Any] = []
    total_pnl = 0.0

    for h in holdings[:8]:  # Line Flex 最多 ~30 components
        sid = str(h.get('stock_id', '????'))
        entry = h.get('entry_price', 0)
        current = h.get('current_price', 0)
        pnl = h.get('pnl_pct', 0)
        days = h.get('hold_days', 0)
        strat = h.get('strategy', '')
        total_pnl += pnl

        pnl_pct_val = pnl * 100 if abs(pnl) <= 1 else pnl
        sign = '+' if pnl_pct_val >= 0 else ''
        pnl_color = '#1DB446' if pnl_pct_val >= 0 else '#DD2222'

        # Stock ID + strategy tag row
        rows.append(FlexBox(
            layout='horizontal',
            contents=[
                FlexText(text=f'📌 {sid}', weight='bold', size='md',
                         color='#FFFFFF', flex=3),
                FlexText(text=strat if strat else '-', size='xxs',
                         color='#AAAAAA', align='end', flex=2,
                         gravity='center'),
            ],
            margin='lg',
        ))

        # Price + P/L row
        rows.append(FlexBox(
            layout='horizontal',
            contents=[
                FlexText(text=f'入場 ${entry:.1f} → ${current:.1f}',
                         size='xs', color='#CCCCCC', flex=4),
                FlexText(text=f'{sign}{pnl_pct_val:.1f}%',
                         size='sm', color=pnl_color, align='end',
                         weight='bold', flex=2),
            ],
            margin='sm',
        ))

        # Days row
        rows.append(FlexText(
            text=f'持有 {days} 天',
            size='xxs', color='#888888', margin='xs',
        ))

        rows.append(FlexSeparator(margin='md'))

    # Total P/L summary
    avg_pnl = total_pnl / len(holdings) if holdings else 0
    avg_pct = avg_pnl * 100 if abs(avg_pnl) <= 1 else avg_pnl
    avg_sign = '+' if avg_pct >= 0 else ''
    rows.append(FlexBox(
        layout='horizontal',
        contents=[
            FlexText(text=f'💰 平均損益', weight='bold', size='sm',
                     color='#FFFFFF', flex=3),
            FlexText(text=f'{avg_sign}{avg_pct:.1f}%', weight='bold',
                     size='md',
                     color='#1DB446' if avg_pct >= 0 else '#DD2222',
                     align='end', flex=2),
        ],
        margin='lg',
    ))

    body = FlexBox(
        layout='vertical',
        contents=rows,
        padding_all='14px',
        background_color='#0F3460',
    )

    bubble = FlexBubble(header=header, body=body)
    return FlexMessage(
        alt_text=f'💼 持有 {len(holdings)} 檔 | 平均 {avg_sign}{avg_pct:.1f}%',
        contents=bubble,
    )
