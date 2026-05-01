"""Canonical application package integrating Web and LINE entrypoints."""

# -*- coding: utf-8 -*-
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
import traceback
import unicodedata
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as V3TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from config import Config
from config import (
    MODE_CMD_MAP,
    MODE_EMOJI,
    MODE_REPLY_TEMPLATE,
    V34_MODE_PRESETS,
    V35_MODE_PRESETS,
)
from core.db_helper import (
    build_dashboard_aggregation_cache_key,
    create_user_simulation_trade,
    format_market_fallback_notice,
    get_actual_latest_date,
    get_backtest_trades,
    get_backtest_equity_curve,
    get_daily_recommendations,
    get_backtest_summary_from_db,
    get_latest_trade_date,
    get_news_sentiment,
    get_open_holdings,
    get_recent_backtest_trades,
    get_recommendations_with_market_fallback,
    get_setting,
    get_stock_data,
    get_stock_history,
    get_stock_sector,
    merge_recommendations_with_market_data,
    normalize_date_str,
    resolve_dashboard_aggregation_cache,
    safe_float,
    safe_int,
    supplement_financial_data,
    update_setting,
    validate_setting,
)
from core.calc_indicators import calculate_kd_full, calculate_rsi
from core.line_message_builder import (
    build_backtest_reflection_flex,
    build_macro_summary_flex,
    build_strategy_prompt_flex,
    create_backtest_summary_flex,
    create_empty_state_flex,
    create_holdings_flex,
    create_journal_reflection_flex,
    create_news_flex,
    create_recommendation_carousel,
    create_strategy_picker_message,
    create_stock_flex_message,
)
from core.mcp_client import MCPClientError, TWSEMCPClient as MCPClient
from core.report_helper import format_stock_diagnosis, get_stock_report
from core.strategy import (
    calculate_v30_signal,
    format_stock_query,
    format_v30_recommendation,
    format_v31_recommendation,
    get_best_stocks_v31_hybrid,
    get_v30_params_from_db,
)
from core.strategy_manager import StrategyManager


if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')


REPO_ROOT = Path(__file__).resolve().parents[1]
app = Flask(
    __name__,
    template_folder=str(REPO_ROOT / 'templates'),
)
app.secret_key = Config.FLASK_SECRET_KEY


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入以存取此頁面'
login_manager.login_message_category = 'error'


class User(UserMixin):
    """簡易使用者類別 (基於環境變數驗證)"""

    def __init__(self, user_id):
        self.id = user_id

    @staticmethod
    def validate_password(password):
        return password == Config.ADMIN_PASSWORD

    @staticmethod
    def get(user_id):
        if user_id == 'admin':
            return User('admin')
        return None


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

print('[AI] 模型將按策略動態載入')
print('[AI] 正在初始化策略管理器...')
strategy_manager = StrategyManager()
print(f"[OK] 當前策略: {strategy_manager.get_active_strategy_name()}")


class _PostbackCache:
    """執行緒安全的記憶體 TTL 快取，用於 LINE Postback 上游市場資料。"""

    _TTL_SECONDS: float = 3600.0

    def __init__(self) -> None:
        self._store: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def _today_taipei(self) -> str:
        return datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')

    def _make_key(self, action: str) -> str:
        return f"{action}:{self._today_taipei()}"

    def get(self, action: str) -> object | None:
        key = self._make_key(action)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            payload, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return payload

    def set(self, action: str, payload: object) -> None:
        if payload is None:
            return
        if isinstance(payload, dict):
            records = payload.get('records')
            if isinstance(records, list) and len(records) == 0:
                return
        key = self._make_key(action)
        expires_at = time.monotonic() + self._TTL_SECONDS
        with self._lock:
            self._store[key] = (payload, expires_at)


_postback_cache = _PostbackCache()


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


def get_ngrok_url() -> str:
    """自動偵測 ngrok 公開 URL。"""
    import json as _json
    import urllib.request

    ngrok_api = 'http://localhost:4040/api/tunnels'
    try:
        with urllib.request.urlopen(ngrok_api, timeout=2) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
        tunnels = data.get('tunnels', [])
        for tunnel in tunnels:
            if tunnel.get('proto') == 'https':
                return tunnel['public_url'].rstrip('/')
        if tunnels:
            return tunnels[0]['public_url'].rstrip('/')
    except Exception:
        pass
    return Config.PUBLIC_DASHBOARD_URL.rstrip('/')


