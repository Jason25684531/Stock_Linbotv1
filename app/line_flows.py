"""LINE conversation flows: text/postback parsing, strategy menus, message builders."""

# -*- coding: utf-8 -*-
import random
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pandas as pd

import app as app_pkg
from config import Config
from core.db_helper import (
    get_backtest_summary_from_db,
    get_backtest_trades,
    get_recent_backtest_trades,
    safe_float,
    safe_int,
)
from core.line_message_builder import (
    build_backtest_reflection_flex,
    build_macro_summary_flex,
    build_strategy_prompt_flex,
    create_empty_state_flex,
)
from core.mcp_client import MCPClientError
from core.strategy import format_v31_recommendation
from linebot.v3.messaging import TextMessage as V3TextMessage


REPO_ROOT = Path(__file__).resolve().parents[1]


class _LineInteractionStateStore:
    """短生命週期的 LINE 對話狀態儲存，用於 Rich Menu 引導式流程。"""

    _TTL_SECONDS: float = 300.0

    def __init__(self) -> None:
        self._store: dict[str, tuple[dict[str, str], float]] = {}
        self._lock = threading.Lock()

    def get(self, source_id: str) -> dict[str, str] | None:
        source_key = str(source_id or '').strip()
        if not source_key:
            return None
        with self._lock:
            entry = self._store.get(source_key)
            if entry is None:
                return None
            payload, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[source_key]
                return None
            return dict(payload)

    def set(self, source_id: str, payload: dict[str, str]) -> None:
        source_key = str(source_id or '').strip()
        if not source_key or not payload:
            return
        expires_at = time.monotonic() + self._TTL_SECONDS
        with self._lock:
            self._store[source_key] = (dict(payload), expires_at)

    def clear(self, source_id: str) -> None:
        source_key = str(source_id or '').strip()
        if not source_key:
            return
        with self._lock:
            self._store.pop(source_key, None)


_line_interaction_state = _LineInteractionStateStore()


def _normalize_line_text(text: str) -> str:
    raw = text or ''
    normalized = unicodedata.normalize('NFKC', raw)
    normalized = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Cf')
    return normalized.strip()


def _compact_command_key(text: str) -> str:
    normalized = _normalize_line_text(text)
    compact = re.sub(r'[\s\-_/,，。．、:：;；!！?？\[\]()（）【】{}"\'\`]+', '', normalized)
    return compact.lower()


def _extract_line_source_id(source_or_event) -> str:
    source = getattr(source_or_event, 'source', source_or_event)
    for attr in ('user_id', 'group_id', 'room_id'):
        value = getattr(source, attr, None)
        if value:
            return str(value)
    return ''


def _parse_postback_payload(data: str) -> dict[str, str]:
    raw = str(data or '').strip()
    if not raw:
        return {}
    parsed = {
        key: (values[0].strip() if values else '')
        for key, values in parse_qs(raw, keep_blank_values=True).items()
    }
    if not parsed:
        return {'action': raw}
    if 'action' not in parsed:
        parsed['action'] = raw
    return parsed


def _get_strategy_display_name(strategy_key: str) -> str:
    strategy = app_pkg.strategy_manager.get_strategy(strategy_key)
    if strategy is None:
        return strategy_key
    return getattr(strategy, 'display_name', strategy_key)


def _get_strategy_payload_key(strategy_key: str, display_name: str = '') -> str:
    for candidate in (strategy_key, display_name):
        match = re.search(r'V\d+', str(candidate or ''), flags=re.IGNORECASE)
        if match:
            return match.group(0).lower()
    return str(strategy_key or '').strip()


def _normalize_strategy_request_key(strategy_value: str) -> str:
    raw = str(strategy_value or '').strip()
    if not raw:
        return ''

    lowered = raw.lower()
    for canonical, metadata in app_pkg.strategy_manager.STRATEGY_METADATA.items():
        if lowered == canonical or lowered in metadata.legacy_ids:
            return canonical
        if re.fullmatch(r'v\d+', lowered) and any(key.startswith(f'{lowered}_') for key in metadata.legacy_ids):
            return canonical
    strategy_keys = app_pkg.strategy_manager.list_strategies()
    for strategy_key in strategy_keys:
        if strategy_key.lower() == lowered:
            return strategy_key

    for strategy_key in strategy_keys:
        display_name = _get_strategy_display_name(strategy_key)
        aliases = {
            strategy_key.lower(),
            _get_strategy_payload_key(strategy_key, display_name).lower(),
        }
        display_match = re.search(r'V\d+', display_name, flags=re.IGNORECASE)
        if display_match:
            aliases.add(display_match.group(0).lower())
        if lowered in aliases:
            return strategy_key

    return raw


