"""Internal MCP HTTP service for TWSE dataflow modernization."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
import requests
from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db_helper import get_stock_history, get_stock_sector, normalize_date_str  # noqa: E402
from core.crawlers.quarterly_scraper import QuarterlyScraper  # noqa: E402
from core.report_helper import get_stock_report  # noqa: E402

MarketCode = Literal['TWSE', 'TPEx', 'ALL']
ToolRouteHandler = Callable[[dict[str, Any]], tuple[Any, int]]

app = Flask(__name__)
app.config['TRUSTED_HOSTS'] = [
    host.strip()
    for host in os.getenv(
        'MCP_TRUSTED_HOSTS',
        'localhost,127.0.0.1,twse-mcp-server,twse-mcp-server:8080',
    ).split(',')
    if host.strip()
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'X-Requested-With': 'XMLHttpRequest',
}

LOGGER = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Raised when an incoming MCP request payload is invalid."""


class NotFoundError(ValueError):
    """Raised when a requested MCP resource cannot be found."""


def _normalize_market(market: Any) -> MarketCode:
    normalized = str(market).strip().upper()
    mapping: dict[str, MarketCode] = {
        'TWSE': 'TWSE',
        'TPEX': 'TPEx',
        'ALL': 'ALL',
    }
    if normalized not in mapping:
        raise ValidationError(f'Unsupported market: {market}')
    return mapping[normalized]


def _normalize_trade_date(trade_date: Any) -> str:
    if isinstance(trade_date, datetime):
        return trade_date.date().isoformat()
    if isinstance(trade_date, date):
        return trade_date.isoformat()

    normalized = str(trade_date).strip()
    if not normalized:
        raise ValidationError('trade_date is required')
    try:
        return date.fromisoformat(normalized[:10]).isoformat()
    except ValueError as exc:
        raise ValidationError('trade_date must be a valid ISO date') from exc


def _require_correlation_id(payload: dict[str, Any]) -> str:
    correlation_id = str(payload.get('correlation_id', '')).strip()
    if not correlation_id:
        raise ValidationError('correlation_id is required')
    return correlation_id


def _require_stock_id(payload: dict[str, Any]) -> str:
    stock_id = str(payload.get('stock_id', '')).strip()
    if not stock_id:
        raise ValidationError('stock_id is required')
    return stock_id


def _error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    retryable: bool,
    correlation_id: str,
    details: dict[str, Any] | None = None,
):
    return jsonify({
        'error_code': error_code,
        'message': message,
        'retryable': retryable,
        'correlation_id': correlation_id,
        'details': details or {},
    }), status_code


def _success_response(body: dict[str, Any]) -> tuple[Any, int]:
    return jsonify(body), 200


def _extract_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError('request payload must be a JSON object')
    return payload


