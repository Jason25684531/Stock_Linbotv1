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

from core.crawlers.quarterly_scraper import QuarterlyScraper  # noqa: E402

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


TOOL_ROUTE_HANDLERS: dict[str, ToolRouteHandler] = {
    'get_company_basic_info': _handle_company_basic_info_payload,
    'get_market_statistics': _handle_market_statistics_payload,
    'get_foreign_investment': _handle_foreign_investment_payload,
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
