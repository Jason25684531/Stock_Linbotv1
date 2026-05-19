"""Lightweight scheduled backtest validation for the daily pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import Config
from core.db_helper import (
    MIN_VALID_MARKET_ROWS,
    get_db_engine,
    get_latest_trade_date,
    normalize_date_str,
    record_pipeline_step_finish,
    record_pipeline_step_start,
)
from jobs.run_backtest import BacktestEngine, get_registered_strategy_names

STEP_NAME = 'lightweight_backtest_validation'
SUCCESS_STATUSES = {'success', 'not_configured', 'skipped'}


@dataclass(frozen=True)
class DailyBacktestValidationConfig:
    enabled: bool
    window_days: int
    strategies: tuple[str, ...]
    universe: tuple[str, ...]
    initial_capital: float = 1_000_000.0


def _parse_csv_values(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return ()
    values = []
    seen = set()
    for item in str(raw_value).split(','):
        token = item.strip()
        if token and token not in seen:
            seen.add(token)
            values.append(token)
    return tuple(values)


def get_daily_backtest_validation_config() -> DailyBacktestValidationConfig:
    available = set(get_registered_strategy_names())
    requested_strategies = _parse_csv_values(Config.DAILY_BACKTEST_STRATEGIES)
    strategies = tuple(name for name in requested_strategies if name in available) or ('v34_turbo',)

    requested_universe = _parse_csv_values(Config.DAILY_BACKTEST_UNIVERSE)
    universe = requested_universe or (Config.MARKET_SYMBOL, '2317', '2454')

    return DailyBacktestValidationConfig(
        enabled=bool(Config.ENABLE_DAILY_BACKTEST_VALIDATION),
        window_days=max(20, min(int(Config.DAILY_BACKTEST_WINDOW_DAYS), 250)),
        strategies=strategies,
        universe=universe,
        initial_capital=float(Config.DAILY_BACKTEST_INITIAL_CAPITAL),
    )


def _resolve_validation_end_date(engine=None) -> str | None:
    latest_trade_date = get_latest_trade_date()
    return normalize_date_str(latest_trade_date)


def _resolve_validation_start_date(end_date: str, window_days: int, engine=None) -> str:
    engine = engine or get_db_engine()
    normalized_end_date = normalize_date_str(end_date)
    if not normalized_end_date:
        raise ValueError('validation end date is required')

    query = text(
        """
        SELECT trade_date
        FROM (
            SELECT trade_date, COUNT(*) AS row_count
            FROM daily_market_data
            WHERE trade_date <= :end_date
            GROUP BY trade_date
        ) market_dates
        WHERE row_count >= :min_rows
        ORDER BY trade_date DESC
        LIMIT :window_days
        """
    )
    params = {
        'end_date': normalized_end_date,
        'min_rows': MIN_VALID_MARKET_ROWS,
        'window_days': int(window_days),
    }

    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        raise ValueError('no market dates available for lightweight validation')

    normalized_dates = [normalize_date_str(row[0]) for row in rows]
    return normalized_dates[-1]


def _validate_market_data_window(start_date: str, end_date: str, universe: tuple[str, ...], engine=None) -> dict[str, object]:
    engine = engine or get_db_engine()
    anomaly_flags: list[str] = []
    universe_size = len(universe)
    missing_price_count = 0
    stale_price_count = 0
    alignment_issue_count = 0

    if not universe:
        return {
            'universe_size': 0,
            'missing_price_count': 0,
            'stale_price_count': 0,
            'alignment_issue_count': 0,
            'anomaly_flags': [],
        }

    placeholders = ', '.join(f':stock_id_{index}' for index, _ in enumerate(universe))
    query = text(
        f"""
        SELECT latest.stock_id, latest.trade_date, latest.close_price
        FROM daily_market_data latest
        INNER JOIN (
            SELECT stock_id, MAX(trade_date) AS trade_date
            FROM daily_market_data
            WHERE stock_id IN ({placeholders})
              AND trade_date BETWEEN :start_date AND :end_date
            GROUP BY stock_id
        ) anchor
            ON anchor.stock_id = latest.stock_id
           AND anchor.trade_date = latest.trade_date
        """
    )
    params = {
        'start_date': normalize_date_str(start_date),
        'end_date': normalize_date_str(end_date),
    }
    params.update({f'stock_id_{index}': stock_id for index, stock_id in enumerate(universe)})

    with engine.connect() as conn:
        rows = conn.execute(query, params).mappings().fetchall()

    latest_by_stock = {
        str(row.get('stock_id')).strip(): {
            'trade_date': normalize_date_str(row.get('trade_date')),
            'close_price': row.get('close_price'),
        }
        for row in rows
    }

    for stock_id in universe:
        row = latest_by_stock.get(stock_id)
        if row is None:
            missing_price_count += 1
            alignment_issue_count += 1
            anomaly_flags.append(f'missing_market_data:{stock_id}')
            continue

        close_price = row.get('close_price')
        trade_date = row.get('trade_date')
        if close_price is None or float(close_price) <= 0:
            missing_price_count += 1
            anomaly_flags.append(f'invalid_close_price:{stock_id}')

        if trade_date != normalize_date_str(end_date):
            stale_price_count += 1
            alignment_issue_count += 1
            anomaly_flags.append(f'stale_trade_date:{stock_id}:{trade_date}')

    return {
        'universe_size': universe_size,
        'missing_price_count': missing_price_count,
        'stale_price_count': stale_price_count,
        'alignment_issue_count': alignment_issue_count,
        'anomaly_flags': anomaly_flags,
    }


def _metric_is_invalid(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return math.isnan(value) or math.isinf(value)
    return False


def _run_strategy_backtest_validations(start_date: str, end_date: str, strategies: tuple[str, ...]) -> dict[str, object]:
    anomaly_flags: list[str] = []
    trades_evaluated = 0
    nan_result_count = 0
    impossible_return_count = 0

    for strategy_name in strategies:
        engine = BacktestEngine(
            mode=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=float(Config.DAILY_BACKTEST_INITIAL_CAPITAL),
            persist_results=False,
        )
        metrics = engine.run(return_metrics=True)

        trades_evaluated += int(metrics.get('trade_count') or 0)
        for metric_name in (
            'roi',
            'win_rate',
            'profit_ratio',
            'avg_hold_days',
            'max_drawdown',
            'sharpe_ratio',
            'final_value',
            'initial_capital',
            'date_count',
        ):
            if _metric_is_invalid(metrics.get(metric_name)):
                nan_result_count += 1
                anomaly_flags.append(f'invalid_metric:{strategy_name}:{metric_name}')

        roi = float(metrics.get('roi') or 0.0)
        win_rate = float(metrics.get('win_rate') or 0.0)
        if abs(roi) > 1000 or win_rate < 0 or win_rate > 100:
            impossible_return_count += 1
            anomaly_flags.append(f'impossible_return:{strategy_name}')

    return {
        'strategy_count': len(strategies),
        'trades_evaluated': trades_evaluated,
        'nan_result_count': nan_result_count,
        'impossible_return_count': impossible_return_count,
        'anomaly_flags': anomaly_flags,
    }


def _build_summary_text(summary: dict[str, object]) -> str:
    compact = json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
    return compact[:1000]


def run_daily_backtest_validation(
    *,
    pipeline_name: str = 'daily',
    run_date: str | None = None,
    engine=None,
) -> dict[str, object]:
    engine = engine or get_db_engine()
    normalized_run_date = normalize_date_str(run_date) or datetime.utcnow().strftime('%Y-%m-%d')

    record_pipeline_step_start(
        pipeline_name=pipeline_name,
        step_name=STEP_NAME,
        run_date=normalized_run_date,
        engine=engine,
    )

    config = get_daily_backtest_validation_config()
    if not config.enabled:
        summary_text = 'Daily lightweight backtest validation disabled by configuration'
        record_pipeline_step_finish(
            pipeline_name=pipeline_name,
            step_name=STEP_NAME,
            run_date=normalized_run_date,
            status='not_configured',
            error_summary=summary_text,
            engine=engine,
        )
        return {
            'status': 'not_configured',
            'status_code': 0,
            'summary': {
                'window_days': config.window_days,
                'strategy_count': len(config.strategies),
                'universe_size': len(config.universe),
            },
        }

    validation_end_date = None
    try:
        validation_end_date = _resolve_validation_end_date(engine=engine)
        if not validation_end_date:
            summary_text = 'No valid market trade date available for lightweight validation'
            record_pipeline_step_finish(
                pipeline_name=pipeline_name,
                step_name=STEP_NAME,
                run_date=normalized_run_date,
                status='skipped',
                error_summary=summary_text,
                engine=engine,
            )
            return {
                'status': 'skipped',
                'status_code': 0,
                'summary': {'reason': summary_text},
            }

        validation_start_date = _resolve_validation_start_date(
            validation_end_date,
            config.window_days,
            engine=engine,
        )
        market_summary = _validate_market_data_window(
            validation_start_date,
            validation_end_date,
            config.universe,
            engine=engine,
        )
        strategy_summary = _run_strategy_backtest_validations(
            validation_start_date,
            validation_end_date,
            config.strategies,
        )

        anomaly_flags = [
            *list(market_summary.get('anomaly_flags') or []),
            *list(strategy_summary.get('anomaly_flags') or []),
        ]
        status = 'success'
        if (
            market_summary.get('missing_price_count')
            or market_summary.get('stale_price_count')
            or market_summary.get('alignment_issue_count')
            or strategy_summary.get('nan_result_count')
            or strategy_summary.get('impossible_return_count')
        ):
            status = 'failed'

        summary = {
            'window_days': config.window_days,
            'validation_start_date': validation_start_date,
            'validation_end_date': validation_end_date,
            'strategies': list(config.strategies),
            'strategy_count': int(strategy_summary.get('strategy_count') or 0),
            'trades_evaluated': int(strategy_summary.get('trades_evaluated') or 0),
            'universe_size': int(market_summary.get('universe_size') or 0),
            'missing_price_count': int(market_summary.get('missing_price_count') or 0),
            'stale_price_count': int(market_summary.get('stale_price_count') or 0),
            'alignment_issue_count': int(market_summary.get('alignment_issue_count') or 0),
            'nan_result_count': int(strategy_summary.get('nan_result_count') or 0),
            'impossible_return_count': int(strategy_summary.get('impossible_return_count') or 0),
            'anomaly_flags': anomaly_flags[:10],
        }
        summary_text = _build_summary_text(summary)
        record_pipeline_step_finish(
            pipeline_name=pipeline_name,
            step_name=STEP_NAME,
            run_date=normalized_run_date,
            status=status,
            trade_date=validation_end_date,
            source_date=validation_end_date,
            error_summary=summary_text,
            engine=engine,
        )
        return {
            'status': status,
            'status_code': 0 if status in SUCCESS_STATUSES else 1,
            'summary': summary,
        }
    except Exception as exc:
        record_pipeline_step_finish(
            pipeline_name=pipeline_name,
            step_name=STEP_NAME,
            run_date=normalized_run_date,
            status='failed',
            trade_date=validation_end_date,
            source_date=validation_end_date,
            error_summary=str(exc),
            engine=engine,
        )
        return {
            'status': 'failed',
            'status_code': 1,
            'summary': {'error': str(exc)},
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Run lightweight daily backtest validation for the official scheduler path.',
    )
    parser.add_argument(
        '--pipeline-name',
        default='',
        help='Pipeline name to attribute in pipeline_runs.',
    )
    parser.add_argument(
        '--run-date',
        default='',
        help='Run date to attribute in pipeline_runs (YYYY-MM-DD).',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    pipeline_name = str(args.pipeline_name or '').strip() or str(os.environ.get('STOCK_PIPELINE_NAME') or 'daily').strip() or 'daily'
    run_date = str(args.run_date or '').strip() or str(os.environ.get('STOCK_PIPELINE_RUN_DATE') or '').strip() or None
    result = run_daily_backtest_validation(
        pipeline_name=pipeline_name,
        run_date=run_date,
    )
    print(json.dumps(result, ensure_ascii=False))
    return int(result['status_code'])


if __name__ == '__main__':
    raise SystemExit(main())