def _log_tool_dispatch(
    *,
    tool_name: str,
    correlation_id: str,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    message_parts = [
        'MCP tool dispatch',
        f'tool={tool_name}',
        f'correlation_id={correlation_id}',
        f'event={event}',
    ]
    if extra:
        message_parts.extend(
            f'{key}={value}'
            for key, value in extra.items()
        )
    LOGGER.info(' | '.join(message_parts))


def _records_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(',', '', regex=False).replace({'--': None, 'nan': None, 'None': None, '': None})
    return pd.to_numeric(cleaned, errors='coerce').fillna(0)


def _prepare_snapshot_frame(
    frame: pd.DataFrame,
    trade_date: str,
    include_etfs: bool,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=[
                'stock_id',
                'trade_date',
                'open_price',
                'high_price',
                'low_price',
                'close_price',
                'volume',
                'pe_ratio',
                'stock_name',
                'security_type',
            ]
        )

    prepared = frame.copy()
    prepared['stock_id'] = prepared['stock_id'].astype(str).str.strip()
    if 'stock_name' not in prepared.columns:
        prepared['stock_name'] = None
    if 'pe_ratio' not in prepared.columns:
        prepared['pe_ratio'] = 0
    prepared['security_type'] = prepared['stock_id'].apply(
        lambda stock_id: 'etf' if stock_id.startswith('00') else 'stock'
    )
    if not include_etfs:
        prepared = prepared[prepared['security_type'] != 'etf']

    for column in [
        'open_price',
        'high_price',
        'low_price',
        'close_price',
        'volume',
        'pe_ratio',
    ]:
        prepared[column] = _coerce_numeric_series(prepared[column])

    prepared['trade_date'] = trade_date
    return prepared[
        [
            'stock_id',
            'trade_date',
            'open_price',
            'high_price',
            'low_price',
            'close_price',
            'volume',
            'pe_ratio',
            'stock_name',
            'security_type',
        ]
    ]


def _prepare_flow_frame(frame: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=[
                'stock_id',
                'trade_date',
                'foreign_buy',
                'trust_buy',
                'dealer_buy',
            ]
        )

    prepared = frame.copy()
    prepared['stock_id'] = prepared['stock_id'].astype(str).str.strip()
    for column in ['foreign_buy', 'trust_buy', 'dealer_buy']:
        if column not in prepared.columns:
            prepared[column] = 0
        prepared[column] = _coerce_numeric_series(prepared[column])
    prepared['trade_date'] = trade_date
    return prepared[
        ['stock_id', 'trade_date', 'foreign_buy', 'trust_buy', 'dealer_buy']
    ]


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(value: Any) -> int | None:
    parsed = _coerce_optional_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def _normalize_history_limit(payload: dict[str, Any]) -> int:
    raw_value = payload.get('history_limit', 120)
    try:
        history_limit = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError('history_limit must be an integer') from exc
    if history_limit < 20 or history_limit > 240:
        raise ValidationError('history_limit must be between 20 and 240')
    return history_limit


def _parse_aggregation_request(payload: dict[str, Any]) -> tuple[str, MarketCode, str]:
    correlation_id = _require_correlation_id(payload)
    market = _normalize_market(payload.get('market') or 'ALL')
    trade_date = _normalize_trade_date(payload.get('trade_date') or date.today().isoformat())
    return correlation_id, market, trade_date


def _parse_stock_aggregation_request(
    payload: dict[str, Any],
) -> tuple[str, MarketCode, str, str, int]:
    correlation_id, market, trade_date = _parse_aggregation_request(payload)
    stock_id = _require_stock_id(payload)
    history_limit = _normalize_history_limit(payload)
    return correlation_id, market, trade_date, stock_id, history_limit


def _resolve_flow_record(
    *,
    market: MarketCode,
    trade_date: str,
    stock_id: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    degraded_fields: list[str] = []
    warnings: list[str] = []
    try:
        flow_frame = fetch_foreign_investor_flow(market, trade_date)
        if flow_frame.empty:
            degraded_fields.append('institutional')
            warnings.append('institutional data unavailable for requested date')
            return {}, degraded_fields, warnings
        filtered = flow_frame[flow_frame['stock_id'].astype(str).str.strip() == stock_id].copy()
        if filtered.empty:
            degraded_fields.append('institutional')
            warnings.append(f'no institutional flow found for stock_id={stock_id}')
            return {}, degraded_fields, warnings
        record = filtered.iloc[0].to_dict()
        return {
            'foreign_buy': _coerce_optional_int(record.get('foreign_buy')),
            'trust_buy': _coerce_optional_int(record.get('trust_buy')),
            'dealer_buy': _coerce_optional_int(record.get('dealer_buy')),
        }, degraded_fields, warnings
    except Exception as exc:
        degraded_fields.append('institutional')
        warnings.append(f'institutional flow degraded: {exc}')
        return {}, degraded_fields, warnings


def _build_trend_series(history_df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    candles: list[dict[str, Any]] = []
    volume_series: list[dict[str, Any]] = []
    ma5_series: list[dict[str, Any]] = []
    ma20_series: list[dict[str, Any]] = []
    ma60_series: list[dict[str, Any]] = []

    for _, row in history_df.iterrows():
        trade_date = normalize_date_str(row.get('trade_date'))
        open_price = _coerce_optional_float(row.get('open_price'))
        high_price = _coerce_optional_float(row.get('high_price'))
        low_price = _coerce_optional_float(row.get('low_price'))
        close_price = _coerce_optional_float(row.get('close_price'))
        volume = _coerce_optional_int(row.get('volume')) or 0
        if trade_date and None not in (open_price, high_price, low_price, close_price):
            candles.append(
                {
                    'time': trade_date,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                }
            )
            volume_series.append(
                {
                    'time': trade_date,
                    'value': volume,
                    'color': '#f85149' if close_price >= open_price else '#2ea043',
                }
            )
        for source_key, target_list in (
            ('ma5', ma5_series),
            ('ma20', ma20_series),
            ('ma60', ma60_series),
        ):
            value = _coerce_optional_float(row.get(source_key))
            if trade_date and value is not None:
                target_list.append({'time': trade_date, 'value': value})

    return {
        'candles': candles,
        'volume': volume_series,
        'ma5': ma5_series,
        'ma20': ma20_series,
        'ma60': ma60_series,
    }


def _build_twse_stock_trend_payload(
    *,
    stock_id: str,
    requested_date: str,
    market: MarketCode,
    history_limit: int,
) -> dict[str, Any]:
    history_df = get_stock_history(stock_id, limit=history_limit, end_date=requested_date)
    if history_df.empty:
        raise NotFoundError(f'No history found for stock_id={stock_id}')

    latest_row = history_df.iloc[-1]
    as_of_date = normalize_date_str(latest_row.get('trade_date')) or requested_date
    fallback_used = as_of_date != requested_date
    report = get_stock_report(stock_id, as_of_date=as_of_date) or {
        'stock_id': stock_id,
        'trade_date': as_of_date,
    }
    sector = get_stock_sector(stock_id)
    institutional, degraded_fields, warnings = _resolve_flow_record(
        market=market,
        trade_date=as_of_date,
        stock_id=stock_id,
    )

    record = {
        'stock_id': stock_id,
        'trade_date': as_of_date,
        'stock_name': report.get('stock_name'),
        'sector': sector,
        'close_price': _coerce_optional_float(report.get('close_price')),
        'open_price': _coerce_optional_float(report.get('open_price')),
        'high_price': _coerce_optional_float(report.get('high_price')),
        'low_price': _coerce_optional_float(report.get('low_price')),
        'volume': _coerce_optional_int(latest_row.get('volume')),
        'ma5': _coerce_optional_float(report.get('ma5')),
        'ma20': _coerce_optional_float(report.get('ma20')),
        'ma60': _coerce_optional_float(report.get('ma60')),
        'rsi': _coerce_optional_float(report.get('rsi')),
        'bias': _coerce_optional_float(report.get('bias')),
        'chip_score': _coerce_optional_float(report.get('chip_score')),
        'ai_score': _coerce_optional_float(report.get('ai_score')),
        'strategy_name': report.get('strategy_name'),
        'foreign_buy': institutional.get('foreign_buy', _coerce_optional_int(report.get('foreign_buy'))),
        'trust_buy': institutional.get('trust_buy', _coerce_optional_int(report.get('trust_buy'))),
        'dealer_buy': institutional.get('dealer_buy', _coerce_optional_int(report.get('dealer_buy'))),
    }

    return {
        'dataset': 'twse_stock_trend',
        'requested_date': requested_date,
        'as_of_date': as_of_date,
        'market': market,
        'stock_id': stock_id,
        'status': 'partial' if degraded_fields else 'ok',
        'fallback_used': fallback_used,
        'source': [
            'daily_market_data',
            'daily_recommendations',
            'financial_statements',
            'monthly_revenue',
            'foreign_investor_flow',
        ],
        'degraded_fields': degraded_fields,
        'warnings': warnings,
        'quote': {
            'open_price': record['open_price'],
            'high_price': record['high_price'],
            'low_price': record['low_price'],
            'close_price': record['close_price'],
            'volume': record['volume'],
        },
        'indicators': {
            'ma5': record['ma5'],
            'ma20': record['ma20'],
            'ma60': record['ma60'],
            'rsi': record['rsi'],
            'bias': record['bias'],
            'chip_score': record['chip_score'],
            'ai_score': record['ai_score'],
        },
        'institutional': {
            'foreign_buy': record['foreign_buy'],
            'trust_buy': record['trust_buy'],
            'dealer_buy': record['dealer_buy'],
        },
        'series': _build_trend_series(history_df),
        'record': record,
        'records': [record],
        'meta': {
            'record_count': 1,
            'history_points': len(history_df),
            'history_limit': history_limit,
        },
    }


def _build_investment_screening_payload(
    *,
    stock_id: str,
    requested_date: str,
    market: MarketCode,
) -> dict[str, Any]:
    report = get_stock_report(stock_id, as_of_date=requested_date)
    if not report:
        raise NotFoundError(f'No screening data found for stock_id={stock_id}')

    as_of_date = normalize_date_str(report.get('trade_date')) or requested_date
    institutional, degraded_fields, warnings = _resolve_flow_record(
        market=market,
        trade_date=as_of_date,
        stock_id=stock_id,
    )
    sector = get_stock_sector(stock_id)
    ai_score = _coerce_optional_float(report.get('ai_score'))
    rsi = _coerce_optional_float(report.get('rsi'))
    revenue_yoy = _coerce_optional_float(report.get('revenue_yoy'))
    op_margin = _coerce_optional_float(report.get('op_margin'))
    chip_score = _coerce_optional_float(report.get('chip_score'))
    technical_pass = bool(report.get('close_price')) and bool(report.get('ma20'))
    momentum_pass = ai_score is not None and ai_score >= 0.5
    chip_pass = chip_score is not None and chip_score >= 0
    fundamental_pass = (revenue_yoy is not None and revenue_yoy >= 0) or (op_margin is not None and op_margin >= 0)
    screening_score = sum(int(flag) for flag in (technical_pass, momentum_pass, chip_pass, fundamental_pass))

    record = {
        'stock_id': stock_id,
        'trade_date': as_of_date,
        'sector': sector,
        'close_price': _coerce_optional_float(report.get('close_price')),
        'rsi': rsi,
        'ai_score': ai_score,
        'chip_score': chip_score,
        'revenue_yoy': revenue_yoy,
        'op_margin': op_margin,
        'foreign_buy': institutional.get('foreign_buy', _coerce_optional_int(report.get('foreign_buy'))),
        'trust_buy': institutional.get('trust_buy', _coerce_optional_int(report.get('trust_buy'))),
        'dealer_buy': institutional.get('dealer_buy', _coerce_optional_int(report.get('dealer_buy'))),
        'strategy_name': report.get('strategy_name'),
        'screening_score': screening_score,
    }

    return {
        'dataset': 'investment_screening',
        'requested_date': requested_date,
        'as_of_date': as_of_date,
        'market': market,
        'stock_id': stock_id,
        'status': 'partial' if degraded_fields else 'ok',
        'fallback_used': as_of_date != requested_date,
        'source': [
            'daily_market_data',
            'daily_recommendations',
            'financial_statements',
            'monthly_revenue',
            'foreign_investor_flow',
        ],
        'degraded_fields': degraded_fields,
        'warnings': warnings,
        'screening': {
            'technical_pass': technical_pass,
            'momentum_pass': momentum_pass,
            'chip_pass': chip_pass,
            'fundamental_pass': fundamental_pass,
            'score': screening_score,
        },
        'report_sections': {
            'technical': [
                f"RSI {rsi:.1f}" if rsi is not None else 'RSI unavailable',
                f"AI score {ai_score:.2f}" if ai_score is not None else 'AI score unavailable',
            ],
            'fundamental': [
                f"Revenue YoY {revenue_yoy:.1f}%" if revenue_yoy is not None else 'Revenue YoY unavailable',
                f"Op margin {op_margin:.2f}" if op_margin is not None else 'Op margin unavailable',
            ],
            'institutional': [
                f"Foreign {record['foreign_buy']}" if record['foreign_buy'] is not None else 'Foreign flow unavailable',
                f"Trust {record['trust_buy']}" if record['trust_buy'] is not None else 'Trust flow unavailable',
                f"Dealer {record['dealer_buy']}" if record['dealer_buy'] is not None else 'Dealer flow unavailable',
            ],
        },
        'record': record,
        'records': [record],
        'meta': {'record_count': 1},
    }


def _build_market_hotspot_payload(
    *,
    requested_date: str,
    market: MarketCode,
) -> dict[str, Any]:
    snapshot_frame = fetch_stock_basic_snapshot(market, requested_date, include_etfs=False)
    if snapshot_frame.empty:
        raise NotFoundError(f'No market hotspot snapshot found for trade_date={requested_date}')

    flow_frame = fetch_foreign_investor_flow(market, requested_date)
    prepared_snapshot = _prepare_snapshot_frame(snapshot_frame, requested_date, include_etfs=False)
    prepared_snapshot['pct_change'] = prepared_snapshot.apply(
        lambda row: ((row['close_price'] - row['open_price']) / row['open_price'] * 100)
        if row['open_price'] not in (0, None)
        else 0,
        axis=1,
    )

    advancing = int((prepared_snapshot['close_price'] > prepared_snapshot['open_price']).sum())
    declining = int((prepared_snapshot['close_price'] < prepared_snapshot['open_price']).sum())
    unchanged = int((prepared_snapshot['close_price'] == prepared_snapshot['open_price']).sum())

    top_gainers = prepared_snapshot.sort_values('pct_change', ascending=False).head(5).copy()
    gainers_records = [
        {
            'stock_id': str(row['stock_id']).strip(),
            'trade_date': requested_date,
            'stock_name': row.get('stock_name'),
            'close_price': _coerce_optional_float(row.get('close_price')),
            'pct_change': _coerce_optional_float(row.get('pct_change')),
            'volume': _coerce_optional_int(row.get('volume')),
        }
        for _, row in top_gainers.iterrows()
    ]

    flow_records: list[dict[str, Any]] = []
    if not flow_frame.empty:
        prepared_flow = _prepare_flow_frame(flow_frame, requested_date)
        merged = prepared_flow.merge(
            prepared_snapshot[['stock_id', 'stock_name']],
            on='stock_id',
            how='left',
        )
        top_inflows = merged.sort_values('foreign_buy', ascending=False).head(5)
        flow_records = [
            {
                'stock_id': str(row['stock_id']).strip(),
                'trade_date': requested_date,
                'stock_name': row.get('stock_name'),
                'foreign_buy': _coerce_optional_int(row.get('foreign_buy')),
                'trust_buy': _coerce_optional_int(row.get('trust_buy')),
                'dealer_buy': _coerce_optional_int(row.get('dealer_buy')),
            }
            for _, row in top_inflows.iterrows()
        ]

    return {
        'dataset': 'market_hotspot',
        'requested_date': requested_date,
        'as_of_date': requested_date,
        'market': market,
        'status': 'ok',
        'fallback_used': False,
        'source': ['stock_basic_snapshot', 'foreign_investor_flow'],
        'degraded_fields': [] if flow_records else ['institutional'],
        'warnings': [] if flow_records else ['institutional flow unavailable for hotspot ranking'],
        'breadth': {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'universe_count': len(prepared_snapshot),
        },
        'hotspots': {
            'top_gainers': gainers_records,
            'top_foreign_inflows': flow_records,
        },
        'records': gainers_records,
        'meta': {
            'record_count': len(gainers_records),
            'universe_count': len(prepared_snapshot),
        },
    }


def _build_snapshot_success_payload(
    *,
    frame: pd.DataFrame,
    trade_date: str,
    market: MarketCode,
    include_etfs: bool,
    dataset: str = 'stock_basic_snapshot',
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {
        'record_count': len(frame),
        'include_etfs': include_etfs,
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        'dataset': dataset,
        'as_of_date': trade_date,
        'market': market,
        'records': _records_from_frame(frame),
        'meta': meta,
    }


def _build_flow_success_payload(
    *,
    frame: pd.DataFrame,
    trade_date: str,
    market: MarketCode,
) -> dict[str, Any]:
    return {
        'dataset': 'foreign_investor_flow',
        'as_of_date': trade_date,
        'market': market,
        'records': _records_from_frame(frame),
        'meta': {'record_count': len(frame)},
    }


def _parse_snapshot_request(
    payload: dict[str, Any],
) -> tuple[str, MarketCode, str, bool]:
    correlation_id = _require_correlation_id(payload)
    market = _normalize_market(payload.get('market'))
    trade_date = _normalize_trade_date(payload.get('trade_date'))
    include_etfs = bool(payload.get('include_etfs', True))
    return correlation_id, market, trade_date, include_etfs


def _parse_flow_request(payload: dict[str, Any]) -> tuple[str, MarketCode, str]:
    correlation_id = _require_correlation_id(payload)
    market = _normalize_market(payload.get('market'))
    trade_date = _normalize_trade_date(payload.get('trade_date'))
    return correlation_id, market, trade_date


def _handle_market_statistics_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date, include_etfs = _parse_snapshot_request(payload)
    frame = fetch_stock_basic_snapshot(market, trade_date, include_etfs)
    _log_tool_dispatch(
        tool_name='get_market_statistics',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'stock_basic_snapshot',
            'record_count': len(frame),
            'market': market,
        },
    )
    return _success_response(
        _build_snapshot_success_payload(
            frame=frame,
            trade_date=trade_date,
            market=market,
            include_etfs=include_etfs,
        )
    )


def _handle_company_basic_info_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date, include_etfs = _parse_snapshot_request(payload)
    stock_id = _require_stock_id(payload)
    frame = fetch_stock_basic_snapshot(market, trade_date, include_etfs)
    filtered = frame[frame['stock_id'].astype(str).str.strip() == stock_id].copy()
    if filtered.empty:
        raise NotFoundError(f'No market snapshot found for stock_id={stock_id}')

    filtered = filtered.head(1).reset_index(drop=True)
    records = _records_from_frame(filtered)
    record = records[0]
    success_payload = _build_snapshot_success_payload(
        frame=filtered,
        trade_date=trade_date,
        market=market,
        include_etfs=include_etfs,
        dataset='company_basic_info',
        extra_meta={'stock_id': stock_id},
    )
    success_payload['record'] = record
    _log_tool_dispatch(
        tool_name='get_company_basic_info',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'company_basic_info',
            'stock_id': stock_id,
            'record_count': 1,
        },
    )
    return _success_response(success_payload)


def _handle_foreign_investment_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date = _parse_flow_request(payload)
    frame = fetch_foreign_investor_flow(market, trade_date)
    _log_tool_dispatch(
        tool_name='get_foreign_investment',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'foreign_investor_flow',
            'record_count': len(frame),
            'market': market,
        },
    )
    return _success_response(
        _build_flow_success_payload(
            frame=frame,
            trade_date=trade_date,
            market=market,
        )
    )


def _handle_twse_stock_trend_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date, stock_id, history_limit = _parse_stock_aggregation_request(payload)
    response_payload = _build_twse_stock_trend_payload(
        stock_id=stock_id,
        requested_date=trade_date,
        market=market,
        history_limit=history_limit,
    )
    _log_tool_dispatch(
        tool_name='twse_stock_trend',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'twse_stock_trend',
            'stock_id': stock_id,
            'record_count': len(response_payload['records']),
        },
    )
    return _success_response(response_payload)


