"""22-strategy status matrix and Track A readiness, assembled from the stage outputs (no new computation).

Four independent axes: ENGINE (code correct), DATA (inputs certified, per dataset), BACKTEST (DEVELOPMENT / DIAGNOSTIC / BLOCKED), EVIDENCE.
Nothing here is inferred from performance.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import read, write
from scripts.track_b_validation import OUT

NATIVE7 = ROOT / 'outputs/native7_recovery/2026-10-22'
NATIVE22 = ROOT / 'outputs/native22_validation/2026-10-22'
MODEL_VALIDATION = ROOT / 'outputs/model_data_validation/2026-10-23'
NATIVE = ('defensive_low_volatility', 'growth_momentum_breakout', 'hybrid_trend_rank', 'institutional_flow_confirmation', 'mean_reversion', 'quality_growth',
          'quality_value_low_volatility')
INPUT_GATES = {  # frame input -> data gate that certifies it
    'prices': 'OFFICIAL_CALENDAR_AND_PRICES', 'flows': 'OFFICIAL_T86_FULL_RANGE', 'margin': 'OFFICIAL_MI_MARGN_FULL_RANGE',
    'revenue': 'MONTHLY_REVENUE_TIMING', 'financials': 'FUNDAMENTALS_VALUES'}
MISSING_DATA_GATES = {'MONTHLY_REVENUE_TIMING'}  # no data at all (not merely unverified): the backtest cannot be run
UNBLOCK = {
    'TOTAL_RETURN_BLOCKED': 'split rights-and-dividend events into cash and shares with payment dates (MOPS dividend details)',
    'statistical_gates': 'no configuration passes the bootstrap and deflated-Sharpe gates; only a new independent sample can change this',
    'fundamentals_sample_only': 'independent re-verification of the PIT financial values (re-parse of the local MOPS cache, then a second source)',
    'execution_audit_material': 'a fill simulation with queue position and partial fills (the audit only flags)',
    'nonofficial_valuation_price_held': 'official prices for the 67 untradable days or a rule that excludes holding them',
    'DELISTING_EXIT_UNVERIFIED': 'official exit prices or payouts for delisted holdings', 'beyond_family_boundary': 'truncate to the family boundary'}


def worst(statuses):
    order = ['DATA_BLOCKED', 'DATA_FAIL', 'DATA_PASS']
    return min(statuses, key=order.index) if statuses else 'DATA_BLOCKED'


def gate_status():
    return {g['gate']: g['status'] for g in read(OUT / 'data_gate_results.json')['gates']}


def input_groups(inputs):
    groups = set()
    for name in inputs:
        if name in ('foreign_ratio', 'trust_ratio', 'dealer_ratio', 'foreign_consec_days', 'trust_consec_days', 'dealer_consec_days', 'foreign_buy', 'trust_buy', 'dealer_buy'):
            groups.add('flows')
        elif name in ('chip_score', 'margin_change_pct', 'margin_balance'):
            groups |= {'flows', 'margin'}
        elif name == 'revenue_yoy':
            groups.add('revenue')
        elif name in ('eps', 'op_profit_margin', 'rd_ratio'):
            groups.add('financials')
        else:
            groups.add('prices')
    return groups


RULES_DIR = OUT / 'native7_od29'  # OD-29 reruns (finite_positive_stale); earlier native7_rules_only_*.json in OUT are superseded, kept, never counted


def adopted_rules_results():
    """One adopted rules-only result per strategy. Byte-identical content in several files (a backup) counts once; different content for the same
    strategy is a conflict and neither result is adopted. Backups live outside the glob (names not starting with native7_rules_only_)."""
    found = {}
    for path in sorted(RULES_DIR.glob('native7_rules_only_*.json')):
        if 'smoke' in path.name:
            continue
        payload = read(path)
        source = {'file': path.name, 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  **(payload['run'] if 'run' in payload else {'run_id': None, 'provenance': 'UNCONFIRMED: written before run_id/input hashes were recorded'})}
        for key, result in payload['strategies'].items():
            digest = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
            found.setdefault(key, {}).setdefault(digest, []).append((path.name, {**result, 'source': source}))
    adopted = {key: next(iter(versions.values()))[0][1] for key, versions in found.items() if len(versions) == 1}
    conflicts = {key: ' vs '.join(f'{v[0][0]} ({d[:8]})' for d, v in versions.items()) for key, versions in found.items() if len(versions) > 1}
    return adopted, conflicts


def track_a_rows(gates):
    from core.research.native_frame import rule_inputs
    from core.strategy_manager import StrategyManager

    manager = StrategyManager()
    engine = {r['strategy']: r for r in csv.DictReader(open(NATIVE7 / 'native7_status_matrix.csv', encoding='utf-8-sig'))}
    decisions = read(MODEL_VALIDATION / 'retrain_decision.json')['models']
    rules, conflicts = adopted_rules_results()
    od29 = OUT / 'od29_valuation' / 'native7_od29_results.json'  # zero-volume classes (PROBABLE_SUSPENSION) are added post-run by od29_valuation.py results
    od29_flags = {k: [f for f in v['row']['flags'].split(';') if f] for k, v in read(od29).items()} if od29.exists() else {}
    rows = []
    for key in NATIVE:
        groups = input_groups(rule_inputs(manager.get_strategy(key)))
        need = {g: INPUT_GATES[g] for g in groups}
        status = {g: gates.get(name, 'DATA_BLOCKED') for g, name in need.items()}
        data = worst(list(status.values()))
        missing = [g for g, name in need.items() if name in MISSING_DATA_GATES]
        run = rules.get(key)
        backtest = 'BLOCKED' if missing or key in conflicts else (run['BACKTEST'] if run else 'BLOCKED')  # a finished run is never upgraded; a missing run is not a pass
        blockers = [f'{INPUT_GATES[g]}={s}' for g, s in status.items() if s != 'DATA_PASS'] + ['MODEL_BLOCKED (retraining needed)', 'EVIDENCE: no independent sample']
        if key in conflicts:
            blockers.append(f'RESULT_CONFLICT: {conflicts[key]}')
        elif run and run['BACKTEST'] == 'BLOCKED':
            blockers.append(f"BACKTEST_BLOCKED: {run['metrics']['sharpe']['reason'] or run.get('stale_valuation', {}).get('flags')}")
        if run and key not in conflicts:
            flags = run.get('stale_valuation', {}).get('flags', []) + [f for f in od29_flags.get(key, []) if f not in run.get('stale_valuation', {}).get('flags', [])]
            blockers += [f'STALE_VALUATION: {f}' for f in flags]
        detail = ('result conflict between files: ' + conflicts[key] if key in conflicts else 'rules only, raw prices, price-return basis; rule parameters tuned earlier with unknown trial count'
                  if run and run['BACKTEST'] != 'BLOCKED' else 'rules-only run finished but equity is non-finite: all metrics undefined (None)' if run else 'no run (required data missing or run not finished)')
        rows.append({'group': 'A_NATIVE_AI', 'strategy': key, 'ENGINE': engine[key]['engine_after'], 'DATA': data, 'DATA_detail': json.dumps(status), 'BACKTEST': backtest,
                     'BACKTEST_detail': detail,
                     'EVIDENCE': 'INSUFFICIENT', 'RETRAIN': decisions[key]['decision'], 'MODEL': 'MODEL_BLOCKED', 'independent_validation_eligible': 'NO',
                     'blockers': ' | '.join(blockers), 'unblock': 'owner-approved retraining on certified data; ' + ('monthly revenue announcement times; ' if missing else '') + 'new independent sample (252 sessions)'})
    return rows


def track_b_rows(gates):
    results = pd.read_csv(OUT / 'backtest_results.csv')
    native = {r['family_id']: r for r in csv.DictReader(open(NATIVE22 / 'family_validation_matrix.csv', encoding='utf-8-sig'))}
    price_ok = gates.get('D3_ADJUSTED_PRICES') == 'DATA_PASS' and gates.get('OFFICIAL_CALENDAR_AND_PRICES') == 'DATA_PASS'
    rows = []
    for name, g in results.groupby('family_name'):
        family = g.family.iloc[0]
        factors = set(name.split('+'))  # factor order differs between the two files (RS_004_NEAR_HIGH_120D+A101_003 vs A101_003+RS_004_NEAR_HIGH_120D)
        fid = next(k for k in native if (family == 'D5' and 'portfolio_research' in k) or (family != 'D5' and set(k.split(':')[-1].split('+')) == factors))
        blockers = pd.Series(';'.join(g.blockers.fillna('')).split(';')).replace('', pd.NA).dropna().value_counts()
        data = 'DATA_BLOCKED' if family == 'G2G3' else ('DATA_PASS' if price_ok else 'DATA_BLOCKED')
        counts = g.BACKTEST.value_counts().to_dict()
        rows.append({'group': {'G2G3': 'B1_G2_G3', 'D5': 'B2_D5', 'DAY3': 'B3_DAY3'}[family], 'strategy': name, 'ENGINE': native[fid]['ENGINE'], 'DATA': data,
                     'DATA_detail': ('prices/calendar/universe/costs certified; fundamentals values unverified' if family == 'G2G3' else
                                     'prices/calendar/universe/costs certified (D3 801,084 rows, 0 mismatches); total return blocked'),
                     'BACKTEST': 'DEVELOPMENT' if counts.get('DEVELOPMENT') else 'DIAGNOSTIC',
                     'BACKTEST_detail': f"{counts.get('DEVELOPMENT', 0)} of {len(g)} configurations DEVELOPMENT, {counts.get('DIAGNOSTIC', 0)} DIAGNOSTIC",
                     'EVIDENCE': 'CANDIDATE_FOR_INDEPENDENT_VALIDATION' if (g.EVIDENCE == 'CANDIDATE_FOR_INDEPENDENT_VALIDATION').any() else 'INSUFFICIENT', 'RETRAIN': 'NOT_APPLICABLE',
                     'MODEL': 'NOT_APPLICABLE', 'independent_validation_eligible': 'YES' if (g.EVIDENCE == 'CANDIDATE_FOR_INDEPENDENT_VALIDATION').any() else 'NO',
                     'configurations': len(g), 'median_net_sharpe': round(float(g.sharpe_net.median()), 3), 'median_excess_sharpe_vs_TAIEX_TR': round(float(g.excess_sharpe.median()), 3),
                     'max_bootstrap_lower': round(float(g.bootstrap_lower.max()), 3), 'max_deflated_sharpe': round(float(g.deflated_sharpe.max()), 4),
                     'blockers': ' | '.join(f'{k}({v})' for k, v in blockers.items()),
                     'unblock': ' ; '.join(UNBLOCK[k] for k in blockers.index if k in UNBLOCK)})
    return rows


def matrix():
    gates = gate_status()
    rows = track_a_rows(gates) + track_b_rows(gates)
    frame = pd.DataFrame(rows)
    assert len(frame) == 22, len(frame)
    frame.to_csv(OUT / 'strategy_status_matrix.csv', index=False, encoding='utf-8-sig')
    summary = {'ENGINE_PASS': int((frame.ENGINE == 'ENGINE_PASS').sum()), 'DATA': frame.DATA.value_counts().to_dict(), 'BACKTEST': frame.BACKTEST.value_counts().to_dict(),
               'EVIDENCE': frame.EVIDENCE.value_counts().to_dict(), 'RETRAIN': frame.RETRAIN.value_counts().to_dict(),
               'independent_validation_eligible': int((frame.independent_validation_eligible == 'YES').sum())}
    write(OUT / 'strategy_status_summary.json', summary)
    print(summary)


def track_a_readiness():
    gates = gate_status()
    decisions = read(MODEL_VALIDATION / 'retrain_decision.json')['models']
    spec = read(ROOT / 'outputs/official_data_recovery/2026-10-23/retraining_spec.json')
    fixes = [
        {'fix': 'dealer_consec_days and institutional_consec_buy_days from the stock history', 'where': 'core/calc_indicators.py build_feature_history',
         'tests': ['test_dealer_consec_days_counts_consecutive_sessions_and_stays_null_without_a_flow', 'test_institutional_consec_buy_days_uses_the_history_streak']},
        {'fix': 'z-score population = rows with every model input, rules applied afterwards', 'where': 'core/research/native_frame.py frame(model_inputs=...)',
         'tests': ['test_rule_inputs_never_change_the_zscore_population', 'test_a_row_missing_a_model_input_is_outside_the_population', 'test_legacy_frame_behavior_is_unchanged_without_model_inputs']},
        {'fix': 'TWSE-listed common stocks only in recovered Daily', 'where': 'core/research/native_frame.py listed_universe; jobs/run_daily.py load_recovered_data',
         'tests': ['listed_universe reads only the cached official MI_INDEX response and raises LISTED_UNIVERSE_UNAVAILABLE instead of guessing']},
        {'fix': 'model contract reads training_data_end and spec_sha256', 'where': 'core/research/native_models.py load_model_contract',
         'tests': ['test_training_data_end_bounds_the_window_and_the_spec_hash_is_checked', 'test_a_model_with_a_spec_hash_requires_every_feature_not_only_those_with_importance',
                   'test_an_old_model_without_the_new_fields_still_loads_as_before']},
        {'fix': 'recovered status helper: no unproven-chip flag for a spec-trained model', 'where': 'jobs/run_daily.py recovered_model_status',
         'tests': ['test_recovered_status_only_blocks_on_missing_cutoff_or_unproven_chip_definition']},
        {'fix': 'training frame builder and approval-gated train()', 'where': 'core/research/model_training.py',
         'tests': ['test_training_refuses_without_a_matching_approval_before_reading_any_data', 'test_training_frame_equals_the_recovered_frame_for_the_same_session',
                   'test_labels_use_only_the_next_sessions_and_rows_without_a_full_horizon_are_dropped']},
        {'fix': 'rules-only research runs (no AI feature can exclude a stock)', 'where': 'core/backtest/runner.py BacktestEngine(rules_only=True)',
         'tests': ['test_rules_only_engine_needs_no_model_and_excludes_only_on_rule_inputs']}]
    data_gate = {g: gates.get(g) for g in ('OFFICIAL_T86_FULL_RANGE', 'OFFICIAL_MI_MARGN_FULL_RANGE', 'OFFICIAL_CALENDAR_AND_PRICES', 'D3_ADJUSTED_PRICES')}
    blockers = ['SPEC_NOT_APPROVED (retraining-spec-v1 is DRAFT_PENDING_OWNER_APPROVAL)', 'OWNER_APPROVAL_FILE_MISSING', 'ACCEPTANCE_EVALUATION_NOT_IMPLEMENTED (train() records acceptance NOT_EVALUATED)',
                'NO_FRESH_OOS_SAMPLE']
    write(OUT / 'track_a_readiness.json', {
        'fixes': fixes, 'spec': {'version': spec['version'], 'status': spec['status'], 'sha256': spec['sha256']}, 'official_data_gates': data_gate,
        'models': {k: {'MODEL_STATUS': 'MODEL_BLOCKED', 'retrain_decision': decisions[k]['decision'], 'ready_for_training': False,
                       'blockers': blockers + ([] if all(v == 'DATA_PASS' for v in data_gate.values()) else ['OFFICIAL_DATA_NOT_FULLY_CERTIFIED'])} for k in NATIVE},
        'no_model_trained': True, 'old_models_unchanged': True})
    print('ok')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'matrix': matrix, 'readiness': track_a_readiness}[sys.argv[1]]()
