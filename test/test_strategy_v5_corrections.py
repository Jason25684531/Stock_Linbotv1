"""V5 execution-defect corrections (change 2026-11-17-strategy-execution-defect-corrections-v5): each correction is proven by a
counterexample the original rule gets wrong, the original rule is untouched outside the correction context, and the comparison
only runs registered cells and judges them by the registered rule."""
import numpy as np
import pandas as pd
import pytest

from config import Config
from core.strategies import _tquant
from core.strategies.defensive_low_volatility import DefensiveLowVolatilityStrategy
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.regression_momentum import RegressionMomentumStrategy
from scripts import strategy_v5_corrections as v5
from scripts import strategy_v5_rules as rules

ASOF = pd.Timestamp('2023-06-30')


def _trend_history(codes, window=252):
    """Exponential price paths: a higher code climbs faster, so the score order is the reverse of the code order."""
    dates = pd.bdate_range(end=ASOF, periods=window)
    rows = [pd.DataFrame({'stock_id': code, 'trade_date': dates, 'close_price': 50 * np.exp(np.arange(window) * 0.0005 * (i + 1)),
                          'high_price': np.nan, 'low_price': np.nan, 'volume': 1_000_000.0}) for i, code in enumerate(codes)]
    return pd.concat(rows, ignore_index=True)


def _today(codes):
    return pd.DataFrame({'stock_id': codes, 'trade_date': [ASOF] * len(codes)})


def test_regression_momentum_original_picks_lowest_codes_and_corrected_picks_highest_scores(monkeypatch):
    codes = [str(1101 + i) for i in range(25)]
    monkeypatch.setattr(_tquant, 'load_history', lambda asof: _trend_history(codes))
    strategy = RegressionMomentumStrategy()
    assert strategy.filter_candidates(_today(codes)).stock_id.head(3).tolist() == ['1106', '1107', '1108']
    with rules.applied('regression_momentum'):
        assert strategy.filter_candidates(_today(codes)).stock_id.head(3).tolist() == ['1125', '1124', '1123']


def _mean_reversion_rows(widths):
    return pd.DataFrame({'stock_id': [str(2301 + i) for i in range(len(widths))], 'trade_date': ASOF, 'close_price': 100.0, 'ma20': 100.0,
                         'ma60': 90.0, 'volume': 1_000_000, 'volume_ratio': Config.V37_VOLUME_RATIO_MAX / 2, 'kd_k': Config.V37_KD_LOW - 5,
                         'bb_width': widths, 'bias': (Config.V37_BIAS_LOW + Config.V37_BIAS_HIGH) / 2, 'rsi': Config.V37_RSI_LOW + 5,
                         'std_20': 1.0, 'natr': 2.0, 'macd_hist': 0.0, 'atr': 1.0})


def test_mean_reversion_original_band_limit_admits_a_wide_band_and_corrected_excludes_it(monkeypatch):
    monkeypatch.setattr(MeanReversionStrategy, '_check_market_filter', lambda self, *a, **k: True)
    strategy = MeanReversionStrategy()
    rows = _mean_reversion_rows([0.40, 0.05])
    assert strategy.filter_candidates(rows.copy()).bb_width.tolist() == [0.40, 0.05]
    with rules.applied('mean_reversion'):
        assert strategy.filter_candidates(rows.copy()).bb_width.tolist() == [0.05]


def _defensive_rows():
    """2301 is a 10 TWD stock moving 5% a day; 2302 is a 500 TWD stock moving 0.4% a day."""
    return pd.DataFrame({'stock_id': ['2301', '2302'], 'trade_date': ASOF, 'close_price': [10.0, 500.0], 'ma20': [9.5, 480.0],
                         'ma60': [9.0, 450.0], 'volume': 10_000_000, 'volume_ratio': 1.5, 'natr': 2.0, 'rsi': 55.0,
                         'std_20': [0.5, 2.0], 'macd_hist': 0.1, 'bias': 1.0})


def test_defensive_original_ranks_the_cheap_volatile_stock_first_and_corrected_ranks_the_calm_one_first(monkeypatch):
    monkeypatch.setattr(DefensiveLowVolatilityStrategy, '_check_market_filter', lambda self, *a, **k: True)
    monkeypatch.setattr(DefensiveLowVolatilityStrategy, '_filter_real_stocks', lambda self, df: df)
    strategy = DefensiveLowVolatilityStrategy()
    assert strategy.filter_candidates(_defensive_rows()).stock_id.tolist() == ['2301', '2302']
    with rules.applied('defensive_low_volatility'):
        assert strategy.filter_candidates(_defensive_rows()).stock_id.tolist() == ['2302', '2301']


