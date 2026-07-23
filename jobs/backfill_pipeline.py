"""Backfill missing market and recommendation dates in the pipeline."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
import logging
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db_helper import (  # noqa: E402
    get_completed_recommendation_strategy_days,
    get_db_engine,
    get_recommendation_dates,
    get_valid_market_dates,
    normalize_date_str,
)
from core.mcp_client import TWSEMCPClient as MCPClient  # noqa: E402
from core.strategy_manager import StrategyManager  # noqa: E402
from jobs.run_daily import run_daily_for_date  # noqa: E402
from jobs.update_database import prewarm_dashboard_aggregation_cache, update_market_date  # noqa: E402


DEFAULT_START_DATE = '2026-03-27'
TWSE_CALENDAR_SYMBOL = '^TWII'
_LOGGER = logging.getLogger(__name__)


class _YFinanceProxy:
    """Lazy proxy so test collection does not require yfinance."""

    def _load(self):
        try:
            import yfinance as module
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                'yfinance is required for Yahoo Finance backfill. '
                'Install yfinance or monkeypatch jobs.backfill_pipeline.yf.download in tests.'
            ) from exc
        return module

    def download(self, *args, **kwargs):
        return self._load().download(*args, **kwargs)


yf = _YFinanceProxy()


def _build_expected_weekdays(start_date: str, end_date: str) -> list[str]:
    dates = pd.date_range(start=start_date, end=end_date, freq='B')
    return [date.strftime('%Y-%m-%d') for date in dates]


def _is_exchange_trading_day(
    date_str: str,
    mcp_client: MCPClient | None = None,
) -> bool | None:
    """透過 MCP 驗證指定日期是否為實際交易日。"""
    client = mcp_client or MCPClient()
    try:
        payload = client.get_market_statistics_sync(date_str)
    except Exception as exc:
        _LOGGER.warning('無法透過 MCP 驗證交易日 %s: %s', date_str, exc)
        return None

    if payload is None:
        return None

    records = payload.get('records') if isinstance(payload, dict) else None
    if isinstance(records, list):
        return len(records) > 0
    return None


def _fetch_twse_calendar_dates(start_date: str, end_date: str) -> list[str]:
    """以 ^TWII 日線作為台股交易日曆來源。"""
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    if start_dt > end_dt:
        return []

    frame = yf.download(
        TWSE_CALENDAR_SYMBOL,
        start=start_dt.strftime('%Y-%m-%d'),
        end=(end_dt + timedelta(days=1)).strftime('%Y-%m-%d'),
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return []

    return sorted({pd.Timestamp(index_value).strftime('%Y-%m-%d') for index_value in frame.index})


def _resolve_expected_trading_dates(
    start_date: str,
    end_date: str,
    market_dates: list[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """建立應被視為有效市場交易日的日期集合。"""
    expected_weekdays = _build_expected_weekdays(start_date, end_date)
    market_date_set = set(market_dates)

    try:
        calendar_dates = _fetch_twse_calendar_dates(start_date, end_date)
    except Exception as exc:
        _LOGGER.warning('無法取得 ^TWII 交易日曆 %s ~ %s: %s', start_date, end_date, exc)
        calendar_dates = []

    if calendar_dates:
        calendar_date_set = set(calendar_dates)
        expected_trading_dates = sorted(calendar_date_set | market_date_set)
        excluded_non_trading_dates = [date for date in expected_weekdays if date not in calendar_date_set]
        return expected_weekdays, expected_trading_dates, excluded_non_trading_dates, []

    probe_dates = [date for date in expected_weekdays if date not in market_date_set]
    mcp_client = MCPClient() if probe_dates else None

    expected_trading_dates: list[str] = []
    excluded_non_trading_dates: list[str] = []
    unverified_dates: list[str] = []

    for date_str in expected_weekdays:
        if date_str in market_date_set:
            expected_trading_dates.append(date_str)
            continue

        is_trading_day = _is_exchange_trading_day(date_str, mcp_client=mcp_client)
        if is_trading_day is False:
            excluded_non_trading_dates.append(date_str)
            continue
        if is_trading_day is None:
            unverified_dates.append(date_str)

        expected_trading_dates.append(date_str)

    return expected_weekdays, expected_trading_dates, excluded_non_trading_dates, unverified_dates


def scan_pipeline_gaps(start_date: str, end_date: str | None = None) -> dict:
    """掃描市場表與推薦表缺口。"""
    resolved_start = normalize_date_str(start_date) or DEFAULT_START_DATE
    resolved_end = normalize_date_str(end_date) or datetime.now().strftime('%Y-%m-%d')

    market_dates = sorted(set(get_valid_market_dates(resolved_start, resolved_end)))
    recommendation_dates = sorted(
        set(get_recommendation_dates(resolved_start, resolved_end, include_heartbeats=True))
    )
    persistence_strategy_names = StrategyManager().get_persistence_strategy_names()
    completed_strategy_days = get_completed_recommendation_strategy_days(
        resolved_start,
        resolved_end,
        persistence_strategy_names,
    )
    expected_weekdays, expected_dates, excluded_non_trading_dates, unverified_dates = _resolve_expected_trading_dates(
        resolved_start,
        resolved_end,
        market_dates,
    )

    missing_market_dates = [date for date in expected_dates if date not in set(market_dates)]
    missing_recommendation_strategy_days = [
        {'date': date, 'strategy': strategy_name}
        for date in market_dates
        for strategy_name in persistence_strategy_names
        if (date, strategy_name) not in completed_strategy_days
    ]
    missing_recommendation_dates = sorted({item['date'] for item in missing_recommendation_strategy_days})

    return {
        'start_date': resolved_start,
        'end_date': resolved_end,
        'expected_weekdays': expected_weekdays,
        'expected_dates': expected_dates,
        'market_dates': market_dates,
        'recommendation_dates': recommendation_dates,
        'persistence_strategy_names': persistence_strategy_names,
        'completed_recommendation_strategy_days': sorted(completed_strategy_days),
        'excluded_non_trading_dates': excluded_non_trading_dates,
        'unverified_dates': unverified_dates,
        'missing_market_dates': missing_market_dates,
        'missing_recommendation_dates': missing_recommendation_dates,
        'missing_recommendation_strategy_days': missing_recommendation_strategy_days,
    }


def backfill_pipeline(
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    dry_run: bool = False,
    prewarm_stock_ids: list[str] | None = None,
) -> dict:
    """補齊市場表與推薦表缺口。"""
    engine = get_db_engine()
    initial_gaps = scan_pipeline_gaps(start_date, end_date)
    repaired_market_dates: list[str] = []
    rebuilt_recommendation_dates: list[str] = []

    print(
        f"🔎 掃描範圍: {initial_gaps['start_date']} ~ {initial_gaps['end_date']} | "
        f"市場缺口 {len(initial_gaps['missing_market_dates'])} 天 | "
        f"推薦缺口 {len(initial_gaps['missing_recommendation_dates'])} 天"
    )

    if dry_run:
        print(f"  市場缺口: {initial_gaps['missing_market_dates']}")
        print(f"  推薦缺口: {initial_gaps['missing_recommendation_dates']}")
        print(f"  推薦策略缺口: {initial_gaps['missing_recommendation_strategy_days']}")
        if initial_gaps['excluded_non_trading_dates']:
            print(f"  已排除休市日: {initial_gaps['excluded_non_trading_dates']}")
        if initial_gaps['unverified_dates']:
            print(f"  無法驗證的平日: {initial_gaps['unverified_dates']}")
        return {
            'dry_run': True,
            **initial_gaps,
            'repaired_market_dates': [],
            'rebuilt_recommendation_dates': [],
            'remaining_market_dates': initial_gaps['missing_market_dates'],
            'remaining_recommendation_dates': initial_gaps['missing_recommendation_dates'],
        }

    for date_str in initial_gaps['missing_market_dates']:
        updated_rows = update_market_date(engine, date_str)
        if updated_rows > 0:
            repaired_market_dates.append(date_str)

    post_market_gaps = scan_pipeline_gaps(start_date, end_date)
    missing_recommendation_dates = []
    for item in post_market_gaps['missing_recommendation_strategy_days']:
        date_str = item['date']
        if date_str in missing_recommendation_dates:
            continue
        missing_recommendation_dates.append(date_str)

    for date_str in missing_recommendation_dates:
        run_daily_for_date(date_str)
        rebuilt_recommendation_dates.append(date_str)

    prewarm_summaries: list[dict] = []
    candidate_prewarm_dates = sorted(set(repaired_market_dates + rebuilt_recommendation_dates))
    for date_str in candidate_prewarm_dates:
        prewarm_summaries.append(
            prewarm_dashboard_aggregation_cache(
                trade_date=date_str,
                tracked_stock_ids=prewarm_stock_ids,
            )
        )

    final_gaps = scan_pipeline_gaps(start_date, end_date)
    return {
        'dry_run': False,
        'start_date': final_gaps['start_date'],
        'end_date': final_gaps['end_date'],
        'initial_market_dates': initial_gaps['market_dates'],
        'initial_recommendation_dates': initial_gaps['recommendation_dates'],
        'repaired_market_dates': repaired_market_dates,
        'rebuilt_recommendation_dates': rebuilt_recommendation_dates,
        'prewarm_summaries': prewarm_summaries,
        'remaining_market_dates': final_gaps['missing_market_dates'],
        'remaining_recommendation_dates': final_gaps['missing_recommendation_dates'],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='補齊市場與推薦資料缺口。')
    parser.add_argument('--start-date', default=DEFAULT_START_DATE, help='缺口掃描起始日 (YYYY-MM-DD)。')
    parser.add_argument('--end-date', help='缺口掃描結束日 (YYYY-MM-DD)。')
    parser.add_argument('--dry-run', action='store_true', help='只顯示缺口，不執行補齊。')
    parser.add_argument('--prewarm-stock', action='append', default=[], help='指定補齊後要預熱的個股代號，可重複使用。')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = backfill_pipeline(
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=args.dry_run,
        prewarm_stock_ids=args.prewarm_stock,
    )
    print(summary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
