"""
Line Bot 主程式 (V31 混合策略版)
============================================
功能:
1. V31 混合策略選股（V30篩選 + ML智慧排名）
2. V30 純技術分析選股（均線突破+量能確認）
3. 個股查詢（含策略報告+停損停利）
4. 動態參數調整（資料庫設定）
5. 目標：獲利 10-20%，停損 5%
"""
# -*- coding: utf-8 -*-
import sys

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import os
import re
import json
import traceback
import unicodedata
import random
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs
from flask import Flask, request, abort, render_template, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

# Line Bot SDK v3 (2024更新)
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as V3TextMessage
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from config import Config
from config import (
    V34_MODE_PRESETS, V35_MODE_PRESETS,
    MODE_CMD_MAP, MODE_EMOJI, MODE_REPLY_TEMPLATE,
)

# 引入策略模組
from tool.strategy import (
    calculate_v30_signal, get_best_stocks_v31_hybrid, get_v30_params_from_db,
    format_v30_recommendation, format_v31_recommendation, format_stock_query
)
# 引入資料庫輔助模組
from tool.db_helper import (
    get_setting,
    update_setting,
    validate_setting,
    get_recommendations_with_market_fallback,
    format_market_fallback_notice,
    merge_recommendations_with_market_data,
    get_stock_data,
    create_user_simulation_trade,
    supplement_financial_data,
    get_open_holdings,
    get_recent_backtest_trades,
    get_backtest_equity_curve,
    get_backtest_summary_from_db,
    get_news_sentiment,
    get_stock_sector,
    safe_float,
    safe_int,
)
# 引入策略工廠
from tool.strategy_manager import StrategyManager
# 引入 MCP 客戶端（Rich Menu postback 上游資料來源）
from tool.mcp_client import MCPClientError, TWSEMCPClient as MCPClient
# 引入診斷報告工具
from tool.report_helper import get_stock_report, format_stock_diagnosis
# 引入 Flex Message 建構器
from tool.line_message_builder import (
    create_stock_flex_message,
    create_recommendation_carousel,
    create_backtest_summary_flex,
    create_holdings_flex,
    create_news_flex,
)

app = Flask(__name__)
app.secret_key = Config.FLASK_SECRET_KEY

# ==========================================
# 🔐 Flask-Login 設定 (Phase 1 Security)
# ==========================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入以存取此頁面'
login_manager.login_message_category = 'error'