def _build_postback_empty_state(title: str, message: str, subtitle: str = ''):
    return create_empty_state_flex(
        title=title,
        message=message,
        date_str=app_pkg._current_line_date(),
        subtitle=subtitle,
    )


def _list_strategy_picker_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for strategy_key in app_pkg.strategy_manager.list_strategies():
        strategy = app_pkg.strategy_manager.get_strategy(strategy_key)
        if strategy is None:
            continue
        display_name = getattr(strategy, 'display_name', strategy_key)
        match = re.search(r'V\d+', display_name, flags=re.IGNORECASE)
        short_label = match.group(0).upper() if match else strategy_key.upper()
        options.append(
            {
                'key': strategy_key,
                'label': display_name,
                'short_label': short_label,
                'payload_key': _get_strategy_payload_key(strategy_key, display_name),
                'display_text': f'查看 {display_name}',
            }
        )
    return options


def _summarize_today_pick_status(strategy_keys: list[str], date_str: str) -> str:
    ready_labels: list[str] = []
    for strategy_key in strategy_keys:
        df = app_pkg.get_daily_recommendations(date_str=date_str, strategy=strategy_key, limit=1)
        if not df.empty:
            ready_labels.append(_get_strategy_display_name(strategy_key))

    if not ready_labels:
        return '今日無標的'
    if len(ready_labels) == 1:
        return f'有標的（{ready_labels[0]}）'
    return f'有標的（{ready_labels[0]} 等 {len(ready_labels)} 策略）'


def _load_backtest_summary_snapshot() -> dict | None:
    summary = get_backtest_summary_from_db()
    if summary is not None:
        return summary
    try:
        from core.viz_helper import get_backtest_summary

        return get_backtest_summary()
    except Exception as exc:
        print(f'⚠️ 讀取回測摘要 fallback 失敗: {exc}')
        return None


def _build_journal_reflection_snapshot() -> dict[str, object]:
    active_keys = app_pkg.strategy_manager.get_active_strategy_names()
    active_labels = [_get_strategy_display_name(key) for key in active_keys] or ['尚未啟用策略']
    date_str = app_pkg._resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
    today_pick_status = _summarize_today_pick_status(active_keys, date_str)

    try:
        recent_trades = get_recent_backtest_trades(limit=1) or []
    except Exception as exc:
        print(f'⚠️ 讀取最近交易失敗: {exc}')
        recent_trades = []

    latest_trade_summary = ''
    if recent_trades:
        latest_trade = recent_trades[0]
        stock_id = latest_trade.get('stock_id') or 'N/A'
        profit_pct = safe_float(latest_trade.get('profit_pct'))
        reason = str(latest_trade.get('reason') or '未記錄').strip()
        if profit_pct is None:
            latest_trade_summary = f'{stock_id}｜出場原因：{reason}'
        else:
            latest_trade_summary = f'{stock_id} {profit_pct:+.1f}%｜出場原因：{reason}'

    return {
        'active_keys': active_keys,
        'active_labels': active_labels,
        'date_str': date_str,
        'today_pick_status': today_pick_status,
        'summary': _load_backtest_summary_snapshot(),
        'latest_trade_summary': latest_trade_summary,
    }


def _is_quick_mode_cmd(text: str, version: str, style: str) -> bool:
    compact = _compact_command_key(text)
    aliases = {
        ('34', 'aggressive'): ['v34積極', '設定v34積極'],
        ('34', 'balanced'): ['v34平衡', '設定v34平衡'],
        ('34', 'loose'): ['v34寬鬆', '設定v34寬鬆', 'v34放寬'],
        ('34', 'conservative'): ['v34保守', '設定v34保守'],
        ('35', 'aggressive'): ['v35積極', '設定v35積極'],
        ('35', 'balanced'): ['v35平衡', '設定v35平衡'],
        ('35', 'loose'): ['v35寬鬆', '設定v35寬鬆', 'v35放寬'],
        ('35', 'conservative'): ['v35保守', '設定v35保守'],
    }
    return compact in aliases.get((version, style), [])


