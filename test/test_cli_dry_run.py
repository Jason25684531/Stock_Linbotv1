from __future__ import annotations

from types import SimpleNamespace

import pandas as pd


def test_scheduler_accepts_dry_run_and_keeps_canonical_launchers():
    from jobs import scheduler

    args = scheduler.build_parser().parse_args(['evening', '--stop-on-error', '--dry-run'])

    assert args.dry_run is True
    assert scheduler.JOBS['run_daily'].script_path == 'jobs/run_daily.py'
    assert scheduler.JOBS['push_to_line'].script_path == 'jobs/push_to_line.py'
    assert '1_update_database.py' not in {job.script_path for job in scheduler.JOBS.values()}
    assert '2_rundaily.py' not in {job.script_path for job in scheduler.JOBS.values()}


def test_scheduler_dry_run_does_not_run_unsupported_destructive_steps(monkeypatch, capsys):
    from jobs import scheduler

    commands = []

    def fake_run(command, cwd=None, check=False, env=None):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(scheduler.subprocess, 'run', fake_run)

    exit_code = scheduler.run_pipeline('evening', stop_on_error=True, dry_run=True)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '[DRY-RUN]' in output
    assert 'skipped/preview-only' in output
    assert all('1_update_database.py' not in ' '.join(command) for command in commands)
    assert all('2_rundaily.py' not in ' '.join(command) for command in commands)
    assert not any('update_database.py' in ' '.join(command) for command in commands)
    assert any('run_daily.py' in ' '.join(command) and '--dry-run' in command for command in commands)
    assert any('push_to_line.py' in ' '.join(command) and '--dry-run' in command for command in commands)


def test_run_daily_accepts_dry_run_and_skips_persistence():
    from jobs import run_daily

    args = run_daily.build_parser().parse_args(['--dry-run'])

    class _FailingEngine:
        def connect(self):
            raise AssertionError('dry-run must not connect for persistence')

    count, heartbeat = run_daily._persist_strategy_recommendations(
        pd.DataFrame([{'stock_id': '2330', 'close_price': 900.0, 'ai_score': 0.8}]),
        'v33_low_vol',
        '2026-05-28',
        _FailingEngine(),
        dry_run=True,
    )

    assert args.dry_run is True
    assert count == 1
    assert heartbeat is False


def test_run_daily_dry_run_records_strategy_error_without_stopping(monkeypatch):
    from jobs import run_daily

    class _BadStrategy:
        name = 'bad_strategy'
        display_name = 'Bad'
        target_return = 10
        look_ahead_days = 5

        def filter_candidates(self, _df):
            raise RuntimeError('strategy exploded')

    class _GoodStrategy:
        name = 'v33_low_vol'
        display_name = 'V33'
        target_return = 10
        look_ahead_days = 5

        def filter_candidates(self, _df):
            return pd.DataFrame([{'stock_id': '2330', 'close_price': 900.0, 'ai_score': 0.8}])

    class _Manager:
        def get_persistence_strategies(self):
            return [_BadStrategy(), _GoodStrategy()]

        def get_persistence_strategy_names(self):
            return ['bad_strategy', 'v33_low_vol']

        def get_active_strategy_names(self):
            return ['v33_low_vol']

    monkeypatch.setattr(run_daily, 'StrategyManager', _Manager)
    monkeypatch.setattr(run_daily, 'get_db_engine', lambda: object())
    monkeypatch.setattr(run_daily, 'get_latest_trade_date', lambda: '2026-05-28')
    monkeypatch.setattr(
        run_daily,
        'compute_indicators_from_history',
        lambda date_str, engine, dry_run=False: pd.DataFrame([
            {
                'stock_id': '2330',
                'trade_date': pd.Timestamp('2026-05-28'),
                'close_price': 900.0,
            }
        ]),
    )
    monkeypatch.setattr(run_daily, 'calculate_ratio_features', lambda df: df)
    monkeypatch.setattr(run_daily, 'merge_financial_data', lambda df, engine: df.assign(op_profit_margin=0.1, eps=1.2))
    monkeypatch.setattr(run_daily, 'merge_revenue_data', lambda df, engine, dry_run=False: df.assign(revenue_yoy=12.3))
    monkeypatch.setattr(run_daily.Config, 'NEWS_BOOST_ENABLED', False)
    monkeypatch.setattr(run_daily, 'load_strategy_model', lambda strategy_name: (None, None))

    summary = run_daily.run_daily_for_date('2026-05-28', dry_run=True)

    assert summary['dry_run'] is True
    assert summary['preview_recommendation_count'] == 1
    assert summary['skipped_persistence'] is True
    assert summary['strategy_errors'] == {'bad_strategy': 'strategy exploded'}
    assert summary['strategy_counts']['v33_low_vol'] == 1