class User(UserMixin):
    """簡易使用者類別 (基於環境變數驗證)
    
    只有一個管理員帳號，密碼從環境變數讀取
    """
    def __init__(self, user_id):
        self.id = user_id
    
    @staticmethod
    def validate_password(password):
        """驗證密碼是否正確"""
        return password == Config.ADMIN_PASSWORD
    
    @staticmethod
    def get(user_id):
        """取得使用者物件"""
        if user_id == 'admin':
            return User('admin')
        return None


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login 回呼：載入使用者"""
    return User.get(user_id)

# Line Bot SDK v3 設定
configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# 模型改為按策略動態載入（各函式內部自行呼叫 load_model(strategy_key)）
print("[AI] 模型將按策略動態載入")

# 初始化策略管理器
print("[AI] 正在初始化策略管理器...")
strategy_manager = StrategyManager()
print(f"[OK] 當前策略: {strategy_manager.get_active_strategy_name()}")


@app.route(Config.APP_HEALTH_PATH)
def health_check():
    """提供 compose readiness 使用的輕量健康檢查。"""
    checks = [
        {"component": "flask", "status": "ok"},
        {
            "component": "mcp_config",
            "status": "ok" if Config.MCP_BASE_URL else "missing",
        },
    ]
    return jsonify({
        'status': 'ok',
        'service': 'stock_bot',
        'version': '0.1.0',
        'checks': checks,
    }), 200



# ============================================
# 📊 核心業務邏輯
# ============================================

# ------------------------------------------------------------------
# T001: Postback 記憶體 TTL 快取（market_summary / chip_trend 共用）
# ------------------------------------------------------------------

class _PostbackCache:
    """執行緒安全的記憶體 TTL 快取，用於 LINE Postback 上游市場資料。

    快取鍵格式：``"{action}:{YYYY-MM-DD}"``（Asia/Taipei 日期），確保
    跨日自動失效。空的上游回應（records 為空 list）不會寫入快取，以便
    稍後重試時仍可取得正確資料。

    TTL 預設為 3600 秒（1 小時）。
    """

    _TTL_SECONDS: float = 3600.0

    def __init__(self) -> None:
        self._store: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def _today_taipei(self) -> str:
        """回傳台灣時區今日日期字串（YYYY-MM-DD）。"""
        return datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')

    def _make_key(self, action: str) -> str:
        return f"{action}:{self._today_taipei()}"

    def get(self, action: str) -> object | None:
        """取得快取值；若不存在或已過期則回傳 None。"""
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
        """寫入快取；payload 為 None 或 records 為空時拒絕寫入。"""
        if payload is None:
            return
        if isinstance(payload, dict):
            records = payload.get('records')
            if isinstance(records, list) and len(records) == 0:
                return  # 空 records 不快取，保留重試機會
        key = self._make_key(action)
        expires_at = time.monotonic() + self._TTL_SECONDS
        with self._lock:
            self._store[key] = (payload, expires_at)


# 模組層級 singleton
_postback_cache = _PostbackCache()


def get_ngrok_url() -> str:
    """自動偵測 ngrok 公開 URL（查詢本機 ngrok Agent API）

    ngrok 啟動後會在 http://localhost:4040/api/tunnels 提供 REST API。
    本函式優先回傳 https tunnel；若無 ngrok 或查詢失敗，
    則退回 Config.PUBLIC_DASHBOARD_URL（環境變數 PUBLIC_DASHBOARD_URL
    或預設 http://localhost:1688）。

    Returns:
        str: 可供外部連線的 Dashboard 基底 URL（無尾斜線）
    """
    import urllib.request
    import json as _json

    ngrok_api = 'http://localhost:4040/api/tunnels'
    try:
        with urllib.request.urlopen(ngrok_api, timeout=2) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
        tunnels = data.get('tunnels', [])
        # 優先回傳 https tunnel
        for t in tunnels:
            if t.get('proto') == 'https':
                return t['public_url'].rstrip('/')
        # 若只有 http tunnel，回傳第一個
        if tunnels:
            return tunnels[0]['public_url'].rstrip('/')
    except Exception:
        pass  # ngrok 未啟動或逾時，靜默退回預設值

    return Config.PUBLIC_DASHBOARD_URL.rstrip('/')


def _normalize_backtest_dates(start_date, end_date):
    from datetime import datetime, timedelta

    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')

    return start_date, end_date


def _get_backtest_module():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

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
    """載入回測摘要，優先使用 DB（與 /api/trades, /api/performance 一致）。"""
    summary = get_backtest_summary_from_db()
    if summary is None:
        from tool.viz_helper import get_backtest_summary
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
        'avg_hold_days': summary['avg_hold_days']
    }


def _normalize_line_text(text: str) -> str:
    """Normalize LINE input to reduce command mismatch by unicode/spacing artifacts."""
    raw = text or ''
    normalized = unicodedata.normalize('NFKC', raw)
    normalized = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Cf')
    return normalized.strip()


def _compact_command_key(text: str) -> str:
    """Create compact key for intent matching (remove spaces/symbols, lowercase latin)."""
    normalized = _normalize_line_text(text)
    compact = re.sub(r'[\s\-_/,，。．、:：;；!！?？\[\]()（）【】{}"\'\`]+', '', normalized)
    return compact.lower()


def _is_quick_mode_cmd(text: str, version: str, style: str) -> bool:
    """Match V34/V35 aggressive/balanced/loose commands with spacing/case variants."""
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


# 策略切換指令查找表（消除 7 段重複 if-elif 區塊）
_STRATEGY_SWITCH_MAP = {
    'v31_hybrid': {
        'aliases': ["切換v30", "v30策略", "切v30"],
        'display': 'V31 混合策略',
        'features': "• 均線多頭 + RSI 中性 + 量能放大\n• XGBoost AI 智慧排名\n• 經回測驗證\n",
    },
    'v33_low_vol': {
        'aliases': ["切換v33", "v33策略", "切v33", "低波動"],
        'display': 'V33 低波動策略',
        'features': "• 波動率 NATR < 4%\n• 穩定成長優先\n• 適合保守型投資者\n",
    },
    'v34_turbo': {
        'aliases': ["切換v34", "v34策略", "切v34", "飆股", "渦輪"],
        'display': 'V34 雙渦輪飆股策略',
        'features': "• 營收高成長 + 近60日高突破\n• 接近 60 日新高（價格突破）\n• 高風險高報酬，適合積極投資者\n",
    },
    'v35_innovation': {
        'aliases': ["切換v35", "v35策略", "切v35", "創新", "經營效益"],
        'display': 'V35 經營效益策略',
        'features': "• 營業利益率 + 營收成長 + 多頭趨勢\n• 營收正成長 + 多頭趨勢\n• 中長線穩健，適合價值投資者\n",
    },
    'v36_chip_momentum': {
        'aliases': ["切換v36", "v36策略", "切v36", "籌碼", "法人"],
        'display': 'V36 籌碼動能策略',
        'features': "• 法人連續買超 + 籌碼評分\n• 外資/投信同步追蹤\n• 跟隨主力動向，適合趨勢投資者\n",
    },
    'v37_mean_reversion': {
        'aliases': ["切換v37", "v37策略", "切v37", "均值回歸", "反轉"],
        'display': 'V37 均值回歸策略',
        'features': "• KD 超賣回升 + BB 收斂\n• 量縮整理後的反彈行情\n• 短線反轉操作，持股 5-8 天\n",
    },
    'v38_value_dividend': {
        'aliases': ["切換v38", "v38策略", "切v38", "高殖利", "價值股", "定存股"],
        'display': 'V38 高殖利率價值策略',
        'features': "• 高 EPS + 高營業利益率\n• 低波動穩健價值股\n• 類定存配置，適合保守投資者\n",
    },
}

# 預建反向查找索引（alias → strategy_key）
_STRATEGY_ALIAS_INDEX = {}
for _key, _info in _STRATEGY_SWITCH_MAP.items():
    for _alias in _info['aliases']:
        _STRATEGY_ALIAS_INDEX[_alias] = _key


def _match_strategy_switch(text_lower: str):
    """匹配策略切換指令，回傳 (strategy_key, display_name, features_text) 或 None。"""
    key = _STRATEGY_ALIAS_INDEX.get(text_lower)
    if key is None:
        return None
    info = _STRATEGY_SWITCH_MAP[key]
    return key, info['display'], info['features']


def _parse_news_reason(news_reason: str) -> dict:
    """解析消息面理由字串，供 Web / Line 顯示使用。"""
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
    """讀取個股層級新聞摘要。"""
    if not stock_ids:
        return {}
    try:
        from tool.news_agent import get_stock_news_mentions
        return get_stock_news_mentions(stock_ids)
    except Exception as exc:
        print(f"⚠️ 個股新聞讀取失敗: {exc}")
        return {}


def _get_sector_news_summary(sector: str, date_str: str = None) -> dict:
    """依產業回傳可直接顯示的消息面摘要。"""
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
            items.append(f"主題: {theme}")
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
            items.append(f"主題: {theme}")
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
    """依個股新聞偵測結果組合顯示摘要。"""
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
    item = f"利空: {reason}" if is_bearish else f"利多: {reason}"
    return {
        'raw': item,
        'items': [item],
        'is_bearish': is_bearish,
        'title': '🔴 個股新聞' if is_bearish else '🟢 個股新聞',
    }


def _resolve_signal_news_info(row, date_str: str, stock_mentions_map: dict) -> dict:
    """統一決定單一標的要顯示的新聞摘要來源。"""
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
    """Apply sector and stock-news overlay to live recommendations."""
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
        print(f"⚠️ 即時選股消息面加權失敗: {exc}")
        return boosted


def _build_macro_news_messages() -> list:
    """Build reply messages for the Rich Menu macro-news action."""
    try:
        from tool.news_agent import get_morning_news_summary

        news_summary = get_morning_news_summary()
        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        return [create_news_flex(news_summary, today_str)]
    except Exception as exc:
        print(f"⚠️ 總經摘要產生失敗: {exc}")
        return [V3TextMessage(text="📰 總經摘要\n\n目前暫時無法取得最新摘要，請稍後再試。")]


# ------------------------------------------------------------------
# T005: 總經與大盤摘要訊息（market_summary postback）
# ------------------------------------------------------------------

def _build_market_summary_messages() -> list:
    """從 TWSE MCP 取得大盤快照並回傳 LINE 訊息清單。

    呼叫 ``TWSEMCPClient.get_market_statistics_sync()``，統計
    上漲/下跌/平盤家數與總成交量，結果快取於 ``_postback_cache``。
    若 MCP 呼叫失敗或回傳空 records 則回傳友善提示訊息。
    """
    cached = _postback_cache.get('market_summary')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = MCPClient().get_market_statistics_sync(trade_date)
        if result is None:
            return [V3TextMessage(text="📊 大盤快照\n\n目前暫時無法連線至 TWSE MCP Server，請稍後再試。")]
        records: list[dict] = result.get('records') or []
        if not records:
            return [V3TextMessage(text="📊 大盤快照\n\n目前尚無今日交易資料，請於盤後再試。")]

        rising = sum(
            1 for r in records
            if (safe_float(r.get('close_price')) or 0) > (safe_float(r.get('open_price')) or 0)
        )
        falling = sum(
            1 for r in records
            if (safe_float(r.get('close_price')) or 0) < (safe_float(r.get('open_price')) or 0)
        )
        flat = len(records) - rising - falling
        total_vol = sum(int(safe_float(r.get('volume')) or 0) for r in records)
        total_vol_b = total_vol / 1_000_000_000  # 轉換為億股
        result_date = result.get('as_of_date') or trade_date

        body = (
            f"📊 台股大盤快照 {result_date}\n"
            f"{'─' * 26}\n"
            f"▲ 上漲  {rising:4d} 檔\n"
            f"▼ 下跌  {falling:4d} 檔\n"
            f"─ 平盤  {flat:4d} 檔\n"
            f"{'─' * 26}\n"
            f"💹 總成交量 {total_vol_b:.1f} 億股\n"
            f"\n💡 資料來源：TWSE MCP Server"
        )
        _postback_cache.set('market_summary', body)
        return [V3TextMessage(text=body)]

    except MCPClientError as exc:
        print(f"⚠️ MCP 大盤快照失敗: {exc}")
        return [V3TextMessage(text="📊 大盤快照\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。")]
    except Exception as exc:
        print(f"⚠️ _build_market_summary_messages 未預期錯誤: {exc}")
        return [V3TextMessage(text="📊 大盤快照\n\n資料處理異常，請稍後再試。")]


# ------------------------------------------------------------------
# T007: 籌碼動向訊息（chip_trend postback）
# ------------------------------------------------------------------

def _build_chip_trend_messages() -> list:
    """從 TWSE MCP 取得三大法人買賣超並回傳 LINE 訊息清單。

    呼叫 ``TWSEMCPClient.get_foreign_investment_sync()``，加總
    外資、投信、自營商三大法人買賣超，結果快取於 ``_postback_cache``。
    若 MCP 呼叫失敗或回傳空 records 則回傳友善提示訊息。
    """
    cached = _postback_cache.get('chip_trend')
    if cached is not None:
        return [V3TextMessage(text=cached)]

    try:
        trade_date = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        result = MCPClient().get_foreign_investment_sync(trade_date)
        if result is None:
            return [V3TextMessage(text="🏦 籌碼動向\n\n目前暫時無法連線至 TWSE MCP Server，請稍後再試。")]
        records: list[dict] = result.get('records') or []
        if not records:
            return [V3TextMessage(text="🏦 籌碼動向\n\n目前尚無今日法人資料，請於盤後再試。")]

        def _sum_net(key: str) -> int:
            return sum(int(safe_float(r.get(key)) or 0) for r in records)

        foreign_net = _sum_net('foreign_buy')
        trust_net = _sum_net('trust_buy')
        dealer_net = _sum_net('dealer_buy')
        total_net = foreign_net + trust_net + dealer_net
        result_date = result.get('as_of_date') or trade_date

        def _fmt(val: int) -> str:
            sign = '▲' if val > 0 else ('▼' if val < 0 else '─')
            return f"{sign} {abs(val):>10,}"

        body = (
            f"🏦 三大法人買賣超 {result_date}\n"
            f"{'─' * 30}\n"
            f"外資   {_fmt(foreign_net)} 張\n"
            f"投信   {_fmt(trust_net)} 張\n"
            f"自營商 {_fmt(dealer_net)} 張\n"
            f"{'─' * 30}\n"
            f"合計   {_fmt(total_net)} 張\n"
            f"\n💡 資料來源：TWSE MCP Server"
        )
        _postback_cache.set('chip_trend', body)
        return [V3TextMessage(text=body)]

    except MCPClientError as exc:
        print(f"⚠️ MCP 籌碼動向失敗: {exc}")
        return [V3TextMessage(text="🏦 籌碼動向\n\n目前暫時無法連線至 TWSE 資料源，請稍後再試。")]
    except Exception as exc:
        print(f"⚠️ _build_chip_trend_messages 未預期錯誤: {exc}")
        return [V3TextMessage(text="🏦 籌碼動向\n\n資料處理異常，請稍後再試。")]


# ------------------------------------------------------------------
# T009: 策略盲盒訊息（random_strategy postback）
# ------------------------------------------------------------------

def _build_random_strategy_messages() -> list:
    """從隨機策略池挑選一個策略並回傳今日推薦股票訊息。

    從 ``StrategyManager.get_random_strategy_pool()`` 取得候選策略鍵，
    以 ``random.sample`` 洗牌後依序嘗試 ``filter_candidates()``，
    取第一個非空結果格式化後回傳。若所有策略均無推薦則回傳提示訊息。
    """
    try:
        sm = strategy_manager  # 使用模組層級 singleton
        pool = sm.get_random_strategy_pool()
        if not pool:
            return [V3TextMessage(text="🎲 策略盲盒\n\n目前策略池為空，請至設定頁面配置可用策略。")]

        shuffled = random.sample(pool, len(pool))

        # 全市場資料只需讀一次
        df, date_str = get_stock_data()
        if df is None or df.empty:
            return [V3TextMessage(text="🎲 策略盲盒\n\n目前無法取得市場資料，請稍後再試。")]
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
                print(f"⚠️ 策略盲盒 [{strategy_key}] 失敗，嘗試下一個: {exc}")
                continue

        return [V3TextMessage(
            text=(
                f"🎲 策略盲盒\n\n今日所有策略均無符合條件的股票，"
                f"可能是市場整體條件偏弱。\n📅 {date_str}"
            )
        )]

    except Exception as exc:
        print(f"⚠️ _build_random_strategy_messages 未預期錯誤: {exc}")
        return [V3TextMessage(text="🎲 策略盲盒\n\n資料處理異常，請稍後再試。")]


def _build_journal_reflection_text() -> str:
    """Summarize recent trades into a compact journal reflection."""
    try:
        recent_trades = get_recent_backtest_trades(limit=5) or []
    except Exception as exc:
        print(f"⚠️ 讀取最近交易失敗: {exc}")
        recent_trades = []

    try:
        open_holdings = get_open_holdings(limit=5) or []
    except Exception as exc:
        print(f"⚠️ 讀取持股日誌失敗: {exc}")
        open_holdings = []

    if not recent_trades:
        return "📝 日誌反思\n\n目前還沒有足夠的交易紀錄可供反思。\n可以先用「推薦」找標的，或先執行一次回測累積樣本。"

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
        "📝 日誌反思\n\n"
        f"最近 {len(recent_trades)} 筆回測交易勝率 {win_rate:.0f}%，平均報酬 {avg_profit:+.1f}%，平均持有 {avg_hold_days:.1f} 天。\n"
        f"最近一筆：{last_stock} {last_profit:+.1f}% ，出場原因：{last_reason}。\n"
        f"目前仍有 {holdings_count} 檔開倉，建議確認進場理由是否仍成立、停損停利是否有跟上。"
    )


def _extract_postback_action(data: str) -> str:
    """Extract rich-menu action from a LINE postback payload."""
    raw = str(data or '').strip()
    if not raw:
        return ''
    parsed = parse_qs(raw, keep_blank_values=True)
    return (parsed.get('action') or [raw])[0].strip()


# ------------------------------------------------------------------
# T011: Postback 路由　Dict-dispatch 模式
# ------------------------------------------------------------------

def _register_postback_handlers() -> dict:
    """建立 action → handler 映射表。

    明確列舉已支援的 Postback action，並在每次呼叫時重新解析 handler。
    這樣可避免模組層級 dict 與 router 內 dict 重複定義，也能讓測試 patch
    到最新的函式參考。
    """
    return {
        # 原有指令（保持向下相容）
        'get_macro_news': _build_macro_news_messages,
        'get_journal': lambda: [V3TextMessage(text=_build_journal_reflection_text())],
        # T005/T007/T009 新增指令
        'market_summary': _build_market_summary_messages,
        'chip_trend': _build_chip_trend_messages,
        'random_strategy': _build_random_strategy_messages,
    }


def _build_postback_reply_messages(action: str) -> list:
    """Resolve LINE rich-menu postback action into reply messages.

    Handler 表在每次呼叫時動態建立，以確保測試 patch 可正確攔截，
    並維持單一來源的 dispatch 定義。
    """
    handler = _register_postback_handlers().get(action)
    if handler is not None:
        return handler()  # type: ignore[operator]
    return [V3TextMessage(text='⚠️ 尚未支援的 Rich Menu 指令')]


def _load_strategy_candidates(active, strategy_key: str, market_df: pd.DataFrame,
                              requested_date: str, limit: int) -> tuple[pd.DataFrame, dict, bool]:
    """統一載入策略候選股，優先使用落庫推薦並套用 fallback。"""
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
    """
    V30 策略選股（均線突破 + 量能確認）
    已在回測中實現 40% 報酬率
    
    Returns:
        推薦訊息字串
    """
    try:
        # 撈取最新資料
        df, date_str = get_stock_data()
        if df.empty: 
            return "💤 今日無資料"

        # 確保必要欄位存在
        required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return f"⚠️ 資料庫缺少欄位: {', '.join(missing_cols)}\n請執行 tool/calc_indicators.py"

        # 套用 V30 策略篩選
        picks = []
        for _, row in df.iterrows():
            v30_result = calculate_v30_signal(row)
            if v30_result['signal_strength'] == 'strong':
                picks.append({
                    'stock_id': row['stock_id'],
                    'close_price': row['close_price'],
                    'rsi': row.get('rsi', 0),
                    'volume': row.get('volume', 0),
                    'stop_loss': v30_result['stop_loss'],
                    'take_profit': v30_result['take_profit'],
                    'foreign_buy': row.get('foreign_buy', 0),
                })

        # 使用 Strategy 模組的格式化函數
        return format_v30_recommendation(picks, date_str)
        
    except Exception as e:
        print(f"❌ V30 推薦失敗: {e}")
        traceback.print_exc()
        return f"❌ 運算錯誤: {str(e)[:100]}"


def get_strategy_recommendation(as_flex: bool = False):
    """
    根據當前活躍策略推薦股票（含 AI 排名）
    
    流程：
    1. 載入當前策略 (StrategyManager)
    2. 撈取最新市場資料 (get_stock_data)
    3. 策略硬篩選 (filter_candidates) 
    4. AI 排名 (XGBoost)
    5. 格式化前 5 名輸出
    
    Args:
        as_flex: True 回傳 FlexMessage Carousel, False 回傳純文字

    Returns:
        str (純文字) 或 FlexMessage (Flex Carousel)
    """
    try:
        # 1. 取得當前策略
        mgr = StrategyManager()
        active = mgr.get_active_strategy()
        if active is None:
            return "❌ 策略載入失敗，請先輸入「切換V30」設定策略"
        
        strategy_name = active.display_name
        strategy_key = active.name
        
        # 2. 撈取最新資料
        df, date_str = get_stock_data()
        if df.empty:
            return "💤 今日無資料\n請確認已執行 python 1_update_database.py"
        
        # 2.5 補充財務資料（V34/V35 需要 revenue_yoy / op_profit_margin）
        df = supplement_financial_data(df)
        
        # 3. 優先讀取已落庫推薦結果，熔斷日必要時回推最近安全日
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
            warning_block = f"{market_notice}\n\n" if market_notice else ''
            return (
                f"🔍 【{strategy_name}】選股結果\n"
                f"日期：{display_date}\n\n"
                f"{warning_block}"
                f"❌ 今日無符合條件的股票\n\n"
                f"💡 建議：\n"
                f"• 觀望等待進場訊號\n"
                f"• 嘗試「切換V33」低波動等其他策略\n"
                f"• 輸入「查看策略」檢視篩選條件"
            )
        
        # 4. 若沒有已落庫結果，才即時計算 AI 排名
        has_ai = False
        if has_persisted:
            has_ai = 'ai_score' in candidates.columns and candidates['ai_score'].notna().any()
        else:
            try:
                from tool.model_utils import load_model as _load_model
                strat_model, strat_features, _, _ = _load_model(strategy_key)
                if strat_model is not None:
                    features = strat_features or active.features
                    df_score = candidates.copy()
                    for f in features:
                        if f not in df_score.columns:
                            df_score[f] = 0

                    probs = strat_model.predict_proba(df_score[features].fillna(0))[:, 1]
                    candidates = candidates.copy()
                    candidates['ai_score'] = probs
                    candidates = candidates.sort_values('ai_score', ascending=False)
                    has_ai = True
            except Exception as e:
                print(f"⚠️ AI 排名失敗（使用原始排序）: {e}")

        if not has_persisted and has_ai:
            candidates = _apply_news_sentiment_overlay(candidates, display_date)
        
        # 5. 格式化輸出
        top_n = candidates.head(5)
        total_count = len(candidates)
        stock_mentions_map = _get_stock_mentions_map([str(sid) for sid in top_n['stock_id'].tolist()])

        # ── Flex Carousel 模式 ──
        if as_flex:
            picks_list = []
            for _, row in top_n.iterrows():
                close = row.get('close_price', 0)
                sid = str(row.get('stock_id', '????'))
                news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
                picks_list.append({
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
                })
            return create_recommendation_carousel(
                picks=picks_list,
                strategy_name=strategy_name,
                date_str=display_date,
            )

        # ── 純文字模式（相容舊版）──
        reply = f"🎯 【{strategy_name}】推薦\n"
        reply += f"📅 日期：{display_date}\n"
        if has_ai:
            reply += f"🤖 AI 排名：已啟用\n"
        if market_notice:
            reply += f"{market_notice}\n"
        reply += "-" * 28 + "\n\n"
        
        for i, (_, row) in enumerate(top_n.iterrows(), 1):
            stock_id = row.get('stock_id', 'N/A')
            close = row.get('close_price', 0)
            rsi = row.get('rsi', 0)
            volume = row.get('volume', 0)
            ma20 = row.get('ma20', 0)
            ma60 = row.get('ma60', 0)
            sector = get_stock_sector(str(stock_id))
            news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
            news_reason = news_info['raw']

            # 計算停損停利價位
            sl_price = close * (1 - active.stop_loss)
            tp_price = close * (1 + active.take_profit) if active.take_profit > 0 else 0

            reply += f"{'🥇🥈🥉'[i-1] if i <= 3 else '▪️'} {i}. {stock_id}（{sector}）\n"
            reply += f"   💰 收盤：{close:.2f}"
            if has_ai:
                ai_pct = row.get('ai_score', 0) * 100
                reply += f"  🤖 {ai_pct:.0f}分"
            reply += "\n"
            reply += f"   📊 RSI: {rsi:.1f}"
            if volume > 0:
                reply += f"  📈 量: {volume/10000:.0f}萬"
            reply += "\n"
            if news_reason:
                reply += f"   {news_info['title']}\n"
                for item in news_info['items'][:3]:
                    reply += f"   • {item}\n"
            reply += f"   🛡️ 停損: {sl_price:.2f}"
            if tp_price > 0:
                reply += f"  🎯 停利: {tp_price:.2f}"
            reply += "\n\n"
        
        reply += "-" * 28 + "\n"
        reply += f"📊 共篩選出 {total_count} 檔"
        if total_count > 5:
            reply += f"（顯示前 5 名）"
        reply += f"\n⏰ 最長持有：{active.max_hold_days} 天\n"
        reply += f"⚠️ 僅供參考，請自行評估風險"
        
        return reply
        
    except Exception as e:
        print(f"❌ 策略推薦失敗: {e}")
        traceback.print_exc()
        return f"❌ 策略推薦失敗: {str(e)[:100]}"


def query_stock(stock_id):
    """
    個股查詢（V2.0 完整策略報告版）
    
    Args:
        stock_id: 股票代號
    
    Returns:
        策略報告字串
    """
    try:
        # 1. 撈取資料
        df, date_str = get_stock_data(stock_id=stock_id)
        if df.empty: 
            return f"🔍 找不到 {stock_id} 的資料"
        
        row = df.iloc[0]
        
        # 2. AI 預測（載入當前策略模型）
        prob = 0.5
        try:
            from tool.model_utils import load_model as _load_model
            mgr = StrategyManager()
            skey = mgr.get_active_strategy_names()[0] if mgr.get_active_strategy_names() else None
            _m, _f, _, _ = _load_model(skey)
            if _m and _f:
                df_feat = pd.DataFrame([row])
                for f in _f:
                    if f not in df_feat.columns:
                        df_feat[f] = 0
                prob = _m.predict_proba(df_feat[_f].fillna(0))[:, 1][0]
        except Exception:
            pass
        
        # 3. 判斷是否啟用完整策略報告
        enable_strategy = get_setting('enable_strategy_report', 'true') == 'true'
        
        # 4. 使用 Strategy 模組的格式化函數
        return format_stock_query(stock_id, date_str, row, prob, enable_strategy)
        
    except Exception as e:
        print(f"❌ 個股查詢失敗: {e}")
        traceback.print_exc()
        return f"❌ 查詢失敗: {str(e)[:100]}"


def get_settings_info():
    """
    查看當前設定
    
    Returns:
        設定資訊字串
    """
    try:
        # AI 設定
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        
        # V30 策略參數
        v30_stop_loss = float(get_setting('v30_stop_loss', str(Config.V30_PARAMS['STOP_LOSS'])))
        v30_take_profit = float(get_setting('v30_take_profit', str(Config.V30_PARAMS['TAKE_PROFIT'])))
        v30_max_hold = int(get_setting('v30_max_hold_days', str(Config.V30_PARAMS['MAX_HOLD_DAYS'])))
        
        # V34 / V35（優先 DB 設定，否則用 Config）
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

        msg = "⚙️ 【當前設定】\n"
        msg += "-" * 30 + "\n"
        msg += "🚀 V30 策略參數:\n"
        msg += f"  🛡️ 停損: {int(v30_stop_loss*100)}%\n"
        if v30_take_profit > 0:
            msg += f"  🎯 停利: {int(v30_take_profit*100)}%\n"
        else:
            msg += f"  🎯 停利: 不停利（持有至到期）\n"
        msg += f"  ⏰ 最長持有: {v30_max_hold}天\n"
        msg += "\n"
        msg += "🚀 V34 參數（嚴格 / 放寬）:\n"
        msg += f"  📈 YoY: {v34_yoy:.1f}% / {v34_relaxed_yoy:.1f}%\n"
        msg += f"  💥 突破: {v34_breakout:.2f} / {v34_relaxed_breakout:.2f}\n"
        msg += f"  📦 量比: {v34_volume:.2f} / {v34_relaxed_volume:.2f}\n"
        msg += "\n"
        msg += "💼 V35 參數（嚴格 / 放寬）:\n"
        msg += f"  🏢 營業利益率: {v35_op*100:.1f}% / {v35_relaxed_op*100:.1f}%\n"
        msg += f"  📈 營收YoY: {v35_revenue:.1f}% / {v35_relaxed_revenue:.1f}%\n"
        msg += f"  📦 量比: {v35_volume:.2f} / {v35_relaxed_volume:.2f}\n"
        msg += "\n"
        msg += "🧠 AI 參數:\n"
        msg += f"  AI 門檻: {int(ai_threshold*100)}%\n"
        msg += "-" * 30 + "\n"
        msg += "💡 可用指令:\n"
        msg += "• 設定停損 5 (設為5%)\n"
        msg += "• 設定停利 20 (設為20%)\n"
        msg += "• 設定停利 0 (不停利)\n"
        msg += "• 設定信心 60 (AI門檻60%)\n"
        msg += "• 切換積極 / 切換平衡 / 切換寬鬆\n"
        msg += "• V34積極 / V34平衡 / V34寬鬆\n"
        msg += "• V35積極 / V35平衡 / V35寬鬆\n"
        msg += "• 設定V34 18 0.93 0.9\n"
        msg += "• 設定V35 6 0 0.8"
        
        return msg
    except Exception as e:
        return f"❌ 讀取設定失敗: {e}"



# ============================================
# 🌐 Flask 路由
# ============================================

# ==========================================
# 🔐 登入/登出路由 (Phase 1 Security)
# ==========================================

@app.route("/login", methods=['GET', 'POST'])
def login():
    """登入頁面"""
    # 如果已登入，重定向到 Dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        
        if User.validate_password(password):
            user = User('admin')
            login_user(user)
            flash('✅ 登入成功！', 'success')
            
            # 重定向到原本要訪問的頁面，或 Dashboard
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('❌ 密碼錯誤，請重試', 'error')
    
    return render_template('login.html')


@app.route("/logout")
@login_required
def logout():
    """登出"""
    logout_user()
    flash('👋 已登出', 'success')
    return redirect(url_for('login'))


# ==========================================
# V32: Web Dashboard 路由
# ==========================================

@app.route("/")
@login_required
def index():
    """首頁重定向到 Dashboard"""
    return redirect(url_for('dashboard'))


@app.route("/dashboard")
@login_required
def dashboard():
    """V32 Dashboard 主頁面"""
    # 傳遞策略資訊給前端
    active_strategies = strategy_manager.get_active_strategy_names()
    strategy_options = strategy_manager.list_strategies()
    
    return render_template('dashboard.html', 
                         active_strategies=active_strategies,
                         strategy_options=strategy_options,
                         current_strategy=active_strategies[0] if active_strategies else 'v31_hybrid',
                         current_mode=str(get_setting('mode', 'balanced')))


@app.route('/update_strategy', methods=['POST'])
@login_required
def update_strategy():
    """切換策略 (V2: 支援多策略)"""
    try:
        # 使用 getlist 取得多個 checkbox 的值
        selected_strategies = request.form.getlist('strategies')
        
        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('dashboard'))
        
        # 設定多策略
        success = strategy_manager.set_active_strategies(selected_strategies)
        
        if success:
            if len(selected_strategies) == 1:
                strategy_obj = strategy_manager.get_active_strategy()
                flash(f'✅ 已切換至 {strategy_obj.display_name}', 'success')
            else:
                flash(f'✅ 已啟用 {len(selected_strategies)} 個策略', 'success')
            print(f"[Strategy] 切換至: {selected_strategies}")
        else:
            flash('❌ 策略切換失敗', 'error')
        
    except Exception as e:
        flash(f'❌ 切換失敗: {str(e)}', 'error')
        print(f"[ERROR] 策略切換失敗: {e}")
    
    return redirect(url_for('dashboard'))


@app.route('/update_mode', methods=['POST'])
@login_required
def update_mode():
    """切換 V34/V35 共用模式：積極 / 平衡 / 寬鬆"""
    try:
        mode_map = {
            'aggressive': ('積極', 'aggressive'),
            'balanced': ('平衡', 'balanced'),
            'loose': ('寬鬆', 'loose'),
            # 向後相容
            'conservative': ('穩健', 'conservative'),
        }
        req_mode = (request.form.get('mode') or '').strip().lower()
        if req_mode not in mode_map:
            flash('❌ 未知模式，請使用 積極/平衡/寬鬆', 'error')
            return redirect(url_for('dashboard'))

        mode_label, preset_key = mode_map[req_mode]
        updates = {**V34_MODE_PRESETS[preset_key], **V35_MODE_PRESETS[preset_key]}
        ok_mode = update_setting('mode', req_mode)
        ok_params = _apply_settings_batch(updates)

        if ok_mode and ok_params:
            flash(f'✅ 已切換至【{mode_label}模式】（V34/V35 同步更新）', 'success')
        elif ok_mode:
            flash(f'⚠️ 已切換模式，但部分參數更新失敗（{mode_label}）', 'error')
        else:
            flash('❌ 模式切換失敗', 'error')
    except Exception as e:
        flash(f'❌ 模式切換異常: {e}', 'error')

    return redirect(url_for('dashboard'))


@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route("/api/performance")
def api_performance():
    """
    API: 取得回測資產曲線數據
    Returns: JSON {dates: [], equity: [], roi: []}
    """
    try:
        db_curve = get_backtest_equity_curve()
        if db_curve.get('dates'):
            return jsonify(db_curve)

        profit_file = 'ML_Data/backtest_profit_report.csv'
        if not os.path.exists(profit_file):
            return jsonify({'error': '回測數據不存在，請先執行 4_run_backtest.py'}), 404
        
        df = pd.read_csv(profit_file)
        
        return jsonify({
            'dates': df['date'].tolist(),
            'equity': df['asset_value'].tolist(),
            'roi': df['roi'].tolist()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/trades")
def api_trades():
    """
    API: 取得交易明細（最近 50 筆）
    Returns: JSON list of trades
    """
    try:
        # 優先讀取 DB（4_run_backtest.py 已同步寫入）
        db_trades = get_recent_backtest_trades(limit=50)
        if db_trades:
            return jsonify(db_trades)

        # 向下相容：若 DB 尚無資料，回退 CSV
        trades_file = 'ML_Data/backtest_result.csv'
        if not os.path.exists(trades_file):
            return jsonify({'error': '交易數據不存在，請先執行 4_run_backtest.py'}), 404
        
        df = pd.read_csv(trades_file)

        # 補齊缺少的 strategy 欄位（舊版 CSV 可能沒有）
        if 'strategy' not in df.columns:
            df['strategy'] = 'unknown'

        # 只回傳最近 50 筆
        df_recent = df.tail(50)

        # 清除 NaN（JSON 不支援 NaN，需轉為 None/null）
        df_recent = df_recent.where(pd.notnull(df_recent), None)

        # 轉換為 JSON
        trades = df_recent.to_dict('records')

        return jsonify(trades)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/summary")
def api_summary():
    """
    API: 取得回測摘要統計
    Returns: JSON {total_roi, mdd, sharpe, win_rate, trade_count, avg_hold_days}
    """
    try:
        summary, error_response = _load_backtest_summary_or_error('回測數據不存在')
        if error_response:
            return error_response
        return jsonify(_build_summary_response(summary))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==========================================
# 🎮 V33 Phase 3: PK System API
# ==========================================
@app.route("/api/user/trade", methods=['POST'])
def api_user_trade():
    """
    API: 記錄使用者模擬交易
    Request Body: {user_id, stock_id, buy_price, buy_date}
    """
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        stock_id = data.get('stock_id')
        buy_price = data.get('buy_price')
        buy_date = data.get('buy_date')
        
        if not all([user_id, stock_id, buy_price, buy_date]):
            return jsonify({'error': '缺少必要參數'}), 400
        
        # 插入資料庫
        if not create_user_simulation_trade(
            user_id=user_id,
            stock_id=stock_id,
            buy_price=buy_price,
            buy_date=buy_date,
        ):
            return jsonify({'error': '資料庫寫入失敗'}), 500
        
        return jsonify({'success': True, 'message': '模擬交易記錄成功'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/pk/battle", methods=['GET'])
def api_pk_battle():
    """
    API: 取得人機對決統計數據
    Returns: JSON {user_roi, ai_roi, user_win_rate, ai_win_rate}
    """
    try:
        # Mock 數據示範（未來可連接真實交易記錄）
        user_roi = 15.5  # 使用者報酬率
        ai_roi = 19.2    # AI 報酬率 (來自 backtest_result.csv)
        
        # 從 backtest_result.csv 讀取 AI 實際數據
        trades_file = 'ML_Data/backtest_result.csv'
        if os.path.exists(trades_file):
            df_trades = pd.read_csv(trades_file)
            if 'roi' in df_trades.columns and not df_trades.empty:
                ai_roi = df_trades['roi'].mean()
                ai_win_rate = (df_trades['roi'] > 0).mean() * 100
            else:
                ai_win_rate = 50
        else:
            ai_win_rate = 50
        
        # Mock 使用者數據（未來從 user_simulation_trades 計算）
        user_win_rate = 45
        
        return jsonify({
            'user_roi': round(user_roi, 2),
            'ai_roi': round(ai_roi, 2),
            'user_win_rate': round(user_win_rate, 2),
            'ai_win_rate': round(ai_win_rate, 2)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/strategies")
def api_strategies():
    """
    API: 取得所有策略清單及當前啟用策略
    Returns: JSON {strategies: [...], active: [...]}
    """
    try:
        mgr = StrategyManager()
        available = mgr.list_available_strategies()
        active_names = mgr.get_active_strategy_names()
        
        strategies = []
        for name, display in available.items():
            try:
                strategy = mgr._get_or_load_strategy(name)
                info = strategy.get_strategy_info() if hasattr(strategy, 'get_strategy_info') else {}
                strategies.append({
                    'name': name,
                    'display_name': display,
                    'type': info.get('type', ''),
                    'risk_level': info.get('risk_level', ''),
                    'description': info.get('description', strategy.description if hasattr(strategy, 'description') else ''),
                    'active': name in active_names,
                })
            except Exception:
                strategies.append({
                    'name': name,
                    'display_name': display,
                    'active': name in active_names,
                })
        
        return jsonify({
            'strategies': strategies,
            'active': active_names,
            'count': len(strategies)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/daily-signals")
def api_daily_signals():
    """
    取得今日選股訊號
    Query Params:
      - strategy: 策略名稱（可選，預設為目前啟用策略）
      - top_n: 顯示筆數（可選，預設 5，範圍 1~20）
    Returns: JSON list of recommended stocks for today
    """
    try:
        # 讀取查詢參數
        requested_strategy = (request.args.get('strategy') or '').strip()
        try:
            top_n = int(request.args.get('top_n', 5))
        except (TypeError, ValueError):
            top_n = 5
        top_n = max(1, min(top_n, 20))

        strategy_alias = {
            'v31': 'v31_hybrid',
            'v33': 'v33_low_vol',
            'v34': 'v34_turbo',
            'v35': 'v35_innovation',
            'v36': 'v36_chip_momentum',
            'v37': 'v37_mean_reversion',
            'v38': 'v38_value_dividend',
        }

        # 取得最新資料
        df, date_str = get_stock_data()
        
        if df.empty:
            return jsonify({
                'error': '無最新資料',
                'date': None,
                'strategy_key': None,
                'strategy_display': None,
                'top_n': top_n,
                'signals': []
            })

        # 補充財務欄位（供 V34/V35/V38 與詳細資訊顯示）
        df = supplement_financial_data(df)
        
        # 使用指定策略或目前啟用策略
        try:
            mgr = StrategyManager()

            if requested_strategy:
                key = requested_strategy.lower().strip()
                strategy_key = strategy_alias.get(key, key)
                active = mgr.get_strategy(strategy_key)
                if active is None:
                    return jsonify({
                        'error': f'無效策略: {requested_strategy}',
                        'date': date_str,
                        'strategy_key': strategy_key,
                        'strategy_display': None,
                        'top_n': top_n,
                        'signals': []
                    }), 400
            else:
                active = mgr.get_active_strategy()
                strategy_key = mgr.get_active_strategy_names()[0] if mgr.get_active_strategy_names() else 'v31_hybrid'

            strategy_name = active.display_name
            candidates, fallback_meta, has_persisted = _load_strategy_candidates(
                active=active,
                strategy_key=strategy_key,
                market_df=df,
                requested_date=date_str,
                limit=top_n,
            )

            if not has_persisted and not candidates.empty:
                try:
                    from tool.model_utils import load_model as _load_model
                    strat_model, strat_features, _, _ = _load_model(strategy_key)
                    if strat_model is not None:
                        features = strat_features or active.features
                        score_df = candidates.copy()
                        for feature_name in features:
                            if feature_name not in score_df.columns:
                                score_df[feature_name] = 0
                        probs = strat_model.predict_proba(score_df[features].fillna(0))[:, 1]
                        candidates = candidates.copy()
                        candidates['ai_score'] = probs
                        candidates = candidates.sort_values('ai_score', ascending=False)
                except Exception as score_error:
                    print(f"⚠️ /api/daily-signals AI 排名失敗: {score_error}")

            if not has_persisted and not candidates.empty and 'ai_score' in candidates.columns:
                candidates = _apply_news_sentiment_overlay(
                    candidates,
                    fallback_meta.get('recommendation_date') or date_str,
                )
            
            if candidates.empty:
                return jsonify({
                    'date': fallback_meta.get('recommendation_date') or date_str,
                    'requested_date': fallback_meta.get('requested_date') or date_str,
                    'strategy_key': strategy_key,
                    'strategy_display': strategy_name,
                    'top_n': top_n,
                    'fallback_used': fallback_meta.get('fallback_used', False),
                    'market_warning': format_market_fallback_notice(fallback_meta, strategy_name),
                    'signals': [],
                    'message': f'今日 {strategy_name} 無符合條件的股票'
                })
            
            picks = candidates.head(top_n)
            _active_strategy = active
        except Exception:
            # Fallback: 使用 V31 混合策略
            picks = get_best_stocks_v31_hybrid(df, top_n=top_n)
            strategy_key = 'v31_hybrid'
            strategy_name = 'V31 混合策略'
            _active_strategy = None
        
        if picks.empty:
            return jsonify({
                'date': date_str,
                'strategy_key': strategy_key,
                'strategy_display': strategy_name,
                'top_n': top_n,
                'signals': [],
                'message': '今日無符合條件的股票'
            })

        # 格式化輸出
        signals = []
        signal_date = fallback_meta.get('recommendation_date') or date_str if 'fallback_meta' in locals() else date_str
        stock_mentions_map = _get_stock_mentions_map([str(sid) for sid in picks['stock_id'].tolist()])
        for _, row in picks.iterrows():
            close_price = float(row['close_price'])
            stop_loss_rate = float(getattr(_active_strategy, 'stop_loss', Config.V30_STOP_LOSS)) if _active_strategy else Config.V30_STOP_LOSS
            take_profit_rate = float(getattr(_active_strategy, 'take_profit', Config.V30_TAKE_PROFIT)) if _active_strategy else Config.V30_TAKE_PROFIT
            news_info = _resolve_signal_news_info(row, signal_date, stock_mentions_map)

            suggested_buy = close_price
            suggested_sell = close_price * (1 + take_profit_rate)
            suggested_stop = close_price * (1 - stop_loss_rate)

            signal = {
                'stock_id': row['stock_id'],
                'close_price': close_price,
                'strategy': strategy_name,
                'strategy_key': strategy_key,
                'ai_score': safe_float(row.get('ai_score')) if 'ai_score' in row else None,
                'rsi': safe_float(row.get('rsi')) if 'rsi' in row else None,
                'volume': safe_int(row.get('volume')) if 'volume' in row else None,
                'ma20': safe_float(row.get('ma20')) if 'ma20' in row else None,
                'ma60': safe_float(row.get('ma60')) if 'ma60' in row else None,
                'bias': safe_float(row.get('bias')) if 'bias' in row else None,
                'op_profit_margin': safe_float(row.get('op_profit_margin')) if 'op_profit_margin' in row else None,
                'revenue_yoy': safe_float(row.get('revenue_yoy')) if 'revenue_yoy' in row else None,
                'chip_score': safe_float(row.get('chip_score')) if 'chip_score' in row else None,
                'foreign_buy': safe_int(row.get('foreign_buy')) if 'foreign_buy' in row else None,
                'news_boost_reason': news_info['raw'],
                'news_reason_items': news_info['items'],
                'news_signal_title': news_info['title'],
                'news_is_bearish': news_info['is_bearish'],
                'suggested_buy_price': round(suggested_buy, 2),
                'suggested_sell_price': round(suggested_sell, 2),
                'suggested_stop_loss_price': round(suggested_stop, 2),
                'detail_url': f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={row['stock_id']}",
            }
            signals.append(signal)
        
        return jsonify({
            'date': signal_date,
            'requested_date': fallback_meta.get('requested_date') if 'fallback_meta' in locals() else date_str,
            'strategy_key': strategy_key,
            'strategy_display': strategy_name,
            'top_n': top_n,
            'fallback_used': fallback_meta.get('fallback_used', False) if 'fallback_meta' in locals() else False,
            'market_warning': format_market_fallback_notice(fallback_meta, strategy_name) if 'fallback_meta' in locals() else '',
            'signals': signals,
            'count': len(signals)
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==========================================
# News Sentiment API
# ==========================================

@app.route("/api/news_sentiment")
@login_required
def api_news_sentiment():
    """回傳指定日期（或最新）消息面情緒摘要"""
    date_str = request.args.get('date')
    data = get_news_sentiment(date_str)
    return jsonify(data)


# ==========================================
# Phase 5: Backtesting & Visualization
# ==========================================

@app.route("/backtest", methods=['GET', 'POST'])
@login_required
def backtest():
    """
    回測頁面
    - GET: 顯示回測設定表單
    - POST: 執行回測並顯示結果
    """
    if request.method == 'GET':
        # 顯示回測設定頁面
        available_strategies = list(strategy_manager.STRATEGY_REGISTRY.keys())
        return render_template('backtest.html', strategies=available_strategies)
    
    # POST: 執行回測
    try:
        from tool.viz_helper import generate_report_from_csv

        # 取得表單資料
        selected_strategies = request.form.getlist('strategies')  # 多選策略
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        weights_raw = (request.form.get('weights') or '').strip()
        weights = [float(w.strip()) for w in weights_raw.split(',') if w.strip()] if weights_raw else None
        
        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('backtest'))
        
        # 執行組合回測
        result, start_date, end_date = _run_portfolio_backtest(
            selected_strategies,
            start_date=start_date,
            end_date=end_date,
            weights=weights,
        )
        
        # 生成視覺化報告
        report = generate_report_from_csv()
        
        # 傳遞給模板
        return render_template(
            'backtest_result.html',
            metrics=result['metrics'],
            strategy_performance=result['strategy_performance'],
            equity_chart=report['equity_curve'],
            drawdown_chart=report['drawdown'],
            monthly_chart=report['monthly_returns'],
            selected_strategies=selected_strategies,
            start_date=start_date,
            end_date=end_date
        )
        
    except Exception as e:
        traceback.print_exc()
        flash(f'回測執行失敗: {str(e)}', 'error')
        return redirect(url_for('backtest'))


@app.route("/api/backtest/run", methods=['POST'])
@login_required
def api_run_backtest():
    """
    API: 執行回測
    Returns: JSON {success, data}
    """
    try:
        data = request.get_json() or {}
        selected_strategies = data.get('strategies', ['v31_hybrid'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        raw_weights = data.get('weights')

        weights = None
        if isinstance(raw_weights, list):
            weights = [float(w) for w in raw_weights]
        elif isinstance(raw_weights, str) and raw_weights.strip():
            weights = [float(w.strip()) for w in raw_weights.split(',') if w.strip()]

        # 執行回測
        result, start_date, end_date = _run_portfolio_backtest(
            selected_strategies,
            start_date=start_date,
            end_date=end_date,
            weights=weights,
        )
        
        return jsonify({
            'success': True,
            'data': {
                'metrics': result['metrics'],
                'strategy_performance': result['strategy_performance']
            }
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# Line Bot Webhook 路由
# ==========================================

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print(f"❌ callback 處理失敗: {e}")
        traceback.print_exc()

        # 防呆：即使內部錯誤，也嘗試回覆使用者，避免看起來「完全沒反應」
        try:
            payload = json.loads(body) if body else {}
            events = payload.get('events', []) if isinstance(payload, dict) else []
            reply_token = None
            if events and isinstance(events[0], dict):
                reply_token = events[0].get('replyToken')

            if reply_token:
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=[V3TextMessage(text="⚠️ 系統忙碌中，請稍後再試一次")]
                        )
                    )
        except Exception as fallback_error:
            print(f"⚠️ callback fallback 回覆失敗: {fallback_error}")
    return 'OK'


@handler.add(PostbackEvent)
def postback_handler(event):
    """Handle Rich Menu postback actions."""
    action = _extract_postback_action(getattr(getattr(event, 'postback', None), 'data', ''))
    messages = _build_postback_reply_messages(action)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages,
            )
        )


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    Line 訊息處理中心（V2.0 完整指令版）
    """
    raw_text = event.message.text if event.message and event.message.text else ''
    msg_text = _normalize_line_text(raw_text)
    msg_key = _compact_command_key(msg_text)
    
    # ========== 設定管理指令（資料驅動模式切換）==========
    # 全域模式切換（積極 / 平衡 / 寬鬆 / 穩健）
    if msg_key in MODE_CMD_MAP:
        mode_label, preset_key = MODE_CMD_MAP[msg_key]
        emoji = MODE_EMOJI.get(preset_key, '')
        if update_setting('mode', preset_key):
            updates = {**V34_MODE_PRESETS[preset_key], **V35_MODE_PRESETS[preset_key]}
            all_ok = _apply_settings_batch(updates)
            if all_ok:
                reply = f"{emoji} {MODE_REPLY_TEMPLATE[preset_key]}\n💡 直接輸入「推薦」即可生效"
            else:
                reply = f"⚠️ 模式已切換至{mode_label}，但部分 V34/V35 參數更新失敗"
        else:
            reply = "❌ 切換失敗，請稍後再試"

    # V34/V35 個別模式快捷指令
    elif any(_is_quick_mode_cmd(msg_text, ver, style)
             for ver in ('34', '35') for style in ('aggressive', 'balanced', 'loose', 'conservative')):
        reply = None
        for ver in ('34', '35'):
            for style in ('aggressive', 'balanced', 'loose', 'conservative'):
                if _is_quick_mode_cmd(msg_text, ver, style):
                    presets = V34_MODE_PRESETS if ver == '34' else V35_MODE_PRESETS
                    all_ok = _apply_settings_batch(presets[style])
                    emoji = MODE_EMOJI.get(style, '')
                    style_label = {'aggressive': '積極', 'balanced': '平衡', 'loose': '寬鬆', 'conservative': '保守'}[style]
                    if all_ok:
                        reply = f"{emoji} V{ver} 已設為{style_label}檔位"
                    else:
                        reply = f"❌ V{ver} {style_label}設定失敗"
                    break
            if reply:
                break

    elif re.match(r'^設定\s*[Vv]34\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]34\s*放寬\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = "❌ 格式錯誤\n用法：設定V34放寬 10 0.90 0.7"
            else:
                yoy_min = float(parts[0])
                breakout_ratio = float(parts[1])
                vol_min = float(parts[2])

                if not (-50 <= yoy_min <= 300 and 0.70 <= breakout_ratio <= 1.10 and 0.0 <= vol_min <= 5.0):
                    reply = "❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5"
                else:
                    updates = {
                        'v34_relaxed_revenue_yoy_min': str(yoy_min),
                        'v34_relaxed_breakout_ratio': str(breakout_ratio),
                        'v34_relaxed_volume_ratio_min': str(vol_min),
                    }
                    ok = all(update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f"✅ V34 放寬參數已更新\nYoY>{yoy_min:.1f}% / 突破>={breakout_ratio:.2f} / 量比>{vol_min:.2f}"
                        if ok else "❌ V34 放寬參數更新失敗"
                    )
        except ValueError:
            reply = "❌ 格式錯誤\n用法：設定V34放寬 10 0.90 0.7"

    elif re.match(r'^設定\s*[Vv]34\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]34\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = "❌ 格式錯誤\n用法：設定V34 18 0.93 0.9"
            else:
                yoy_min = float(parts[0])
                breakout_ratio = float(parts[1])
                vol_min = float(parts[2])

                if not (-50 <= yoy_min <= 300 and 0.70 <= breakout_ratio <= 1.10 and 0.0 <= vol_min <= 5.0):
                    reply = "❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5"
                else:
                    updates = {
                        'v34_revenue_yoy_min': str(yoy_min),
                        'v34_breakout_ratio': str(breakout_ratio),
                        'v34_volume_ratio_min': str(vol_min),
                    }
                    ok = all(update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f"✅ V34 嚴格參數已更新\nYoY>{yoy_min:.1f}% / 突破>={breakout_ratio:.2f} / 量比>{vol_min:.2f}"
                        if ok else "❌ V34 嚴格參數更新失敗"
                    )
        except ValueError:
            reply = "❌ 格式錯誤\n用法：設定V34 18 0.93 0.9"

    elif re.match(r'^設定\s*[Vv]35\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]35\s*放寬\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = "❌ 格式錯誤\n用法：設定V35放寬 4 -5 0.6"
            else:
                op_margin_pct = float(parts[0])
                revenue_yoy = float(parts[1])
                vol_min = float(parts[2])
                op_margin = op_margin_pct / 100

                if not (-20 <= op_margin_pct <= 100 and -100 <= revenue_yoy <= 300 and 0.0 <= vol_min <= 5.0):
                    reply = "❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5"
                else:
                    updates = {
                        'v35_relaxed_op_margin_min': str(op_margin),
                        'v35_relaxed_revenue_yoy_min': str(revenue_yoy),
                        'v35_relaxed_volume_ratio_min': str(vol_min),
                    }
                    ok = all(update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f"✅ V35 放寬參數已更新\n利益率>{op_margin_pct:.1f}% / YoY>{revenue_yoy:.1f}% / 量比>{vol_min:.2f}"
                        if ok else "❌ V35 放寬參數更新失敗"
                    )
        except ValueError:
            reply = "❌ 格式錯誤\n用法：設定V35放寬 4 -5 0.6"

    elif re.match(r'^設定\s*[Vv]35\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]35\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = "❌ 格式錯誤\n用法：設定V35 6 0 0.8"
            else:
                op_margin_pct = float(parts[0])
                revenue_yoy = float(parts[1])
                vol_min = float(parts[2])
                op_margin = op_margin_pct / 100

                if not (-20 <= op_margin_pct <= 100 and -100 <= revenue_yoy <= 300 and 0.0 <= vol_min <= 5.0):
                    reply = "❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5"
                else:
                    updates = {
                        'v35_op_margin_min': str(op_margin),
                        'v35_revenue_yoy_min': str(revenue_yoy),
                        'v35_volume_ratio_min': str(vol_min),
                    }
                    ok = all(update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f"✅ V35 嚴格參數已更新\n利益率>{op_margin_pct:.1f}% / YoY>{revenue_yoy:.1f}% / 量比>{vol_min:.2f}"
                        if ok else "❌ V35 嚴格參數更新失敗"
                    )
        except ValueError:
            reply = "❌ 格式錯誤\n用法：設定V35 6 0 0.8"
            
    elif msg_text.startswith("設定信心"):
        try:
            # 解析數字：「設定信心 60」→ 60
            value_str = msg_text.replace("設定信心", "").strip()
            value = float(value_str) / 100
            
            # 驗證範圍
            is_valid, err_msg = validate_setting('ai_threshold', str(value))
            if not is_valid:
                reply = f"❌ {err_msg}\n範例：設定信心 60（代表60%）"
            elif update_setting('ai_threshold', str(value)):
                reply = f"🧠 AI 信心門檻已設為 {int(value*100)}%\n將只推薦高於此門檻的股票"
            else:
                reply = "❌ 設定失敗"
        except ValueError:
            reply = "❌ 格式錯誤\n正確用法：設定信心 60"
    
    # ========== V30 參數調整指令 ==========
    elif msg_text.startswith("設定停損"):
        try:
            value_str = msg_text.replace("設定停損", "").strip()
            value = float(value_str) / 100
            if 0.01 <= value <= 0.20:  # 1%-20% 範圍
                if update_setting('v30_stop_loss', str(value)):
                    reply = f"🛡️ V30停損已設為 {int(value*100)}%\n下次選股將使用新參數"
                else:
                    reply = "❌ 設定失敗"
            else:
                reply = "❌ 停損需在 1%-20% 之間\n範例：設定停損 5"
        except ValueError:
            reply = "❌ 格式錯誤\n正確用法：設定停損 5（代表5%）"
    
    elif msg_text.startswith("設定停利"):
        try:
            value_str = msg_text.replace("設定停利", "").strip()
            if value_str == "0" or value_str.lower() == "不停利":
                if update_setting('v30_take_profit', '0.0'):
                    params = get_v30_params_from_db()
                    reply = f"🎯 V30停利已取消\n將持有至停損或到期（{params['MAX_HOLD_DAYS']}天）"
                else:
                    reply = "❌ 設定失敗"
            else:
                value = float(value_str) / 100
                if 0.05 <= value <= 0.50:  # 5%-50% 範圍
                    if update_setting('v30_take_profit', str(value)):
                        reply = f"🎯 V30停利已設為 {int(value*100)}%\n下次選股將使用新參數"
                    else:
                        reply = "❌ 設定失敗"
                else:
                    reply = "❌ 停利需在 5%-50% 之間\n範例：設定停利 20（代表20%）\n或輸入「設定停利 0」取消停利"
        except ValueError:
            reply = "❌ 格式錯誤\n用法：\n• 設定停利 20（20%停利）\n• 設定停利 0（不停利）"
            
    elif msg_text == "查看設定":
        reply = get_settings_info()
    
    # ========== 策略切換指令（資料驅動） ==========
    elif _match_strategy_switch(msg_text.lower()):
        strategy_key, strategy_display, features_text = _match_strategy_switch(msg_text.lower())
        try:
            mgr = StrategyManager()
            if mgr.set_active_strategy(strategy_key):
                reply = f"🔄 已切換至【{strategy_display}】\n\n"
                reply += "🎯 特色：\n"
                reply += features_text
                reply += "\n💡 輸入「推薦」開始選股"
            else:
                reply = "❌ 切換失敗，請稍後再試"
        except Exception as e:
            reply = f"❌ 切換失敗: {e}"

    elif msg_text in ["查看策略", "目前策略", "策略狀態"]:
        try:
            mgr = StrategyManager()
            active = mgr.get_active_strategy()
            all_names = mgr.get_active_strategy_names()
            
            reply = "📊 【目前使用策略】\n\n"
            reply += f"🎯 策略：{active.display_name}\n"
            reply += f"📝 說明：{active.description}\n"
            reply += f"🛡️ 停損：{active.stop_loss*100:.0f}%\n"
            reply += f"🎯 停利：{active.take_profit*100:.0f}%\n"
            reply += f"⏰ 最長持有：{active.max_hold_days} 天\n\n"
            reply += "💡 可用切換指令：\n"
            reply += "• 切換V30 → V31 混合策略（均衡）\n"
            reply += "• 切換V33 → 低波動策略（穩健）\n"
            reply += "• 切換V34 → 雙渦輪策略（積極）\n"
            reply += "• 切換V35 → 經營效益策略（成長）\n"
            reply += "• 切換V36 → 籌碼動能策略（追蹤）\n"
            reply += "• 切換V37 → 均值回歸策略（反轉）\n"
            reply += "• 切換V38 → 高殖利率策略（價值）\n"
        except Exception as e:
            reply = f"❌ 查詢策略失敗: {e}"
        
    # ========== 核心功能指令 ==========
    elif msg_text in ["V30", "v30", "策略"]:
        # V30 策略選股（40% 報酬實績）
        reply = get_v30_recommendation()
        
    elif msg_text in ["推薦", "選股", "AI"]:
        # 🆕 Flex Carousel 推薦卡片（降級為純文字）
        try:
            flex_msg = get_strategy_recommendation(as_flex=True)
            if isinstance(flex_msg, str):
                # 篩選結果為空或錯誤 → 純文字回覆
                reply = flex_msg
            else:
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[flex_msg]
                        )
                    )
                return
        except Exception as e:
            print(f"⚠️ Flex Carousel 建構失敗，降級為純文字: {e}")
            reply = get_strategy_recommendation(as_flex=False)
        
    elif msg_text.isdigit() and len(msg_text) == 4:  # 股票代號（4碼）
        # 使用 AI 健康診斷報告 + Flex Message 卡片
        report = get_stock_report(msg_text)
        if report is not None:
            try:
                flex_msg = create_stock_flex_message(msg_text, report)
                # 直接回覆 Flex Message 並 return，不走底部的純文字回覆
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[flex_msg]
                        )
                    )
                return
            except Exception as e:
                print(f"⚠️ Flex Message 建構失敗，降級為文字: {e}")
                reply = format_stock_diagnosis(report)
        else:
            reply = "❌ 查無此股票資料"
        
    elif msg_text.startswith("查詢"):
        stock_id = msg_text.replace("查詢", "").strip()
        if stock_id.isdigit():
            reply = query_stock(stock_id)
        else:
            reply = "❌ 請輸入正確的股票代號"
    
    # ========== 持股查詢 ==========
    elif msg_text in ["持股", "目前持股", "AI持股", "庫存"]:
        try:
            rows = get_open_holdings(limit=10)
            
            if rows:
                holdings_list = []
                for r in rows:
                    entry_p = float(r[2]) if r[2] else 0
                    current_p = float(r[4]) if r[4] else entry_p
                    pnl = (current_p - entry_p) / entry_p if entry_p > 0 else 0
                    trade_dt = r[3]
                    days = (pd.Timestamp.now() - pd.Timestamp(trade_dt)).days if trade_dt else 0
                    holdings_list.append({
                        'stock_id': str(r[0]),
                        'entry_price': entry_p,
                        'current_price': current_p,
                        'pnl_pct': pnl,
                        'hold_days': days,
                        'strategy': str(r[1]) if r[1] else '',
                    })
                flex_msg = create_holdings_flex(
                    holdings=holdings_list,
                    strategy_name=holdings_list[0].get('strategy', ''),
                    date_str=str(pd.Timestamp.now().date()),
                )
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[flex_msg]
                        )
                    )
                return
            else:
                reply = "📭 目前無 AI 持股\n輸入「推薦」取得今日選股建議"
        except Exception as e:
            print(f"⚠️ 持股查詢失敗: {e}")
            reply = "📭 目前無持股紀錄\n輸入「推薦」取得今日選股建議"

    elif msg_text in ["日誌", "反思", "journal"]:
        reply = _build_journal_reflection_text()

    # ========== 回測摘要 ==========
    elif msg_text in ["回測", "績效", "回測結果", "backtest"]:
        try:
            from tool.viz_helper import get_backtest_summary
            summary = get_backtest_summary()
            if summary:
                metrics = {
                    'total_return': summary.get('total_roi', 0),
                    'max_drawdown': summary.get('max_drawdown', 0),
                    'sharpe_ratio': summary.get('sharpe_ratio', 0),
                    'win_rate': summary.get('win_rate', 0),
                    'total_trades': summary.get('trade_count', 0),
                    'period': summary.get('period', '最近回測'),
                }
                mgr = StrategyManager()
                active = mgr.get_active_strategy()
                sname = active.display_name if active else ''
                flex_msg = create_backtest_summary_flex(metrics, strategy_name=sname)
                with ApiClient(configuration) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[flex_msg]
                        )
                    )
                return
            else:
                reply = "📊 尚無回測數據\n請先執行 python 4_run_backtest.py"
        except Exception as e:
            print(f"⚠️ 回測摘要失敗: {e}")
            reply = "📊 尚無回測數據\n請先執行 python 4_run_backtest.py"

    # ========== V32: Dashboard 連結 ==========
    elif msg_text in ["dashboard", "儀表板", "Dashboard", "看板"]:
        dashboard_base = get_ngrok_url()
        dashboard_url = f"{dashboard_base}/dashboard"
        is_ngrok = 'ngrok' in dashboard_base
        url_hint = "（ngrok 公開連結，可直接點擊）" if is_ngrok else "（本機連結，需在同一網路）"
        reply = "📊 【V32 量化交易儀表板】\n\n"
        reply += f"🔗 Dashboard URL {url_hint}:\n"
        reply += f"{dashboard_url}\n\n"
        reply += "📈 功能:\n"
        reply += "• 資產曲線圖 (Equity Curve)\n"
        reply += "• 回測績效指標 (ROI, MDD, Sharpe)\n"
        reply += "• 交易明細表 (Recent Trades)\n"
        reply += "• 即時選股訊號 (Live Signals)\n\n"
        reply += "💡 提示: 請在電腦瀏覽器開啟以獲得最佳體驗"
            
    # ========== 新聞 + 選股 ==========
    elif msg_text in ["新聞", "News", "news", "早報"]:
        try:
            import datetime as _dt
            from tool.news_agent import get_morning_news_summary
            from tool.line_message_builder import create_news_flex
            news_summary = get_morning_news_summary()
            today_str = _dt.datetime.now().strftime('%Y-%m-%d')

            # 新聞 Flex Bubble
            news_flex = create_news_flex(news_summary, today_str)

            # 選股 Flex Carousel
            picks_flex = get_strategy_recommendation(as_flex=True)

            messages = [news_flex]
            if not isinstance(picks_flex, str):
                # Flex Carousel 成功
                messages.append(picks_flex)

            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages,
                    )
                )
            return
        except Exception as e:
            print(f"⚠️ 新聞 Flex 失敗，降級為純文字: {e}")
            traceback.print_exc()
            try:
                from tool.news_agent import get_morning_news_summary
                news_summary = get_morning_news_summary()
                picks_text = get_strategy_recommendation(as_flex=False)
                reply = f"📰 【今日新聞摘要】\n\n{news_summary}\n\n{'='*28}\n\n{picks_text}"
                if len(reply) > 4900:
                    reply = reply[:4900] + "\n..."
            except Exception as e2:
                reply = f"❌ 新聞取得失敗: {str(e2)[:100]}"

    # ========== 說明選單 ==========
    else:
        # 顯示當前策略
        try:
            mgr = StrategyManager()
            current = mgr.get_active_strategy()
            current_name = current.display_name if current else 'V31 混合策略'
        except:
            current_name = '未知'
        
        reply = f"🤖 【StockAI Line Bot V3.0】\n"
        reply += f"📌 目前策略：{current_name}\n"
        reply += "\n📋 指令清單:\n"
        reply += "-" * 30 + "\n"
        reply += "【🎯 選股功能】\n"
        reply += "• 推薦 → 使用當前策略選出前5名\n"
        reply += "• 新聞 → 今日新聞摘要 + 選股推薦\n"
        reply += "• V30 → 純技術分析選股\n"
        reply += "• 2330 → 個股 AI 健康診斷\n"
        reply += "• 持股 → 查看 AI 模擬持股\n"
        reply += "• 回測 → 最新回測績效摘要\n"
        reply += "\n【🔄 策略切換】\n"
        reply += "• 切換V30 → V31 混合（均衡型）\n"
        reply += "• 切換V33 → 低波動（穩健型）\n"
        reply += "• 切換V34 → 雙渦輪（積極型）\n"
        reply += "• 切換V35 → 經營效益（成長型）\n"
        reply += "• 切換V36 → 籌碼動能（追蹤型）\n"
        reply += "• 切換V37 → 均值回歸（反轉型）\n"
        reply += "• 切換V38 → 高殖利率（價值型）\n"
        reply += "• 查看策略 → 顯示目前策略詳情\n"
        reply += "\n【📊 Dashboard】\n"
        reply += "• dashboard → 開啟視覺化儀表板\n"
        reply += "\n【⚙️ V30 參數調整】\n"
        reply += "• 設定停損 5 (停損5%)\n"
        reply += "• 設定停利 20 (停利20%)\n"
        reply += "• 設定停利 0 (不停利)\n"
        reply += "• 查看設定\n"
        reply += "\n【🚀 V34/V35 參數調整】\n"
        reply += "• 切換積極 / 切換平衡 / 切換寬鬆\n"
        reply += "• V34積極 / V34平衡 / V34寬鬆\n"
        reply += "• V35積極 / V35平衡 / V35寬鬆\n"
        reply += "• 設定V34 18 0.93 0.9\n"
        reply += "• 設定V34放寬 10 0.90 0.7\n"
        reply += "• 設定V35 6 0 0.8\n"
        reply += "• 設定V35放寬 4 -5 0.6\n"
        reply += "-" * 30 + "\n"
        reply += "💡 輸入「推薦」開始選股\n"
        reply += "⚠️ 所有推薦僅供參考"
    
    # 使用 Line Bot SDK v3 回覆訊息
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[V3TextMessage(text=reply)]
            )
        )


if __name__ == "__main__":
    print("=" * 60)
    print("[START] Line Bot V3.0 啟動中 (V30策略增強版)")
    print(f"[MODEL] 模型：按策略動態載入")
    print(f"[INFO] 主要策略: V30 純技術分析 (40%報酬實績)")
    print(f"[PORT] 伺服器端口: 1688")
    print("=" * 60)

    if os.getenv('LINE_RICH_MENU_AUTO_SYNC', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        try:
            from tool.richmenu import sync_default_rich_menu_from_token

            rich_menu_id = sync_default_rich_menu_from_token()
            print(f"[OK] Rich Menu 綁定完成: {rich_menu_id}")
        except Exception as exc:
            print(f"[WARN] Rich Menu 自動綁定失敗: {exc}")

    app.run(host='0.0.0.0', port=1688, debug=False)