def _normalize_backtest_dates(start_date, end_date):
    from datetime import datetime as _datetime
    from datetime import timedelta

    if not start_date:
        start_date = (_datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = _datetime.now().strftime('%Y-%m-%d')
    return start_date, end_date


def _get_backtest_module():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(base_dir)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    from importlib import import_module
    return import_module('4_run_backtest')


def _run_portfolio_backtest(selected_strategies, start_date=None, end_date=None, weights=None):
    start_date, end_date = _normalize_backtest_dates(start_date, end_date)
    backtest_module = _get_backtest_module()
    engine = backtest_module.PortfolioBacktestEngine(
        strategies=selected_strategies,
        start_date=start_date,
        end_date=end_date,
        weights=weights,
    )
    result = engine.run_portfolio_backtest()
    return result, start_date, end_date


def _load_backtest_summary_or_error(error_message):
    summary = get_backtest_summary_from_db()
    if summary is None:
        from core.viz_helper import get_backtest_summary

        summary = get_backtest_summary()
    if summary is None:
        return None, (jsonify({'error': error_message}), 404)
    return summary, None


def _apply_settings_batch(updates):
    return all(update_setting(key, value) for key, value in updates.items())


def _build_summary_response(summary):
    return {
        'total_roi': summary['total_roi'],
        'win_rate': summary['win_rate'],
        'mdd': summary['max_drawdown'],
        'sharpe': summary['sharpe_ratio'],
        'trade_count': summary['trade_count'],
        'avg_hold_days': summary['avg_hold_days'],
    }


def _resolve_ui_baseline_date() -> str | None:
    """取得 UI 與 LINE 共用的資料基準日。"""
    return normalize_date_str(get_actual_latest_date() or get_latest_trade_date())


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
    strategy = strategy_manager.get_strategy(strategy_key)
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
    strategy_keys = list(strategy_manager.list_strategies())
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


def _current_line_date() -> str:
    return _resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')


def _build_postback_empty_state(title: str, message: str, subtitle: str = ''):
    return create_empty_state_flex(
        title=title,
        message=message,
        date_str=_current_line_date(),
        subtitle=subtitle,
    )


def _list_strategy_picker_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for strategy_key in strategy_manager.list_strategies():
        strategy = strategy_manager.get_strategy(strategy_key)
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
        df = get_daily_recommendations(date_str=date_str, strategy=strategy_key, limit=1)
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
    active_keys = strategy_manager.get_active_strategy_names()
    active_labels = [_get_strategy_display_name(key) for key in active_keys] or ['尚未啟用策略']
    date_str = _resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
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
        'display': 'V38 高殖利率價值策略',
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


_stock_news_runtime = threading.local()


def _get_current_stock_news_deadline() -> float | None:
    deadline = getattr(_stock_news_runtime, 'deadline_monotonic', None)
    if isinstance(deadline, (int, float)):
        return float(deadline)
    return None


@contextmanager
def _live_signal_news_timeout_scope():
    timeout_seconds = 0.0
    try:
        timeout_seconds = max(0.0, float(Config.DASHBOARD_NEWS_TIMEOUT_SECONDS))
    except Exception:
        timeout_seconds = 3.0

    previous_deadline = _get_current_stock_news_deadline()
    next_deadline = previous_deadline
    if timeout_seconds > 0:
        candidate_deadline = time.monotonic() + timeout_seconds
        next_deadline = min(previous_deadline, candidate_deadline) if previous_deadline is not None else candidate_deadline

    _stock_news_runtime.deadline_monotonic = next_deadline
    try:
        yield next_deadline
    finally:
        if previous_deadline is None:
            if hasattr(_stock_news_runtime, 'deadline_monotonic'):
                delattr(_stock_news_runtime, 'deadline_monotonic')
        else:
            _stock_news_runtime.deadline_monotonic = previous_deadline


def _parse_news_reason(news_reason: str) -> dict:
    raw = str(news_reason or '').strip()
    normalized = raw.replace('|', '｜')
    items = [part.strip() for part in normalized.split('｜') if part.strip()]
    is_bearish = any(('利空' in item or '承壓' in item or '下修' in item or '風險' in item) for item in items)
    return {
        'raw': raw,
        'items': items,
        'is_bearish': is_bearish,
        'title': '🔴 利空警示' if is_bearish else '🟢 利多原因',
    }


def _get_stock_mentions_map(stock_ids: list[str]) -> dict:
    if not stock_ids:
        return {}
    if not Config.is_news_boost_enabled():
        return {}
    try:
        from core.news_agent import get_stock_news_mentions

        return get_stock_news_mentions(
            stock_ids,
            deadline_monotonic=_get_current_stock_news_deadline(),
        )
    except Exception as exc:
        print(f'⚠️ 個股新聞讀取失敗: {exc}')
        return {}


def _get_sector_news_summary(sector: str, date_str: str = None) -> dict:
    payload = {
        'raw': '',
        'items': [],
        'is_bearish': False,
        'title': '',
    }
    if not sector:
        return payload
    if not Config.is_news_boost_enabled():
        return payload

    sentiment = get_news_sentiment(date_str)
    bull_sectors = sentiment.get('bull_sectors', [])
    bear_sectors = sentiment.get('bear_sectors', [])

    if sector in bull_sectors:
        items = []
        theme = (sentiment.get('bull_theme_map') or {}).get(sector)
        if theme:
            items.append(f'主題: {theme}')
        items.extend([str(item) for item in sentiment.get('bull_reasons', []) if str(item).strip()])
        items = items[:3]
        return {
            'raw': '｜'.join(items),
            'items': items,
            'is_bearish': False,
            'title': f'🟢 {sector} 消息面',
        }

    if sector in bear_sectors:
        items = []
        theme = (sentiment.get('bear_theme_map') or {}).get(sector)
        if theme:
            items.append(f'主題: {theme}')
        items.extend([str(item) for item in sentiment.get('bear_reasons', []) if str(item).strip()])
        items = items[:3]
        return {
            'raw': '｜'.join(items),
            'items': items,
            'is_bearish': True,
            'title': f'🔴 {sector} 消息面',
        }

    return payload


def _get_stock_specific_news_summary(stock_id: str, stock_mentions_map: dict) -> dict:
    payload = {
        'raw': '',
        'items': [],
        'is_bearish': False,
        'title': '',
    }
    stock_info = (stock_mentions_map or {}).get(str(stock_id))
    if not stock_info:
        return payload

    score = safe_int(stock_info.get('score')) or 0
    reason = str(stock_info.get('reason') or '').strip()
    if score == 0 or not reason:
        return payload

    is_bearish = score < 0
    item = f'利空: {reason}' if is_bearish else f'利多: {reason}'
    return {
        'raw': item,
        'items': [item],
        'is_bearish': is_bearish,
        'title': '🔴 個股新聞' if is_bearish else '🟢 個股新聞',
    }


def _resolve_signal_news_info(row, date_str: str, stock_mentions_map: dict) -> dict:
    stock_id = str(row.get('stock_id', '')).strip()
    sector = get_stock_sector(stock_id)

    sector_info = _get_sector_news_summary(sector, date_str)
    if sector_info['items']:
        return sector_info

    stock_info = _get_stock_specific_news_summary(stock_id, stock_mentions_map)
    if stock_info['items']:
        return stock_info

    return _parse_news_reason(row.get('news_boost_reason') or '')


def _apply_news_sentiment_overlay(candidates: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame() if candidates is None else candidates

    boosted = candidates.copy()
    if 'news_boost_reason' not in boosted.columns:
        boosted['news_boost_reason'] = ''
    if 'ai_score' not in boosted.columns:
        return boosted
    boosted['ai_score'] = pd.to_numeric(boosted['ai_score'], errors='coerce').astype('float64')
    if not Config.is_news_boost_enabled():
        return boosted

    try:
        sentiment = get_news_sentiment(date_str)
        bull_sectors = set(sentiment.get('bull_sectors') or [])
        bear_sectors = set(sentiment.get('bear_sectors') or [])
        bull_theme_map = sentiment.get('bull_theme_map') or {}
        bear_theme_map = sentiment.get('bear_theme_map') or {}
        stock_mentions_map = _get_stock_mentions_map([str(sid) for sid in boosted['stock_id'].tolist()])

        bull_factor = min(Config.NEWS_BOOST_FACTOR, Config.NEWS_BOOST_MAX)
        bear_factor = Config.NEWS_PENALTY_FACTOR

        for idx, row in boosted.iterrows():
            stock_id = str(row.get('stock_id', '')).strip()
            sector = get_stock_sector(stock_id)
            score = safe_float(row.get('ai_score')) or 0.0
            reason_parts: list[str] = []

            if sector in bull_sectors:
                score *= (1 + bull_factor)
                topic = bull_theme_map.get(sector)
                reason_parts.append(f'{sector}題材: {topic or "消息面偏多"}')

            stock_news = stock_mentions_map.get(stock_id) or {}
            stock_news_score = safe_int(stock_news.get('score')) or 0
            stock_news_reason = str(stock_news.get('reason') or '').strip()
            if stock_news_score > 0 and stock_news_reason:
                extra = min(bull_factor, max(Config.NEWS_BOOST_MAX - bull_factor, 0))
                if extra > 0:
                    score *= (1 + extra)
                reason_parts.append(f'個股: {stock_news_reason}')
            elif stock_news_score < 0 and stock_news_reason:
                score *= (1 - bear_factor)
                reason_parts.append(f'個股利空: {stock_news_reason}')

            if sector in bear_sectors:
                score *= (1 - bear_factor)
                topic = bear_theme_map.get(sector)
                reason_parts.append(f'{sector}承壓: {topic or "消息面偏空"}')

            boosted.at[idx, 'ai_score'] = score
            boosted.at[idx, 'news_boost_reason'] = '｜'.join(reason_parts)[:100] if reason_parts else ''

        return boosted.sort_values('ai_score', ascending=False)
    except Exception as exc:
        print(f'⚠️ 即時選股消息面加權失敗: {exc}')
        return boosted


def _summarize_market_snapshot(rising: int, falling: int, flat: int) -> str:
    total = max(rising + falling + flat, 1)
    if rising >= falling * 1.2 and rising >= total * 0.45:
        return '盤面偏多，上漲家數明顯優於下跌家數。'
    if falling >= rising * 1.2 and falling >= total * 0.45:
        return '盤面偏弱，下跌家數占優，短線宜控管追價風險。'
    return '盤勢中性，建議搭配題材與法人方向交叉確認。'


def _summarize_chip_snapshot(foreign_net: int, trust_net: int, dealer_net: int) -> str:
    total_net = foreign_net + trust_net + dealer_net
    if total_net > 0 and foreign_net > 0:
        return '三大法人偏多，且外資站在買方，籌碼面偏正向。'
    if total_net < 0 and foreign_net < 0:
        return '三大法人偏空，外資同步調節，需留意高檔震盪。'
    return '法人分歧，籌碼面缺乏一致性，宜搭配價格結構判讀。'


def _build_market_snapshot() -> dict[str, object]:
    cached = _postback_cache.get('market_summary_snapshot')
    if isinstance(cached, dict) and cached:
        return dict(cached)

    trade_date = _current_line_date()
    try:
        result = MCPClient().get_market_statistics_sync(trade_date)
        if result is None:
            return {
                'status': 'error',
                'date_str': trade_date,
                'message': '目前暫時無法連線至 TWSE MCP Server，盤勢快照暫不可用。',
            }

        records: list[dict] = result.get('records') or []
        if not records:
            return {
                'status': 'empty',
                'date_str': result.get('as_of_date') or trade_date,
                'message': '目前尚無今日盤勢資料，請於盤後再試。',
            }

        rising = sum(1 for record in records if (safe_float(record.get('close_price')) or 0) > (safe_float(record.get('open_price')) or 0))
        falling = sum(1 for record in records if (safe_float(record.get('close_price')) or 0) < (safe_float(record.get('open_price')) or 0))
        flat = len(records) - rising - falling
        total_volume = sum(int(safe_float(record.get('volume')) or 0) for record in records)
        snapshot = {
            'status': 'ok',
            'date_str': result.get('as_of_date') or trade_date,
            'rising': rising,
            'falling': falling,
            'flat': flat,
            'total_volume_b': total_volume / 1_000_000_000,
            'summary': _summarize_market_snapshot(rising, falling, flat),
        }
        _postback_cache.set('market_summary_snapshot', snapshot)
        return snapshot
    except MCPClientError as exc:
        print(f'⚠️ MCP market snapshot 失敗: {exc}')
        return {
            'status': 'error',
            'date_str': trade_date,
            'message': '盤勢快照暫時無法取得，請稍後再試。',
        }
    except Exception as exc:
        print(f'⚠️ _build_market_snapshot 未預期錯誤: {exc}')
        return {
            'status': 'error',
            'date_str': trade_date,
            'message': '盤勢快照資料處理異常，請稍後再試。',
        }


def _build_chip_snapshot() -> dict[str, object]:
    cached = _postback_cache.get('chip_trend_snapshot')
    if isinstance(cached, dict) and cached:
        return dict(cached)

    trade_date = _current_line_date()
    try:
        result = MCPClient().get_foreign_investment_sync(trade_date)
        if result is None:
            return {
                'status': 'error',
                'date_str': trade_date,
                'message': '目前暫時無法連線至 TWSE MCP Server，籌碼資料暫不可用。',
            }

        records: list[dict] = result.get('records') or []
        if not records:
            return {
                'status': 'empty',
                'date_str': result.get('as_of_date') or trade_date,
                'message': '目前尚無今日法人資料，請於盤後再試。',
            }

        def _sum_net(key: str) -> int:
            return sum(int(safe_float(record.get(key)) or 0) for record in records)

        foreign_net = _sum_net('foreign_buy')
        trust_net = _sum_net('trust_buy')
        dealer_net = _sum_net('dealer_buy')
        total_net = foreign_net + trust_net + dealer_net
        snapshot = {
            'status': 'ok',
            'date_str': result.get('as_of_date') or trade_date,
            'foreign_net': foreign_net,
            'trust_net': trust_net,
            'dealer_net': dealer_net,
            'total_net': total_net,
            'summary': _summarize_chip_snapshot(foreign_net, trust_net, dealer_net),
        }
        _postback_cache.set('chip_trend_snapshot', snapshot)
        return snapshot
    except MCPClientError as exc:
        print(f'⚠️ MCP chip snapshot 失敗: {exc}')
        return {
            'status': 'error',
            'date_str': trade_date,
            'message': '籌碼資料暫時無法取得，請稍後再試。',
        }
    except Exception as exc:
        print(f'⚠️ _build_chip_snapshot 未預期錯誤: {exc}')
        return {
            'status': 'error',
            'date_str': trade_date,
            'message': '籌碼資料處理異常，請稍後再試。',
        }


def _build_dashboard_signal_light(label: str, signal: str, detail: str) -> dict[str, str]:
    return {
        'label': label,
        'signal': signal,
        'detail': detail,
    }


def _safe_price(value, fallback: float = 0.0) -> float:
    numeric = safe_float(value)
    return float(numeric) if numeric is not None else fallback


def _format_price(value: float | None) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return 'N/A'
    return f'{numeric:.2f}'


def _format_price_range(low_value: float | None, high_value: float | None) -> str:
    low = safe_float(low_value)
    high = safe_float(high_value)
    if low is None and high is None:
        return 'N/A'
    if low is None:
        return _format_price(high)
    if high is None:
        return _format_price(low)
    return f'{low:.2f} - {high:.2f}'


def _build_dashboard_action_scripts(latest_row: pd.Series) -> list[dict[str, object]]:
    close_price = _safe_price(latest_row.get('close_price'))
    ma5 = _safe_price(latest_row.get('ma5'), close_price)
    ma20 = _safe_price(latest_row.get('ma20'), close_price)

    return [
        {
            'key': 'gap_up',
            'title': '① 開高走高劇本',
            'signal': 'red',
            'entry_range': _format_price_range(close_price * 1.0, close_price * 1.01),
            'stop_loss': _format_price(max(ma5 * 0.995, close_price * 0.98)),
            'targets': [_format_price(close_price * 1.04), _format_price(close_price * 1.06)],
        },
        {
            'key': 'range_trade',
            'title': '② 震盪整理劇本',
            'signal': 'amber',
            'entry_range': _format_price_range(close_price * 0.99, close_price * 1.0),
            'stop_loss': _format_price(max(ma20 * 0.995, close_price * 0.97)),
            'targets': [_format_price(close_price * 1.025), _format_price(close_price * 1.04)],
        },
        {
            'key': 'pullback',
            'title': '③ 開低回測劇本',
            'signal': 'green',
            'entry_range': _format_price_range(close_price * 0.975, close_price * 0.99),
            'stop_loss': _format_price(close_price * 0.95),
            'targets': [_format_price(close_price * 1.015), _format_price(close_price * 1.03)],
        },
    ]


def _build_dashboard_rule_report(
    stock_id: str,
    report: dict[str, object],
    latest_row: pd.Series,
    news_info: dict[str, object],
    market_snapshot: dict[str, object],
    chip_snapshot: dict[str, object],
) -> dict[str, object]:
    close_price = _safe_price(report.get('close_price'))
    ma20 = safe_float(report.get('ma20'))
    ma60 = safe_float(report.get('ma60'))
    rsi = safe_float(report.get('rsi'))
    ai_score = safe_float(report.get('ai_score'))
    chip_score = safe_float(report.get('chip_score'))
    foreign_buy = safe_int(report.get('foreign_buy')) or 0
    trust_buy = safe_int(report.get('trust_buy')) or 0
    dealer_buy = safe_int(report.get('dealer_buy')) or 0
    total_institutional = foreign_buy + trust_buy + dealer_buy

    if ma20 is not None and ma60 is not None and close_price > ma20 > ma60:
        trend_label = '多頭排列'
        trend_signal = 'green'
    elif ma20 is not None and ma60 is not None and close_price < ma20 < ma60:
        trend_label = '空頭排列'
        trend_signal = 'red'
    else:
        trend_label = '區間整理'
        trend_signal = 'amber'

    if total_institutional > 0:
        chip_label = f'法人偏多 ({total_institutional:+,} 張)'
        chip_signal = 'green'
    elif total_institutional < 0:
        chip_label = f'法人偏空 ({total_institutional:+,} 張)'
        chip_signal = 'red'
    else:
        chip_label = '法人分歧'
        chip_signal = 'amber'

    if news_info.get('items'):
        news_label = str(news_info.get('title') or '消息面中性')
        news_signal = 'red' if news_info.get('is_bearish') else 'green'
        news_detail = '；'.join([str(item) for item in news_info.get('items', [])])
    else:
        news_label = '消息面中性'
        news_signal = 'amber'
        news_detail = '目前無明確個股/族群消息偏向。'

    confidence = 50
    if trend_signal == 'green':
        confidence += 15
    elif trend_signal == 'red':
        confidence -= 15
    if ai_score is not None:
        confidence += int(ai_score * 20)
    if chip_signal == 'green':
        confidence += 10
    elif chip_signal == 'red':
        confidence -= 10
    if rsi is not None and 45 <= rsi <= 70:
        confidence += 5
    confidence = max(10, min(confidence, 95))

    if rsi is not None:
        trend_text = f'{stock_id} 目前股價 {_format_price(close_price)}，均線結構為 {trend_label}，RSI {rsi:.1f}。'
    else:
        trend_text = f'{stock_id} 目前股價 {_format_price(close_price)}，均線結構為 {trend_label}。'

    chip_text = f'{chip_label}；市場籌碼摘要：{chip_snapshot.get("summary") or "暫無法人摘要"}。'
    if news_info.get('items'):
        chip_text += f' 個股消息：{news_detail}'

    macro_text = str(market_snapshot.get('summary') or '盤勢快照暫不可用。')
    if news_info.get('items'):
        macro_text += f' {news_detail}'

    return {
        'title': f'系統量化指標 ({report.get("strategy_name") or "規則版"})',
        'confidence': confidence,
        'signal_lights': [
            _build_dashboard_signal_light('主力動向', chip_signal, chip_label),
            _build_dashboard_signal_light('技術型態', trend_signal, trend_label),
            _build_dashboard_signal_light('消息面', news_signal, news_label),
        ],
        'trend': trend_text,
        'chips': chip_text,
        'macro': macro_text,
        'action_scripts': _build_dashboard_action_scripts(latest_row),
        'indicators': {
            'trend_status': {'label': trend_label, 'signal': trend_signal},
            'chip_status': {'label': chip_label, 'signal': chip_signal},
            'news_status': {'label': news_label, 'signal': news_signal},
            'ai_score_pct': round((ai_score or 0) * 100, 1) if ai_score is not None else None,
            'chip_score': chip_score,
        },
    }


def _build_dashboard_llm_report(
    stock_id: str,
    report: dict[str, object],
    rule_report: dict[str, object],
    market_snapshot: dict[str, object],
    chip_snapshot: dict[str, object],
) -> dict[str, object]:
    payload = {
        'provider': 'gemini',
        'status': 'fallback',
        'available': False,
        'used_fallback': True,
        'message': 'Gemini 不可用，顯示規則版診斷。',
        'trend': '',
        'chips': '',
        'macro': '',
        'risk': '',
    }
    if not Config.GEMINI_API_KEY:
        payload['message'] = '未設定 GEMINI_KEY，顯示規則版診斷。'
        return payload

    try:
        from google import genai
        from google.genai import types
        from core.news_agent import GEMINI_MODEL

        timeout_ms = max(1000, int(max(0.1, float(Config.DASHBOARD_NEWS_TIMEOUT_SECONDS)) * 1000))
        client = genai.Client(
            api_key=Config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        prompt = f"""你是台股資深分析師。請根據以下結構化資料，為股票 {stock_id} 產生 JSON 診斷，不要輸出任何 JSON 以外文字。

資料日期: {report.get('trade_date')}
技術摘要: {rule_report.get('trend')}
籌碼摘要: {rule_report.get('chips')}
大盤摘要: {market_snapshot.get('summary')}
法人摘要: {chip_snapshot.get('summary')}
基本面: 營業利益率={report.get('op_margin')}, 營收YoY={report.get('revenue_yoy')}

回傳格式:
{{
  "trend": "...",
  "chips": "...",
  "macro": "...",
  "risk": "..."
}}"""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=300,
                response_mime_type='application/json',
            ),
        )
        raw_text = str(getattr(response, 'text', '') or '').strip()
        if not raw_text:
            payload['message'] = 'Gemini 無回應，顯示規則版診斷。'
            return payload
        parsed = json.loads(raw_text)
        return {
            'provider': 'gemini',
            'status': 'ok',
            'available': True,
            'used_fallback': False,
            'message': 'Gemini 診斷完成。',
            'trend': str(parsed.get('trend') or '').strip(),
            'chips': str(parsed.get('chips') or '').strip(),
            'macro': str(parsed.get('macro') or '').strip(),
            'risk': str(parsed.get('risk') or '').strip(),
        }
    except Exception as exc:
        payload['message'] = f'Gemini 診斷失敗，顯示規則版診斷: {exc}'
        return payload


def _merge_unique_strings(*values) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple, set)):
            candidates = [str(item) for item in value]
        else:
            candidates = []
        for candidate in candidates:
            normalized = str(candidate or '').strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _select_dashboard_status(*statuses) -> str:
    ranking = {'error': 4, 'degraded': 3, 'partial': 2, 'ok': 1, 'empty': 0}
    resolved = 'empty'
    resolved_rank = -1
    for status in statuses:
        normalized = str(status or '').strip().lower() or 'empty'
        rank = ranking.get(normalized, 0)
        if rank > resolved_rank:
            resolved = normalized
            resolved_rank = rank
    return resolved