def test_push_to_line_accepts_dry_run_and_main_skips_status_and_api(monkeypatch, capsys):
    from jobs import push_to_line

    args = push_to_line.build_parser().parse_args(['--time', 'evening', '--dry-run'])

    monkeypatch.setattr(push_to_line, 'record_pipeline_step_start', lambda **kwargs: (_ for _ in ()).throw(AssertionError('no status writes')))
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_finish', lambda **kwargs: (_ for _ in ()).throw(AssertionError('no status writes')))
    monkeypatch.setattr(push_to_line.Config, 'LINE_CHANNEL_ACCESS_TOKEN', None, raising=False)
    monkeypatch.setattr(
        push_to_line,
        'run_evening',
        lambda dry_run=False: {
            'target_time': 'evening',
            'message_type': 'flex',
            'recommendation_count': 2,
            'would_push': False,
        },
    )

    exit_code = push_to_line.main(['--time', 'evening', '--dry-run'])

    output = capsys.readouterr().out
    assert args.dry_run is True
    assert exit_code == 0
    assert '[DRY-RUN]' in output
    assert 'would_push: false' in output


def test_push_to_line_dry_run_broadcast_does_not_call_line_api(monkeypatch, capsys):
    from jobs import push_to_line

    monkeypatch.setattr(push_to_line, 'ApiClient', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('no API')))

    push_to_line._broadcast_flex(object(), dry_run=True)

    assert '[DRY-RUN] LINE push skipped' in capsys.readouterr().out


def test_run_backtest_accepts_dry_run_and_no_persist():
    from jobs import run_backtest

    args = run_backtest.parse_cli_args([
        '--portfolio',
        '--strategies',
        'v33_low_vol,v35_innovation',
        '--dry-run',
    ])
    no_persist_args = run_backtest.parse_cli_args(['--v35', '--no-persist'])

    assert args.dry_run is True
    assert no_persist_args.no_persist is True


def test_run_backtest_dry_run_skips_persistence(monkeypatch, capsys):
    from jobs import run_backtest

    created = {}

    def fake_plan(_args):
        return {
            'strategy_names': ['v33_low_vol', 'v35_innovation'],
            'run_portfolio': True,
            'weights': None,
            'start_date': '2026-05-01',
            'end_date': '2026-05-28',
            'initial_capital': 1000000,
            'strategy_filter_mode': None,
            'persist_results': False,
            'dry_run': True,
        }

    class _FakePortfolioBacktestEngine:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def run_portfolio_backtest(self):
            return {'equity_curve': pd.DataFrame(), 'trades': [], 'metrics': {}}

    monkeypatch.setattr(run_backtest, 'resolve_backtest_plan', fake_plan)
    monkeypatch.setattr(run_backtest, 'PortfolioBacktestEngine', _FakePortfolioBacktestEngine)
    monkeypatch.setattr(run_backtest, 'save_backtest_results', lambda **kwargs: (_ for _ in ()).throw(AssertionError('no DB writes')))

    exit_code = run_backtest.main([
        '--portfolio',
        '--strategies',
        'v33_low_vol,v35_innovation',
        '--dry-run',
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert created['persist_results'] is False
    assert '[DRY-RUN] backtest persistence skipped' in output