_STRATEGY_SWITCH_MAP = {
    'v31_hybrid': {
        'aliases': ['切換v30', 'v30策略', '切v30'],
        'display': 'V31 混合策略',
        'features': '• 均線多頭 + RSI 中性 + 量能放大\n• XGBoost AI 智慧排名\n• 經回測驗證\n',
    },
    'v33_low_vol': {
        'aliases': ['切換v33', 'v33策略', '切v33', '低波動'],
        'display': 'V33 低波動策略',
        'features': '• 波動率 NATR < 4%\n• 穩定成長優先\n• 適合保守型投資者\n',
    },
    'v34_turbo': {
        'aliases': ['切換v34', 'v34策略', '切v34', '飆股', '渦輪'],
        'display': 'V34 雙渦輪飆股策略',
        'features': '• 營收高成長 + 近60日高突破\n• 接近 60 日新高（價格突破）\n• 高風險高報酬，適合積極投資者\n',
    },
    'v35_innovation': {
        'aliases': ['切換v35', 'v35策略', '切v35', '創新', '經營效益'],
        'display': 'V35 經營效益策略',
        'features': '• 營業利益率 + 營收成長 + 多頭趨勢\n• 營收正成長 + 多頭趨勢\n• 中長線穩健，適合價值投資者\n',
    },
    'v36_chip_momentum': {
        'aliases': ['切換v36', 'v36策略', '切v36', '籌碼', '法人'],
        'display': 'V36 籌碼動能策略',
        'features': '• 法人連續買超 + 籌碼評分\n• 外資/投信同步追蹤\n• 跟隨主力動向，適合趨勢投資者\n',
    },
    'v37_mean_reversion': {
        'aliases': ['切換v37', 'v37策略', '切v37', '均值回歸', '反轉'],
        'display': 'V37 均值回歸策略',
        'features': '• KD 超賣回升 + BB 收斂\n• 量縮整理後的反彈行情\n• 短線反轉操作，持股 5-8 天\n',
    },
    'v38_value_dividend': {
        'aliases': ['切換v38', 'v38策略', '切v38', '高殖利', '價值股', '定存股'],
        'display': 'V38 品質價值低波動策略',
        'features': '• 高 EPS + 高營業利益率\n• 低波動穩健價值股\n• 類定存配置，適合保守投資者\n',
    },
}


_STRATEGY_ALIAS_INDEX = {}
for _key, _info in _STRATEGY_SWITCH_MAP.items():
    for _alias in _info['aliases']:
        _STRATEGY_ALIAS_INDEX[_alias] = _key


def _match_strategy_switch(text_lower: str):
    key = _STRATEGY_ALIAS_INDEX.get(text_lower)
    if key is None:
        return None
    info = _STRATEGY_SWITCH_MAP[key]
    return key, info['display'], info['features']


def _load_strategy_backtest_frame(strategy_key: str) -> tuple[pd.DataFrame, str]:
    normalized_key = _normalize_strategy_request_key(strategy_key)
    if not normalized_key:
        return pd.DataFrame(), ''

    try:
        trades = get_backtest_trades(strategy=normalized_key)
    except Exception as exc:
        print(f'⚠️ 讀取 DB 回測交易失敗 [{normalized_key}]: {exc}')
        trades = []

    if trades:
        return pd.DataFrame(trades), '資料來源: 回測資料庫'

    csv_path = REPO_ROOT / Config.BACKTEST_TRADES_CSV
    if not csv_path.exists():
        return pd.DataFrame(), ''

    try:
        trades_df = pd.read_csv(csv_path, encoding='utf-8-sig')
    except Exception as exc:
        print(f'⚠️ 讀取回測 CSV 失敗 [{normalized_key}]: {exc}')
        return pd.DataFrame(), ''

    if 'strategy' not in trades_df.columns:
        return pd.DataFrame(), ''

    filtered = trades_df[trades_df['strategy'].astype(str).str.strip().str.lower() == normalized_key.lower()].copy()
    if filtered.empty:
        return pd.DataFrame(), ''
    return filtered, '資料來源: backtest_result.csv'