def _handle_investment_screening_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date, stock_id, _ = _parse_stock_aggregation_request(payload)
    response_payload = _build_investment_screening_payload(
        stock_id=stock_id,
        requested_date=trade_date,
        market=market,
    )
    _log_tool_dispatch(
        tool_name='investment_screening',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'investment_screening',
            'stock_id': stock_id,
            'record_count': len(response_payload['records']),
        },
    )
    return _success_response(response_payload)


def _handle_market_hotspot_payload(payload: dict[str, Any]) -> tuple[Any, int]:
    correlation_id, market, trade_date = _parse_aggregation_request(payload)
    response_payload = _build_market_hotspot_payload(
        requested_date=trade_date,
        market=market,
    )
    _log_tool_dispatch(
        tool_name='market_hotspot',
        correlation_id=correlation_id,
        event='success',
        extra={
            'dataset': 'market_hotspot',
            'record_count': len(response_payload['records']),
            'market': market,
        },
    )
    return _success_response(response_payload)


TOOL_ROUTE_HANDLERS: dict[str, ToolRouteHandler] = {
    'get_company_basic_info': _handle_company_basic_info_payload,
    'get_market_statistics': _handle_market_statistics_payload,
    'get_foreign_investment': _handle_foreign_investment_payload,
    'twse_stock_trend': _handle_twse_stock_trend_payload,
    'investment_screening': _handle_investment_screening_payload,
    'market_hotspot': _handle_market_hotspot_payload,
}


