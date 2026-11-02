"""Offline, read-only lineage assessment and first-stage execution inventory."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.research_mode import D5_RESEARCH_ASSUMPTIONS_V5, D5_RESEARCH_STRATEGY, RESEARCH_ASSUMPTIONS_V6, assumptions_sha

DAY3_GRID = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
D5_GRID = ROOT / 'outputs/portfolio_research/d5_portfolio_research_20260814_v1/parameter_grid.csv'
G2_GRID = ROOT / 'outputs/dual_track_validation/2026-10-23/track_b_runs/g2g3/strategy_grid.csv'
LINEAGE = ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/publication_lineage_ledger.csv'
TRACK_A = ('defensive_low_volatility', 'hybrid_trend_rank', 'mean_reversion', 'institutional_flow_confirmation',
           'growth_momentum_breakout', 'quality_growth', 'quality_value_low_volatility')
G2_NAMES = ('G2_OPERATING_INCOME_YOY', 'G3_EPS_YOY', 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def build_inventory(day3_grid=DAY3_GRID, d5_grid=D5_GRID, g2_grid=G2_GRID, lineage=LINEAGE):
    day3, d5, g2 = rows(day3_grid), rows(d5_grid), rows(g2_grid)
    if len(day3) != 300 or len(d5) != 48 or len({r['factor_set'] for r in day3}) != 11:
        raise ValueError('ARCHIVED_GRID_COUNT_MISMATCH')
    records = rows(lineage)
    usable = [r for r in records if r['parse_status'] == 'PASS' and r['available_date'] and r['available_date'] <= '2025-12-31']
    keys = {(r['ticker'], int(r['roc_year']), int(r['quarter'])) for r in usable if r['statement_type'] == 'income_statement'}
    pairs = {(ticker, year, quarter) for ticker, year, quarter in keys if (ticker, year - 1, quarter) in keys}
    paired_tickers = {ticker for ticker, _, _ in pairs}
    min_top = min(int(r['top_n']) for r in g2)
    labels = {'data_label': 'RESEARCH_UNCERTIFIED', 'label': 'DIAGNOSTIC_ONLY', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6)}
    strategies = [{'strategy': name, 'family': 'DAY3', 'execution_disposition': 'PENDING', **labels}
                  for name in sorted({r['factor_set'] for r in day3})]
    strategies.append({'strategy': 'D5_PORTFOLIO_5_FACTORS', 'family': 'D5', 'status': 'BLOCKED_OOS_LEAKAGE',
                       'reason': 'Archived D4 candidate selection and weights include observations through 2026-07-28.', **labels})
    for name in G2_NAMES:
        status = 'RESEARCH_NOT_RUNNABLE' if len(paired_tickers) < min_top else 'COVERAGE_ASSESSED'
        strategies.append({'strategy': name, 'family': 'G2G3', 'status': status,
                           'reason': f'{len(paired_tickers)} tickers with verified same-quarter prior-year lineage; minimum Top-N is {min_top}.', **labels})
    strategies.extend({'strategy': name, 'family': 'TRACK_A', 'execution_disposition': 'DEFERRED_BY_SCOPE', **labels}
                      for name in TRACK_A)
    return {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
            'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6),
            'development_end': '2025-12-31', 'strategy_count': len(strategies),
            'day3_configuration_count': len(day3), 'original_d5_configuration_count': len(d5),
            'g2g3_configuration_count': len(g2), 'lineage_rows_total': len(records), 'lineage_rows_available_in_development': len(usable),
            'lineage_tickers_available_in_development': len({r['ticker'] for r in usable}),
            'yoy_pairs_available_in_development': len(pairs), 'yoy_tickers_available_in_development': len(paired_tickers),
            'source_sha256': {Path(path).name: sha(path) for path in (day3_grid, d5_grid, g2_grid, lineage)},
            'strategies': strategies, 'd5_variant': {'strategy': D5_RESEARCH_STRATEGY, 'execution_disposition': 'PENDING',
              'data_label': 'RESEARCH_UNCERTIFIED', 'label': 'DIAGNOSTIC_ONLY', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V5)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/inventory_20261102.json')
    args = parser.parse_args()
    result = build_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output.exists():
        if args.output.read_text(encoding='utf-8') != encoded:
            raise ValueError('EXISTING_INVENTORY_DIFFERS')
    else:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    print(f"DIAGNOSTIC_ONLY: {result['strategy_count']} strategies; {result['yoy_tickers_available_in_development']} YoY tickers")