def _sanitize_dashboard_json(value):
    import math

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _sanitize_dashboard_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_dashboard_json(item) for item in value]

    try:
        is_missing = pd.isna(value)
        if bool(is_missing):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        return normalize_date_str(value)

    item_method = getattr(value, 'item', None)
    if callable(item_method):
        try:
            return _sanitize_dashboard_json(item_method())
        except Exception:
            pass

    if hasattr(value, 'strftime'):
        normalized_date = normalize_date_str(value)
        if normalized_date:
            return normalized_date

    return value


def _count_dashboard_series_points(series_payload) -> int:
    if not isinstance(series_payload, dict):
        return 0
    candles = series_payload.get('candles')
    if isinstance(candles, list):
        return len(candles)
    return max(
        (
            len(items)
            for items in series_payload.values()
            if isinstance(items, list)
        ),
        default=0,
    )


def _merge_dashboard_series_payload(base_series, incoming_series) -> dict[str, object]:
    """Keep the richer war-room series when MCP cache has a shorter chart history."""
    merged = dict(base_series or {})
    if not isinstance(incoming_series, dict):
        return merged

    if _count_dashboard_series_points(incoming_series) >= _count_dashboard_series_points(merged):
        merged.update({key: value for key, value in incoming_series.items() if value is not None})
    else:
        for key, value in incoming_series.items():
            if key not in merged and value is not None:
                merged[key] = value
    return merged


_WAR_ROOM_PERIOD_LABELS = {
    'daily': '日線',
    'weekly': '週線',
    'monthly': '月線',
}
_WAR_ROOM_OVERLAY_OPTIONS = [
    {'key': 'ma5', 'label': 'MA5'},
    {'key': 'ma20', 'label': 'MA20'},
    {'key': 'ma60', 'label': 'MA60'},
    {'key': 'support', 'label': '支撐'},
    {'key': 'resistance', 'label': '壓力'},
]
_WAR_ROOM_PANE_OPTIONS = [
    {'key': 'macd', 'label': 'MACD'},
    {'key': 'rsi', 'label': 'RSI'},
    {'key': 'kd', 'label': 'KD'},
    {'key': 'flow', 'label': 'Flow'},
]
_WAR_ROOM_DEFAULT_OVERLAYS = ['ma5', 'ma20', 'ma60', 'support', 'resistance']
_WAR_ROOM_DEFAULT_PANES = ['macd', 'rsi', 'kd', 'flow']


def _normalize_war_room_period(period: str | None) -> str:
    normalized = str(period or '').strip().lower()
    return normalized if normalized in _WAR_ROOM_PERIOD_LABELS else 'daily'


def _normalize_war_room_selection(
    values,
    *,
    options: list[dict[str, str]],
    default_values: list[str],
) -> list[str]:
    allowed = {option['key'] for option in options}
    raw_values: list[str] = []
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(value) for value in values]

    normalized_values: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for candidate in str(raw_value or '').split(','):
            normalized = str(candidate or '').strip().lower()
            if not normalized or normalized not in allowed or normalized in seen:
                continue
            seen.add(normalized)
            normalized_values.append(normalized)
    return normalized_values or list(default_values)


def _build_war_room_view_state(
    *,
    period: str,
    overlays: list[str],
    panes: list[str],
) -> dict[str, object]:
    return {
        'period': period,
        'period_label': _WAR_ROOM_PERIOD_LABELS.get(period, '日線'),
        'available_periods': [
            {'key': key, 'label': label, 'selected': key == period}
            for key, label in _WAR_ROOM_PERIOD_LABELS.items()
        ],
        'overlays': {
            'selected': list(overlays),
            'available': [
                {**option, 'selected': option['key'] in overlays}
                for option in _WAR_ROOM_OVERLAY_OPTIONS
            ],
        },
        'panes': {
            'selected': list(panes),
            'available': [
                {**option, 'selected': option['key'] in panes}
                for option in _WAR_ROOM_PANE_OPTIONS
            ],
        },
    }


def _build_empty_war_room_payload(
    *,
    stock_id: str,
    requested_date: str,
    period: str,
    view_state: dict[str, object],
) -> dict[str, object]:
    return {
        'timeframe': {
            'stock_id': stock_id,
            'period': period,
            'label': view_state.get('period_label'),
            'requested_date': requested_date,
            'as_of_date': None,
            'source_mode': 'unavailable',
            'status': 'empty',
            'resolved_bars': 0,
        },
        'structure': {
            'status': 'unavailable',
            'source_mode': 'unavailable',
            'method': None,
            'as_of_date': None,
            'supports': [],
            'resistances': [],
            'message': '結構價位資料暫不可用。',
        },
        'price_map': {
            'status': 'unavailable',
            'close_price': None,
            'open_price': None,
            'session_high': None,
            'session_low': None,
            'supports': [],
            'resistances': [],
            'ma_levels': [],
            'distance_to_support_pct': None,
            'distance_to_resistance_pct': None,
        },
        'panes': {},
        'quant_status': [],
        'tactical_summary': {
            'status': 'unavailable',
            'headline': '資料不足',
            'tone': 'neutral',
            'message': '目前無法建立戰情室摘要。',
            'confidence': None,
        },
        'chip_flow': {
            'status': 'unavailable',
            'source_mode': 'unavailable',
            'message': '籌碼流向資料暫不可用。',
            'chip_score': None,
        },
        'fundamentals': {
            'status': 'unavailable',
            'revenue_yoy': None,
            'op_margin': None,
            'eps': None,
            'strategy_name': '',
        },
        'news': {},
        'provenance': {
            'stock_id': stock_id,
            'requested_date': requested_date,
            'period': period,
            'as_of_date': None,
        },
    }