def test_defensive_correction_keeps_empty_results_and_puts_an_unpriced_stock_last(monkeypatch):
    monkeypatch.setattr(DefensiveLowVolatilityStrategy, '_check_market_filter', lambda self, *a, **k: True)
    monkeypatch.setattr(DefensiveLowVolatilityStrategy, '_filter_real_stocks', lambda self, df: df)
    strategy = DefensiveLowVolatilityStrategy()
    with rules.applied('defensive_low_volatility'):
        assert strategy.filter_candidates(pd.DataFrame()).empty
    unpriced = _defensive_rows().assign(close_price=[0.0, 500.0])
    assert rules.order_by_relative_volatility(unpriced).stock_id.tolist() == ['2302', '2301']
    assert rules.order_by_relative_volatility(unpriced.iloc[:0]).empty


def _hooks():
    return {cls: (cls.__dict__.get('select_history'), cls.__dict__.get('filter_candidates')) for cls in rules.STRATEGY_CLASSES.values()}


def test_every_correction_changes_its_hook_inside_the_context_and_restores_it_on_exit():
    from scripts.strategy_v4_ranking import ranked_filter, ranked_history
    before, width = _hooks(), Config.V37_BB_WIDTH_MAX
    for sid, cls in rules.STRATEGY_CLASSES.items():
        with rules.applied(sid):
            inside = (cls.__dict__.get('select_history'), cls.__dict__.get('filter_candidates'))
            assert inside != before[cls] or Config.V37_BB_WIDTH_MAX == rules.BB_WIDTH_MAX_RATIO, sid
            if sid in ('mad_distance', 'volatility_adjusted_momentum'):
                assert inside == (ranked_history, ranked_filter)
    assert _hooks() == before and Config.V37_BB_WIDTH_MAX == width


def test_the_correction_table_names_a_documented_defect_for_each_corrected_strategy():
    assert set(rules.CORRECTIONS) == {'mad_distance', 'volatility_adjusted_momentum', 'regression_momentum', 'mean_reversion', 'defensive_low_volatility'}
    assert all(entry['defect'] and entry['hook'] for entry in rules.CORRECTIONS.values())
    with pytest.raises(KeyError):
        with rules.applied('contrarian_pullback'):
            pass


def _summary(stress, trades=40, cagr=0.05):
    return {'cagr': {'value': cagr}, 'cagr_stress': {'value': stress}, 'closed_trades': trades}


@pytest.mark.parametrize('base,treated,status', [
    (_summary(-0.10), _summary(0.02), 'CORRECTED_BETTER'),
    (_summary(-0.10), _summary(-0.05), 'CORRECTED_NOT_BETTER'),
    (_summary(-0.10, cagr=0.02), _summary(-0.36, cagr=0.05), 'CORRECTED_NOT_BETTER'),
    (_summary(0.03), _summary(0.03), 'CORRECTED_NOT_BETTER'),
    (_summary(-0.10), _summary(0.02, trades=29), 'NOT_EVALUABLE'),
    (_summary(None), _summary(0.02), 'NOT_EVALUABLE'),
    (None, _summary(0.02), 'NOT_EVALUABLE'),
])
def test_verdict_uses_the_double_cost_return_and_a_minimum_of_30_trades(base, treated, status):
    result = v5.verdict(base, treated)
    assert result['status'] == status and (result['reason'] is None) == (status != 'NOT_EVALUABLE')


def test_the_runner_refuses_a_cell_that_is_not_registered_or_a_register_that_changed(tmp_path):
    cell = {'strategy_id': 'mad_distance', 'rules': 'corrected', 'lot_rule': 'LOT_ODD'}
    register = v5.write_register(tmp_path / 'register.json', [cell], snapshot_id='HS1-x', verdict_rule='r')
    assert v5.require_registered(tmp_path / 'register.json', cell) == register['cells'][0]
    with pytest.raises(ValueError, match='UNREGISTERED_CELL'):
        v5.require_registered(tmp_path / 'register.json', cell | {'lot_rule': 'LOT_BOARD_1000'})
    (tmp_path / 'register.json').write_text((tmp_path / 'register.json').read_text(encoding='utf-8').replace('HS1-x', 'HS1-y'), encoding='utf-8')
    with pytest.raises(ValueError, match='REGISTER_CHANGED'):
        v5.require_registered(tmp_path / 'register.json', cell)


def test_a_missing_or_failed_cell_gives_not_evaluable_and_never_drops_the_effect():
    keys = {('original', 'LOT_ENGINE_V1'), ('original', 'LOT_ODD'), ('corrected', 'LOT_ENGINE_V1'), ('corrected', 'LOT_ODD')}
    numbers = {('original', 'LOT_ENGINE_V1'): _summary(-0.1), ('original', 'LOT_ODD'): _summary(0.02), ('corrected', 'LOT_ODD'): _summary(0.05)}
    result = v5.effect_verdicts(numbers, keys)
    assert result['budget']['status'] == 'CORRECTED_BETTER' and result['joint']['status'] == 'CORRECTED_BETTER'
    assert result['rule']['status'] == 'NOT_EVALUABLE'
    assert set(v5.effect_verdicts({}, {('original', 'LOT_ENGINE_V1'), ('original', 'LOT_ODD')})) == {'budget'}


