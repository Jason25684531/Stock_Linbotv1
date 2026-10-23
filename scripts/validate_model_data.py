"""Model & data validation: training provenance, feature definitions, retraining decision, download plan, OOS feasibility.

Read-only against MySQL, models and code; writes only into one immutable output directory. No model is trained,
no bulk download runs (at most PROBE_LIMIT official probe requests in the whole change).
Order: baseline -> provenance -> features -> decision -> inventory -> probe -> plan -> oos -> tests -> report -> verify.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.validate_native22 import NATIVE

OUT = ROOT / 'outputs/model_data_validation/2026-10-23'
PREV = ROOT / 'outputs/daily_selection_recovery/2026-10-22'
NATIVE7 = ROOT / 'outputs/native7_recovery/2026-10-22'
TRAINING_DATE = '2026-05-20'


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # immutable evidence; rerun into a new directory
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True).stdout


def models():
    """strategy key -> (strategy name, model path) for the seven native strategies."""
    from core.model_utils import get_model_path
    from core.strategy_manager import StrategyManager

    manager = StrategyManager()
    return {k: (manager.get_strategy(k).name, Path(get_model_path(manager.get_strategy(k).name))) for k in NATIVE}


def archived_hashes():
    """Every recorded output of the archived runs still matches its manifest."""
    result = {}
    for folder in (PREV, NATIVE7, ROOT / 'outputs/native22_validation/2026-10-22'):
        recorded = read(folder / 'reproduction_manifest.json')['outputs']
        result[folder.relative_to(ROOT).as_posix()] = {
            'files': len(recorded), 'changed': [n for n, h in recorded.items() if digest(folder / n) != h]}
    return result


def baseline():
    from config import Config
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    from scripts.validate_native22 import preservation

    kept = preservation()
    review = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json')['documents']['promotion_review']
    write(out('baseline.json'), {
        'git_head': git('rev-parse', 'HEAD').strip(), 'git_status': git('status', '--short').splitlines(),
        'archived_outputs': archived_hashes(), 'preservation': kept,
        'frozen': {'fingerprint_unchanged': FrozenStrategyLoader().load().fingerprint == review['strategy_fingerprint'],
                   'cutoff': review['evaluation_window']['cutoff'], 'BROKER_ORDER_SUBMISSION': review['BROKER_ORDER_SUBMISSION']},
        'models': {k: {'name': n, 'path': p.as_posix(), 'sha256': digest(ROOT / p)} for k, (n, p) in models().items()},
        'DAILY_FEATURE_MODE': Config.DAILY_FEATURE_MODE})
    print(json.dumps(read(OUT / 'baseline.json')['archived_outputs']), kept['protected_changed'])


def daily_stats():
    """Per-date shape of daily_market_data up to the training date (what train_model's SELECT * read, as it is today)."""
    from sqlalchemy import text

    from core.db_helper import get_db_engine

    sql = text("""SELECT trade_date, COUNT(*) n, SUM(stock_id REGEXP '^[1-9][0-9]{3}$') stocks,
                         SUM(stock_id REGEXP '^[1-9][0-9]{3}$' AND foreign_buy = 0) foreign_zero,
                         SUM(stock_id REGEXP '^[1-9][0-9]{3}$' AND trust_buy = 0) trust_zero,
                         SUM(rsi IS NULL) rsi_null, SUM(bb_width IS NULL) bb_null, SUM(natr <> 0) natr_non_zero
                  FROM daily_market_data WHERE trade_date <= :d AND close_price > 0 GROUP BY trade_date ORDER BY trade_date""")
    stats = pd.read_sql(sql, get_db_engine(), params={'d': TRAINING_DATE}).fillna(0)
    stats['trade_date'] = pd.to_datetime(stats.trade_date)
    stats['weekend'] = stats.trade_date.dt.weekday >= 5
    stats['foreign_zero_share'] = stats.foreign_zero / stats.stocks.where(stats.stocks > 0)
    return stats


def tree_stats(model, feature):
    trees = model.get_booster().trees_to_dataframe()
    split = trees[trees.Feature == feature].Split
    return {'splits': int(len(split)), 'threshold_quantiles': None if split.empty else
            {q: round(float(split.quantile(q)), 4) for q in (0.05, 0.5, 0.95)}}


def provenance():
    """What the seven models were trained on, each item graded PROVEN / INFERRED / UNKNOWN with its evidence."""
    import joblib

    from core.calc_indicators import Z_SCORE_FEATURE_COLUMNS

    stats = daily_stats()
    dates = sorted(stats.trade_date)
    split = dates[int(len(dates) * 0.8) - 1]  # jobs/train_model.py:time_series_split, TRAIN_RATIO = 0.8
    whole_day_zero = stats[(stats.foreign_zero_share > 0.9) & (stats.stocks > 100)]
    quality = read(PREV / 'data_quality_report.json')['gates']
    official_bad = next(g for g in quality if g['gate'] == 'MYSQL_FOREIGN_BUY_RESEARCH_WINDOW')['reconciliation']['dates_below_99pct']
    overlap = whole_day_zero[whole_day_zero.trade_date >= '2025-12-15'].trade_date.dt.strftime('%Y-%m-%d').tolist()
    commits_between = git('log', '--format=%h %ad %s', '--date=iso', '--after=2026-05-19 17:28:58', '--before=2026-05-21 21:13:36').splitlines()
    backfill = (ROOT / 'openspec/changes/archive/2026-10-13-backfill-market-data/verification.md').read_text(encoding='utf-8')
    schema_line = next(l.strip() for l in backfill.splitlines() if 'dealer_buy、margin_balance' in l and 'NULL DEFAULT NULL' in l)

    common = {
        'code_version': {'value': 'uncommitted working tree on 2026-05-20; equivalent to be448d9 (train_model.py identical be448d9..HEAD, '
                                  'calc_indicators differs by one comment line)', 'grade': 'INFERRED',
                         'evidence': ['4e0311a (last commit before training) has no build_multi_factor_matrix / Z_SCORE features',
                                      'be448d9 (2026-05-21 21:13) first contains them; no commit in between: ' + json.dumps(commits_between, ensure_ascii=False),
                                      'model features == Z_SCORE_FEATURE_COLUMNS of be448d9/HEAD']},
        'uncommitted_code_used': {'value': True, 'grade': 'PROVEN',
                                  'evidence': '24 *_z features cannot be produced by the code committed before 2026-05-20 20:55'},
        'data_source': {'value': 'MySQL daily_market_data, SELECT * without filtering (all markets, weekend and duplicate rows included)',
                        'grade': 'INFERRED', 'evidence': 'jobs/train_model.py:load_and_prepare_data (code version inferred)'},
        'training_log_or_snapshot': {'value': None, 'grade': 'UNKNOWN',
                                     'evidence': 'no log, data snapshot or tracked model in ML_Data/, logs/, outputs/, data/ for 2026-05-19..22; models are untracked'},
        'dealer_margin_columns_at_training': {'value': 'absent', 'grade': 'PROVEN',
                                              'evidence': f'2026-10-13 backfill verification: "{schema_line}"'},
        'chip_score_definition': {
            'value': 'calculate_chip_score with dealer_buy = 0 (train_model.py:165) and margin_change_pct = 0 (:184): dealer and '
                     'margin components fixed at score 50; chip_score varies only with foreign_ratio and trust_ratio',
            'grade': 'INFERRED',
            'evidence': ['columns absent at training (PROVEN)', 'code version INFERRED',
                         'chip_score_z has splits in every model, so it varied in training (rules out all-NaN-then-0)',
                         'dealer_ratio_z and dealer_consec_days_z have zero splits in every model (constant, as dealer_buy = 0)']},
        'technical_indicators_source': {
            'value': 'stored DB columns (rsi, bias, macd_hist, kd_k, bb_width, natr, std_20) read as is, not recomputed; NULL -> 0 '
                     'in build_multi_factor_matrix', 'grade': 'INFERRED', 'evidence': 'train_model.py:load_and_prepare_data has no indicator calculation'},
        'training_data_start': {'value': dates[0].strftime('%Y-%m-%d'), 'grade': 'INFERRED', 'evidence': 'current DB minimum; DB state on 2026-05-20 not recorded'},
        'training_data_last_row': {'value': dates[-1].strftime('%Y-%m-%d'), 'grade': 'INFERRED',
                                   'evidence': 'current DB rows <= training date; whether 2026-05-20 was loaded before 20:55 is not recorded'},
        'train_test_boundary': {'value': {'train_end': split.strftime('%Y-%m-%d'), 'unique_dates': len(dates),
                                          'test_start': dates[int(len(dates) * 0.8)].strftime('%Y-%m-%d')},
                                'grade': 'INFERRED', 'evidence': '80% of unique dates (weekend dates included) in the current DB'},
        'db_changed_after_training': {
            'value': True, 'grade': 'INFERRED',
            'evidence': [f"natr non-zero on {int((stats.natr_non_zero > 0).sum())} dates up to the training date, yet natr_z has zero splits in every model",
                         '2026-10-13 backfill added columns and new rows', 'native7: 2026-04-07 whole-day wrong rows, weekend TPEx rows']},
        'look_ahead_bias': {
            'value': 'features use only current and past rows; labels use future highs by design. Labels of the last look_ahead '
                     'sessions before the train/test boundary read prices inside the test period, and tail rows are labelled 0 '
                     '(future_max_return.fillna(0)) instead of being dropped', 'grade': 'INFERRED', 'evidence': 'train_model.py:calculate_future_target, time_series_split'},
        'wrong_institutional_flows': {
            'value': {'whole_day_foreign_zero_dates': len(whole_day_zero),
                      'by_year': whole_day_zero.trade_date.dt.year.value_counts().sort_index().to_dict(),
                      'weekend_dates': int(stats.weekend.sum()), 'weekend_rows': int(stats[stats.weekend].n.sum()),
                      'rows_total': int(stats.n.sum()), 'rows_2026': int(stats[stats.trade_date.dt.year == 2026].n.sum()),
                      'overlap_with_official_window': overlap,
                      'overlap_confirmed_by_official': sorted(set(overlap) & set(official_bad))},
            'grade': 'PROVEN for dates confirmed against official T86, INFERRED elsewhere (> 90 % of stocks with foreign_buy = 0)',
            'evidence': 'daily_market_data per-date aggregates; data_quality_report.json (previous run)'},
    }
    result = {}
    for key, (name, path) in models().items():
        data = joblib.load(ROOT / path)
        model = data['model']
        booster_names = model.get_booster().feature_names
        result[key] = {
            'model': name, 'path': path.as_posix(), 'sha256': digest(ROOT / path),
            'training_date': {'value': data['training_date'], 'grade': 'PROVEN', 'evidence': 'model metadata; file mtime the same minute'},
            'training_data_end': {'value': data.get('training_data_end'), 'grade': 'UNKNOWN', 'evidence': 'not recorded in the model file'},
            'metadata': {k: data[k] for k in ('version', 'look_ahead_days', 'target_return', 'accuracy', 'precision')},
            'features': {'value': data['features'], 'grade': 'PROVEN',
                         'evidence': f"booster feature_names equal: {booster_names == data['features']}; "
                                     f"equal to Z_SCORE_FEATURE_COLUMNS: {list(Z_SCORE_FEATURE_COLUMNS) == data['features']}"},
            'importance': {f: round(float(v), 4) for f, v in zip(data['features'], model.feature_importances_)},
            'tree_splits': {f: tree_stats(model, f) for f in ('chip_score_z', 'dealer_ratio_z', 'natr_z', 'volume_ratio_z', 'bb_width_z')},
            'last_labelled_row_with_full_horizon': dates[-1 - int(data['look_ahead_days'])].strftime('%Y-%m-%d'),
            **common}
    write(out('training_provenance.json'), {'models': result, 'per_date_stats_rows': len(stats)})
    stats.assign(trade_date=stats.trade_date.dt.strftime('%Y-%m-%d')).to_csv(out('training_period_daily_stats.csv'), index=False)
    print(common['train_test_boundary']['value'], common['wrong_institutional_flows']['value'])


COMPARE_WINDOW = ('2025-06-02', '2025-07-30')  # inside the training split, after a 5-month warm-up in the official snapshot
STORED = ('rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width')


def stored_vs_recomputed():
    """DB-stored indicators (what training read) against the shared core recomputed from official prices."""
    from sqlalchemy import text

    from core.calc_indicators import build_feature_history
    from core.db_helper import get_db_engine

    db = pd.read_sql(text(f"""SELECT stock_id, trade_date, volume, {', '.join(STORED)} FROM daily_market_data
                              WHERE trade_date BETWEEN :a AND :b AND stock_id REGEXP '^[1-9][0-9]{{3}}$'"""),
                     get_db_engine(), params={'a': COMPARE_WINDOW[0], 'b': COMPARE_WINDOW[1]})
    db = db.assign(stock_id=db.stock_id.astype(str).str.strip(), trade_date=pd.to_datetime(db.trade_date))
    snap = pd.read_parquet(NATIVE7 / 'research_snapshot.parquet')
    core = build_feature_history(snap[snap.trade_date <= COMPARE_WINDOW[1]])
    joined = db.merge(core[['stock_id', 'trade_date', 'volume', *STORED]], on=['stock_id', 'trade_date'], suffixes=('_db', '_core'))
    result = {}
    for c in STORED:
        a, b = pd.to_numeric(joined[f'{c}_db'], errors='coerce'), joined[f'{c}_core']
        both = a.notna() & b.notna()
        diff = (a[both] - b[both]).abs()
        result[c] = {'pairs': int(both.sum()), 'equal_rtol_1e-9': round(float((diff <= 1e-12 + 1e-9 * b[both].abs()).mean()), 4),
                     'within_1pct': round(float((diff <= 0.01 * b[both].abs().clip(lower=1e-9)).mean()), 4),
                     'median_abs_diff': round(float(diff.median()), 6), 'db_zero': int((a == 0).sum()), 'core_null': int(b.isna().sum())}
    return result, len(joined)


def ranking_variants(frame, full, contract, candidates, day):
    """Model scores for the same candidates under the recovered features and two training-style variants."""
    from core.calc_indicators import build_multi_factor_matrix, calculate_chip_score

    neutral = full.assign(dealer_buy=0.0, dealer_ratio=0.0, margin_change_pct=0.0)  # training: columns absent -> 0
    neutral['chip_score'] = calculate_chip_score(neutral)
    variants = {'recovered': frame.set_index('stock_id'),
                'population_full_market': build_multi_factor_matrix(full, trade_date=day).set_index('stock_id'),
                'training_emulation': build_multi_factor_matrix(neutral, trade_date=day).set_index('stock_id')}
    ids = list(candidates.stock_id)
    scores = {k: pd.Series(contract.model.predict_proba(v.loc[ids, contract.features].astype(float))[:, 1], index=ids)
              for k, v in variants.items()}
    top = {k: list(s.sort_values(ascending=False, kind='stable').index[:3]) for k, s in scores.items()}
    return {'candidates': len(ids), 'top3': top,
            'top3_equal_to_recovered': {k: top[k] == top['recovered'] for k in top},
            'spearman_vs_recovered': {k: None if len(ids) < 3 else round(float(scores[k].corr(scores['recovered'], method='spearman')), 4)
                                      for k in scores if k != 'recovered'}}


def official_inputs():
    """The previous run's parity inputs: official-chip snapshot, PIT fundamentals, recorded user settings."""
    from core.research.native_frame import NativeResearchData, load_publication_dates

    snap = pd.read_parquet(PREV / 'research_snapshot_official_chips.parquet')
    fin = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_financial_statements.parquet')
    rev = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_monthly_revenue.parquet')
    return NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates()), read(NATIVE7 / 'user_settings.json')