def _dispatch_tool_request(tool_name: str) -> tuple[Any, int]:
    payload = _extract_payload()
    correlation_id = str(payload.get('correlation_id', '')).strip() or 'unknown'
    _log_tool_dispatch(
        tool_name=tool_name,
        correlation_id=correlation_id,
        event='received',
    )
    handler = TOOL_ROUTE_HANDLERS.get(tool_name)
    if handler is None:
        return _error_response(
            status_code=404,
            error_code='UNKNOWN_TOOL',
            message=f'Unsupported tool route: {tool_name}',
            retryable=False,
            correlation_id=correlation_id,
            details={'tool_name': tool_name},
        )

    try:
        return handler(payload)
    except ValidationError as exc:
        return _error_response(
            status_code=400,
            error_code='INVALID_REQUEST',
            message=str(exc),
            retryable=False,
            correlation_id=correlation_id,
            details={'tool_name': tool_name},
        )
    except NotFoundError as exc:
        return _error_response(
            status_code=404,
            error_code='NOT_FOUND',
            message=str(exc),
            retryable=False,
            correlation_id=correlation_id,
            details={'tool_name': tool_name},
        )
    except Exception as exc:
        return _error_response(
            status_code=502,
            error_code='UPSTREAM_FAILURE',
            message=str(exc),
            retryable=True,
            correlation_id=correlation_id,
            details={'tool_name': tool_name},
        )