def _calculate_trade_sequence_drawdown(profit_pct_values: list[float]) -> tuple[float, float]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0

    for profit_pct in profit_pct_values:
        factor = 1 + (profit_pct / 100.0)
        equity = 0.0 if factor <= 0 else equity * factor
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = ((equity - peak) / peak) * 100
            if drawdown < max_drawdown:
                max_drawdown = drawdown

    total_roi = (equity - 1) * 100
    return round(total_roi, 2), round(max_drawdown, 2)


def _format_backtest_trade_summary(row: pd.Series) -> str:
    stock_id = str(row.get('stock_id') or 'N/A').strip()
    profit_pct = safe_float(row.get('profit_pct'))
    reason = str(row.get('reason') or '未記錄').strip()
    if profit_pct is None:
        return f'{stock_id}｜出場原因：{reason}'
    return f'{stock_id} {profit_pct:+.1f}%｜出場原因：{reason}'


def _build_strategy_reflection_suggestions(snapshot: dict[str, object]) -> list[str]:
    roi = safe_float(snapshot.get('total_roi')) or 0.0
    win_rate = safe_float(snapshot.get('win_rate')) or 0.0
    max_drawdown = safe_float(snapshot.get('max_drawdown')) or 0.0
    trade_count = safe_int(snapshot.get('trade_count')) or 0
    avg_hold_days = safe_float(snapshot.get('avg_hold_days')) or 0.0

    suggestions: list[str] = []
    if max_drawdown <= -15:
        suggestions.append('回撤偏深，建議先收斂停損幅度或降低單筆部位曝險。')
    elif max_drawdown <= -8:
        suggestions.append('回撤仍需關注，建議搭配市場濾網與題材強弱同步判斷。')

    if win_rate < 45 and trade_count >= 8:
        suggestions.append('勝率偏低且樣本已具規模，建議提高進場條件或縮短持有週期。')
    elif win_rate >= 55 and roi > 0:
        suggestions.append('勝率與報酬同步為正，可維持現行節奏並觀察強勢族群延續性。')

    if trade_count < 5:
        suggestions.append('目前樣本數偏少，建議累積更多回測樣本後再做參數微調。')
    elif avg_hold_days >= 10:
        suggestions.append('平均持有天數偏長，建議檢查出場條件是否過於遲滯。')

    if not suggestions:
        suggestions.append('目前指標中性，建議持續觀察最新交易樣本與市場 regime 變化。')
    return suggestions[:3]


def _build_strategy_backtest_snapshot(strategy_key: str) -> dict[str, object]:
    normalized_key = _normalize_strategy_request_key(strategy_key)
    display_name = _get_strategy_display_name(normalized_key or strategy_key)
    trades_df, source_label = _load_strategy_backtest_frame(normalized_key)
    if trades_df.empty:
        return {
            'has_data': False,
            'strategy_key': normalized_key,
            'strategy_name': display_name,
            'date_str': app_pkg._current_line_date(),
        }

    prepared = trades_df.copy()
    for column in ('profit_pct', 'days'):
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors='coerce')
    for column in ('buy_date', 'sell_date'):
        if column in prepared.columns:
            prepared[column] = pd.to_datetime(prepared[column], errors='coerce')

    valid_profits = [float(value) for value in prepared.get('profit_pct', pd.Series(dtype='float64')).dropna().tolist()]
    if not valid_profits:
        return {
            'has_data': False,
            'strategy_key': normalized_key,
            'strategy_name': display_name,
            'date_str': app_pkg._current_line_date(),
        }

    total_roi, max_drawdown = _calculate_trade_sequence_drawdown(valid_profits)
    trade_count = len(valid_profits)
    win_rate = round((sum(1 for value in valid_profits if value > 0) / trade_count) * 100, 1) if trade_count else 0.0
    avg_hold_days = round(float(prepared['days'].dropna().mean()), 1) if 'days' in prepared.columns and prepared['days'].notna().any() else None

    sort_columns = [column for column in ('sell_date', 'buy_date') if column in prepared.columns]
    latest_trade = prepared.sort_values(sort_columns, ascending=False, na_position='last').iloc[0] if sort_columns else prepared.iloc[0]
    latest_trade_summary = _format_backtest_trade_summary(latest_trade)
    latest_date = latest_trade.get('sell_date') if 'sell_date' in latest_trade.index else None
    date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') and not pd.isna(latest_date) else app_pkg._current_line_date()

    return {
        'has_data': True,
        'strategy_key': normalized_key,
        'strategy_name': display_name,
        'date_str': date_str,
        'source_label': source_label,
        'total_roi': total_roi,
        'win_rate': win_rate,
        'max_drawdown': max_drawdown,
        'trade_count': trade_count,
        'avg_hold_days': avg_hold_days,
        'latest_trade_summary': latest_trade_summary,
    }


