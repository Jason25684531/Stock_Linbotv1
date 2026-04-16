"""Canonical application package integrating Web and LINE entrypoints."""

# -*- coding: utf-8 -*-
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
    get_backtest_equity_curve,
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
    create_backtest_summary_flex,
    create_holdings_flex,
    create_news_flex,
    create_recommendation_carousel,
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
    try:
        from core.news_agent import get_stock_news_mentions

        return get_stock_news_mentions(stock_ids)
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


def _build_macro_news_messages() -> list:
    try:
        from core.news_agent import get_morning_news_summary

        news_summary = get_morning_news_summary()
        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        return [create_news_flex(news_summary, today_str)]
    except Exception as exc:
        print(f'⚠️ 總經摘要產生失敗: {exc}')
        return [V3TextMessage(text='📰 總經摘要\n\n目前暫時無法取得最新摘要，請稍後再試。')]


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
    try:
        recent_trades = get_recent_backtest_trades(limit=5) or []
    except Exception as exc:
        print(f'⚠️ 讀取最近交易失敗: {exc}')
        recent_trades = []

    try:
        open_holdings = get_open_holdings(limit=5) or []
    except Exception as exc:
        print(f'⚠️ 讀取持股日誌失敗: {exc}')
        open_holdings = []

    if not recent_trades:
        return '📝 日誌反思\n\n目前還沒有足夠的交易紀錄可供反思。\n可以先用「推薦」找標的，或先執行一次回測累積樣本。'

    profit_values = [safe_float(trade.get('profit_pct')) or 0.0 for trade in recent_trades]
    avg_profit = sum(profit_values) / len(profit_values)
    win_rate = sum(1 for value in profit_values if value > 0) / len(profit_values) * 100
    avg_hold_days = sum((safe_int(trade.get('days')) or 0) for trade in recent_trades) / len(recent_trades)
    last_trade = recent_trades[0]
    last_stock = last_trade.get('stock_id') or 'N/A'
    last_reason = last_trade.get('reason') or '未記錄'
    last_profit = safe_float(last_trade.get('profit_pct')) or 0.0
    holdings_count = len(open_holdings)

    return (
        '📝 日誌反思\n\n'
        f'最近 {len(recent_trades)} 筆回測交易勝率 {win_rate:.0f}%，平均報酬 {avg_profit:+.1f}%，平均持有 {avg_hold_days:.1f} 天。\n'
        f'最近一筆：{last_stock} {last_profit:+.1f}% ，出場原因：{last_reason}。\n'
        f'目前仍有 {holdings_count} 檔開倉，建議確認進場理由是否仍成立、停損停利是否有跟上。'
    )


def _extract_postback_action(data: str) -> str:
    raw = str(data or '').strip()
    if not raw:
        return ''
    parsed = parse_qs(raw, keep_blank_values=True)
    return (parsed.get('action') or [raw])[0].strip()


def _register_postback_handlers() -> dict:
    return {
        'get_macro_news': _build_macro_news_messages,
        'get_journal': lambda: [V3TextMessage(text=_build_journal_reflection_text())],
        'market_summary': _build_market_summary_messages,
        'chip_trend': _build_chip_trend_messages,
        'random_strategy': _build_random_strategy_messages,
    }


def _build_postback_reply_messages(action: str) -> list:
    handler_fn = _register_postback_handlers().get(action)
    if handler_fn is not None:
        return handler_fn()
    return [V3TextMessage(text='⚠️ 尚未支援的 Rich Menu 指令')]


def _load_strategy_candidates(active, strategy_key: str, market_df: pd.DataFrame, requested_date: str, limit: int) -> tuple[pd.DataFrame, dict, bool]:
    persisted, meta = get_recommendations_with_market_fallback(
        date_str=requested_date,
        strategy=strategy_key,
        limit=limit,
        max_fallback_age_days=Config.RECOMMENDATION_FALLBACK_MAX_AGE_DAYS,
    )
    if not persisted.empty:
        return merge_recommendations_with_market_data(persisted, market_df), meta, True
    return active.filter_candidates(market_df.copy()), meta, False


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


def get_strategy_recommendation(as_flex: bool = False):
    try:
        mgr = StrategyManager()
        active = mgr.get_active_strategy()
        if active is None:
            return '❌ 策略載入失敗，請先輸入「切換V30」設定策略'

        strategy_name = active.display_name
        strategy_key = active.name

        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
        if df.empty:
            return '💤 今日無資料\n請確認已執行 python jobs/update_database.py'

        df = supplement_financial_data(df)
        candidates, fallback_meta, has_persisted = _load_strategy_candidates(
            active=active,
            strategy_key=strategy_key,
            market_df=df,
            requested_date=date_str,
            limit=5,
        )
        display_date = fallback_meta.get('recommendation_date') or date_str
        market_notice = format_market_fallback_notice(fallback_meta, strategy_name)

        if candidates.empty:
            warning_block = f'{market_notice}\n\n' if market_notice else ''
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

        has_ai = False
        if has_persisted:
            has_ai = 'ai_score' in candidates.columns and candidates['ai_score'].notna().any()
        else:
            try:
                from core.model_utils import load_model as _load_model

                strat_model, strat_features, _, _ = _load_model(strategy_key)
                if strat_model is not None:
                    features = strat_features or active.features
                    df_score = candidates.copy()
                    for feature_name in features:
                        if feature_name not in df_score.columns:
                            df_score[feature_name] = 0
                    probs = strat_model.predict_proba(df_score[features].fillna(0))[:, 1]
                    candidates = candidates.copy()
                    candidates['ai_score'] = probs
                    candidates = candidates.sort_values('ai_score', ascending=False)
                    has_ai = True
            except Exception as exc:
                print(f'⚠️ AI 排名失敗（使用原始排序）: {exc}')

        if not has_persisted and has_ai:
            candidates = _apply_news_sentiment_overlay(candidates, display_date)

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