def features():
    """Training-time vs recovered definitions of the 24 model features, with numeric and ranking checks."""
    import contextlib

    import joblib

    from jobs import run_daily
    from scripts.daily_selection_recovery import SNAPSHOT_DATES

    provenance = read(OUT / 'training_provenance.json')['models']
    numeric, compared_rows = stored_vs_recomputed()
    data, settings = official_inputs()
    ranking = {k: [] for k in NATIVE}
    from scripts.native_recovery import isolated
    with isolated(data, settings), open(OUT / 'features_stdout.log', 'w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
        for day in SNAPSHOT_DATES:
            full = data.cross_section(pd.Timestamp(day))
            for key, (name, _path) in models().items():
                strategy = run_daily.StrategyManager().get_strategy(key)
                frame, _note, contract, _status = run_daily.recovered_strategy_frame(strategy, data, day)
                candidates = strategy.filter_candidates(frame.copy()) if not frame.empty else frame
                if contract is None or candidates.empty:
                    ranking[key].append({'date': day, 'candidates': len(candidates), 'skipped': 'no contract or no candidates'})
                    continue
                ranking[key].append({'date': day, **ranking_variants(frame, full, contract, candidates, day)})
    rows = feature_rows(provenance, numeric)
    pd.DataFrame(rows).to_csv(out('feature_definition_diff.csv'), index=False, encoding='utf-8-sig')
    write(out('feature_ranking_impact.json'), {
        'dates': SNAPSHOT_DATES, 'inputs': 'previous run research_snapshot_official_chips.parquet; same candidates per variant',
        'variants': {'recovered': 'shared core; z-score over the strategy\'s included rows (Daily and research today)',
                     'population_full_market': 'same raw features; z-score over the whole cross-section with NaN -> z 0 (training population)',
                     'training_emulation': 'population_full_market plus dealer_buy = 0 and margin_change_pct = 0 (training chip_score)'},
        'stored_indicator_check': {'window': COMPARE_WINDOW, 'rows': compared_rows, 'per_indicator': numeric}, 'strategies': ranking})
    summary = {k: sum(r.get('top3_equal_to_recovered', {}).get('training_emulation', True) is False for r in v) for k, v in ranking.items()}
    print('top3 changed (dates) under training emulation:', summary, numeric)


FEATURE_NOTES = {
    'rsi': ('stored DB column (writer unknown), full history', 'calculate_rsi over the 150-day window'),
    'bias': ('stored DB column', '(close - ma20) / ma20 * 100'),
    'macd_hist': ('stored DB column', 'calculate_macd over the 150-day window'),
    'kd_k': ('stored DB column', 'calculate_kd over the 150-day window'),
    'bb_width': ('stored DB column', 'calculate_bb_width over the 150-day window'),
    'natr': ('stored DB column, 0 for every row up to 2025 (constant)', 'calculate_natr'),
    'std_20': ('stored DB column, 0 for every row up to 2025 (constant)', 'calculate_std_20'),
    'volume_ratio': ('calculate_ratio_features over the whole table: volume / 20-row mean, min_periods=1', 'same formula; NaN below 20 observations (OD-1)'),
    'foreign_ratio': ('MySQL foreign_buy / volume, clip +-0.5', 'same formula; official T86 in research, MySQL in live Daily'),
    'trust_ratio': ('MySQL trust_buy / volume, clip +-0.5', 'same formula; official T86 in research'),
    'dealer_ratio': ('dealer_buy absent -> 0 (constant)', 'real dealer_buy / volume'),
    'foreign_consec_days': ('calculate_consec_days on MySQL foreign_buy (missing -> not buying)', 'same; NaN where the flow is missing'),
    'trust_consec_days': ('calculate_consec_days on MySQL trust_buy', 'same; NaN where the flow is missing'),
    'dealer_consec_days': ('dealer_buy = 0 -> 0 (constant)', 'from real dealer_buy'),
    'institutional_net_buy': ('foreign + trust + 0', 'foreign + trust + real dealer'),
    'institutional_consec_buy_days': ('foreign + trust consec (dealer 0)', 'includes dealer consec'),
    'chip_score': ('0.4 f + 0.3 t + 0.15*50 + 0.15*50 (dealer and margin neutral)', '0.4 f + 0.3 t + 0.15 d + 0.15 m with real dealer and margin; NaN if any missing'),
    'large_holder_ratio': ('absent -> 0 (constant)', 'absent -> 0'),
    'large_holder_ratio_change': ('absent -> 0 (constant)', 'absent -> 0'),
    'news_sentiment_score': ('constant per run', 'constant per run'),
    'revenue_yoy': ('stored DB column, 0 up to 2025 (constant)', 'PIT monthly revenue (deadline estimate)'),
    'rd_ratio': ('absent -> 0 (constant)', 'PIT financials'),
    'op_profit_margin': ('absent -> 0 (constant)', 'PIT financials'),
    'eps': ('absent -> 0 (constant)', 'PIT financials'),
}


def feature_rows(provenance, numeric):
    importance = pd.DataFrame({k: v['importance'] for k, v in provenance.items()})
    rows = []
    for feature in importance.index:
        raw = feature.removesuffix('_z')
        train_def, recovered_def = FEATURE_NOTES[raw]
        used = importance.loc[feature].max() > 0
        if raw in numeric:
            check = numeric[raw]
            status = 'SAME' if check['within_1pct'] >= 0.99 else 'DIFFERENT'
            evidence = f"stored vs recomputed {COMPARE_WINDOW}: within 1% {check['within_1pct']:.2%}, exact {check['equal_rtol_1e-9']:.2%}"
        elif raw in ('dealer_ratio', 'dealer_consec_days', 'institutional_net_buy', 'institutional_consec_buy_days', 'chip_score'):
            status, evidence = 'DIFFERENT', 'dealer/margin columns absent at training (PROVEN); recovered uses real values'
        elif raw in ('volume_ratio', 'foreign_ratio', 'trust_ratio', 'foreign_consec_days', 'trust_consec_days'):
            status, evidence = 'SAME', 'same formula; differences only for short histories and the data source (MySQL vs official)'
        elif not used and raw in ('natr', 'std_20', 'revenue_yoy', 'rd_ratio', 'op_profit_margin', 'eps'):
            status, evidence = 'DIFFERENT', 'constant in training (zero splits); recovered supplies real values, which the model ignores'
        elif not used:
            status, evidence = 'SAME', 'constant in training and recovered (zero splits); no effect'
        else:
            status, evidence = 'UNKNOWN', 'no evidence'
        rows.append({'feature': feature, 'importance_min': round(float(importance.loc[feature].min()), 4),
                     'importance_max': round(float(importance.loc[feature].max()), 4), 'used_by_models': bool(used),
                     'training_definition': train_def, 'recovered_definition': recovered_def,
                     'zscore_population': 'training: all rows of the date (NaN -> z 0); recovered: rows passing the exclusion',
                     'status': status, 'evidence': evidence})
    return rows


def decide(criteria: dict) -> str:
    """Retraining class from graded criteria (design §4). Every outcome keeps the model MODEL_BLOCKED until certified."""
    grade = {k: v['grade'] for k, v in criteria.items()}
    if grade['wrong_flows_in_fit'] == 'PROVEN' or (grade['definition_changed'] == 'PROVEN' and grade['ranking_impact'] == 'PROVEN'):
        return 'RETRAIN_REQUIRED'
    if any(g in ('PROVEN', 'INFERRED') for k, g in grade.items() if k != 'cutoff_provable'):
        return 'RETRAIN_RECOMMENDED'
    if grade['cutoff_provable'] != 'PROVEN':
        return 'UNDETERMINED'
    return 'RETRAIN_NOT_REQUIRED'


def decision():
    provenance = read(OUT / 'training_provenance.json')['models']
    impact = read(OUT / 'feature_ranking_impact.json')['strategies']
    diff = pd.read_csv(OUT / 'feature_definition_diff.csv')
    changed = diff[diff.used_by_models & (diff.status == 'DIFFERENT')].feature.tolist()
    stats = pd.read_csv(OUT / 'training_period_daily_stats.csv', parse_dates=['trade_date'])
    common = next(iter(provenance.values()))
    train_end = pd.Timestamp(common['train_test_boundary']['value']['train_end'])
    zero_days = stats[(stats.foreign_zero_share > 0.9) & (stats.stocks > 100)]
    fit_zero = zero_days[zero_days.trade_date <= train_end].trade_date.dt.strftime('%Y-%m-%d').tolist()
    confirmed = common['wrong_institutional_flows']['value']['overlap_confirmed_by_official']
    result = {}
    for key, model in provenance.items():
        rows = [r for r in impact[key] if 'top3' in r]
        changed_dates = [r['date'] for r in rows if not r['top3_equal_to_recovered']['training_emulation']]
        criteria = {
            'wrong_flows_in_fit': {'grade': 'UNKNOWN', 'value': {'fit_split': f"{common['training_data_start']['value']}..{train_end:%Y-%m-%d}",
                                                                 'whole_day_foreign_zero_dates_in_fit': fit_zero},
                                   'evidence': 'MySQL flows before 2025-12-15 were never reconciled with official T86; the fit split shows '
                                               f'{len(fit_zero)} whole-day zero dates by pattern only'},
            'wrong_flows_in_evaluation': {'grade': 'PROVEN', 'value': confirmed,
                                          'evidence': 'reported accuracy/precision come from the test split, which contains dates where '
                                                      'MySQL stored 0 against non-zero official T86, weekend TPEx rows and NULL indicators'},
            'definition_changed': {'grade': 'PROVEN', 'value': changed + ['z-score population (all features)'],
                                   'evidence': 'dealer/margin columns absent at training (PROVEN); macd_hist stored vs recomputed within 1% '
                                               'for 89.6% only; training z-score over all rows of a date, recovered over included rows'},
            'ranking_impact': {'grade': 'PROVEN' if changed_dates else 'NOT_OBSERVED', 'value': changed_dates,
                               'evidence': f'feature_ranking_impact.json: top-3 under training emulation differs on {len(changed_dates)} of '
                                           f'{len(rows)} scored dates'},
            'cutoff_provable': {'grade': 'UNKNOWN', 'value': None, 'evidence': 'training_data_end not recorded; DB changed after training'},
            'leakage': {'grade': 'INFERRED', 'value': f"labels of the last {model['metadata']['look_ahead_days']} sessions before the boundary "
                                                      'read test-period prices; tail rows labelled 0', 'evidence': 'train_model.py:calculate_future_target'},
            'distribution_shift': {'grade': 'INFERRED', 'value': 'dealer/margin constant in training, real now; natr/std_20/revenue_yoy 0 in training',
                                   'evidence': 'feature_definition_diff.csv'},
        }
        result[key] = {'model': model['model'], 'decision': decide(criteria), 'MODEL_STATUS': 'MODEL_BLOCKED', 'criteria': criteria}
    write(out('retrain_decision.json'), {'rule': 'design.md §4; decide() in scripts/validate_model_data.py', 'models': result,
                                         'executed_training': False})
    print({k: v['decision'] for k, v in result.items()})


MI_INDEX_CACHE = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
CHIP_CACHE = ROOT / 'artifacts/official_chip_cache/twse'
PROBE_CACHE = ROOT / 'artifacts/official_probe_cache'
PROBE_LIMIT = 20  # owner limit for the whole change, at least 3 s apart
PROBES = {  # question -> URL; only what existing code and docs cannot answer
    't86_month_query': 'https://www.twse.com.tw/rwd/zh/fund/T86?date=202507&selectType=ALLBUT0999&response=json',
    'mi_margn_month_query': 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=202507&selectType=ALL&response=json',
    'twse_openapi_catalog': 'https://openapi.twse.com.tw/v1/swagger.json',
    'tpex_openapi_catalog': 'https://www.tpex.org.tw/openapi/swagger.json',
    'mops_monthly_revenue_summary': 'https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_114_6_0.html',
}


def official_sessions():
    """Official TWSE sessions from the cached MI_INDEX responses (stat OK), and the per-file record."""
    sessions, files = [], {}
    for path in sorted(MI_INDEX_CACHE.glob('MI_INDEX_*.json')):
        day = pd.Timestamp(path.stem.split('_')[-1]).strftime('%Y-%m-%d')
        stat = json.loads(path.read_text(encoding='utf-8')).get('stat')
        files[day] = stat
        if stat == 'OK':
            sessions.append(day)
    return sessions, files


def inventory():
    """Local official data, what it covers, and what is still missing (no requests)."""
    sessions, mi_files = official_sessions()
    chips = read(PREV / 'official_chip_manifest.json')
    chip_days = {c['date'] for c in chips['coverage'] if c['dataset'] == 'T86'}
    chip_ok = {c['date'] for c in chips['coverage'] if c['dataset'] == 'T86' and c['stat'] == 'OK'}
    pit = read(ROOT / 'data/processed/fundamental_pit_v3/request_summary.json')
    records = pd.read_parquet(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet', columns=['fiscal_year', 'quarter'])
    train_sessions = [d for d in sessions if d <= TRAINING_DATE]
    rows = [
        {'dataset': 'TWSE MI_INDEX (prices, calendar)', 'location': MI_INDEX_CACHE.relative_to(ROOT).as_posix(),
         'have_from': min(mi_files), 'have_to': max(mi_files), 'files': len(mi_files), 'official_sessions': len(sessions),
         'missing': '2026-07-29..latest', 'certified': 'DATA_PASS (native7 snapshot uses 2025-01..2026-07; earlier files parsed, hashed here)',
         'still_needed_sessions': 'post-2026-07-28 only'},
        {'dataset': 'TWSE TWT49U (ex-rights events)', 'location': MI_INDEX_CACHE.relative_to(ROOT).as_posix(),
         'have_from': '2023-01-03', 'have_to': '2026-07-28', 'files': 1, 'official_sessions': None,
         'missing': '2022-01-03..2022-12-30, 2026-07-29..latest', 'certified': 'DATA_PASS for price-return checks (native7)',
         'still_needed_sessions': '2 range requests'},
        {'dataset': 'TWSE T86 (institutional net buy)', 'location': CHIP_CACHE.relative_to(ROOT).as_posix(),
         'have_from': min(chip_days), 'have_to': max(chip_days), 'files': len(chip_days), 'official_sessions': len(chip_ok),
         'missing': '2022-01-03..2025-12-12, 2026-07-29..2026-09-25, 2026-10-22..latest',
         'certified': 'DATA_PASS for cached days', 'still_needed_sessions': len([d for d in sessions if d not in chip_days])},
        {'dataset': 'TWSE MI_MARGN (margin balance)', 'location': CHIP_CACHE.relative_to(ROOT).as_posix(),
         'have_from': min(chip_days), 'have_to': max(chip_days), 'files': len(chip_days), 'official_sessions': len(chip_ok),
         'missing': 'same as T86', 'certified': 'DATA_PASS for cached days', 'still_needed_sessions': len([d for d in sessions if d not in chip_days])},
        {'dataset': 'MOPS quarterly filings (fundamental_pit_v3)', 'location': 'data/processed/fundamental_pit_v3',
         'have_from': f"{int(records.fiscal_year.min())}Q1", 'have_to': f"{int(records.fiscal_year.max())}Q4", 'files': pit['ledger_row_count'],
         'official_sessions': None, 'missing': 'FY2026Q1 onwards; 7,666 failed requests in the original ledger',
         'certified': 'publication dates PROVEN where present', 'still_needed_sessions': None},
        {'dataset': 'MOPS monthly revenue', 'location': 'MySQL monthly_revenue only', 'have_from': None, 'have_to': None, 'files': 0,
         'official_sessions': None, 'missing': 'all (no official file, no publication timestamp)', 'certified': 'DATA_BLOCKED',
         'still_needed_sessions': None},
        {'dataset': 'TPEx prices / flows / margin', 'location': 'none', 'have_from': None, 'have_to': None, 'files': 0, 'official_sessions': None,
         'missing': 'all', 'certified': 'DATA_BLOCKED', 'still_needed_sessions': None},
        {'dataset': 'MySQL snapshots (2026-10-21)', 'location': 'outputs/strategy_validation/2026-10-21',
         'have_from': '2022-01-03', 'have_to': '2026-10-20', 'files': 3, 'official_sessions': None, 'missing': 'n/a (not official)',
         'certified': 'reconciliation input only (DATA_FAIL flows)', 'still_needed_sessions': None},
    ]
    pd.DataFrame(rows).to_csv(out('official_data_gap_inventory.csv'), index=False, encoding='utf-8-sig')
    write(out('mi_index_cache_hashes.json'), {'sessions': sessions, 'training_period_sessions': len(train_sessions),
                                               'files': {p.name: digest(p) for p in sorted(MI_INDEX_CACHE.glob('*.json'))}})
    print({r['dataset']: r['still_needed_sessions'] for r in rows}, len(sessions))


def probe():
    """At most PROBE_LIMIT official requests in the whole change, each recorded; answers endpoint capability questions."""
    import requests
    from datetime import datetime, timezone

    from core.research.sources.twse import RequestGate

    log_path = OUT / 'probe_log.json'
    log = read(log_path) if log_path.exists() else []
    assert len(log) + len(PROBES) <= PROBE_LIMIT, 'PROBE_LIMIT'
    gate = RequestGate(minimum_interval=3.5)
    PROBE_CACHE.mkdir(parents=True, exist_ok=True)
    for question, url in PROBES.items():
        if any(entry['question'] == question for entry in log):
            continue
        gate.wait()
        try:
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            body = res.content
            path = PROBE_CACHE / f'{question}.bin'
            path.write_bytes(body)
            entry = {'question': question, 'url': url, 'status': res.status_code, 'bytes': len(body), 'sha256': digest(path),
                     'content_type': res.headers.get('content-type'), 'summary': summarize(question, body)}
        except requests.RequestException as exc:
            entry = {'question': question, 'url': url, 'status': None, 'error': type(exc).__name__}
        entry['retrieved_at'] = datetime.now(timezone.utc).isoformat()
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')  # progress kept after each request
    print([(e['question'], e.get('status'), e.get('summary')) for e in log])


def summarize(question, body):
    text = body.decode('utf-8', errors='replace')
    if question.endswith('catalog'):
        try:
            paths = list(json.loads(text).get('paths', {}))
        except ValueError:
            return 'not JSON'
        keys = ('T86', 'fund', 'MI_MARGN', 'margin', 'MI_INDEX', 'insti', 'quotes', 'Margin')
        return {'paths': len(paths), 'related': [p for p in paths if any(k.lower() in p.lower() for k in keys)][:25]}
    if question.endswith('month_query'):
        try:
            data = json.loads(text)
        except ValueError:
            return 'not JSON'
        rows = len(data.get('data', [])) or sum(len(t.get('data', [])) for t in data.get('tables', []))
        return {'stat': data.get('stat'), 'date': data.get('date'), 'rows': rows}
    return {'bytes': len(body), 'has_table': '<table' in text.lower(), 'mentions_announcement_time': any(k in text for k in ('公告', '申報日'))}


SECONDS_PER_REQUEST = 3.0
LATEST = '2026-10-23'


def needed(days, cached):
    """Sessions still to request: cached and verified files are never fetched again."""
    return [d for d in days if d not in cached]


def mean_size(pattern, folder):
    sizes = [p.stat().st_size for p in folder.glob(pattern) if not p.name.endswith('.meta.json')]
    return sum(sizes) / len(sizes) if sizes else 0


def plan():
    """Request estimates for the minimal, staged and complete plans, and the recommended resumable batch plan."""
    sessions, _ = official_sessions()
    sessions = [d for d in sessions if d >= '2022-01-03']
    chips = read(PREV / 'official_chip_manifest.json')['coverage']
    cached_chip = {c['date'] for c in chips if c['dataset'] == 'T86'}  # T86 and MI_MARGN were fetched for the same days
    probes = read(OUT / 'probe_log.json')
    train_end = read(OUT / 'training_provenance.json')['models']['defensive_low_volatility']['train_test_boundary']['value']['train_end']
    fit = [d for d in sessions if d <= train_end]
    monthly = sorted({min(d for d in fit if d[:7] == m) for m in {d[:7] for d in fit}})
    sample = sorted(set(monthly) | {'2024-07-18'})  # one session per month of the fit split, plus the detected whole-day-zero date
    history = needed([d for d in sessions if d <= '2025-12-12'], cached_chip)
    # After the cached calendar: weekdays as an upper bound (holidays unknown until fetched; non-trading responses cost a request too).
    post = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2026-07-29', LATEST)]
    post_chip = needed(post, cached_chip)
    size = {'T86': mean_size('T86_*.json', CHIP_CACHE), 'MI_MARGN': mean_size('MI_MARGN_*.json', CHIP_CACHE),
            'MI_INDEX': mean_size('MI_INDEX_*.json', MI_INDEX_CACHE)}

    def cost(parts):
        n = sum(len(days) for _, days in parts)
        mb = sum(len(days) * size[ds] for ds, days in parts) / 1e6
        return {'requests': n, 'hours_at_3s': round(n * SECONDS_PER_REQUEST / 3600, 2), 'megabytes': round(mb, 1),
                'by_dataset': {ds: len(days) for ds, days in parts}}

    stage_1 = [('T86', sample)]
    stage_2 = [('T86', needed(history, set(sample))), ('MI_MARGN', history)]
    stage_3 = [('MI_INDEX', post), ('T86', post_chip), ('MI_MARGN', post_chip)]
    other = {'TWT49U ranges (2022, after 2026-07-28)': 2, 'MOPS monthly revenue summary pages (2 markets x 57 months, values only)': 114,
             'MOPS FY2026Q1-Q2 filings (pit_v3 ledger rate ~890 per quarter)': 1780,
             'TPEx quotes + institutional + margin, per day since 2022 (endpoints to be confirmed)': 3 * (len(sessions) + len(post))}
    reuse_total = 3 * len(sessions)
    reused = len(sessions) + 2 * len([d for d in sessions if d in cached_chip])
    estimate = {
        'original_estimate': {'requests': 3000, 'basis': 'about 1,000 sessions x 3 datasets, without checking the caches'},
        'calendar': {'cached_official_sessions_since_2022': len(sessions), 'fit_split_sessions': len(fit),
                     'post_cache_weekdays_upper_bound': len(post)},
        'reuse': {'needed_twse_files_2022_to_2026_07': reuse_total, 'already_cached': reused, 'share': round(reused / reuse_total, 3)},
        'endpoint_capabilities': {
            'T86': 'per day only (month query rejected, probe t86_month_query); history from 2012-05-02',
            'MI_MARGN': 'per day only (probe mi_margn_month_query)',
            'MI_INDEX': 'per day only; cached 2021-12-30..2026-07-28',
            'TWT49U': 'date range in one request (cached file TWT49U_20230103_20260728.json)',
            'TWSE OpenAPI': 'MI_INDEX / MI_MARGN latest day only, no parameters, no T86 (probe twse_openapi_catalog)',
            'TPEx OpenAPI': 'quotes / 3insti / margin latest day only, no parameters (probe tpex_openapi_catalog)',
            'MOPS monthly revenue': 'one summary page per market and month, no announcement time (probe mops_monthly_revenue_summary)'},
        'plans': {
            'A_minimal': {**cost(stage_1), 'purpose': 'measure MySQL foreign/trust accuracy inside the fit split (decides wrong_flows_in_fit)',
                          'unblocks': ['retrain criterion wrong_flows_in_fit: UNKNOWN -> measured (sample)'],
                          'not_unblocked': ['training_data_end', 'model contract', 'OOS', 'fundamentals', 'TPEx']},
            'B_staged': {'stage_1': cost(stage_1), 'stage_2_training_and_research_history': cost(stage_2),
                         'stage_3_after_2026_07_28': cost(stage_3), 'stage_4_other': other,
                         'total_stages_1_to_3': cost(stage_1 + stage_2 + stage_3),
                         'unblocks': ['certified flows and margin 2022..2026 for retraining and research',
                                      'calendar / prices / flows after 2026-07-28 for the OOS clock and live Daily checks'],
                         'not_unblocked': ['monthly revenue timing', 'FY2026 filing dates (stage 4)', 'TPEx (stage 4)', 'OOS sample length']},
            'C_complete': {**cost(stage_1 + stage_2 + stage_3), 'plus_other': other,
                           'requests_total': cost(stage_1 + stage_2 + stage_3)['requests'] + sum(other.values())}},
        'recommended': 'B: run stage 1 and stage 3 now (on approval); stage 2 only together with an approved retraining; stage 4 per data owner',
        'probe_requests_used': len(probes)}
    write(out('download_request_estimate.json'), estimate)
    (out('optimized_download_plan.md')).write_text(plan_markdown(estimate, sample), encoding='utf-8')
    print({k: v.get('requests', v.get('total_stages_1_to_3', {}).get('requests')) for k, v in estimate['plans'].items()}, estimate['reuse'])


def plan_markdown(e, sample):
    p = e['plans']
    b = p['B_staged']
    return f"""# Optimized Official Data Download Plan — 2026-10-23

Not executed. Owner approval is needed before any batch below runs.

## Why fewer than {e['original_estimate']['requests']:,} requests
- MI_INDEX (prices, calendar) is already cached for 2021-12-30..2026-07-28 ({e['calendar']['cached_official_sessions_since_2022']} sessions since 2022): no historical price request is needed.
- T86 and MI_MARGN for 2025-12-15..2026-07-28 and 2026-09-28..10-21 are cached with SHA-256.
- Reuse of the TWSE files needed for 2022..2026-07: {e['reuse']['already_cached']:,} of {e['reuse']['needed_twse_files_2022_to_2026_07']:,} ({e['reuse']['share']:.0%}).
- The retraining question needs only a sample, not the full history.

## Endpoint capabilities (5 probe requests)
""" + '\n'.join(f'- {k}: {v}' for k, v in e['endpoint_capabilities'].items()) + f"""

No official batch or range download exists for T86 / MI_MARGN / MI_INDEX history; one request per session is the minimum.

## Plans
| Plan | Requests | Hours at 3 s | MB | Purpose |
|---|---:|---:|---:|---|
| A minimal | {p['A_minimal']['requests']} | {p['A_minimal']['hours_at_3s']} | {p['A_minimal']['megabytes']} | {p['A_minimal']['purpose']} |
| B stage 1 (= A) | {b['stage_1']['requests']} | {b['stage_1']['hours_at_3s']} | {b['stage_1']['megabytes']} | fit-split flow accuracy |
| B stage 2 | {b['stage_2_training_and_research_history']['requests']} | {b['stage_2_training_and_research_history']['hours_at_3s']} | {b['stage_2_training_and_research_history']['megabytes']} | certified T86 + MI_MARGN 2022-01-03..2025-12-12 (retraining, research) |
| B stage 3 | {b['stage_3_after_2026_07_28']['requests']} | {b['stage_3_after_2026_07_28']['hours_at_3s']} | {b['stage_3_after_2026_07_28']['megabytes']} | MI_INDEX + T86 + MI_MARGN 2026-07-29..{LATEST} (weekday upper bound) |
| B stages 1-3 | {b['total_stages_1_to_3']['requests']} | {b['total_stages_1_to_3']['hours_at_3s']} | {b['total_stages_1_to_3']['megabytes']} | |
| C complete | {p['C_complete']['requests_total']} | — | — | B plus TPEx, MOPS FY2026, monthly revenue pages (stage 4 estimates) |

Stage 4 estimates: """ + '; '.join(f'{k}: {v}' for k, v in b['stage_4_other'].items()) + f"""

## Recommendation
{e['recommended']}. Stage 1 + 3 = {b['stage_1']['requests'] + b['stage_3_after_2026_07_28']['requests']} requests (about {round((b['stage_1']['requests'] + b['stage_3_after_2026_07_28']['requests']) * 3 / 60)} minutes).

Stage 1 sessions ({len(sample)}): {', '.join(sample)}

## Batch procedure (for the approved run)
1. Reuse `scripts/daily_selection_recovery.py` `fetch_raw` (skips cached files, writes URL / retrieval time beside each file) and `parse_chip`; MI_INDEX through `core.research.sources.twse`.
2. One batch per dataset and month; a progress file `progress_<dataset>.json` records each session as OK / NON_TRADING / FAILED(n); a rerun resumes from it.
3. At most 3 attempts per session (3 s, 6 s, 12 s); stop the batch after 5 consecutive failures and report the remaining dates.
4. Validate `stat`, payload date = requested date and schema before keeping a file; mismatches are quarantined, never kept.
5. Official files stay in `artifacts/official_*_cache/` (git-ignored); MySQL is only read for reconciliation; nothing is written to the production DB.
6. After each stage: rebuild the manifest (per-file SHA-256), reconcile per stock and date with `match_table`, and grade DATA_PASS / DATA_FAIL / DATA_BLOCKED.
"""


MIN_OOS_SESSIONS = 252  # existing policy, not lowered
# Periods whose native-strategy results or data were already read (contaminated for native OOS).
READ_PERIODS = [
    ('2022-01-03', '2026-05-20', 'model training data of the seven native models (fit 2022-01-03..2025-07-30, test 2025-07-31..2026-05-20)'),
    ('2025-06-02', '2026-06-26', 'native historical backtests (identity_matrix_final.json, family:native:*)'),
    ('2026-05-21', '2026-07-28', 'native7 diagnostic replays (native7 reproduction_manifest window)'),
    ('2026-05-21', '2026-07-20', 'daily_selection_recovery parity dates'),
    ('2026-10-13', '2026-10-20', 'daily_selection_recovery shadow dates'),
]
OTHER_FAMILY_PERIODS = [
    ('2023-01-03', '2025-12-31', 'Day3 train/validation (other family; factor_validation_20260910_v1)'),
    ('2026-01-01', '2026-07-28', 'Day3 strict OOS reserved for Day3 (not read by Day3; partly read by native7)'),
    ('2026-07-29', '2026-10-14', 'G2/G3 Frozen Candidate fresh OOS ledger (its own OOS)'),
]


def clean_sessions(sessions, periods):
    """Sessions outside every read period: the only ones that may count as OOS."""
    return [d for d in sessions if not any(a <= d <= b for a, b, _ in periods)]


def date_after_sessions(start, count, sessions, ratio):
    """Date by which `count` clean sessions exist from `start`: known sessions first, then weekdays x observed session ratio."""
    known = [d for d in sessions if d >= start]
    if len(known) >= count:
        return known[count - 1], 'official calendar'
    remaining = count - len(known)
    tail = max(known[-1] if known else start, start)
    weekdays = pd.bdate_range(pd.Timestamp(tail) + pd.Timedelta(days=1), periods=int(remaining / ratio) + 1)
    return weekdays[-1].strftime('%Y-%m-%d'), f'estimate: {len(known)} known sessions + weekdays x {ratio:.3f}'


def oos():
    sessions, _ = official_sessions()
    chips = read(PREV / 'official_chip_manifest.json')['coverage']
    later = sorted({c['date'] for c in chips if c['stat'] == 'OK' and c['date'] > sessions[-1]})
    known = sorted(set(sessions) | set(later))
    hist = [d for d in sessions if '2022-01-03' <= d <= '2025-12-31']
    ratio = len(hist) / len(pd.bdate_range('2022-01-03', '2025-12-31'))
    clean = clean_sessions(known, READ_PERIODS)
    # The cached calendar has a gap (2026-07-29..09-25 not fetched); count from the first unread day by weekdays, and add back
    # the known sessions that were read later (2026-10-13..20) because they cannot count.
    a_start = '2026-07-29'
    read_later = [d for d in known if d >= a_start and d not in clean]
    a_date, a_basis = date_after_sessions(a_start, MIN_OOS_SESSIONS + len(read_later), [], ratio)
    b_start = (pd.Timestamp(LATEST) + pd.offsets.BDay(1)).strftime('%Y-%m-%d')
    b_date, b_basis = date_after_sessions(b_start, MIN_OOS_SESSIONS, [], ratio)
    pre = [d for d in sessions if '2022-01-03' <= d <= '2025-06-01']
    result = {
        'minimum_sessions': MIN_OOS_SESSIONS, 'session_ratio_2022_2025': round(ratio, 4),
        'read_periods_native': [{'from': a, 'to': b, 'what': w} for a, b, w in READ_PERIODS],
        'other_family_periods': [{'from': a, 'to': b, 'what': w} for a, b, w in OTHER_FAMILY_PERIODS],
        'scenarios': {
            'A_keep_original_models': {
                'eligible': False, 'reason': 'all seven models are MODEL_BLOCKED and six are RETRAIN_REQUIRED; training_data_end unknown',
                'if_certified': {'first_unread_session': a_start, 'read_sessions_after_start': read_later,
                                 'date_252_reached': a_date, 'basis': a_basis,
                                 'excluded': '2026-05-21..07-28 and 2026-10-13..20 were read; only later unread sessions count'}},
            'B_retrain_latest_cutoff': {
                'eligible': 'after approval', 'training_data_end': f'{LATEST} or the last certified session after download stage 3',
                'oos_start': b_start, 'date_252_reached': b_date, 'basis': b_basis,
                'requires': ['owner approval to retrain', 'certified flows (download stage 2)', 'pre-registered OOS start and metrics']},
            'C_retrain_earlier_cutoff': {
                'eligible': False,
                'reason': 'every session since 2022-01-03 is either model training data or read by native backtests/diagnostics; '
                          f'the largest unread-by-backtests stretch before 2025-06-02 ({len(pre)} sessions from 2022-01-03) was still '
                          'model training data, and the period in which the native rules and parameters were designed is not recorded',
                'clean_sessions_before_2026_07_29': len([d for d in clean if d < '2026-07-29'])}},
        'not_allowed': ['relabel the original 20% test split as OOS (it set the reported accuracy)',
                        'count diagnostic replay or parity dates', 'use dates before 2022-01-03 (no DB data)', 'lower the 252-session minimum']}
    write(out('oos_feasibility.json'), result)
    print({k: v.get('date_252_reached', v.get('if_certified', {}).get('date_252_reached')) for k, v in result['scenarios'].items()})


BLOCKED = [
    ('MODEL_TRAINING_DATA_END', 'all 7 models', 'UNPROVABLE', 'no log, snapshot or metadata; DB changed after training',
     'record training_data_end in a retrained model', 'retraining (owner)'),
    ('MODEL_FEATURE_CONTRACT', '6 models RETRAIN_REQUIRED, hybrid RETRAIN_RECOMMENDED', 'OWNER_DECISION',
     'retrain_decision.json: definitions differ (PROVEN) and top-3 changes', 'approve retraining with certified data', 'retraining run'),
    ('INSTITUTIONAL_FLOWS_FIT_SPLIT', 'all 7 (training input)', 'DATA_REPAIRABLE', 'MySQL flows 2022..2025 never reconciled',
     'download plan B stage 1 (44 requests) to measure, stage 2 to certify', '2 min / 1.6 h'),
    ('INSTITUTIONAL_FLOWS_AFTER_2026_07_28', 'all 7', 'DATA_REPAIRABLE', 'no official T86/MI_MARGN for 2026-07-29..09-25',
     'download plan B stage 3', '8 min'),
    ('CALENDAR_PRICES_AFTER_2026_07_28', 'all 7', 'DATA_REPAIRABLE', 'MI_INDEX cache ends 2026-07-28', 'download plan B stage 3', 'included'),
    ('MYSQL_FLOWS_PRODUCTION', 'live Daily', 'OWNER_DECISION', 'whole-day zeros stored in MySQL', 'approve staged DB repair (P-3)', 'DB change window'),
    ('FUNDAMENTALS_FY2026', 'growth_momentum_breakout, quality_growth, quality_value_low_volatility', 'DATA_REPAIRABLE',
     'publication dates only to FY2025', 'MOPS FY2026 filings (stage 4, ~1,780 requests)', '1.5 h'),
    ('MONTHLY_REVENUE_TIMING', 'growth_momentum_breakout, quality_growth', 'UNPROVABLE',
     'official summary pages have values but no announcement time (probe)', 'find a timestamped official source', 'unknown'),
    ('TPEX_UNIVERSE', 'live Daily', 'DATA_REPAIRABLE', 'no official TPEx cache', 'stage 4 TPEx per-day endpoints', '~5 h'),
    ('OOS_252_SESSIONS', 'all 7', 'WAIT_FOR_SAMPLES', 'oos_feasibility.json', '252 clean sessions after a certified cutoff', 'see oos_feasibility.json'),
]


def classify():
    rows = [dict(zip(('item', 'affects', 'class', 'evidence', 'unblock_condition', 'effort'), r)) for r in BLOCKED]
    pd.DataFrame(rows).to_csv(out('blocked_classification.csv'), index=False, encoding='utf-8-sig')
    (out('owner_proposals.md')).write_text(PROPOSALS, encoding='utf-8')
    print(pd.DataFrame(rows).groupby('class').size().to_dict())


PROPOSALS = """# Owner Proposals — 2026-10-23 (not executed)

## P-1 Credential rotation (MySQL `trader`)
Commit `b1c33c8` once stored `MYSQL_PASSWORD` in `docker-compose.yaml`; HEAD reads it from the environment. The value is not reproduced here.
1. Create a new password in the secret store; never in Git.
2. `ALTER USER 'trader'@'%' IDENTIFIED BY <new>` in a maintenance window (DBA).
3. Update `.env` / deployment secrets; restart web, scheduler and LINE workers; check `/health` and one scheduler dry-run.
4. Verify the old password is rejected; list other hosts or tools that used it.
5. Git history is not rewritten; if the owner wants it purged, plan a coordinated history rewrite separately (force-push and every clone affected).

## P-2 Retraining (requires approval)
- Data: certified T86 / MI_MARGN (download stages 1-3), official prices, TWSE stocks only, PIT fundamentals; no NULL -> 0 for missing flows.
- Features: the shared core (`build_feature_history`) plus `build_multi_factor_matrix` on the same population used in Daily.
- Record `training_data_end`, code commit, data manifest SHA-256 and the train/test boundary in the model file.
- Labels: drop rows without a full horizon instead of labelling them 0; purge `look_ahead_days` sessions at the train/test boundary.
- Pre-register the OOS start (next session after `training_data_end`), metrics and the 252-session minimum before training.
- Keep the current pickles unchanged beside the new ones until certified.

## P-3 Production DB repair (requires approval)
- Load official T86 / MI_MARGN into a staging table; diff against `daily_market_data` per stock and date; review the diff.
- Update only mismatching rows, in a transaction, with a backup table and a rollback script; NULL where the official source has no row.

## P-4 Switch Daily to `recovered`
Conditions: retrained models certified; data gates PASS for the live window; parity PASS; at least 20 shadow sessions with no errors; owner sign-off.
"""


TESTS = ['test/test_model_data_validation.py', 'test/test_daily_selection_recovery.py', 'test/test_native_recovery.py',
         'test/characterization', 'test/test_run_daily.py', 'test/test_native22_engine_contracts.py', 'test/test_next_session_execution.py']


def tests():
    import xml.etree.ElementTree as ET
    from collections import Counter

    junit = out('test_junit.xml')
    proc = subprocess.run([sys.executable, '-m', 'pytest', *TESTS, '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    cases = [{'test': f"{c.get('classname')}::{c.get('name')}",
              'status': 'FAIL' if c.find('failure') is not None or c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'}
             for c in ET.parse(junit).getroot().iter('testcase')]
    write(out('test_results.json'), {'command': 'python -m pytest ' + ' '.join(TESTS), 'returncode': proc.returncode,
                                     'summary': proc.stdout.strip().splitlines()[-1], 'counts': dict(Counter(c['status'] for c in cases)), 'cases': cases})
    print(proc.returncode, proc.stdout.strip().splitlines()[-1])


def manifest():
    import platform

    write(out('reproduction_manifest.json'), {
        'change': 'openspec/changes/2026-10-23-validate-model-training-and-data', 'output_dir': OUT.relative_to(ROOT).as_posix(),
        'git_head': git('rev-parse', 'HEAD').strip(),
        'commands': [f'python scripts/validate_model_data.py {s}' for s in
                     ('baseline', 'provenance', 'features', 'decision', 'inventory', 'probe', 'plan', 'oos', 'classify', 'tests', 'manifest', 'verify')],
        'inputs': {'previous_manifest': digest(PREV / 'reproduction_manifest.json'),
                   'official_chips_snapshot': digest(PREV / 'research_snapshot_official_chips.parquet'),
                   'native7_research_snapshot': digest(NATIVE7 / 'research_snapshot.parquet'),
                   'fundamental_pit_v3_records': digest(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet'),
                   'mi_index_cache': 'mi_index_cache_hashes.json', 'probe_cache': 'probe_log.json',
                   'mysql': 'read-only aggregates at run time (training_period_daily_stats.csv records what was read)'},
        'code': {p: digest(ROOT / p) for p in ('scripts/validate_model_data.py', 'test/test_model_data_validation.py')},
        'models': {k: v['sha256'] for k, v in read(OUT / 'baseline.json')['models'].items()},
        'environment': {'python': platform.python_version(), 'pandas': pd.__version__},
        'outputs': {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(OUT.rglob('*'))
                    if p.is_file() and '_superseded' not in p.parts and p.name != 'reproduction_manifest.json'},
        'official_requests_this_change': len(read(OUT / 'probe_log.json')), 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print(len(read(OUT / 'reproduction_manifest.json')['outputs']))


def verify():
    """Read-only guarantees of this change."""
    from config import Config

    start = read(OUT / 'baseline.json')
    assert {k: v['sha256'] for k, v in start['models'].items()} == {k: digest(ROOT / p) for k, (_, p) in models().items()}, 'MODEL_CHANGED'
    archived = archived_hashes()
    assert all(not v['changed'] for v in archived.values()), archived
    from scripts.validate_native22 import preservation
    kept = preservation()
    assert not kept['historical_changed'] and set(kept['protected_changed']) <= {'core/strategies/growth_momentum_breakout.py'}, kept
    touched = git('diff', '--name-only', start['git_head'], '--', 'jobs', 'core', 'config', 'execution', 'app', 'services',
                  'strategy_settings.json', 'ML_Data').split()
    assert not touched, 'PRODUCTION_CODE_CHANGED: ' + str(touched)
    assert Config.DAILY_FEATURE_MODE == 'legacy', 'DEFAULT_MODE'
    requests = len(read(OUT / 'probe_log.json'))
    assert requests <= PROBE_LIMIT, 'PROBE_LIMIT'
    decisions = read(OUT / 'retrain_decision.json')
    assert not decisions['executed_training'] and all(m['MODEL_STATUS'] == 'MODEL_BLOCKED' for m in decisions['models'].values())
    review = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json')['documents']['promotion_review']
    assert review['BROKER_ORDER_SUBMISSION'] == 'DISABLED'
    write(out('verification.json'), {'status': 'PASS', 'models_unchanged': True, 'archived_outputs': archived, 'preservation': kept,
                                     'production_code_unchanged_since': start['git_head'], 'DAILY_FEATURE_MODE': 'legacy',
                                     'official_requests': requests, 'models_blocked': 7, 'training_executed': False,
                                     'mysql': 'SELECT only', 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('VERIFY PASS', requests)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('baseline', 'provenance', 'features', 'decision', 'inventory', 'probe', 'plan', 'oos', 'classify', 'tests', 'manifest', 'verify'))
    globals()[parser.parse_args().stage]()