def test_the_common_span_needs_every_registered_cell_and_every_cell_verified():
    full = {y: 'VERIFIED' for y in range(2017, 2024)}
    years = list(range(2015, 2026))
    assert v5.common_span([full, full], 2, years) == (2017, 2023)
    assert v5.common_span([full, full | {2019: 'PARTIAL'}], 2, years) == (2020, 2023)
    assert v5.common_span([full], 2, years) is None


def test_a_cell_counts_as_done_only_when_its_stored_identity_is_the_registered_one(tmp_path, monkeypatch):
    monkeypatch.setattr(v5, 'OUT', tmp_path)
    cell = {'strategy_id': 'mad_distance', 'rules': 'corrected', 'lot_rule': 'LOT_ODD', 'expected_identity': 'RID5-new'}
    assert not v5.is_done(cell)
    v5.write(tmp_path / 'cells' / f'{v5.cell_name(cell)}.json', {'run_identity': 'RID5-old'})
    assert not v5.is_done(cell)
    v5.write(tmp_path / 'cells' / f'{v5.cell_name(cell)}.json', {'run_identity': 'RID5-new'})
    assert v5.is_done(cell)


def test_the_report_shows_undefined_values_as_a_dash_and_labels_odd_lot_figures_as_diagnostic():
    numbers = {'window': [2017, 2023], 'label': v5.ODD_LOT_NOTE, 'cagr': {'value': 0.0534}, 'cagr_stress': {'value': None, 'reason': 'X'},
               'max_drawdown': {'value': -0.31}, 'annual_turnover': {'value': 12.0}, 'closed_trades': 41, 'benchmark_total_return': {'value': 1.2}}
    row = {'strategy_id': 'mad_distance', 'common_verified_span': [2017, 2023], 'cell_status': {'original/LOT_ODD': 'PUBLISHABLE', 'corrected/LOT_ODD': 'MISSING'},
           'cells': {'original/LOT_ODD': numbers, 'corrected/LOT_ODD': None}, 'verdicts': {'budget': {'status': 'NOT_EVALUABLE', 'reason': 'UNDEFINED_DOUBLE_COST_RETURN'}},
           'verdicts_2021_2023': {}}
    rep = {'labels': v5.LABELS, 'generated_at': '2026-11-17T00:00:00', 'snapshot_id': 'HS1-x', 'register_sha256': '0' * 64, 'cells_registered': 2,
           'cells_publishable': 1, 'replay_check': {'status': 'MATCH', 'trades_equal': True, 'nav_equal': True},
           'governance': {'status': 'MATCH', 'v5_exception': None}, 'odd_lot_note': v5.ODD_LOT_NOTE, 'strategies': [row]}
    text = v5.markdown(rep)
    assert '| 原版・零股 | +5.3% | — | -31.0% | 12.0 倍 | 41 | +120.0% | 診斷 |' in text
    assert '| 修正・零股 | — | — | — | — | — | — | MISSING |' in text and '無法判定（UNDEFINED_DOUBLE_COST_RETURN）' in text


def test_the_replay_check_flags_a_changed_price_on_the_double_cost_track(monkeypatch):
    from core.backtest import run_store
    nav = pd.DataFrame({'date': ['2023-01-03', '2023-01-04'], 'nav_base': [1.0, 1.01], 'nav_stress': [1.0, 1.0]})
    trades = pd.DataFrame({'stock_id': ['2330'], 'buy_date': ['2023-01-03'], 'sell_date': ['2023-01-04'], 'shares': [100], 'buy_price': [500.0], 'sell_price': [505.0]})
    frames = {'stored': {'nav': nav, 'trades': trades, 'trades_stress': trades},
              'fresh': {'nav': nav, 'trades': trades, 'trades_stress': trades.assign(sell_price=[504.0])}}
    monkeypatch.setattr(run_store, 'read_frame', lambda loaded, name: frames[loaded['tag']][name])
    run = lambda tag: {'tag': tag, 'run': {'run_id': tag}}
    assert v5._replay_check(run('stored'), run('stored'))['status'] == 'MATCH'
    result = v5._replay_check(run('stored'), run('fresh'))
    assert result['status'] == 'DIFFERENT' and result['nav_equal'] and not result['trades_equal']


def test_cells_cover_three_effects_for_corrected_strategies_and_the_budget_effect_for_all():
    cells = v5.matrix(['mad_distance', 'kd_oversold'], {'mad_distance', 'mean_reversion'})
    keys = {(c['strategy_id'], c['rules'], c['lot_rule']) for c in cells}
    assert keys == {('mad_distance', 'original', 'LOT_ENGINE_V1'), ('mad_distance', 'original', 'LOT_ODD'),
                    ('mad_distance', 'corrected', 'LOT_ENGINE_V1'), ('mad_distance', 'corrected', 'LOT_ODD'),
                    ('kd_oversold', 'original', 'LOT_ENGINE_V1'), ('kd_oversold', 'original', 'LOT_ODD')}
    assert [e for e, _, _ in v5.EFFECTS] == ['budget', 'rule', 'joint']