def _fetch_twse_snapshot(trade_date: str) -> pd.DataFrame:
    clean_date = trade_date.replace('-', '')
    response = requests.get(
        (
            'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
            f'?date={clean_date}&type=ALL&response=json'
        ),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('stat') != 'OK':
        return pd.DataFrame()

    target_table = next(
        (
            table
            for table in payload.get('tables', [])
            if '每日收盤行情' in table.get('title', '')
        ),
        None,
    )
    if target_table is None:
        return pd.DataFrame()

    frame = pd.DataFrame(target_table['data'], columns=target_table['fields'])
    rename_map = {
        '證券代號': 'stock_id',
        '證券名稱': 'stock_name',
        '開盤價': 'open_price',
        '最高價': 'high_price',
        '最低價': 'low_price',
        '收盤價': 'close_price',
        '成交股數': 'volume',
        '本益比': 'pe_ratio',
    }
    frame = frame.rename(columns=rename_map)
    available_columns = [
        column
        for column in rename_map.values()
        if column in frame.columns
    ]
    return frame[available_columns]


def _fetch_twse_flow(trade_date: str) -> pd.DataFrame:
    clean_date = trade_date.replace('-', '')
    response = requests.get(
        (
            'https://www.twse.com.tw/rwd/zh/fund/T86'
            f'?date={clean_date}&selectType=ALL&response=json'
        ),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('stat') != 'OK':
        return pd.DataFrame()

    frame = pd.DataFrame(payload['data'], columns=payload['fields'])
    foreign_column = next(
        (
            column
            for column in frame.columns
            if '外' in column and '買賣超股數' in column
        ),
        None,
    )
    trust_column = next(
        (
            column
            for column in frame.columns
            if '投信' in column and '買賣超股數' in column
        ),
        None,
    )
    dealer_column = next(
        (
            column
            for column in frame.columns
            if '自營商' in column
            and '買賣超' in column
            and '自行' not in column
            and '避險' not in column
        ),
        None,
    )
    if foreign_column is None or trust_column is None:
        return pd.DataFrame()

    selected_columns = ['證券代號', foreign_column, trust_column]
    selected_names = ['stock_id', 'foreign_buy', 'trust_buy']
    if dealer_column is not None:
        selected_columns.append(dealer_column)
        selected_names.append('dealer_buy')
    selected = frame[selected_columns].copy()
    selected.columns = selected_names
    return selected


def _fetch_tpex_snapshot(
    trade_date: str,
    include_etfs: bool,
) -> pd.DataFrame:
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    minguo_date = f'{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}'
    headers = HEADERS.copy()
    headers.update({
        'Referer': (
            'https://www.tpex.org.tw/web/stock/aftertrading/'
            f'daily_close_quotes/stk_quote_result.php?l=zh-tw&d={minguo_date}'
        ),
        'Origin': 'https://www.tpex.org.tw',
    })

    stock_response = requests.get(
        (
            'https://www.tpex.org.tw/web/stock/aftertrading/'
            'daily_close_quotes/stk_quote_result.php'
            f'?l=zh-tw&d={minguo_date}&o=json'
        ),
        headers=headers,
        timeout=20,
    )
    stock_response.raise_for_status()
    stock_payload = stock_response.json()
    stock_raw = stock_payload.get('aaData')
    if not stock_raw and stock_payload.get('tables'):
        for table in stock_payload['tables']:
            if isinstance(table, dict) and table.get('data'):
                stock_raw = table['data']
                break

    frames: list[pd.DataFrame] = []
    if stock_raw:
        stock_frame = pd.DataFrame(stock_raw)
        stock_frame = stock_frame.iloc[:, [0, 4, 5, 6, 2, 8]].copy()
        stock_frame.columns = [
            'stock_id',
            'open_price',
            'high_price',
            'low_price',
            'close_price',
            'volume',
        ]
        frames.append(stock_frame)

    if include_etfs:
        etf_response = requests.get(
            (
                'https://www.tpex.org.tw/web/etf/etf_daily_close_quotes/'
                'etf_quote_result.php'
                f'?l=zh-tw&d={minguo_date}&o=json'
            ),
            headers=headers,
            timeout=20,
        )
        etf_response.raise_for_status()
        try:
            etf_payload = etf_response.json()
        except ValueError:
            etf_payload = {}
        etf_raw = etf_payload.get('aaData')
        if not etf_raw and etf_payload.get('tables'):
            for table in etf_payload['tables']:
                if isinstance(table, dict) and table.get('data'):
                    etf_raw = table['data']
                    break
        if etf_raw:
            etf_frame = pd.DataFrame(etf_raw)
            etf_frame = etf_frame.iloc[:, [0, 4, 5, 6, 2, 7]].copy()
            etf_frame.columns = [
                'stock_id',
                'open_price',
                'high_price',
                'low_price',
                'close_price',
                'volume',
            ]
            frames.append(etf_frame)

    if not frames:
        return pd.DataFrame()

    snapshot_frame = pd.concat(frames, ignore_index=True)
    snapshot_frame['pe_ratio'] = 0.0
    return snapshot_frame


def _fetch_tpex_flow(trade_date: str) -> pd.DataFrame:
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    minguo_date = f'{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}'
    headers = HEADERS.copy()
    headers.update({
        'Referer': (
            'https://www.tpex.org.tw/web/stock/3insti/'
            'daily_trade/3itrade_hedge.php'
        ),
        'Origin': 'https://www.tpex.org.tw',
    })
    response = requests.get(
        (
            'https://www.tpex.org.tw/web/stock/3insti/daily_trade/'
            '3itrade_hedge_result.php'
            f'?l=zh-tw&se=AL&t=D&d={minguo_date}&o=json'
        ),
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    chips_raw = payload.get('aaData')
    if not chips_raw and payload.get('tables'):
        for table in payload['tables']:
            if isinstance(table, dict) and table.get('data'):
                chips_raw = table['data']
                break
    if not chips_raw:
        return pd.DataFrame()

    frame = pd.DataFrame(chips_raw)
    if frame.shape[1] >= 17:
        selected = frame.iloc[:, [0, 10, 13, 16]].copy()
        selected.columns = [
            'stock_id',
            'foreign_buy',
            'trust_buy',
            'dealer_buy',
        ]
        return selected
    if frame.shape[1] >= 14:
        selected = frame.iloc[:, [0, 10, 13]].copy()
        selected.columns = ['stock_id', 'foreign_buy', 'trust_buy']
        selected['dealer_buy'] = 0
        return selected
    return pd.DataFrame()


def fetch_stock_basic_snapshot(
    market: MarketCode,
    trade_date: str,
    include_etfs: bool,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if market in {'TWSE', 'ALL'}:
        frames.append(
            _prepare_snapshot_frame(
                _fetch_twse_snapshot(trade_date),
                trade_date,
                include_etfs,
            )
        )
    if market in {'TPEx', 'ALL'}:
        frames.append(
            _prepare_snapshot_frame(
                _fetch_tpex_snapshot(trade_date, include_etfs),
                trade_date,
                include_etfs,
            )
        )
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return _prepare_snapshot_frame(
            pd.DataFrame(),
            trade_date,
            include_etfs,
        )
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=['stock_id', 'trade_date']
    )


def fetch_foreign_investor_flow(
    market: MarketCode,
    trade_date: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if market in {'TWSE', 'ALL'}:
        frames.append(
            _prepare_flow_frame(
                _fetch_twse_flow(trade_date),
                trade_date,
            )
        )
    if market in {'TPEx', 'ALL'}:
        frames.append(
            _prepare_flow_frame(
                _fetch_tpex_flow(trade_date),
                trade_date,
            )
        )
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return _prepare_flow_frame(pd.DataFrame(), trade_date)
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=['stock_id', 'trade_date']
    )


def fetch_historical_financial_statements(
    year: int,
    quarter: int,
    market: MarketCode,
) -> pd.DataFrame:
    scraper = QuarterlyScraper()
    roc_year = year - 1911
    if market == 'TWSE':
        frame = scraper._fetch_data(roc_year, quarter, 'sii')
    elif market == 'TPEx':
        frame = scraper._fetch_data(roc_year, quarter, 'otc')
    else:
        frame = scraper.fetch_all_markets(roc_year, quarter)
    if frame is None or frame.empty:
        return pd.DataFrame()
    return frame[
        [
            'stock_id',
            'revenue',
            'rd_expense',
            'operating_expense',
            'operating_profit',
            'eps',
        ]
    ]


@app.get('/health')
def health() -> tuple[Any, int]:
    return jsonify({
        'status': 'ok',
        'service': 'twse_mcp_server',
        'version': '0.1.0',
        'checks': [
            {'component': 'http', 'status': 'ok'},
            {'component': 'transport', 'status': 'ok'},
        ],
    }), 200


@app.post('/v1/tools/<tool_name>')
def post_tool_route(tool_name: str) -> tuple[Any, int]:
    return _dispatch_tool_request(tool_name)


@app.post('/v1/stock-basic-snapshot')
def post_stock_basic_snapshot() -> tuple[Any, int]:
    try:
        return _handle_market_statistics_payload(_extract_payload())
    except ValidationError as exc:
        payload = request.get_json(silent=True) or {}
        return _error_response(
            status_code=400,
            error_code='INVALID_REQUEST',
            message=str(exc),
            retryable=False,
            correlation_id=str(payload.get('correlation_id', '')).strip() or 'unknown',
            details={'tool_name': 'stock_basic_snapshot'},
        )
    except Exception as exc:
        payload = request.get_json(silent=True) or {}
        return _error_response(
            status_code=502,
            error_code='UPSTREAM_FAILURE',
            message=str(exc),
            retryable=True,
            correlation_id=str(payload.get('correlation_id', '')).strip() or 'unknown',
            details={'dataset': 'stock_basic_snapshot'},
        )


@app.post('/v1/foreign-investor-flow')
def post_foreign_investor_flow() -> tuple[Any, int]:
    try:
        return _handle_foreign_investment_payload(_extract_payload())
    except ValidationError as exc:
        payload = request.get_json(silent=True) or {}
        return _error_response(
            status_code=400,
            error_code='INVALID_REQUEST',
            message=str(exc),
            retryable=False,
            correlation_id=str(payload.get('correlation_id', '')).strip() or 'unknown',
            details={'tool_name': 'foreign_investor_flow'},
        )
    except Exception as exc:
        payload = request.get_json(silent=True) or {}
        return _error_response(
            status_code=502,
            error_code='UPSTREAM_FAILURE',
            message=str(exc),
            retryable=True,
            correlation_id=str(payload.get('correlation_id', '')).strip() or 'unknown',
            details={'dataset': 'foreign_investor_flow'},
        )


@app.post('/v1/historical-financial-statements')
def post_historical_financial_statements() -> tuple[Any, int]:
    correlation_id = 'unknown'
    try:
        payload = request.get_json(silent=True) or {}
        correlation_id = _require_correlation_id(payload)
        market = _normalize_market(payload.get('market'))
        year = int(payload.get('year', 0))
        quarter = int(payload.get('quarter', 0))
        if year < 2000:
            raise ValidationError('year must be >= 2000')
        if quarter not in {1, 2, 3, 4}:
            raise ValidationError('quarter must be 1-4')
        frame = fetch_historical_financial_statements(year, quarter, market)
        return jsonify({
            'dataset': 'historical_financial_statements',
            'period': {'year': year, 'quarter': quarter},
            'unit': 'TWD',
            'records': _records_from_frame(frame),
            'meta': {'record_count': len(frame)},
        }), 200
    except ValidationError as exc:
        return _error_response(
            status_code=400,
            error_code='INVALID_REQUEST',
            message=str(exc),
            retryable=False,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        return _error_response(
            status_code=502,
            error_code='UPSTREAM_FAILURE',
            message=str(exc),
            retryable=True,
            correlation_id=correlation_id,
            details={'dataset': 'historical_financial_statements'},
        )


def main() -> int:
    port = int(os.getenv('MCP_PORT', '8080'))
    app.run(host='0.0.0.0', port=port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