def _prepare_dashboard_history_frame(history_df: pd.DataFrame) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame()

    frame = history_df.copy()
    frame['trade_date'] = pd.to_datetime(frame['trade_date'], errors='coerce')
    frame = frame.dropna(subset=['trade_date']).sort_values('trade_date').reset_index(drop=True)

    numeric_columns = [
        'open_price',
        'high_price',
        'low_price',
        'close_price',
        'volume',
        'ma5',
        'ma20',
        'ma60',
        'rsi',
        'bias',
        'chip_score',
        'foreign_buy',
        'trust_buy',
        'dealer_buy',
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
    if 'volume' in frame.columns and not frame.empty:
        frame['volume'] = frame['volume'].fillna(0)
        frame = (
            frame.sort_values(['trade_date', 'volume'], ascending=[True, False])
            .drop_duplicates(subset=['trade_date'], keep='first')
            .sort_values('trade_date')
            .reset_index(drop=True)
        )
    return frame


def _calculate_macd_components(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    exp_fast = series.ewm(span=Config.MACD_FAST, adjust=False).mean()
    exp_slow = series.ewm(span=Config.MACD_SLOW, adjust=False).mean()
    macd_line = exp_fast - exp_slow
    signal_line = macd_line.ewm(span=Config.MACD_SIGNAL, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _aggregate_dashboard_history(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    working = frame.copy()
    working['bucket_trade_date'] = working['trade_date']
    if period == 'daily':
        aggregated = working
    else:
        frequency = 'W-FRI' if period == 'weekly' else 'M'
        aggregated = (
            working.set_index('trade_date')
            .resample(frequency)
            .agg(
                {
                    'bucket_trade_date': 'last',
                    'stock_id': 'last',
                    'open_price': 'first',
                    'high_price': 'max',
                    'low_price': 'min',
                    'close_price': 'last',
                    'volume': 'sum',
                    'chip_score': 'last',
                    'foreign_buy': lambda values: values.sum(min_count=1),
                    'trust_buy': lambda values: values.sum(min_count=1),
                    'dealer_buy': lambda values: values.sum(min_count=1),
                }
            )
            .dropna(subset=['open_price', 'high_price', 'low_price', 'close_price'])
            .rename(columns={'bucket_trade_date': 'trade_date'})
            .reset_index(drop=True)
        )

    if aggregated.empty:
        return aggregated

    aggregated['ma5'] = aggregated['close_price'].rolling(window=5, min_periods=1).mean()
    aggregated['ma20'] = aggregated['close_price'].rolling(window=20, min_periods=1).mean()
    aggregated['ma60'] = aggregated['close_price'].rolling(window=60, min_periods=1).mean()
    aggregated['rsi'] = calculate_rsi(aggregated['close_price'])
    aggregated['bias'] = ((aggregated['close_price'] - aggregated['ma20']) / aggregated['ma20']) * 100
    macd_line, signal_line, macd_hist = _calculate_macd_components(aggregated['close_price'])
    aggregated['macd_line'] = macd_line
    aggregated['macd_signal'] = signal_line
    aggregated['macd_hist'] = macd_hist
    kd_k, kd_d = calculate_kd_full(aggregated[['high_price', 'low_price', 'close_price']])
    aggregated['kd_k'] = kd_k
    aggregated['kd_d'] = kd_d
    aggregated['trade_date'] = pd.to_datetime(aggregated['trade_date'], errors='coerce')
    return aggregated.dropna(subset=['trade_date']).reset_index(drop=True)


def _serialize_dashboard_value_series(
    frame: pd.DataFrame,
    column: str,
    *,
    color_fn=None,
) -> list[dict[str, object]]:
    series: list[dict[str, object]] = []
    if column not in frame.columns:
        return series
    for _, row in frame.iterrows():
        value = safe_float(row.get(column))
        if value is None:
            continue
        entry: dict[str, object] = {
            'time': normalize_date_str(row.get('trade_date')),
            'value': float(value),
        }
        if color_fn is not None:
            entry['color'] = color_fn(value)
        series.append(entry)
    return series


def _format_war_room_levels(
    candidates: list[tuple[str, object]],
    *,
    close_price: float,
    is_support: bool,
) -> list[dict[str, object]]:
    seen: set[float] = set()
    filtered: list[tuple[float, str]] = []
    for source_name, raw_value in candidates:
        level_value = safe_float(raw_value)
        if level_value is None:
            continue
        if is_support and level_value > close_price:
            continue
        if not is_support and level_value < close_price:
            continue
        rounded_value = round(float(level_value), 2)
        if rounded_value in seen:
            continue
        seen.add(rounded_value)
        filtered.append((rounded_value, source_name))

    filtered.sort(key=lambda item: item[0], reverse=is_support)
    prefix = 'S' if is_support else 'R'
    level_kind = 'support' if is_support else 'resistance'
    return [
        {
            'label': f'{prefix}{index}',
            'kind': level_kind,
            'value': value,
            'source': source_name,
        }
        for index, (value, source_name) in enumerate(filtered[:2], start=1)
    ]


def _build_war_room_structure(frame: pd.DataFrame, period: str) -> dict[str, object]:
    empty_payload = {
        'status': 'unavailable',
        'source_mode': 'unavailable',
        'method': None,
        'as_of_date': normalize_date_str(frame.iloc[-1].get('trade_date')) if not frame.empty else None,
        'supports': [],
        'resistances': [],
        'message': '結構價位資料不足。',
    }
    if frame.empty:
        return empty_payload

    latest_close = safe_float(frame.iloc[-1].get('close_price'))
    if latest_close is None or len(frame) < 4:
        return empty_payload

    direct_threshold = {'daily': 20, 'weekly': 8, 'monthly': 4}.get(period, 20)
    source_mode = 'direct' if len(frame) >= direct_threshold else 'proxy'
    status = 'ok' if source_mode == 'direct' else 'partial'
    method = 'swing_range' if source_mode == 'direct' else 'range_proxy'
    lookback = min(len(frame), {'daily': 40, 'weekly': 24, 'monthly': 12}.get(period, 40))
    recent = frame.tail(lookback)

    supports = _format_war_room_levels(
        [
            ('range_low', recent['low_price'].min()),
            ('recent_low', recent['low_price'].tail(max(3, lookback // 2)).min()),
            ('ma20', recent['ma20'].iloc[-1] if 'ma20' in recent.columns else None),
            ('ma60', recent['ma60'].iloc[-1] if 'ma60' in recent.columns else None),
        ],
        close_price=latest_close,
        is_support=True,
    )
    resistances = _format_war_room_levels(
        [
            ('range_high', recent['high_price'].max()),
            ('recent_high', recent['high_price'].tail(max(3, lookback // 2)).max()),
            ('ma20', recent['ma20'].iloc[-1] if 'ma20' in recent.columns else None),
            ('ma60', recent['ma60'].iloc[-1] if 'ma60' in recent.columns else None),
        ],
        close_price=latest_close,
        is_support=False,
    )
    if not supports and not resistances:
        return empty_payload

    return {
        'status': status,
        'source_mode': source_mode,
        'method': method,
        'as_of_date': normalize_date_str(frame.iloc[-1].get('trade_date')),
        'supports': supports,
        'resistances': resistances,
        'message': '已解析結構支撐壓力。' if status == 'ok' else '使用 proxy 結構價位。',
    }


def _build_war_room_flow_pane(frame: pd.DataFrame) -> dict[str, object]:
    direct_columns = ['foreign_buy', 'trust_buy', 'dealer_buy']
    has_direct_flow = any(column in frame.columns and frame[column].notna().any() for column in direct_columns)
    if has_direct_flow:
        total_flow = frame[direct_columns].sum(axis=1, min_count=1)
        flow_frame = frame.copy()
        flow_frame['flow_total'] = total_flow
        return {
            'label': 'Flow',
            'status': 'ok',
            'source_mode': 'direct',
            'proxy_basis': [],
            'message': '使用法人買賣超直接資料。',
            'series': {
                'foreign_buy': _serialize_dashboard_value_series(flow_frame, 'foreign_buy'),
                'trust_buy': _serialize_dashboard_value_series(flow_frame, 'trust_buy'),
                'dealer_buy': _serialize_dashboard_value_series(flow_frame, 'dealer_buy'),
                'total': _serialize_dashboard_value_series(flow_frame, 'flow_total'),
            },
        }

    if 'chip_score' in frame.columns and frame['chip_score'].notna().any():
        return {
            'label': 'Flow',
            'status': 'partial',
            'source_mode': 'proxy',
            'proxy_basis': ['chip_score'],
            'message': 'Direct institutional series unavailable; using chip_score proxy.',
            'series': {
                'chip_score': _serialize_dashboard_value_series(frame, 'chip_score'),
            },
        }

    return {
        'label': 'Flow',
        'status': 'unavailable',
        'source_mode': 'unavailable',
        'proxy_basis': [],
        'message': '法人流向資料暫不可用。',
        'series': {},
    }


def _build_war_room_selected_panes(
    frame: pd.DataFrame,
    selected_panes: list[str],
    flow_pane: dict[str, object],
) -> dict[str, object]:
    panes: dict[str, object] = {}

    if 'macd' in selected_panes:
        panes['macd'] = {
            'label': 'MACD',
            'status': 'ok',
            'source_mode': 'derived',
            'series': {
                'macd': _serialize_dashboard_value_series(frame, 'macd_line'),
                'signal': _serialize_dashboard_value_series(frame, 'macd_signal'),
                'histogram': _serialize_dashboard_value_series(
                    frame,
                    'macd_hist',
                    color_fn=lambda value: '#f85149' if value >= 0 else '#2ea043',
                ),
            },
        }

    if 'rsi' in selected_panes:
        panes['rsi'] = {
            'label': 'RSI',
            'status': 'ok',
            'source_mode': 'derived',
            'series': {
                'rsi': _serialize_dashboard_value_series(frame, 'rsi'),
                'overbought': [
                    {'time': normalize_date_str(row.get('trade_date')), 'value': 70.0}
                    for _, row in frame.iterrows()
                ],
                'oversold': [
                    {'time': normalize_date_str(row.get('trade_date')), 'value': 30.0}
                    for _, row in frame.iterrows()
                ],
            },
        }

    if 'kd' in selected_panes:
        panes['kd'] = {
            'label': 'KD',
            'status': 'ok',
            'source_mode': 'derived',
            'series': {
                'k': _serialize_dashboard_value_series(frame, 'kd_k'),
                'd': _serialize_dashboard_value_series(frame, 'kd_d'),
            },
        }

    if 'flow' in selected_panes:
        panes['flow'] = flow_pane

    return panes


def _build_war_room_price_map(
    *,
    quote: dict[str, object],
    indicators: dict[str, object],
    structure: dict[str, object],
) -> dict[str, object]:
    close_price = safe_float(quote.get('close_price'))
    supports = list(structure.get('supports') or [])
    resistances = list(structure.get('resistances') or [])
    nearest_support = safe_float(supports[0].get('value')) if supports else None
    nearest_resistance = safe_float(resistances[0].get('value')) if resistances else None

    distance_to_support = None
    distance_to_resistance = None
    if close_price and nearest_support is not None:
        distance_to_support = round(((close_price - nearest_support) / close_price) * 100, 2)
    if close_price and nearest_resistance is not None:
        distance_to_resistance = round(((nearest_resistance - close_price) / close_price) * 100, 2)

    ma_levels = []
    for key, label in (('ma5', 'MA5'), ('ma20', 'MA20'), ('ma60', 'MA60')):
        value = safe_float(indicators.get(key))
        if value is not None:
            ma_levels.append({'key': key, 'label': label, 'value': float(value)})

    return {
        'status': 'ok' if close_price is not None else 'unavailable',
        'close_price': close_price,
        'open_price': safe_float(quote.get('open_price')),
        'session_high': safe_float(quote.get('high_price')),
        'session_low': safe_float(quote.get('low_price')),
        'supports': supports,
        'resistances': resistances,
        'ma_levels': ma_levels,
        'distance_to_support_pct': distance_to_support,
        'distance_to_resistance_pct': distance_to_resistance,
    }


def _build_war_room_quant_status(
    *,
    latest_row,
    report: dict[str, object],
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    close_price = safe_float(latest_row.get('close_price'))
    ma20 = safe_float(latest_row.get('ma20'))
    rsi_value = safe_float(latest_row.get('rsi'))
    chip_score = safe_float(latest_row.get('chip_score'))
    ai_score = safe_float(report.get('ai_score'))

    if close_price is not None and ma20 is not None:
        cards.append(
            {
                'key': 'trend',
                'label': '趨勢',
                'value': '站上 MA20' if close_price >= ma20 else '跌破 MA20',
                'tone': 'bullish' if close_price >= ma20 else 'bearish',
            }
        )
    if rsi_value is not None:
        cards.append(
            {
                'key': 'rsi',
                'label': 'RSI',
                'value': round(float(rsi_value), 2),
                'tone': 'bearish' if rsi_value >= 70 else 'bullish' if rsi_value <= 30 else 'neutral',
            }
        )
    if chip_score is not None:
        cards.append(
            {
                'key': 'chip_score',
                'label': 'Chip Score',
                'value': round(float(chip_score), 3),
                'tone': 'bullish' if chip_score >= 0 else 'bearish',
            }
        )
    if ai_score is not None:
        cards.append(
            {
                'key': 'ai_score',
                'label': 'AI Score',
                'value': round(float(ai_score), 3),
                'tone': 'bullish' if ai_score >= 0.6 else 'neutral',
            }
        )
    return cards


def _build_war_room_chip_flow_summary(flow_pane: dict[str, object], latest_row) -> dict[str, object]:
    chip_score = safe_float(latest_row.get('chip_score'))
    if flow_pane.get('source_mode') == 'direct':
        foreign_buy = safe_float(latest_row.get('foreign_buy')) or 0.0
        trust_buy = safe_float(latest_row.get('trust_buy')) or 0.0
        dealer_buy = safe_float(latest_row.get('dealer_buy')) or 0.0
        total_flow = foreign_buy + trust_buy + dealer_buy
        direction = '偏多' if total_flow >= 0 else '偏空'
        return {
            'status': 'ok',
            'source_mode': 'direct',
            'message': f'法人合計 {total_flow:+.0f} 張，籌碼 {direction}。',
            'chip_score': chip_score,
            'foreign_buy': foreign_buy,
            'trust_buy': trust_buy,
            'dealer_buy': dealer_buy,
            'net_flow': total_flow,
        }

    if flow_pane.get('source_mode') == 'proxy':
        return {
            'status': 'partial',
            'source_mode': 'proxy',
            'message': 'Direct institutional series unavailable; using chip_score proxy.',
            'chip_score': chip_score,
        }

    return {
        'status': 'unavailable',
        'source_mode': 'unavailable',
        'message': '籌碼流向資料暫不可用。',
        'chip_score': chip_score,
    }


def _build_war_room_fundamentals(report: dict[str, object]) -> dict[str, object]:
    revenue_yoy = safe_float(report.get('revenue_yoy'))
    op_margin = safe_float(report.get('op_margin'))
    if op_margin is None:
        op_margin = safe_float(report.get('op_profit_margin'))
    eps_value = safe_float(report.get('eps'))
    available_count = sum(value is not None for value in (revenue_yoy, op_margin, eps_value))
    status = 'ok' if available_count >= 2 else 'partial' if available_count == 1 else 'unavailable'
    return {
        'status': status,
        'revenue_yoy': revenue_yoy,
        'op_margin': op_margin,
        'eps': eps_value,
        'strategy_name': str(report.get('strategy_name') or ''),
    }


def _build_war_room_tactical_summary(
    *,
    latest_row,
    structure: dict[str, object],
    news_info: dict[str, object],
    rule_report: dict[str, object],
) -> dict[str, object]:
    close_price = safe_float(latest_row.get('close_price'))
    ma20 = safe_float(latest_row.get('ma20'))
    confidence = safe_int(rule_report.get('confidence'))
    nearest_support = safe_float((structure.get('supports') or [{}])[0].get('value')) if structure.get('supports') else None
    nearest_resistance = safe_float((structure.get('resistances') or [{}])[0].get('value')) if structure.get('resistances') else None

    tone = 'neutral'
    headline = str(rule_report.get('summary') or '等待下一步確認')
    message_parts: list[str] = []
    if close_price is not None and ma20 is not None:
        if close_price >= ma20:
            tone = 'bullish'
            message_parts.append('價格站在 MA20 之上。')
        else:
            tone = 'bearish'
            message_parts.append('價格尚未重新站回 MA20。')
    if nearest_support is not None:
        message_parts.append(f'最近支撐 {nearest_support:.2f}')
    if nearest_resistance is not None:
        message_parts.append(f'最近壓力 {nearest_resistance:.2f}')
    if news_info.get('title'):
        message_parts.append(str(news_info.get('title')))
    return {
        'status': 'ok',
        'headline': headline,
        'tone': tone,
        'message': ' '.join(message_parts).strip() or '暫無明確戰術摘要。',
        'confidence': confidence,
    }


def _overlay_dashboard_health_check_payload(
    base_payload: dict[str, object],
    *,
    trend_payload: dict[str, object] | None,
    screening_payload: dict[str, object] | None,
    requested_period: str = 'daily',
) -> dict[str, object]:
    payload = dict(base_payload)
    payload['source'] = _merge_unique_strings(
        payload.get('source'),
        trend_payload.get('source') if trend_payload else [],
        screening_payload.get('source') if screening_payload else [],
        'mcp_aggregation',
    )
    payload['warnings'] = _merge_unique_strings(
        payload.get('warnings'),
        trend_payload.get('warnings') if trend_payload else [],
        screening_payload.get('warnings') if screening_payload else [],
    )
    payload['degraded_fields'] = _merge_unique_strings(
        trend_payload.get('degraded_fields') if trend_payload else [],
        screening_payload.get('degraded_fields') if screening_payload else [],
    )

    if trend_payload:
        payload['requested_date'] = trend_payload.get('requested_date') or payload.get('requested_date')
        payload['as_of_date'] = trend_payload.get('as_of_date') or payload.get('as_of_date')
        payload['fallback_used'] = bool(trend_payload.get('fallback_used', payload.get('fallback_used')))
        payload['symbol'] = str(trend_payload.get('stock_id') or payload.get('symbol') or '')
        trend_record = trend_payload.get('record') or {}
        if isinstance(trend_record, dict) and trend_record.get('sector') and not payload.get('sector'):
            payload['sector'] = trend_record.get('sector')
        if requested_period == 'daily':
            for key in ('quote', 'indicators', 'institutional'):
                merged = dict(payload.get(key) or {})
                incoming = trend_payload.get(key)
                if isinstance(incoming, dict):
                    merged.update({sub_key: sub_value for sub_key, sub_value in incoming.items() if sub_value is not None})
                payload[key] = merged
            payload['series'] = _merge_dashboard_series_payload(payload.get('series'), trend_payload.get('series'))
        payload['cache_status'] = trend_payload.get('cache_status') or payload.get('cache_status') or 'miss'
        payload['payload_version'] = trend_payload.get('payload_version') or payload.get('payload_version')

    if screening_payload:
        screening_record = screening_payload.get('record') or {}
        if isinstance(screening_record, dict) and screening_record.get('sector') and not payload.get('sector'):
            payload['sector'] = screening_record.get('sector')
        payload['screening'] = dict(screening_payload.get('screening') or {})
        payload['report_sections'] = dict(screening_payload.get('report_sections') or {})
        if not payload.get('payload_version'):
            payload['payload_version'] = screening_payload.get('payload_version')

    payload['status'] = _select_dashboard_status(
        payload.get('status'),
        trend_payload.get('status') if trend_payload else None,
        screening_payload.get('status') if screening_payload else None,
    )
    if payload['status'] == 'ok':
        payload['message'] = '已取得個股健檢資料。'
    elif payload['status'] in {'partial', 'degraded'}:
        payload['message'] = '已取得個股健檢資料，但部分 MCP 聚合欄位降級。'
    return payload


def _overlay_dashboard_macro_payload(
    base_payload: dict[str, object],
    *,
    hotspot_payload: dict[str, object] | None,
) -> dict[str, object]:
    payload = dict(base_payload)
    if not hotspot_payload:
        payload['source'] = _merge_unique_strings(payload.get('source'), 'local_macro_fallback')
        payload['cache_status'] = payload.get('cache_status') or 'miss'
        return payload

    payload['status'] = _select_dashboard_status(payload.get('status'), hotspot_payload.get('status'))
    payload['as_of_date'] = hotspot_payload.get('as_of_date') or payload.get('as_of_date')
    payload['source'] = _merge_unique_strings(payload.get('source'), hotspot_payload.get('source'), 'mcp_aggregation')
    payload['warnings'] = _merge_unique_strings(payload.get('warnings'), hotspot_payload.get('warnings'))
    payload['degraded_fields'] = _merge_unique_strings(hotspot_payload.get('degraded_fields'))
    payload['cache_status'] = hotspot_payload.get('cache_status') or payload.get('cache_status') or 'miss'
    payload['payload_version'] = hotspot_payload.get('payload_version') or payload.get('payload_version')
    payload['market_hotspot'] = {
        'breadth': dict(hotspot_payload.get('breadth') or {}),
        'hotspots': dict(hotspot_payload.get('hotspots') or {}),
    }
    if payload['status'] == 'ok':
        payload['message'] = '已取得大盤總經摘要。'
    else:
        payload['message'] = '已取得大盤總經摘要，但部分 MCP 聚合欄位降級。'
    return payload


def _build_dashboard_health_check_payload_local(
    stock_id: str,
    requested_date: str | None = None,
    *,
    period: str | None = None,
    overlays=None,
    panes=None,
) -> dict[str, object]:
    normalized_stock_id = str(stock_id or '').strip()
    normalized_requested_date = normalize_date_str(requested_date) or _current_line_date()
    normalized_period = _normalize_war_room_period(period)
    selected_overlays = _normalize_war_room_selection(
        overlays,
        options=_WAR_ROOM_OVERLAY_OPTIONS,
        default_values=_WAR_ROOM_DEFAULT_OVERLAYS,
    )
    selected_panes = _normalize_war_room_selection(
        panes,
        options=_WAR_ROOM_PANE_OPTIONS,
        default_values=_WAR_ROOM_DEFAULT_PANES,
    )
    view_state = _build_war_room_view_state(
        period=normalized_period,
        overlays=selected_overlays,
        panes=selected_panes,
    )
    payload: dict[str, object] = {
        'status': 'empty',
        'symbol': normalized_stock_id,
        'requested_date': normalized_requested_date,
        'as_of_date': None,
        'fallback_used': False,
        'period': normalized_period,
        'view_state': view_state,
        'source': ['daily_market_data', 'daily_recommendations', 'financial_statements', 'monthly_revenue'],
        'message': '查無資料',
        'war_room': _build_empty_war_room_payload(
            stock_id=normalized_stock_id,
            requested_date=normalized_requested_date,
            period=normalized_period,
            view_state=view_state,
        ),
    }
    if not normalized_stock_id:
        payload['message'] = '缺少股票代號。'
        return payload

    history_df = get_stock_history(normalized_stock_id, limit=360, end_date=normalized_requested_date)
    prepared_history = _prepare_dashboard_history_frame(history_df)
    aggregated_history = _aggregate_dashboard_history(prepared_history, normalized_period)
    if aggregated_history.empty:
        payload['message'] = f'查無 {normalized_stock_id} 的可用行情資料。'
        payload['llm_report'] = _build_dashboard_llm_report(normalized_stock_id, {}, {}, {}, {})
        return payload

    latest_row = aggregated_history.iloc[-1]
    as_of_date = normalize_date_str(latest_row.get('trade_date'))
    fallback_used = as_of_date != normalized_requested_date
    report = get_stock_report(normalized_stock_id, as_of_date=as_of_date) or {'stock_id': normalized_stock_id, 'trade_date': as_of_date}

    sector = get_stock_sector(normalized_stock_id)
    market_snapshot = _build_market_snapshot()
    chip_snapshot = _build_chip_snapshot()
    with _live_signal_news_timeout_scope():
        stock_mentions_map = _get_stock_mentions_map([normalized_stock_id])
    news_info = _get_stock_specific_news_summary(normalized_stock_id, stock_mentions_map)
    if not news_info.get('items'):
        news_info = _get_sector_news_summary(sector, as_of_date)

    rule_report = _build_dashboard_rule_report(
        normalized_stock_id,
        report,
        latest_row,
        news_info,
        market_snapshot,
        chip_snapshot,
    )
    llm_report = _build_dashboard_llm_report(
        normalized_stock_id,
        report,
        rule_report,
        market_snapshot,
        chip_snapshot,
    )

    candles = []
    volume_series = []
    ma5_series = []
    ma20_series = []
    ma60_series = []
    for _, row in aggregated_history.iterrows():
        trade_date = normalize_date_str(row.get('trade_date'))
        open_price = safe_float(row.get('open_price'))
        high_price = safe_float(row.get('high_price'))
        low_price = safe_float(row.get('low_price'))
        close_price = safe_float(row.get('close_price'))
        volume = safe_int(row.get('volume')) or 0
        if None not in (open_price, high_price, low_price, close_price):
            candles.append(
                {
                    'time': trade_date,
                    'open': float(open_price),
                    'high': float(high_price),
                    'low': float(low_price),
                    'close': float(close_price),
                }
            )
        if close_price is not None:
            color = '#2ea043' if open_price is not None and close_price >= open_price else '#f85149'
            volume_series.append({'time': trade_date, 'value': volume, 'color': color})
        for source_key, target_list in (('ma5', ma5_series), ('ma20', ma20_series), ('ma60', ma60_series)):
            value = safe_float(row.get(source_key))
            if value is not None:
                target_list.append({'time': trade_date, 'value': float(value)})

    quote = {
        'open_price': safe_float(latest_row.get('open_price')),
        'high_price': safe_float(latest_row.get('high_price')),
        'low_price': safe_float(latest_row.get('low_price')),
        'close_price': safe_float(latest_row.get('close_price')),
        'volume': safe_int(latest_row.get('volume')),
    }
    indicators = {
        'ma5': safe_float(latest_row.get('ma5')),
        'ma20': safe_float(latest_row.get('ma20')),
        'ma60': safe_float(latest_row.get('ma60')),
        'rsi': safe_float(latest_row.get('rsi')),
        'bias': safe_float(latest_row.get('bias')),
        'ai_score': safe_float(report.get('ai_score')),
        'chip_score': safe_float(latest_row.get('chip_score')),
    }
    institutional = {
        'foreign_buy': safe_int(latest_row.get('foreign_buy')),
        'trust_buy': safe_int(latest_row.get('trust_buy')),
        'dealer_buy': safe_int(latest_row.get('dealer_buy')),
    }
    structure_payload = _build_war_room_structure(aggregated_history, normalized_period)
    flow_pane = _build_war_room_flow_pane(aggregated_history)
    fundamentals_payload = _build_war_room_fundamentals(report)

    payload.update(
        {
            'status': 'ok',
            'symbol': normalized_stock_id,
            'sector': sector,
            'requested_date': normalized_requested_date,
            'as_of_date': as_of_date,
            'fallback_used': fallback_used,
            'message': '已取得個股健檢資料。',
            'quote': quote,
            'indicators': indicators,
            'institutional': institutional,
            'series': {
                'candles': candles,
                'volume': volume_series,
                'ma5': ma5_series,
                'ma20': ma20_series,
                'ma60': ma60_series,
            },
            'news': news_info,
            'rule_report': rule_report,
            'llm_report': llm_report,
            'war_room': {
                'timeframe': {
                    'stock_id': normalized_stock_id,
                    'period': normalized_period,
                    'label': view_state.get('period_label'),
                    'requested_date': normalized_requested_date,
                    'as_of_date': as_of_date,
                    'source_mode': 'direct',
                    'status': 'ok',
                    'resolved_bars': len(aggregated_history),
                },
                'structure': structure_payload,
                'price_map': _build_war_room_price_map(
                    quote=quote,
                    indicators=indicators,
                    structure=structure_payload,
                ),
                'panes': _build_war_room_selected_panes(
                    aggregated_history,
                    selected_panes,
                    flow_pane,
                ),
                'quant_status': _build_war_room_quant_status(
                    latest_row=latest_row,
                    report=report,
                ),
                'tactical_summary': _build_war_room_tactical_summary(
                    latest_row=latest_row,
                    structure=structure_payload,
                    news_info=news_info,
                    rule_report=rule_report,
                ),
                'chip_flow': _build_war_room_chip_flow_summary(flow_pane, latest_row),
                'fundamentals': fundamentals_payload,
                'news': news_info,
                'provenance': {
                    'stock_id': normalized_stock_id,
                    'requested_date': normalized_requested_date,
                    'period': normalized_period,
                    'as_of_date': as_of_date,
                    'fallback_used': fallback_used,
                },
            },
        }
    )
    return payload


def _build_dashboard_health_check_payload(
    stock_id: str,
    requested_date: str | None = None,
    *,
    period: str | None = None,
    overlays=None,
    panes=None,
) -> dict[str, object]:
    normalized_stock_id = str(stock_id or '').strip()
    normalized_requested_date = normalize_date_str(requested_date) or _current_line_date()
    normalized_period = _normalize_war_room_period(period)
    if not normalized_stock_id:
        return _build_dashboard_health_check_payload_local(
            stock_id,
            normalized_requested_date,
            period=normalized_period,
            overlays=overlays,
            panes=panes,
        )

    client = MCPClient()

    trend_payload = resolve_dashboard_aggregation_cache(
        'twse_stock_trend',
        stock_id=normalized_stock_id,
        market='ALL',
        requested_date=normalized_requested_date,
        refresh_fn=lambda: client.get_twse_stock_trend_sync(
            stock_id=normalized_stock_id,
            trade_date=normalized_requested_date,
        ),
        ttl_seconds=300,
    )
    screening_payload = resolve_dashboard_aggregation_cache(
        'investment_screening',
        stock_id=normalized_stock_id,
        market='ALL',
        requested_date=normalized_requested_date,
        refresh_fn=lambda: client.get_investment_screening_sync(
            stock_id=normalized_stock_id,
            trade_date=normalized_requested_date,
        ),
        ttl_seconds=300,
    )
    base_payload = _build_dashboard_health_check_payload_local(
        normalized_stock_id,
        normalized_requested_date,
        period=normalized_period,
        overlays=overlays,
        panes=panes,
    )
    if not trend_payload and not screening_payload:
        base_payload['status'] = _select_dashboard_status(base_payload.get('status'), 'degraded')
        base_payload['source'] = _merge_unique_strings(base_payload.get('source'), 'local_fallback')
        base_payload['warnings'] = _merge_unique_strings(base_payload.get('warnings'), 'MCP aggregation unavailable; using local fallback')
        base_payload['cache_status'] = 'miss'
        return base_payload

    resolved_payload = _overlay_dashboard_health_check_payload(
        base_payload,
        trend_payload=trend_payload if isinstance(trend_payload, dict) else None,
        screening_payload=screening_payload if isinstance(screening_payload, dict) else None,
        requested_period=normalized_period,
    )
    resolved_payload['cache_keys'] = {
        'trend': build_dashboard_aggregation_cache_key(
            'twse_stock_trend',
            stock_id=normalized_stock_id,
            market='ALL',
            requested_date=normalized_requested_date,
        ),
        'screening': build_dashboard_aggregation_cache_key(
            'investment_screening',
            stock_id=normalized_stock_id,
            market='ALL',
            requested_date=normalized_requested_date,
        ),
    }
    return resolved_payload


def _build_dashboard_macro_payload_local() -> dict[str, object]:
    market_snapshot = _build_market_snapshot()
    chip_snapshot = _build_chip_snapshot()
    display_date = str(market_snapshot.get('date_str') or chip_snapshot.get('date_str') or _current_line_date())
    try:
        from core.news_agent import get_morning_news_summary

        news_summary = get_morning_news_summary()
        if not str(news_summary or '').strip() or str(news_summary).startswith('⚠️'):
            news_summary = '目前暫無可用新聞摘要，顯示市場與籌碼概況。'
    except Exception as exc:
        print(f'⚠️ Macro beta 新聞摘要失敗: {exc}')
        news_summary = '目前暫時無法取得新聞摘要。'

    degraded = market_snapshot.get('status') != 'ok' or chip_snapshot.get('status') != 'ok'
    status = 'degraded' if degraded else 'ok'
    return {
        'status': status,
        'as_of_date': display_date,
        'market_snapshot': market_snapshot,
        'chip_snapshot': chip_snapshot,
        'news_summary': news_summary,
        'message': '已取得大盤總經摘要。' if status == 'ok' else '部分大盤資料暫不可用。',
    }


def _build_dashboard_macro_payload(requested_date: str | None = None) -> dict[str, object]:
    normalized_requested_date = normalize_date_str(requested_date) or _current_line_date()
    client = MCPClient()

    hotspot_payload = resolve_dashboard_aggregation_cache(
        'market_hotspot',
        market='ALL',
        requested_date=normalized_requested_date,
        refresh_fn=lambda: client.get_market_hotspot_sync(normalized_requested_date),
        ttl_seconds=300,
    )
    base_payload = _build_dashboard_macro_payload_local()
    resolved_payload = _overlay_dashboard_macro_payload(
        base_payload,
        hotspot_payload=hotspot_payload if isinstance(hotspot_payload, dict) else None,
    )
    resolved_payload['cache_key'] = build_dashboard_aggregation_cache_key(
        'market_hotspot',
        market='ALL',
        requested_date=normalized_requested_date,
    )
    return resolved_payload


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

    csv_path = REPO_ROOT / 'ML_Data' / 'backtest_result.csv'
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
            'date_str': _current_line_date(),
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
            'date_str': _current_line_date(),
        }

    total_roi, max_drawdown = _calculate_trade_sequence_drawdown(valid_profits)
    trade_count = len(valid_profits)
    win_rate = round((sum(1 for value in valid_profits if value > 0) / trade_count) * 100, 1) if trade_count else 0.0
    avg_hold_days = round(float(prepared['days'].dropna().mean()), 1) if 'days' in prepared.columns and prepared['days'].notna().any() else None

    sort_columns = [column for column in ('sell_date', 'buy_date') if column in prepared.columns]
    latest_trade = prepared.sort_values(sort_columns, ascending=False, na_position='last').iloc[0] if sort_columns else prepared.iloc[0]
    latest_trade_summary = _format_backtest_trade_summary(latest_trade)
    latest_date = latest_trade.get('sell_date') if 'sell_date' in latest_trade.index else None
    date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') and not pd.isna(latest_date) else _current_line_date()

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
    market_snapshot = _build_market_snapshot()
    chip_snapshot = _build_chip_snapshot()
    display_date = str(market_snapshot.get('date_str') or chip_snapshot.get('date_str') or _current_line_date())

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
    options = _list_strategy_picker_options()
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
            date_str=_current_line_date(),
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
    recommendation = get_strategy_recommendation(as_flex=True, strategy_key=strategy_key)
    if isinstance(recommendation, str):
        return [
            create_empty_state_flex(
                title=f'🎯 {display_name}',
                message=recommendation,
                date_str=_current_line_date(),
                subtitle='請重新選擇策略，或稍後再試。',
            )
        ]
    return [recommendation]


def _build_journal_reflection_messages() -> list:
    options = _list_strategy_picker_options()
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
            date_str=_current_line_date(),
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

    snapshot = _build_strategy_backtest_snapshot(raw_strategy_key)
    strategy_name = str(snapshot.get('strategy_name') or raw_strategy_key.upper())
    if not snapshot.get('has_data'):
        return [
            create_empty_state_flex(
                title=f'📝 {strategy_name}',
                message='尚無該策略回測資料，可先執行該策略回測後再查看。',
                date_str=str(snapshot.get('date_str') or _current_line_date()),
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
            date_str=str(snapshot.get('date_str') or _current_line_date()),
            avg_hold_days=safe_float(snapshot.get('avg_hold_days')),
            latest_trade_summary=str(snapshot.get('latest_trade_summary') or ''),
            suggestions=_build_strategy_reflection_suggestions(snapshot),
            source_label=str(snapshot.get('source_label') or ''),
        )
    ]


def _build_market_summary_messages() -> list:
    cached = _postback_cache.get('market_summary')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = _resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = MCPClient().get_market_statistics_sync(trade_date)
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
        _postback_cache.set('market_summary', body)
        return [V3TextMessage(text=body)]
    except MCPClientError as exc:
        print(f'⚠️ MCP 大盤快照失敗: {exc}')
        return [V3TextMessage(text='📊 大盤快照\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。')]
    except Exception as exc:
        print(f'⚠️ _build_market_summary_messages 未預期錯誤: {exc}')
        return [V3TextMessage(text='📊 大盤快照\n\n資料處理異常，請稍後再試。')]


def _build_chip_trend_messages() -> list:
    cached = _postback_cache.get('chip_trend')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = _resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = MCPClient().get_foreign_investment_sync(trade_date)
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
        _postback_cache.set('chip_trend', body)
        return [V3TextMessage(text=body)]
    except MCPClientError as exc:
        print(f'⚠️ MCP 籌碼動向失敗: {exc}')
        return [V3TextMessage(text='🏦 籌碼動向\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。')]
    except Exception as exc:
        print(f'⚠️ _build_chip_trend_messages 未預期錯誤: {exc}')
        return [V3TextMessage(text='🏦 籌碼動向\n\n資料處理異常，請稍後再試。')]


def _build_random_strategy_messages() -> list:
    try:
        sm = strategy_manager
        pool = sm.get_random_strategy_pool()
        if not pool:
            return [V3TextMessage(text='🎲 策略盲盒\n\n目前策略池為空，請至設定頁面配置可用策略。')]

        shuffled = random.sample(pool, len(pool))
        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
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


def _extract_postback_action(data: str) -> str:
    return _parse_postback_payload(data).get('action', '')


def _register_postback_handlers() -> dict:
    return {
        'prompt_stock_diagnosis': lambda payload=None, source_id='': _build_stock_diagnosis_prompt_messages(source_id),
        'macro_summary': lambda payload=None, source_id='': _build_macro_news_messages(),
        'journal_reflection': lambda payload=None, source_id='': _build_journal_reflection_messages(),
        'choose_strategy': lambda payload=None, source_id='': _build_strategy_picker_messages(),
        'backtest_reflect': lambda payload=None, source_id='': _build_backtest_reflection_messages(payload),
        'strategy_select': lambda payload=None, source_id='': _build_selected_strategy_messages(payload),
        'select_strategy': lambda payload=None, source_id='': _build_selected_strategy_messages(payload),
        'get_macro_news': lambda payload=None, source_id='': _build_macro_news_messages(),
        'get_journal': lambda payload=None, source_id='': _build_journal_reflection_messages(),
        'market_summary': lambda payload=None, source_id='': _build_market_summary_messages(),
        'chip_trend': lambda payload=None, source_id='': _build_chip_trend_messages(),
        'random_strategy': lambda payload=None, source_id='': _build_random_strategy_messages(),
    }


def _build_postback_reply_messages(action: str, payload: dict[str, str] | None = None, source_id: str = '') -> list:
    handler_fn = _register_postback_handlers().get(action)
    if handler_fn is not None:
        return handler_fn(payload=payload, source_id=source_id)
    return [
        _build_postback_empty_state(
            title='⚠️ Rich Menu',
            message='尚未支援的 Rich Menu 指令。',
        )
    ]


def _load_strategy_candidates(active, strategy_key: str, market_df: pd.DataFrame, requested_date: str, limit: int) -> tuple[pd.DataFrame, dict, bool]:
    persisted, meta = get_recommendations_with_market_fallback(
        date_str=requested_date,
        strategy=strategy_key,
        limit=limit,
        max_fallback_age_days=Config.RECOMMENDATION_FALLBACK_MAX_AGE_DAYS,
    )
    if not persisted.empty:
        return merge_recommendations_with_market_data(persisted, market_df), meta, True
    return pd.DataFrame(), meta, bool(meta.get('has_persisted_snapshot', False))


def get_v30_recommendation():
    try:
        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
        if df.empty:
            return '💤 今日無資料'

        required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return f"⚠️ 資料庫缺少欄位: {', '.join(missing_cols)}\n請執行 core/calc_indicators.py"

        picks = []
        for _, row in df.iterrows():
            v30_result = calculate_v30_signal(row)
            if v30_result['signal_strength'] == 'strong':
                picks.append(
                    {
                        'stock_id': row['stock_id'],
                        'close_price': row['close_price'],
                        'rsi': row.get('rsi', 0),
                        'volume': row.get('volume', 0),
                        'stop_loss': v30_result['stop_loss'],
                        'take_profit': v30_result['take_profit'],
                        'foreign_buy': row.get('foreign_buy', 0),
                    }
                )
        return format_v30_recommendation(picks, date_str)
    except Exception as exc:
        print(f'❌ V30 推薦失敗: {exc}')
        traceback.print_exc()
        return f'❌ 運算錯誤: {str(exc)[:100]}'


def get_strategy_recommendation(as_flex: bool = False, strategy_key: str | None = None):
    try:
        mgr = StrategyManager()
        active = mgr.get_strategy(strategy_key) if strategy_key else mgr.get_active_strategy()
        if active is None:
            return '❌ 策略載入失敗，請先輸入「切換V30」設定策略'

        strategy_name = active.display_name
        active_strategy_key = active.name

        requested_date = _current_line_date()
        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
        if df.empty:
            return '💤 今日無資料\n請確認已執行 python jobs/update_database.py'

        df = supplement_financial_data(df)
        candidates, fallback_meta, has_persisted = _load_strategy_candidates(
            active=active,
            strategy_key=active_strategy_key,
            market_df=df,
            requested_date=requested_date,
            limit=5,
        )
        display_date = fallback_meta.get('recommendation_date') or date_str
        market_notice = format_market_fallback_notice(fallback_meta, strategy_name)

        if candidates.empty:
            warning_block = f'{market_notice}\n\n' if market_notice else ''
            if as_flex:
                subtitle = f'📅 {display_date}'
                if market_notice:
                    subtitle += f'｜{market_notice}'
                return create_empty_state_flex(
                    title=f'🎯 {strategy_name}',
                    message='今日無符合條件的股票，可改看其他策略或等待下一個交易日。',
                    date_str=display_date,
                    subtitle=subtitle,
                )
            return (
                f'🔍 【{strategy_name}】選股結果\n'
                f'日期：{display_date}\n\n'
                f'{warning_block}'
                '❌ 今日無符合條件的股票\n\n'
                '💡 建議：\n'
                '• 觀望等待進場訊號\n'
                '• 嘗試「切換V33」低波動等其他策略\n'
                '• 輸入「查看策略」檢視篩選條件'
            )

        has_ai = bool(has_persisted and 'ai_score' in candidates.columns and candidates['ai_score'].notna().any())

        top_n = candidates.head(5)
        total_count = len(candidates)
        stock_mentions_map = _get_stock_mentions_map([str(sid) for sid in top_n['stock_id'].tolist()])

        if as_flex:
            picks_list = []
            for _, row in top_n.iterrows():
                close = row.get('close_price', 0)
                sid = str(row.get('stock_id', '????'))
                news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
                picks_list.append(
                    {
                        'stock_id': sid,
                        'sector': get_stock_sector(sid),
                        'close_price': close,
                        'ai_score': row.get('ai_score', 0) if has_ai else 0,
                        'rsi': row.get('rsi', 0),
                        'volume': row.get('volume', 0),
                        'news_boost_reason': news_info['raw'],
                        'news_reason_items': news_info['items'],
                        'news_signal_title': news_info['title'],
                        'news_is_bearish': news_info['is_bearish'],
                        'stop_loss_price': close * (1 - active.stop_loss),
                        'take_profit_price': close * (1 + active.take_profit) if active.take_profit > 0 else 0,
                    }
                )
            return create_recommendation_carousel(
                picks=picks_list,
                strategy_name=strategy_name,
                date_str=display_date,
            )

        reply = f'🎯 【{strategy_name}】推薦\n'
        reply += f'📅 日期：{display_date}\n'
        if has_ai:
            reply += '🤖 AI 排名：已啟用\n'
        if market_notice:
            reply += f'{market_notice}\n'
        reply += '-' * 28 + '\n\n'

        for i, (_, row) in enumerate(top_n.iterrows(), 1):
            stock_id = row.get('stock_id', 'N/A')
            close = row.get('close_price', 0)
            rsi = row.get('rsi', 0)
            volume = row.get('volume', 0)
            sector = get_stock_sector(str(stock_id))
            news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
            news_reason = news_info['raw']

            sl_price = close * (1 - active.stop_loss)
            tp_price = close * (1 + active.take_profit) if active.take_profit > 0 else 0

            reply += f"{'🥇🥈🥉'[i-1] if i <= 3 else '▪️'} {i}. {stock_id}（{sector}）\n"
            reply += f'   💰 收盤：{close:.2f}'
            if has_ai:
                ai_pct = row.get('ai_score', 0) * 100
                reply += f'  🤖 {ai_pct:.0f}分'
            reply += '\n'
            reply += f'   📊 RSI: {rsi:.1f}'
            if volume > 0:
                reply += f'  📈 量: {volume/10000:.0f}萬'
            reply += '\n'
            if news_reason:
                reply += f"   {news_info['title']}\n"
                for item in news_info['items'][:3]:
                    reply += f'   • {item}\n'
            reply += f'   🛡️ 停損: {sl_price:.2f}'
            if tp_price > 0:
                reply += f'  🎯 停利: {tp_price:.2f}'
            reply += '\n\n'

        reply += '-' * 28 + '\n'
        reply += f'📊 共篩選出 {total_count} 檔'
        if total_count > 5:
            reply += '（顯示前 5 名）'
        reply += f'\n⏰ 最長持有：{active.max_hold_days} 天\n'
        reply += '⚠️ 僅供參考，請自行評估風險'
        return reply
    except Exception as exc:
        print(f'❌ 策略推薦失敗: {exc}')
        traceback.print_exc()
        return f'❌ 策略推薦失敗: {str(exc)[:100]}'


def query_stock(stock_id):
    try:
        baseline_date = _resolve_ui_baseline_date()
        query_kwargs = {'stock_id': stock_id}
        if baseline_date:
            query_kwargs['date_str'] = baseline_date
        df, date_str = get_stock_data(**query_kwargs)
        if df.empty:
            return f'🔍 找不到 {stock_id} 的資料'

        row = df.iloc[0]
        prob = 0.5
        try:
            from core.model_utils import load_model as _load_model

            mgr = StrategyManager()
            names = mgr.get_active_strategy_names()
            skey = names[0] if names else None
            model, features, _, _ = _load_model(skey)
            if model and features:
                df_feat = pd.DataFrame([row])
                for feature_name in features:
                    if feature_name not in df_feat.columns:
                        df_feat[feature_name] = 0
                prob = model.predict_proba(df_feat[features].fillna(0))[:, 1][0]
        except Exception:
            pass

        enable_strategy = get_setting('enable_strategy_report', 'true') == 'true'
        return format_stock_query(stock_id, date_str, row, prob, enable_strategy)
    except Exception as exc:
        print(f'❌ 個股查詢失敗: {exc}')
        traceback.print_exc()
        return f'❌ 查詢失敗: {str(exc)[:100]}'


def get_settings_info():
    try:
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        v30_stop_loss = float(get_setting('v30_stop_loss', str(Config.V30_PARAMS['STOP_LOSS'])))
        v30_take_profit = float(get_setting('v30_take_profit', str(Config.V30_PARAMS['TAKE_PROFIT'])))
        v30_max_hold = int(get_setting('v30_max_hold_days', str(Config.V30_PARAMS['MAX_HOLD_DAYS'])))

        v34_yoy = float(get_setting('v34_revenue_yoy_min', str(Config.V34_REVENUE_YOY_MIN)))
        v34_breakout = float(get_setting('v34_breakout_ratio', str(Config.V34_BREAKOUT_RATIO)))
        v34_volume = float(get_setting('v34_volume_ratio_min', str(Config.V34_VOLUME_RATIO_MIN)))
        v34_relaxed_yoy = float(get_setting('v34_relaxed_revenue_yoy_min', str(Config.V34_RELAXED_REVENUE_YOY_MIN)))
        v34_relaxed_breakout = float(get_setting('v34_relaxed_breakout_ratio', str(Config.V34_RELAXED_BREAKOUT_RATIO)))
        v34_relaxed_volume = float(get_setting('v34_relaxed_volume_ratio_min', str(Config.V34_RELAXED_VOLUME_RATIO_MIN)))

        v35_op = float(get_setting('v35_op_margin_min', str(Config.V35_OP_MARGIN_MIN)))
        v35_revenue = float(get_setting('v35_revenue_yoy_min', str(Config.V35_REVENUE_YOY_MIN)))
        v35_volume = float(get_setting('v35_volume_ratio_min', str(Config.V35_VOLUME_RATIO_MIN)))
        v35_relaxed_op = float(get_setting('v35_relaxed_op_margin_min', str(Config.V35_RELAXED_OP_MARGIN_MIN)))
        v35_relaxed_revenue = float(get_setting('v35_relaxed_revenue_yoy_min', str(Config.V35_RELAXED_REVENUE_YOY_MIN)))
        v35_relaxed_volume = float(get_setting('v35_relaxed_volume_ratio_min', str(Config.V35_RELAXED_VOLUME_RATIO_MIN)))

        msg = '⚙️ 【當前設定】\n'
        msg += '-' * 30 + '\n'
        msg += '🚀 V30 策略參數:\n'
        msg += f'  🛡️ 停損: {int(v30_stop_loss*100)}%\n'
        if v30_take_profit > 0:
            msg += f'  🎯 停利: {int(v30_take_profit*100)}%\n'
        else:
            msg += f'  🎯 停利: 不停利（持有至到期）\n'
        msg += f'  ⏰ 最長持有: {v30_max_hold}天\n\n'
        msg += '🚀 V34 參數（嚴格 / 放寬）:\n'
        msg += f'  📈 YoY: {v34_yoy:.1f}% / {v34_relaxed_yoy:.1f}%\n'
        msg += f'  💥 突破: {v34_breakout:.2f} / {v34_relaxed_breakout:.2f}\n'
        msg += f'  📦 量比: {v34_volume:.2f} / {v34_relaxed_volume:.2f}\n\n'
        msg += '💼 V35 參數（嚴格 / 放寬）:\n'
        msg += f'  🏢 營業利益率: {v35_op*100:.1f}% / {v35_relaxed_op*100:.1f}%\n'
        msg += f'  📈 營收YoY: {v35_revenue:.1f}% / {v35_relaxed_revenue:.1f}%\n'
        msg += f'  📦 量比: {v35_volume:.2f} / {v35_relaxed_volume:.2f}\n\n'
        msg += '🧠 AI 參數:\n'
        msg += f'  AI 門檻: {int(ai_threshold*100)}%\n'
        msg += '-' * 30 + '\n'
        msg += '💡 可用指令:\n'
        msg += '• 設定停損 5 (設為5%)\n'
        msg += '• 設定停利 20 (設為20%)\n'
        msg += '• 設定停利 0 (不停利)\n'
        msg += '• 設定信心 60 (AI門檻60%)\n'
        msg += '• 切換積極 / 切換平衡 / 切換寬鬆\n'
        msg += '• V34積極 / V34平衡 / V34寬鬆\n'
        msg += '• V35積極 / V35平衡 / V35寬鬆\n'
        msg += '• 設定V34 18 0.93 0.9\n'
        msg += '• 設定V35 6 0 0.8'
        return msg
    except Exception as exc:
        return f'❌ 讀取設定失敗: {exc}'


def main() -> int:
    print('=' * 60)
    print('[START] Line Bot V3.0 啟動中 (Facade -> app package)')
    print('[MODEL] 模型：按策略動態載入')
    print('[PORT] 伺服器端口: 1688')
    print('=' * 60)

    if os.getenv('LINE_RICH_MENU_AUTO_SYNC', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        try:
            from core.richmenu import sync_default_rich_menu_from_token

            rich_menu_id = sync_default_rich_menu_from_token()
            print(f'[OK] Rich Menu 綁定完成: {rich_menu_id}')
        except Exception as exc:
            print(f'[WARN] Rich Menu 自動綁定失敗: {exc}')

    app.run(host='0.0.0.0', port=1688, debug=False)
    return 0


from . import web_server as _web_server  # noqa: E402,F401
from .line_bot import callback, handle_message, postback_handler, reply_message  # noqa: E402


__all__ = [
    'app',
    'callback',
    'configuration',
    'current_user',
    'handle_message',
    'handler',
    'login_manager',
    'main',
    'postback_handler',
    'reply_message',
    'strategy_manager',
    'User',
    '_PostbackCache',
    '_apply_news_sentiment_overlay',
    '_build_chip_trend_messages',
    '_build_dashboard_health_check_payload',
    '_build_dashboard_macro_payload',
    '_build_journal_reflection_text',
    '_build_macro_news_messages',
    '_build_market_summary_messages',
    '_build_postback_reply_messages',
    '_build_random_strategy_messages',
    '_compact_command_key',
    '_extract_postback_action',
    '_get_sector_news_summary',
    '_get_stock_mentions_map',
    '_get_stock_specific_news_summary',
    '_is_quick_mode_cmd',
    '_load_backtest_summary_or_error',
    '_load_strategy_candidates',
    '_match_strategy_switch',
    '_normalize_backtest_dates',
    '_normalize_line_text',
    '_parse_news_reason',
    '_postback_cache',
    '_resolve_signal_news_info',
    '_run_portfolio_backtest',
    'get_ngrok_url',
    'get_settings_info',
    'get_strategy_recommendation',
    'get_v30_recommendation',
    'query_stock',
]