def _build_macro_news_messages() -> list:
    market_snapshot = app_pkg._build_market_snapshot()
    chip_snapshot = app_pkg._build_chip_snapshot()
    display_date = str(market_snapshot.get('date_str') or chip_snapshot.get('date_str') or app_pkg._current_line_date())

    try:
        from core.news_agent import get_morning_news_summary

        news_summary = get_morning_news_summary()
        if not str(news_summary or '').strip() or str(news_summary).startswith('⚠️'):
            news_summary = '目前暫無當日新聞摘要，請稍後再試。'
    except Exception as exc:
        print(f'⚠️ 總經摘要產生失敗: {exc}')
        news_summary = '目前暫時無法取得最新新聞摘要，請稍後再試。'

    return [
        build_macro_summary_flex(
            news_summary=news_summary,
            market_snapshot=market_snapshot,
            chip_snapshot=chip_snapshot,
            date_str=display_date,
            title='📰 總經摘要',
        )
    ]


def _build_stock_diagnosis_prompt_messages(source_id: str = '') -> list:
    if source_id:
        _line_interaction_state.set(source_id, {'action': 'stock_diagnosis'})
    return [V3TextMessage(text='🔎 請輸入 4 碼股票代號，例如 2330。')]


def _build_strategy_picker_messages() -> list:
    options = app_pkg._list_strategy_picker_options()
    if not options:
        return [
            _build_postback_empty_state(
                title='🎯 策略選股',
                message='目前沒有可用策略，請先檢查策略設定。',
            )
        ]
    return [
        build_strategy_prompt_flex(
            title='🎯 策略選股',
            prompt_text='請選擇您要觀看的策略選股盤勢。',
            strategies=options,
            action='strategy_select',
            date_str=app_pkg._current_line_date(),
            subtitle='固定列出所有已註冊策略（V31~V38），結果階段將沿用既有選股 Flex 樣板。',
            alt_text='🎯 策略選股',
        )
    ]


def _build_selected_strategy_messages(payload: dict[str, str] | None = None) -> list:
    raw_strategy_key = str((payload or {}).get('strategy') or '').strip()
    if not raw_strategy_key:
        return [
            _build_postback_empty_state(
                title='🎯 策略選股',
                message='缺少策略代號，請重新點選「策略選股」。',
            )
        ]

    strategy_key = _normalize_strategy_request_key(raw_strategy_key)
    display_name = _get_strategy_display_name(strategy_key or raw_strategy_key)
    recommendation = app_pkg.get_strategy_recommendation(as_flex=True, strategy_key=strategy_key)
    if isinstance(recommendation, str):
        return [
            create_empty_state_flex(
                title=f'🎯 {display_name}',
                message=recommendation,
                date_str=app_pkg._current_line_date(),
                subtitle='請重新選擇策略，或稍後再試。',
            )
        ]
    return [recommendation]


def _build_journal_reflection_messages() -> list:
    options = app_pkg._list_strategy_picker_options()
    if not options:
        return [
            _build_postback_empty_state(
                title='📝 日誌反思',
                message='目前沒有可用策略，請先檢查策略設定。',
            )
        ]

    return [
        build_strategy_prompt_flex(
            title='📝 日誌反思',
            prompt_text='請選擇您要查看回測數據與反思的策略。',
            strategies=options,
            action='backtest_reflect',
            date_str=app_pkg._current_line_date(),
            subtitle='固定列出所有已註冊策略；若該策略暫無回測資料，將回傳 Empty State Flex Card。',
            alt_text='📝 日誌反思',
        )
    ]


