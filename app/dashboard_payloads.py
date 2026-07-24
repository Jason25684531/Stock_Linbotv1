"""Dashboard payload builders: market/chip snapshots, signal lights, war room."""

# -*- coding: utf-8 -*-
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import app as app_pkg
from config import Config
from core.calc_indicators import calculate_kd_full, calculate_rsi
from core.db_helper import (
    build_dashboard_aggregation_cache_key,
    normalize_date_str,
    safe_float,
    safe_int,
)
from core.mcp_client import MCPClientError
from app.news_overlay import _get_stock_specific_news_summary, _live_signal_news_timeout_scope


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

    trade_date = app_pkg._current_line_date()
    try:
        result = app_pkg.MCPClient().get_market_statistics_sync(trade_date)
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

    trade_date = app_pkg._current_line_date()
    try:
        result = app_pkg.MCPClient().get_foreign_investment_sync(trade_date)
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
        import json

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
    normalized_requested_date = normalize_date_str(requested_date) or app_pkg._current_line_date()
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

    history_df = app_pkg.get_stock_history(normalized_stock_id, limit=360, end_date=normalized_requested_date)
    prepared_history = _prepare_dashboard_history_frame(history_df)
    aggregated_history = _aggregate_dashboard_history(prepared_history, normalized_period)
    if aggregated_history.empty:
        payload['message'] = f'查無 {normalized_stock_id} 的可用行情資料。'
        payload['llm_report'] = app_pkg._build_dashboard_llm_report(normalized_stock_id, {}, {}, {}, {})
        return payload

    latest_row = aggregated_history.iloc[-1]
    as_of_date = normalize_date_str(latest_row.get('trade_date'))
    fallback_used = as_of_date != normalized_requested_date
    report = app_pkg.get_stock_report(normalized_stock_id, as_of_date=as_of_date) or {'stock_id': normalized_stock_id, 'trade_date': as_of_date}

    sector = app_pkg.get_stock_sector(normalized_stock_id)
    market_snapshot = app_pkg._build_market_snapshot()
    chip_snapshot = app_pkg._build_chip_snapshot()
    with _live_signal_news_timeout_scope():
        stock_mentions_map = app_pkg._get_stock_mentions_map([normalized_stock_id])
    news_info = _get_stock_specific_news_summary(normalized_stock_id, stock_mentions_map)
    if not news_info.get('items'):
        news_info = app_pkg._get_sector_news_summary(sector, as_of_date)

    rule_report = app_pkg._build_dashboard_rule_report(
        normalized_stock_id,
        report,
        latest_row,
        news_info,
        market_snapshot,
        chip_snapshot,
    )
    llm_report = app_pkg._build_dashboard_llm_report(
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
        'trade_date': as_of_date,
        'source_date': as_of_date,
        'price_basis': 'latest_actual_close',
        'data_source': 'daily_market_data',
        'is_stale': fallback_used,
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
    normalized_requested_date = normalize_date_str(requested_date) or app_pkg._current_line_date()
    normalized_period = _normalize_war_room_period(period)
    if not normalized_stock_id:
        return _build_dashboard_health_check_payload_local(
            stock_id,
            normalized_requested_date,
            period=normalized_period,
            overlays=overlays,
            panes=panes,
        )

    client = app_pkg.MCPClient()

    trend_payload = app_pkg.resolve_dashboard_aggregation_cache(
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
    screening_payload = app_pkg.resolve_dashboard_aggregation_cache(
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
    market_snapshot = app_pkg._build_market_snapshot()
    chip_snapshot = app_pkg._build_chip_snapshot()
    display_date = str(market_snapshot.get('date_str') or chip_snapshot.get('date_str') or app_pkg._current_line_date())
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
    normalized_requested_date = normalize_date_str(requested_date) or app_pkg._current_line_date()
    client = app_pkg.MCPClient()

    hotspot_payload = app_pkg.resolve_dashboard_aggregation_cache(
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
