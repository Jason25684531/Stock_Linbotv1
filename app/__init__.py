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
    get_stock_sector,
    merge_recommendations_with_market_data,
    normalize_date_str,
    safe_float,
    safe_int,
    supplement_financial_data,
    update_setting,
    validate_setting,
)
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