def _build_backtest_reflection_messages(payload: dict[str, str] | None = None) -> list:
    raw_strategy_key = str((payload or {}).get('strategy') or '').strip()
    if not raw_strategy_key:
        return [
            _build_postback_empty_state(
                title='📝 日誌反思',
                message='缺少策略代號，請重新選擇想查看的策略。',
            )
        ]

    snapshot = app_pkg._build_strategy_backtest_snapshot(raw_strategy_key)
    strategy_name = str(snapshot.get('strategy_name') or raw_strategy_key.upper())
    if not snapshot.get('has_data'):
        return [
            create_empty_state_flex(
                title=f'📝 {strategy_name}',
                message='尚無該策略回測資料，可先執行該策略回測後再查看。',
                date_str=str(snapshot.get('date_str') or app_pkg._current_line_date()),
                subtitle='系統仍保留該策略在清單中，方便你直接檢查是否已有新資料。',
            )
        ]

    return [
        build_backtest_reflection_flex(
            strategy_name=strategy_name,
            total_roi=safe_float(snapshot.get('total_roi')),
            win_rate=safe_float(snapshot.get('win_rate')),
            max_drawdown=safe_float(snapshot.get('max_drawdown')),
            trade_count=safe_int(snapshot.get('trade_count')),
            date_str=str(snapshot.get('date_str') or app_pkg._current_line_date()),
            avg_hold_days=safe_float(snapshot.get('avg_hold_days')),
            latest_trade_summary=str(snapshot.get('latest_trade_summary') or ''),
            suggestions=app_pkg._build_strategy_reflection_suggestions(snapshot),
            source_label=str(snapshot.get('source_label') or ''),
        )
    ]


def _build_market_summary_messages() -> list:
    cached = app_pkg._postback_cache.get('market_summary')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = app_pkg._resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = app_pkg.MCPClient().get_market_statistics_sync(trade_date)
        if result is None:
            return [V3TextMessage(text='📊 大盤快照\n\n目前暫時無法連線至 TWSE MCP Server，請稍後再試。')]
        records: list[dict] = result.get('records') or []
        if not records:
            return [V3TextMessage(text='📊 大盤快照\n\n目前尚無今日交易資料，請於盤後再試。')]

        rising = sum(1 for r in records if (safe_float(r.get('close_price')) or 0) > (safe_float(r.get('open_price')) or 0))
        falling = sum(1 for r in records if (safe_float(r.get('close_price')) or 0) < (safe_float(r.get('open_price')) or 0))
        flat = len(records) - rising - falling
        total_vol = sum(int(safe_float(r.get('volume')) or 0) for r in records)
        total_vol_b = total_vol / 1_000_000_000
        result_date = result.get('as_of_date') or trade_date

        body = (
            f'📊 台股大盤快照 {result_date}\n'
            f"{'─' * 26}\n"
            f'▲ 上漲  {rising:4d} 檔\n'
            f'▼ 下跌  {falling:4d} 檔\n'
            f'─ 平盤  {flat:4d} 檔\n'
            f"{'─' * 26}\n"
            f'💹 總成交量 {total_vol_b:.1f} 億股\n'
            '\n💡 資料來源：TWSE MCP Server'
        )
        app_pkg._postback_cache.set('market_summary', body)
        return [V3TextMessage(text=body)]
    except MCPClientError as exc:
        print(f'⚠️ MCP 大盤快照失敗: {exc}')
        return [V3TextMessage(text='📊 大盤快照\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。')]
    except Exception as exc:
        print(f'⚠️ _build_market_summary_messages 未預期錯誤: {exc}')
        return [V3TextMessage(text='📊 大盤快照\n\n資料處理異常，請稍後再試。')]


