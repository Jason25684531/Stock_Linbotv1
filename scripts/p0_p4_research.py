"""P0 data repair evidence and P1 performance diagnostics for the 12 Day3/D5 strategies (2026-10-24-diagnose-day3-d5-and-gate-p0-p4).

Reads archived results only (no backtest rerun, no held-out OOS, no request, no MySQL); writes only into OUT.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/p0_p4/2026-10-24'
DT = ROOT / 'outputs/dual_track_validation/2026-10-23'
TWT49U = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd/TWT49U_20230103_20260728.json'
FACTOR_POOL = ROOT / 'outputs/factor_validation/factor_validation_20260910_v1/accepted_factor_pool.csv'
NEXT_ACTIONS = ROOT / 'outputs/post_archive_readiness/2026-10-24/strategy15_next_action_matrix.csv'
DAY3_D5 = ('DAY3', 'D5')


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # new evidence only
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def split_event(kind: str, pre_close: float, ex_ref: float, dividend_ref: float) -> dict:
    """TWT49U formulas: dividend reference = (pre close - cash) / (1 + stock ratio); the ex reference also carries a cash capital increase.
    One equation for cash and stock ratio: only pure cash or pure stock events are identified; a gap between the two references is a rights offering."""
    rights_offering = abs(ex_ref - dividend_ref) > 0.005
    if kind == '息':
        split = {'cash': round(pre_close - dividend_ref, 6), 'stock_ratio': 0.0}
    elif kind == '權':
        split = {'cash': 0.0, 'stock_ratio': round(pre_close / dividend_ref - 1, 6)}
    else:
        return {'cash': None, 'stock_ratio': None, 'rights_offering': rights_offering, 'status': 'NOT_IDENTIFIABLE: one equation, two unknowns (cash and stock ratio)'}
    return {**split, 'rights_offering': rights_offering,
            'status': 'SPLIT_PROVEN_RIGHTS_OFFERING_TERMS_UNKNOWN' if rights_offering else 'SPLIT_PROVEN'}


def p0():
    import pandas as pd
    from core.research.adjustment.normalization import parse_number
    payload = json.loads(TWT49U.read_text(encoding='utf-8'))
    rows = []
    for r in payload['data']:
        if not (r[1].isdigit() and len(r[1]) == 4):
            continue
        rows.append({'date': r[0], 'stock_id': r[1], 'kind': r[6], **split_event(r[6], parse_number(r[3]), parse_number(r[4]), parse_number(r[10]))})
    events = pd.DataFrame(rows)
    events.to_csv(out('p0_dividend_split_from_existing_twt49u.csv'), index=False, encoding='utf-8-sig')
    b = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    b = b[b.family.isin(DAY3_D5)]
    exposure = b.groupby('family_name').agg(configurations=('config_id', 'size'), configs_holding_mixed_events=('held_through_mixed_events', lambda s: int((s > 0).sum())),
                                            mixed_event_holdings_median=('held_through_mixed_events', 'median'), configs_on_nonofficial_prices=('held_on_nonofficial_prices', lambda s: int((s > 0).sum())),
                                            nonofficial_holdings_max=('held_on_nonofficial_prices', 'max'), unfillable_orders_median=('unfillable_orders', 'median'),
                                            delisted_holdings_max=('held_delisted_at_last_trade', 'max')).reset_index()
    exposure['DATA'] = 'DATA_BLOCKED'  # total return needs the cash/stock split of every held rights-and-dividend event (OD-P6)
    exposure['blockers'] = exposure.apply(lambda r: '; '.join(x for x in ('TOTAL_RETURN_BLOCKED (mixed events held, OD-P6)' if r.configs_holding_mixed_events else '',
                                                                           'NONOFFICIAL_VALUATION_PRICE (OD-P5)' if r.configs_on_nonofficial_prices else '') if x) or 'none', axis=1)
    exposure.loc[exposure.blockers == 'none', 'DATA'] = 'DATA_PASS'
    exposure.to_csv(out('p0_strategy_data_status.csv'), index=False, encoding='utf-8-sig')
    print(events.status.str[:15].value_counts().to_dict(), '\n', exposure[['family_name', 'configs_holding_mixed_events', 'configs_on_nonofficial_prices', 'DATA']].to_string(index=False))



def unsplit_events() -> 'pd.DataFrame':
    """Events the cached TWT49U cannot fully split: rights-and-dividend (cash/stock unknown) and rights offerings (subscription terms unknown)."""
    import pandas as pd
    from core.research.adjustment.normalization import parse_number
    rows = []
    for r in json.loads(TWT49U.read_text(encoding='utf-8'))['data']:
        if r[1].isdigit() and len(r[1]) == 4:
            split = split_event(r[6], parse_number(r[3]), parse_number(r[4]), parse_number(r[10]))
            if split['status'] != 'SPLIT_PROVEN':
                rows.append({'stock_id': r[1], 'ex_date': pd.Timestamp(r[11].split(',')[1]),
                             'kind': 'MIXED_NOT_IDENTIFIABLE' if split['cash'] is None else 'RIGHTS_OFFERING_TERMS_UNKNOWN'})
    return pd.DataFrame(rows)


def p0_exposure():
    """Review fix: held_through_mixed_events counted pure 權 events too, which P0 proved splittable. Count holdings across only the events that
    cannot be split, per configuration, from the archived orders with the same Holdings logic (track_b_evaluate)."""
    import pandas as pd
    from scripts.track_b_evaluate import Holdings
    events = unsplit_events()
    b = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    b = b[b.family.isin(DAY3_D5)]
    orders = pd.concat([pd.read_parquet(DT / f'orders_{f}.parquet') for f in ('day3', 'd5')])
    orders['asset'] = orders.asset.astype(str)
    rows = []
    for cfg in b.itertuples():
        held = Holdings(orders[orders.config_id == cfg.config_id])
        inside = events[(events.ex_date >= pd.Timestamp(cfg.start)) & (events.ex_date <= pd.Timestamp(cfg.end)) & events.stock_id.isin(held.by_asset)]
        hit = inside[[held.shares(r.stock_id, r.ex_date, strictly_before=True) > 0 for r in inside.itertuples()]]
        rows.append({'family_name': cfg.family_name, 'config_id': cfg.config_id, 'held_mixed_not_identifiable': int((hit.kind == 'MIXED_NOT_IDENTIFIABLE').sum()),
                     'held_rights_offering_terms_unknown': int((hit.kind == 'RIGHTS_OFFERING_TERMS_UNKNOWN').sum()),
                     'archived_held_through_mixed_events': cfg.held_through_mixed_events, 'held_on_nonofficial_prices': cfg.held_on_nonofficial_prices})
    detail = pd.DataFrame(rows)
    detail.to_csv(out('p0_exposure_unsplit_events_by_config.csv'), index=False, encoding='utf-8-sig')
    summary = detail.groupby('family_name').agg(configurations=('config_id', 'size'),
                                                configs_holding_mixed=('held_mixed_not_identifiable', lambda x: int((x > 0).sum())),
                                                configs_holding_rights_offering=('held_rights_offering_terms_unknown', lambda x: int((x > 0).sum())),
                                                configs_on_nonofficial_prices=('held_on_nonofficial_prices', lambda x: int((x > 0).sum()))).reset_index()
    blockers = lambda r: '; '.join(x for x in ('TOTAL_RETURN_BLOCKED: holds unidentifiable 權息 events (OD-P6)' if r.configs_holding_mixed else '',
                                               'TOTAL_RETURN_BLOCKED: holds rights offerings with unknown terms (OD-P6)' if r.configs_holding_rights_offering else '',
                                               'NONOFFICIAL_VALUATION_PRICE (OD-P5)' if r.configs_on_nonofficial_prices else '') if x) or 'none'
    summary['blockers'] = summary.apply(blockers, axis=1)
    summary['DATA'] = summary.blockers.map(lambda x: 'DATA_PASS' if x == 'none' else 'DATA_BLOCKED')
    summary.to_csv(out('p0_strategy_data_status_r2.csv'), index=False, encoding='utf-8-sig')  # r1 relied on held_through_mixed_events, which includes splittable 權
    print(summary.drop(columns='blockers').to_string(index=False))

def p1():
    import pandas as pd
    b = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    b = b[b.family.isin(DAY3_D5)].copy()
    regime = b.regime.map(json.loads)
    b['bull_excess'] = regime.map(lambda r: r['bull']['annualized_excess'])
    b['bear_excess'] = regime.map(lambda r: r['bear']['annualized_excess'])
    b['cost_drag'] = b.gross_total_return - b.total_return
    combo = pd.read_csv(DT / 'factor_combination_comparison.csv', encoding='utf-8-sig')
    ic = pd.read_csv(FACTOR_POOL).set_index('candidate_id')
    hypotheses = pd.read_csv(NEXT_ACTIONS, encoding='utf-8-sig').set_index('strategy')
    rows = []
    for name, g in b.groupby('family_name'):
        members = name.split('+') if name.startswith(('A1', 'RS_')) else []
        m = g.median(numeric_only=True)
        verdicts = combo[combo.family_name == name].verdict.value_counts().to_dict()
        reasons = []
        if m.bull_excess < 0 and m.bear_excess < 0:
            reasons.append(f'trails the TAIEX total-return index in both regimes: median annualised excess bull {m.bull_excess:+.1%}, bear {m.bear_excess:+.1%} (g5 fails)')
        elif m.bear_excess < 0:
            reasons.append(f'bear-market losses: median annualised excess bull {m.bull_excess:+.1%}, bear {m.bear_excess:+.1%} (bear ~84 sessions only)')
        if pd.isna(m.cost_drag):
            reasons.append('cost drag NOT_COMPUTABLE: gross_total_return is empty for this family in the archived results')
        elif m.cost_drag > 0.25 * abs(m.gross_total_return):
            reasons.append(f'costs: median gross-net {m.cost_drag:.1%} of capital at turnover {m.annual_turnover:.1f}x/yr')
        if m.top5_assets_profit_share >= 0.5 or m.best5pct_days_return_share >= 0.5:
            reasons.append(f'concentration: median top-5 profit share {m.top5_assets_profit_share:.0%}, best-5% days {m.best5pct_days_return_share:.0%}')
        reasons.append(f'no statistical edge after 743 trials: best bootstrap lower {g.bootstrap_lower.max():+.3f}, best DSR {g.deflated_sharpe.max():.3f}')
        rows.append({'strategy': name, 'family': g.family.iloc[0], 'configurations': len(g), 'window': f'{g.start.min()}..{g.end.max()}',
                     'factor_ic_60d': '; '.join(f"{f} IC {ic.loc[f, 'mean_ic']:.3f} ICIR {ic.loc[f, 'icir']:.2f}" for f in members if f in ic.index) or 'NOT_IN_FACTOR_POOL (D5 portfolio)',
                     'median_sharpe_net': round(m.sharpe_net, 3), 'median_sharpe_net_2x_cost': round(m.sharpe_net_2x_cost, 3), 'median_excess_sharpe': round(m.excess_sharpe, 3),
                     'configs_excess_sharpe_positive': int((g.excess_sharpe > 0).sum()), 'median_max_drawdown': round(m.max_drawdown, 3),
                     'median_annual_turnover': round(m.annual_turnover, 2), 'median_cost_drag': round(m.cost_drag, 3), 'median_gross_total_return': round(m.gross_total_return, 3),
                     'median_top5_profit_share': round(m.top5_assets_profit_share, 3), 'median_best5pct_days_share': round(m.best5pct_days_return_share, 3),
                     'median_bull_excess': round(m.bull_excess, 3), 'median_bear_excess': round(m.bear_excess, 3), 'median_over_5pct_value_share': round(m.over_5pct_value_share, 4),
                     'combination_vs_best_single': json.dumps(verdicts) if verdicts else 'SINGLE_FACTOR',
                     'random_baseline': 'NOT_IN_ARCHIVED_EVIDENCE (would need a new run; benchmark is TAIEX total return)',
                     'main_failure_reasons': ' | '.join(reasons), 'worth_improving': 'DIAGNOSTIC_ONLY: data incomplete (OD-P5/P6); never proven ineffective',
                     'improvement_hypothesis': hypotheses.loc[name, 'improvement_hypothesis'] if name in hypotheses.index else '',
                     'falsification_test': hypotheses.loc[name, 'falsification_test'] if name in hypotheses.index else ''})
    frame = pd.DataFrame(rows)
    frame.to_csv(out('p1_day3_d5_diagnostics_r2.csv'), index=False, encoding='utf-8-sig')  # r1 flagged regime losses only when bull excess was positive
    print(frame[['strategy', 'median_sharpe_net', 'median_excess_sharpe', 'median_cost_drag', 'median_bull_excess', 'median_bear_excess', 'median_top5_profit_share']].to_string(index=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('p0', 'p0-exposure', 'p1'))
    {'p0': p0, 'p0-exposure': p0_exposure, 'p1': p1}[parser.parse_args().command]()
