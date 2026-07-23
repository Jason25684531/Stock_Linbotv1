import pandas as pd
import pytest
optuna = pytest.importorskip('optuna')
from optuna.distributions import FloatDistribution, IntDistribution
from optuna.trial import create_trial

from jobs import optimize_params


def make_market_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'close_price': 100.0,
                'ma20': 95.0,
                'ma60': 90.0,
                'volume': 6_000_000,
                'rsi': 55.0,
            }
        ]
    )


def make_trial(value: float, params: dict[str, float | int]):
    return create_trial(
        value=value,
        params=params,
        distributions={
            'V30_RSI_LOW': IntDistribution(20, 50),
            'V30_RSI_HIGH': IntDistribution(60, 80),
            'V30_VOLUME_THRESHOLD': IntDistribution(2_000_000, 5_000_000, step=500_000),
            'V30_STOP_LOSS': FloatDistribution(0.05, 0.15, step=0.01),
            'V30_TAKE_PROFIT': FloatDistribution(0.10, 0.30, step=0.05),
        },
    )


def test_load_optimization_data_raises_when_columns_missing(monkeypatch):
    monkeypatch.setattr(
        optimize_params,
        'get_stock_data',
        lambda stock_id=None, date_str=None: (pd.DataFrame({'close_price': [100.0]}), '2026-04-15'),
    )

    with pytest.raises(RuntimeError, match='缺少必要欄位'):
        optimize_params.load_optimization_data()


def test_build_objective_uses_selected_backtest_window(monkeypatch):
    seen = {}

    def fake_backtest(params, start_date, end_date=None):
        seen['params'] = params
        seen['start_date'] = start_date
        seen['end_date'] = end_date
        return {'roi': 0.25, 'sharpe': 1.5, 'mdd': 0.0, 'trade_count': 4}

    monkeypatch.setattr(optimize_params, 'run_backtest_with_params', fake_backtest)

    objective = optimize_params.build_objective('roi', '2026-03-26', '2026-04-15')
    score = objective(
        optuna.trial.FixedTrial(
            {
                'V30_RSI_LOW': 30,
                'V30_RSI_HIGH': 70,
                'V30_VOLUME_THRESHOLD': 3_000_000,
                'V30_STOP_LOSS': 0.10,
                'V30_TAKE_PROFIT': 0.15,
            }
        )
    )

    assert score == 0.25
    assert seen['start_date'] == '2026-03-26'
    assert seen['end_date'] == '2026-04-15'
    assert seen['params']['V30_RSI_LOW'] == 30


def test_get_optimization_candidates_uses_v31_hybrid_strategy(monkeypatch):
    seen = {}

    class FakeV31Strategy:
        def filter_candidates(self, df):
            seen['rows'] = len(df)
            return pd.DataFrame({'stock_id': ['2330']})

    monkeypatch.setattr(optimize_params, 'V31HybridStrategy', FakeV31Strategy)

    candidates = optimize_params.get_optimization_candidates(make_market_df())

    assert seen['rows'] == 1
    assert list(candidates['stock_id']) == ['2330']


def test_get_candidate_universe_size_restores_config(monkeypatch):
    original_low = optimize_params.Config.V30_RSI_LOW
    original_high = optimize_params.Config.V30_RSI_HIGH
    original_volume = optimize_params.Config.V30_VOLUME_THRESHOLD

    monkeypatch.setattr(
        optimize_params,
        'get_optimization_candidates',
        lambda df: pd.DataFrame({'stock_id': ['2330', '2317']}),
    )

    count = optimize_params.get_candidate_universe_size(make_market_df())

    assert count == 2
    assert optimize_params.Config.V30_RSI_LOW == original_low
    assert optimize_params.Config.V30_RSI_HIGH == original_high
    assert optimize_params.Config.V30_VOLUME_THRESHOLD == original_volume


def test_resolve_optimization_date_looks_back_until_candidate_found(monkeypatch):
    monkeypatch.setattr(
        optimize_params,
        'get_recent_trade_dates',
        lambda anchor_date=None, lookback_days=30: ['2026-04-15', '2026-04-14', '2026-04-13'],
    )

    def fake_load(trade_date):
        return pd.DataFrame({'probe_date': [trade_date]}), trade_date

    def fake_candidates(df):
        probe_date = df.iloc[0]['probe_date']
        return {'2026-04-15': 0, '2026-04-14': 0, '2026-04-13': 3}[probe_date]

    monkeypatch.setattr(optimize_params, 'load_optimization_data', fake_load)
    monkeypatch.setattr(optimize_params, 'get_candidate_universe_size', fake_candidates)

    resolved = optimize_params.resolve_optimization_date(lookback_days=3)

    assert resolved['date'] == '2026-04-13'
    assert resolved['candidate_count'] == 3
    assert resolved['backtracked_days'] == 2


def test_run_backtest_with_params_uses_backtest_engine(monkeypatch):
    original_stop_loss = optimize_params.Config.V30_STOP_LOSS
    calls = {}

    class FakeBacktestEngine:
        def __init__(self, **kwargs):
            calls['init_kwargs'] = kwargs

        def run(self, return_metrics=False):
            calls['return_metrics'] = return_metrics
            return {
                'roi': 12.0,
                'sharpe_ratio': 1.23,
                'max_drawdown': 4.5,
                'trade_count': 7,
                'start_date': '2026-03-26',
                'end_date': '2026-04-15',
            }

    monkeypatch.setattr(optimize_params, 'BacktestEngine', FakeBacktestEngine)

    result = optimize_params.run_backtest_with_params(
        {
            'V30_RSI_LOW': 30,
            'V30_RSI_HIGH': 70,
            'V30_VOLUME_THRESHOLD': 3_000_000,
            'V30_STOP_LOSS': 0.10,
            'V30_TAKE_PROFIT': 0.15,
        },
        '2026-03-26',
        '2026-04-15',
    )

    assert calls['init_kwargs']['mode'] == 'v30'
    assert calls['init_kwargs']['start_date'] == '2026-03-26'
    assert calls['init_kwargs']['end_date'] == '2026-04-15'
    assert calls['init_kwargs']['persist_results'] is False
    assert calls['init_kwargs']['use_db_params'] is False
    assert calls['return_metrics'] is True
    assert result['roi'] == 0.12
    assert result['sharpe'] == 1.23
    assert result['mdd'] == -0.045
    assert result['trade_count'] == 7
    assert optimize_params.Config.V30_STOP_LOSS == original_stop_loss


def test_can_generate_param_importances_skips_constant_trials():
    study = optuna.create_study(direction='maximize')
    study.add_trial(
        make_trial(
            0.0,
            {
                'V30_RSI_LOW': 30,
                'V30_RSI_HIGH': 70,
                'V30_VOLUME_THRESHOLD': 3_000_000,
                'V30_STOP_LOSS': 0.10,
                'V30_TAKE_PROFIT': 0.15,
            },
        )
    )
    study.add_trial(
        make_trial(
            0.0,
            {
                'V30_RSI_LOW': 32,
                'V30_RSI_HIGH': 72,
                'V30_VOLUME_THRESHOLD': 3_500_000,
                'V30_STOP_LOSS': 0.11,
                'V30_TAKE_PROFIT': 0.20,
            },
        )
    )

    can_plot, reason = optimize_params.can_generate_param_importances(study)

    assert can_plot is False
    assert reason is not None
    assert '目標值相同' in reason