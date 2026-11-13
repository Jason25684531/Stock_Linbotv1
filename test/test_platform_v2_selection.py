"""Smart selection on fake tables (no database)."""
import pandas as pd

from services import smart_selection as ss

RECS = pd.DataFrame([
    {'stock_id': '2330', 'trade_date': '2026-11-10', 'strategy': 'kd_oversold', 'close_price': 100.0, 'ai_score': 0.7, 'rsi': 30, 'volume': 1e6},
    {'stock_id': '2317', 'trade_date': '2026-11-10', 'strategy': 'kd_oversold', 'close_price': 50.0, 'ai_score': 0.8, 'rsi': 30, 'volume': 1e6},
    {'stock_id': '2330', 'trade_date': '2026-11-10', 'strategy': 'v31_hybrid', 'close_price': 100.0, 'ai_score': 0.6, 'rsi': 30, 'volume': 1e6},
    {'stock_id': '2330', 'trade_date': '2026-11-10', 'strategy': 'macd_cross', 'close_price': 100.0, 'ai_score': 0.5, 'rsi': 30, 'volume': 1e6},
    {'stock_id': '1101', 'trade_date': '2026-11-10', 'strategy': 'macd_cross', 'close_price': 30.0, 'ai_score': 0.4, 'rsi': 30, 'volume': 0},
    {'stock_id': 'NONE', 'trade_date': '2026-11-10', 'strategy': 'v33_low_vol', 'close_price': None, 'ai_score': None, 'rsi': None, 'volume': None},
])
MARKET = pd.DataFrame([
    {'stock_id': '2330', 'stock_name': 'TSMC', 'open_price': 99, 'high_price': 101, 'low_price': 98, 'close_price': 100.0, 'volume': 1e6},
    {'stock_id': '2317', 'stock_name': 'HonHai', 'open_price': 50, 'high_price': 50, 'low_price': 50, 'close_price': 50.0, 'volume': 1e6},
    {'stock_id': '1101', 'stock_name': 'TCC', 'open_price': 30, 'high_price': 30, 'low_price': 30, 'close_price': 30.0, 'volume': 0},
])
VALIDATION = {'kd_oversold': {'trusted_years': [2017, 2018], 'sample': 'ADEQUATE', 'data_trust': 'VERIFIED_TRACK', 'return_target': 'RETURN_TARGET_NOT_MET'},
              'macd_cross': {'trusted_years': [2017], 'sample': 'INSUFFICIENT', 'data_trust': 'VERIFIED_TRACK', 'return_target': 'RETURN_TARGET_NOT_MET'},
              'hybrid_trend_rank': {'trusted_years': [], 'sample': 'INSUFFICIENT', 'data_trust': 'PROVISIONAL_ONLY', 'return_target': 'X'}}
CARDS = {'kd_oversold': {'selection': {'text': 'kd < 20', 'status': 'SOURCED'}}}


def build(next_session=lambda day: ('2026-11-11', 'EXPECTED_OPEN'), authorized=(True, 'TEST')):
    return ss.build(RECS, MARKET, VALIDATION, CARDS, next_session, read_time='2026-11-11T00:00:00+00:00', authorization=authorized)


def test_rows_have_every_field_and_disclose_ranking():
    view = build()
    assert view['status'] == 'OK' and len(view['rows']) == 5  # heartbeat excluded
    row = next(r for r in view['rows'] if r['stock_id'] == '2317')
    for key in ('stock_name', 'signal_date', 'expected_session', 'strategy_id', 'conditions', 'rank', 'rank_basis', 'feasibility',
                'validation', 'risks', 'provenance'):
        assert key in row
    assert row['rank'] == 1 and 'ai_score' in row['rank_basis'] and 'MODEL_BLOCKED' in row['rank_basis']
    assert row['strategy_id'] == 'kd_oversold' and row['conditions']['selection']['text'] == 'kd < 20'
    assert view['evidence']['source_table'] == 'daily_recommendations'
    assert view['no_pick_strategies'] == ['defensive_low_volatility']  # heartbeat: ran, picked nothing


def test_feasibility_states():
    rows = {(r['stock_id'], r['strategy_id']): r['feasibility'] for r in build()['rows']}
    assert rows[('1101', 'macd_cross')]['status'] == 'NOT_TRADABLE' and rows[('1101', 'macd_cross')]['reason'] == 'NO_VOLUME'
    assert rows[('2317', 'kd_oversold')]['status'] == 'LIMITED' and rows[('2317', 'kd_oversold')]['reason'] == 'ONE_PRICE_SESSION'
    assert rows[('2330', 'kd_oversold')]['status'] == 'FEASIBLE'


def test_unknown_next_session():
    row = build(next_session=lambda day: (None, 'NEXT_SESSION_UNKNOWN'))['rows'][0]
    assert (row['expected_session']['date'], row['expected_session']['reason']) == (None, 'NEXT_SESSION_UNKNOWN')


def test_consensus_counts_are_separate():
    consensus = {c['stock_id']: c for c in build()['consensus']}
    assert consensus['2330']['trusted_data_signals'] == 1  # kd_oversold: VERIFIED track and ADEQUATE sample
    assert consensus['2330']['unvalidated_signals'] == 2  # v31_hybrid (no verified track), macd_cross (thin sample)
    assert 'total' not in consensus['2330']
    assert consensus['1101'] == {'stock_id': '1101', 'stock_name': 'TCC', 'trusted_data_signals': 0, 'unvalidated_signals': 1,
                                 'strategies': ['macd_cross']}


def test_two_unvalidated_strategies_give_zero_trusted_signals():
    recs = RECS[RECS.strategy.isin(['v31_hybrid', 'macd_cross']) & (RECS.stock_id == '2330')]
    view = ss.build(recs, MARKET, VALIDATION, CARDS, lambda d: (None, 'NEXT_SESSION_UNKNOWN'), '2026-11-11T00:00:00+00:00', (True, 'TEST'))
    assert view['consensus'][0]['trusted_data_signals'] == 0 and view['consensus'][0]['unvalidated_signals'] == 2


def test_unverified_authorization_fails_closed():
    view = build(authorized=(False, 'NO_EXISTING_ROUTE'))
    assert view['status'] == 'GOVERNANCE_BLOCKED' and view['rows'] == [] and view['consensus'] == []


def test_heartbeat_only_is_no_signals_not_missing_data():
    view = ss.build(RECS[RECS.stock_id == 'NONE'], MARKET, VALIDATION, CARDS, lambda d: (None, 'NEXT_SESSION_UNKNOWN'),
                    '2026-11-11T00:00:00+00:00', (True, 'TEST'))
    assert view['status'] == 'NO_SIGNALS'
    assert view['no_pick_strategies'] == ['defensive_low_volatility'] and not view['rows']


def test_research_code_never_imports_the_selection_service():
    from pathlib import Path
    root = Path(ss.__file__).resolve().parents[1]
    offenders = [p for folder in ('core/research', 'scripts') for p in (root / folder).rglob('*.py')
                 if 'smart_selection' in p.read_text(encoding='utf-8', errors='ignore')]
    assert offenders == []