def _build_chip_trend_messages() -> list:
    cached = app_pkg._postback_cache.get('chip_trend')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = app_pkg._resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = app_pkg.MCPClient().get_foreign_investment_sync(trade_date)
        if result is None:
            return [V3TextMessage(text='🏦 籌碼動向\n\n目前暫時無法連線至 TWSE MCP Server，請稍後再試。')]
        records: list[dict] = result.get('records') or []
        if not records:
            return [V3TextMessage(text='🏦 籌碼動向\n\n目前尚無今日法人資料，請於盤後再試。')]

        def _sum_net(key: str) -> int:
            return sum(int(safe_float(r.get(key)) or 0) for r in records)

        foreign_net = _sum_net('foreign_buy')
        trust_net = _sum_net('trust_buy')
        dealer_net = _sum_net('dealer_buy')
        total_net = foreign_net + trust_net + dealer_net
        result_date = result.get('as_of_date') or trade_date

        def _fmt(val: int) -> str:
            sign = '▲' if val > 0 else ('▼' if val < 0 else '─')
            return f'{sign} {abs(val):>10,}'

        body = (
            f'🏦 三大法人買賣超 {result_date}\n'
            f"{'─' * 30}\n"
            f'外資   {_fmt(foreign_net)} 張\n'
            f'投信   {_fmt(trust_net)} 張\n'
            f'自營商 {_fmt(dealer_net)} 張\n'
            f"{'─' * 30}\n"
            f'合計   {_fmt(total_net)} 張\n'
            '\n💡 資料來源：TWSE MCP Server'
        )
        app_pkg._postback_cache.set('chip_trend', body)
        return [V3TextMessage(text=body)]
    except MCPClientError as exc:
        print(f'⚠️ MCP 籌碼動向失敗: {exc}')
        return [V3TextMessage(text='🏦 籌碼動向\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。')]
    except Exception as exc:
        print(f'⚠️ _build_chip_trend_messages 未預期錯誤: {exc}')
        return [V3TextMessage(text='🏦 籌碼動向\n\n資料處理異常，請稍後再試。')]


def _build_random_strategy_messages() -> list:
    try:
        sm = app_pkg.strategy_manager
        pool = sm.get_random_strategy_pool()
        if not pool:
            return [V3TextMessage(text='🎲 策略盲盒\n\n目前策略池為空，請至設定頁面配置可用策略。')]

        shuffled = random.sample(pool, len(pool))
        baseline_date = app_pkg._resolve_ui_baseline_date()
        df, date_str = app_pkg.get_stock_data(date_str=baseline_date) if baseline_date else app_pkg.get_stock_data()
        if df is None or df.empty:
            return [V3TextMessage(text='🎲 策略盲盒\n\n目前無法取得市場資料，請稍後再試。')]
        if not date_str:
            date_str = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')

        for strategy_key in shuffled:
            try:
                strategy = sm.get_strategy(strategy_key)
                if strategy is None:
                    continue
                candidates = strategy.filter_candidates(df.copy())
                if candidates is None or candidates.empty:
                    continue
                body = format_v31_recommendation(candidates.head(5), date_str)
                display = getattr(strategy, 'display_name', strategy_key)
                header = f"🎲 策略盲盒｜{display}\n{'─' * 26}\n"
                return [V3TextMessage(text=header + body)]
            except Exception as exc:
                print(f'⚠️ 策略盲盒 [{strategy_key}] 失敗，嘗試下一個: {exc}')
                continue

        return [
            V3TextMessage(
                text=(
                    f'🎲 策略盲盒\n\n今日所有策略均無符合條件的股票，可能是市場整體條件偏弱。\n📅 {date_str}'
                )
            )
        ]
    except Exception as exc:
        print(f'⚠️ _build_random_strategy_messages 未預期錯誤: {exc}')
        return [V3TextMessage(text='🎲 策略盲盒\n\n資料處理異常，請稍後再試。')]


def _build_journal_reflection_text() -> str:
    snapshot = _build_journal_reflection_snapshot()
    summary = snapshot.get('summary') or {}
    active_labels = snapshot.get('active_labels') or ['尚未啟用策略']
    today_pick_status = str(snapshot.get('today_pick_status') or '今日無標的')
    latest_trade_summary = str(snapshot.get('latest_trade_summary') or '')

    if not summary:
        return (
            '📝 日誌反思\n\n'
            f'啟用策略：{"、".join(active_labels)}\n'
            f'今日選股：{today_pick_status}\n\n'
            '目前還沒有足夠的回測摘要可供反思。\n可以先執行一次回測累積樣本。'
        )

    total_roi = safe_float(summary.get('total_roi')) or 0.0
    win_rate = safe_float(summary.get('win_rate')) or 0.0
    trade_count = safe_int(summary.get('trade_count')) or 0

    body = (
        '📝 日誌反思\n\n'
        f'啟用策略：{"、".join(active_labels)}\n'
        f'最近回測：總報酬 {total_roi:+.1f}% / 勝率 {win_rate:.1f}% / {trade_count} 筆交易\n'
        f'今日選股：{today_pick_status}'
    )
    if latest_trade_summary:
        body += f'\n最近一筆：{latest_trade_summary}'
    return body
