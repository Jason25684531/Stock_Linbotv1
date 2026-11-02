"""Screen, chart and diagnose completed research-mode runs. Every output is DIAGNOSTIC_ONLY.

Reads only sealed run checkpoints and the prepared raw bars; writes a report directory, never a ledger.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import strategy_search as search
from core.research.attribution import run_attribution
from core.research.d5_research_variant import raw_factor_panels
from core.research.research_mode import (D5_RESEARCH_ASSUMPTIONS_V5, D5_RESEARCH_STRATEGY, RESEARCH_ASSUMPTIONS_V6,
                                         assumptions_sha, registered_version)
from core.research.research_screen import screen
from scripts import research_workflow as workflow
from scripts import track_b_stats as stats
from scripts.research_mode_charts import write_equity_chart, write_factor_charts
from scripts.research_mode_inventory import build_inventory

RESEARCH = ROOT / 'outputs/research_mode'
MASTER = ROOT / 'outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv'
BENCHMARK = 'EW_UNIVERSE_RAW'  # equal-weight price return of every listed stock in the raw closing files; TAIEX and 0050 are not in the local cache
LABELS = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True, 'terms_status': 'RESEARCH_ASSUMPTIONS'}
HORIZONS = (20, 60)


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def benchmark_returns(bars):
    """Equal-weight daily price return of all stocks present on consecutive sessions (research-only market proxy)."""
    days = sorted(bars)
    values = {}
    for prev, day in zip(days, days[1:]):
        common = [asset for asset in bars[day] if asset in bars[prev] and bars[prev][asset]['close'] > 0]
        if common:
            values[pd.Timestamp(day)] = float(np.mean([bars[day][a]['close'] / bars[prev][a]['close'] - 1 for a in common]))
    return pd.Series(values).sort_index()


def load_runs(run_dir, family):
    """Read every configuration checkpoint; a missing manifest is a FAILED or interrupted configuration."""
    if workflow.verify_trials(workflow.RESEARCH_MODE_LEDGER) not in ('PASS', 'EMPTY'):
        raise ValueError('RESEARCH_LEDGER_UNVERIFIED')
    admitted = {row.get('experiment_id') for row in workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)}
    rows = []
    for folder in sorted(p for p in Path(run_dir).iterdir() if p.is_dir() and p.name.isdigit()):
        manifest, failure = folder / 'manifest.json', folder / 'failure.json'
        attempts = sorted(folder.glob('attempt_*/manifest.json'))
        if not manifest.is_file() and attempts:
            manifest = attempts[-1]  # a rerun after an interrupted attempt lands in an attempt_* subfolder
            folder = manifest.parent
        if manifest.is_file():
            record = _load(manifest)
            record['_tracks'] = None
            if record.get('status') == 'COMPUTABLE':
                # Re-verify the sealed checkpoint before screening: output hashes, ledger admission and reconciliation.
                expected = record.get('output_sha256') or {}
                if (set(expected) != {'targets.csv', 'base.json', 'stress.json'}
                        or any(workflow.sha256(folder / name) != digest for name, digest in expected.items())
                        or record.get('experiment_id') not in admitted):
                    record.update(status='UNVERIFIED_CHECKPOINT', reason='OUTPUT_SHA_OR_LEDGER_MISMATCH')
                else:
                    tracks = {name: _load(folder / f'{name}.json') for name in ('base', 'stress')}
                    if any(track.get('reconciliation', {}).get('status') != 'PASS' for track in tracks.values()):
                        record.update(status='UNVERIFIED_CHECKPOINT', reason='RECONCILIATION_NOT_PASS')
                    else:
                        record['_tracks'] = tracks
        elif failure.is_file():
            record = {**_load(failure), 'config': {'config_id': _load(failure).get('config_id'), 'strategy': None}, '_tracks': None}
        else:
            record = {'status': 'INTERRUPTED', 'reason': 'NO_MANIFEST', 'config': {'config_id': folder.name, 'strategy': None}, '_tracks': None}
        record['_family'], record['_folder'] = family, folder
        rows.append(record)
    return rows


def returns_of(track, initial_cash):
    daily = track['result']['daily']
    nav = pd.Series([row['nav'] for row in daily], index=pd.to_datetime([row['date'] for row in daily]))
    return nav / nav.shift(1).fillna(initial_cash) - 1, nav


def asset_pnl(track):
    """Per-stock profit from every cash flow in the ledger (fills, forced exits, dividend credits) plus closing marks."""
    pnl = {}
    for row in track['result']['ledger']:
        if row['kind'] == 'FILL':
            signed = -(row['notional'] + row['fee']) if row['side'] == 'buy' else row['notional'] - row['fee'] - row['tax']
        elif row['kind'] == 'FORCED_EXIT':
            signed = row['notional'] - row['fee'] - row['tax']
        elif row['kind'] == 'CASH_CREDIT':
            signed = row['amount']
        else:
            continue
        pnl[row['stock_id']] = pnl.get(row['stock_id'], 0.0) + signed
    last = track['result']['daily'][-1]
    for asset, shares in last['positions'].items():
        pnl[asset] = pnl.get(asset, 0.0) + shares * last['marks'][asset]
    for claim in last['stock_receivable']:
        pnl[claim['stock_id']] = pnl.get(claim['stock_id'], 0.0) + claim['shares'] * last['marks'][claim['stock_id']]
    for claim in last['cash_receivable_claims']:
        pnl[claim['stock_id']] = pnl.get(claim['stock_id'], 0.0) + claim['amount']
    return pnl


def turnover_of(track, nav):
    traded = sum(row['notional'] for row in track['result']['ledger'] if row['kind'] == 'FILL')
    years = max(len(nav) / 252, 1e-9)
    return float(traded / nav.mean() / years) if len(nav) else None


def build_result(record, bench_returns, regime):
    """Assemble the screen input from the stress (2x cost) track; base is kept for cost sensitivity."""
    tracks = record['_tracks']
    initial = record['config']['initial_cash']
    stress_returns, stress_nav = returns_of(tracks['stress'], initial)
    base_returns, base_nav = returns_of(tracks['base'], initial)
    aligned = pd.concat([stress_returns.rename('net'), bench_returns.rename('bench')], axis=1).dropna()
    excess = aligned['net'] - aligned['bench']
    pnl = asset_pnl(tracks['stress'])
    return {'status': 'COMPUTABLE', 'net_returns_2x': aligned['net'].to_numpy(), 'excess_returns_2x': excess.to_numpy(),
            'top5_assets_profit_share': stats.top_assets_share(pnl), 'best5pct_days_return_share': stats.best_days_share(aligned['net']),
            'regime': stats.regime_excess(excess, regime), 'excess_series': excess, 'base_returns': base_returns,
            'base': {'total_return': float(base_nav.iloc[-1] / initial - 1), 'sharpe': stats.sharpe(base_returns),
                     'max_drawdown': stats.max_drawdown(base_returns), 'turnover': turnover_of(tracks['base'], base_nav)},
            'stress': {'total_return': float(stress_nav.iloc[-1] / initial - 1), 'sharpe': stats.sharpe(stress_returns),
                       'max_drawdown': stats.max_drawdown(stress_returns), 'turnover': turnover_of(tracks['stress'], stress_nav)},
            'benchmark_total_return': float((1 + aligned['bench']).prod() - 1), 'sessions': int(len(aligned))}


def factor_tables(frames, membership, panels):
    """Research-only factor evidence on raw closes (price returns; dividends are not reinvested)."""
    close = frames['close']
    eligible = membership.reindex(index=close.index, columns=close.columns).fillna(False).astype(bool)
    forward = {h: (close.shift(-h) / close - 1).where(eligible) for h in HORIZONS}
    ic_rows, summary, quantile_rows, spread_rows, annual_rows, coverage_rows, persistence_rows, retention_rows, turnover_rows = ([] for _ in range(9))
    stacked = {}
    for factor_id, panel in panels.items():
        values = panel.where(eligible)
        rank = values.rank(axis=1, pct=True)
        stacked[factor_id] = rank.loc[rank.index >= '2023-01-03'].stack(future_stack=True)
        coverage_series = values.notna().sum(axis=1) / eligible.sum(axis=1).replace(0, np.nan)
        for h in HORIZONS:
            label = forward[h].where(rank.notna()).rank(axis=1, pct=True)
            valid = (rank.notna() & label.notna()).sum(axis=1) >= 30
            ic = rank.corrwith(label, axis=1).where(valid).loc[lambda s: (s.index >= '2023-01-03') & (s.index <= '2025-12-31')]
            ic_rows.extend({'factor_id': factor_id, 'horizon': h, 'asof_date': day, 'raw_ic': value, 'raw_rolling_ic': rolling}
                           for day, value, rolling in zip(ic.index, ic, ic.rolling(60, min_periods=20).mean()))
            finite = ic.dropna()
            summary.append({'factor_id': factor_id, 'horizon': h, 'raw_mean_ic': float(finite.mean()) if len(finite) else None,
                            'raw_icir': float(finite.mean() / finite.std(ddof=1)) if len(finite) > 1 and finite.std(ddof=1) > 0 else None})
            for year, part in finite.groupby(finite.index.year):
                annual_rows.append({'factor_id': factor_id, 'horizon': h, 'year': int(year), 'raw_mean_ic': float(part.mean())})
            coverage_rows.append({'factor_id': factor_id, 'horizon': h, 'average_coverage': float(coverage_series.loc[ic.index].mean())})
            bucket = np.ceil(rank * 5).clip(lower=1)
            fwd = forward[h]
            daily_q = {}
            for q in range(1, 6):
                daily_q[q] = fwd.where(bucket == q).mean(axis=1).loc[ic.index]
                quantile_rows.append({'factor_id': factor_id, 'horizon': h, 'quantile': q, 'mean_return': float(daily_q[q].mean())})
            spread_rows.extend({'factor_id': factor_id, 'horizon': h, 'asof_date': day, 'raw_q5_minus_q1': value}
                               for day, value in (daily_q[5] - daily_q[1]).dropna().items())
        lagged = rank.shift(20)
        autocorr = rank.corrwith(lagged, axis=1).loc[lambda s: s.index >= '2023-01-03'].dropna()
        persistence_rows.extend({'factor_id': factor_id, 'asof_date': day, 'rank_autocorrelation': value} for day, value in autocorr.items())
        top, top_lag = rank >= .8, lagged >= .8
        retention = (top & top_lag).sum(axis=1) / top.sum(axis=1).replace(0, np.nan)
        retention = retention.loc[retention.index >= '2023-01-03'].dropna()
        retention_rows.extend({'factor_id': factor_id, 'asof_date': day, 'top_n_retention': value} for day, value in retention.items())
        turnover_rows.append({'factor_id': factor_id, 'equal_weight_turnover': float(1 - retention.mean()) if len(retention) else None})
    correlation = pd.DataFrame(stacked).corr(method='spearman', min_periods=100)
    correlation_rows = [{'factor_id': a, 'other_factor_id': b, 'correlation': correlation.loc[a, b]} for a in correlation.index for b in correlation.columns]
    tables = {'ic_timeseries': pd.DataFrame(ic_rows), 'ic_summary': pd.DataFrame(summary), 'quantile_returns': pd.DataFrame(quantile_rows),
              'quantile_timeseries': pd.DataFrame(spread_rows), 'annual_results': pd.DataFrame(annual_rows), 'coverage': pd.DataFrame(coverage_rows),
              'rank_autocorrelation': pd.DataFrame(persistence_rows), 'top_n_retention': pd.DataFrame(retention_rows),
              'turnover': pd.DataFrame(turnover_rows), 'factor_correlation': pd.DataFrame(correlation_rows)}
    scoreboard = pd.DataFrame({'factor_id': list(panels), 'status': 'RESEARCH_ONLY'})
    return tables, scoreboard


def strategy_status(configs):
    statuses = [c['screen_status'] for c in configs]
    if 'RESEARCH_RUN_PASS_SCREEN' in statuses:
        return 'RESEARCH_RUN_PASS_SCREEN', f"{statuses.count('RESEARCH_RUN_PASS_SCREEN')} of {len(configs)} configurations pass r1-r5 under research assumptions (in-sample for archived Day3 parameters)."
    if 'RESEARCH_RUN_FAIL_SCREEN' in statuses:
        failed = [g for c in configs if c.get('gates') for g, ok in c['gates'].items() if not ok]
        common = pd.Series(failed).value_counts().index.tolist()[:2] if failed else []
        return 'RESEARCH_RUN_FAIL_SCREEN', f"{statuses.count('RESEARCH_RUN_FAIL_SCREEN')} computable configurations all fail the screen; most frequent failing gates: {', '.join(common) or 'n/a'}."
    undefined = sum(c['status'] == 'COMPUTABLE' for c in configs)
    reasons = pd.Series([c['reason'] for c in configs if c['status'] != 'COMPUTABLE']).value_counts()
    text = ('; '.join(f'{k} x{v}' for k, v in reasons.items())) or 'none blocked'
    if undefined:
        why = pd.Series([c.get('screen_reason') or c.get('reason') for c in configs if c['status'] == 'COMPUTABLE']).value_counts()
        detail = '; '.join(f'{k} x{v}' for k, v in why.items())
        return 'RESEARCH_NOT_COMPUTABLE', (f'{undefined} configurations completed both cost tracks but the registered screen could not be evaluated '
                                           f'({detail}: a concentration share is undefined when the per-stock profit total is not positive at 2x cost); blocked: {text}')
    return 'RESEARCH_NOT_COMPUTABLE', 'No configuration completed both cost tracks: ' + text


def conclusion(status, best, configs):
    if status == 'RESEARCH_RUN_PASS_SCREEN':
        return f"Passes r1-r5 at 2x cost (best {best['config_id']}, 2x Sharpe {best['values']['net_sharpe_2x']:.2f}); diagnostic only, certification requires official terms and the frozen 2026 OOS gate."
    if status == 'RESEARCH_RUN_FAIL_SCREEN':
        if best and best.get('values') and best['values']['net_sharpe_2x'] is not None and best['values']['net_sharpe_2x'] <= 0:
            return 'Fails: 2x-cost Sharpe is not positive; costs and turnover remove the raw signal.'
        if best and best.get('gates') and not best['gates'].get('r3', True) and all(best['gates'].get(g) for g in ('r1', 'r2')):
            return 'Fails: positive but not after deflating for the number of trials (r3); the edge is not distinguishable from selection.'
        return 'Fails one or more screening criteria; see gate table.'
    return 'Not computable under the local data: ' + '; '.join(sorted({c['reason'] for c in configs}))


def write_diagnostics(path, strategy, family, status, reason, configs, best, attribution, assumptions_sha256, forfeits, charts):
    lines = [f'# {strategy} — DIAGNOSTIC_ONLY', '',
             f'- family: {family}; research status: **{status}**', f'- reason: {reason}',
             f'- assumptions SHA-256: `{assumptions_sha256}`; held-event policy: FORFEIT_UNKNOWN_ENTITLEMENTS (downward bias)',
             f"- parameter fit scope: {'PER_DECISION_POINT_IN_TIME' if family == 'D5_VARIANT' else 'IN_SAMPLE_DEVELOPMENT_WINDOW (archived directions/weights fitted on 2023-2025)'}",
             f'- benchmark proxy: {BENCHMARK} (equal-weight raw-close price return of all listed stocks); regimes: previous-session proxy index above its 200-session mean = bull', '',
             '## Configurations', '',
             '| config | status | reason | base return | 2x return | 2x Sharpe | max DD (2x) | r1 | r2 | r3 | r4 | r5 | DSR |', '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for c in configs:
        g = c.get('gates') or {}
        v = c.get('values') or {}
        fmt = lambda x, p='.3f': (format(x, p) if isinstance(x, (int, float)) and x is not None and math.isfinite(x) else 'n/a')
        lines.append(f"| {c['config_id']} | {c['screen_status']} | {c.get('reason') or ''} | {fmt(c.get('base_total_return'))} | {fmt(c.get('stress_total_return'))} | "
                     f"{fmt(v.get('net_sharpe_2x'), '.2f')} | {fmt(c.get('stress_max_drawdown'))} | "
                     + ' | '.join(('PASS' if g.get(k) else 'FAIL') if k in g else 'n/a' for k in ('r1', 'r2', 'r3', 'r4', 'r5'))
                     + f" | {fmt(v.get('deflated_sharpe'))} |")
    lines += ['', '## Concentration, cost sensitivity, forfeits', '']
    if best:
        v = best['values']
        lines += [f"- best configuration (by 2x Sharpe): {best['config_id']}",
                  f"- top-5 stock profit share: {v['top5_assets_profit_share']:.3f}; best-5%-days return share: {v['best5pct_days_return_share']:.3f}",
                  f"- cost sensitivity: base Sharpe {best['base_sharpe']:.2f} -> 2x Sharpe {v['net_sharpe_2x']:.2f}; base turnover {best['base_turnover']:.2f}x NAV per year",
                  f"- regimes (2x excess): bull {v['regime']['bull']['status']} over {v['regime']['bull']['sessions']} sessions, bear {v['regime']['bear']['status']} over {v['regime']['bear']['sessions']} sessions",
                  f"- DSR trials: research ledger {best['research_trials']}, certification baseline 743, used {best['dsr_trials']}"]
    if attribution:
        lines.append(f"- attribution vs {BENCHMARK} proxy (2x track): alpha {attribution['alpha'] * 252:.3f}/yr (HAC p={attribution['alpha_p_hac']:.3f}), beta {attribution['beta']:.2f}, R² {attribution['r_squared']:.2f}, n={attribution['n_obs']}")
    if forfeits:
        lines.append(f"- forfeited entitlements (2x track, across computable configs): {forfeits['events']} events, {forfeits['value_share_mean']:.4f} mean share of final NAV; returns are understated by at most this amount")
    blocked = [c for c in configs if c['status'] == 'NOT_COMPUTABLE']
    if blocked:
        stocks = '; '.join(sorted({str(c.get('blocking_stock')) for c in blocked}))
        lines += ['', f"- selection bias: {len(blocked)} of {len(configs)} configurations stopped on a held event ({stocks}); "
                      'the surviving set excludes exactly the configurations that held those stocks, so its statistics are not a random sample of the grid and the bias direction is unknown.']
    lines += ['', '## Charts', ''] + [f'- `{c}`' for c in charts] + ['', '## Conclusion', '', conclusion(status, best, configs), '']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')


def build_report(output, day3_runs, d5_runs, prepared):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    market = _load(Path(prepared) / 'bars.json')
    bars = market['bars']
    bench_returns = benchmark_returns(bars)
    bench_close = (1 + bench_returns).cumprod()  # proxy index level for the 200-session regime rule
    regime = stats.regimes(bench_close)
    records = load_runs(day3_runs, 'DAY3') + load_runs(d5_runs, 'D5_VARIANT')
    computable = [r for r in records if r['status'] == 'COMPUTABLE']
    results = {id(r): build_result(r, bench_returns, regime) for r in computable}
    # deflated_sharpe works in per-session units: the variance across trials of the daily (not annualized) excess Sharpe.
    excess_sharpes = [stats.sharpe(res['excess_returns_2x'], periods=1) for res in results.values() if len(res['excess_returns_2x']) > 1]
    finite = [s for s in excess_sharpes if math.isfinite(s)]
    trial_variance = float(np.var(finite, ddof=1)) if len(finite) > 1 else 0.0
    research_trials = workflow.research_trials()
    rows, charts_written = [], []
    chart_root = output / 'charts'
    for record in records:
        config = record['config']
        family = record['_family']
        row = {'family': family, 'strategy': config.get('strategy'), 'config_id': config.get('config_id'),
               'status': record['status'], 'reason': record.get('reason'), 'assumptions_version': record.get('assumptions_version'),
               'parameter_fit_scope': record.get('parameter_fit_scope'), 'experiment_id': record.get('experiment_id'),
               'blocking_date': record.get('date'), 'blocking_stock': record.get('stock_id'), 'blocking_terms': record.get('missing_terms')}
        if record['status'] == 'COMPUTABLE':
            res = results[id(record)]
            verdict = screen(res, trial_sharpe_variance=trial_variance)
            forfeit = record.get('forfeits', {}).get('stress', {})
            row.update(screen_status=verdict['status'], candidate=verdict.get('candidate', False), gates=verdict.get('gates'),
                       values=verdict.get('values'), research_trials=verdict.get('research_trials'), dsr_trials=verdict.get('dsr_trials'),
                       base_total_return=res['base']['total_return'], base_sharpe=res['base']['sharpe'], base_max_drawdown=res['base']['max_drawdown'],
                       base_turnover=res['base']['turnover'], stress_total_return=res['stress']['total_return'], stress_sharpe=res['stress']['sharpe'],
                       stress_max_drawdown=res['stress']['max_drawdown'], stress_turnover=res['stress']['turnover'],
                       benchmark_total_return=res['benchmark_total_return'], sessions=res['sessions'],
                       forfeit_events=forfeit.get('events'), forfeit_share_of_final_nav=forfeit.get('forfeited_share_of_final_nav'),
                       forced_exits=forfeit.get('forced_exits'), forced_exit_notional_share=forfeit.get('forced_exit_notional_share_of_final_nav'),
                       screen_reason=verdict.get('reason'))
            if verdict['status'] == 'NOT_COMPUTABLE':
                row['screen_status'], row['reason'] = 'NOT_COMPUTABLE', verdict['reason']
            chart = chart_root / family.lower() / f"{config['config_id']}.png"
            if not chart.exists():
                write_equity_chart(chart, {'status': 'COMPUTABLE', 'tracks': record['_tracks']}, label='DIAGNOSTIC_ONLY',
                                   assumptions_sha=record['assumptions_sha256'])
            row['chart'] = str(chart.relative_to(ROOT).as_posix())
            charts_written.append(row['chart'])
            row['_excess'] = res['excess_series']
            row['_returns_2x'] = pd.Series(res['net_returns_2x'], index=res['excess_series'].index)
        else:
            row.update(screen_status='NOT_COMPUTABLE', candidate=False, gates=None, values=None)
        rows.append(row)
    # strategy level
    inventory = build_inventory()
    strategies = []
    for item in inventory['strategies'] + [{'strategy': D5_RESEARCH_STRATEGY, 'family': 'D5_VARIANT'}]:
        name, family = item['strategy'], item['family']
        configs = [r for r in rows if r['strategy'] == name]
        entry = {'strategy': name, 'family': family, **LABELS,
                 'assumptions_sha256': assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V5 if family == 'D5_VARIANT' else RESEARCH_ASSUMPTIONS_V6)}
        if family in ('DAY3', 'D5_VARIANT'):
            status, reason = strategy_status(configs) if configs else ('RESEARCH_NOT_COMPUTABLE', 'No run checkpoint found for this strategy.')
            counts = {'total': len(configs), 'computable': sum(c['status'] == 'COMPUTABLE' for c in configs),
                      'not_computable': sum(c['status'] == 'NOT_COMPUTABLE' for c in configs),
                      'failed_or_interrupted': sum(c['status'] in ('FAILED', 'INTERRUPTED') for c in configs),
                      'pass_screen': sum(c['screen_status'] == 'RESEARCH_RUN_PASS_SCREEN' for c in configs)}
            scored = [c for c in configs if c.get('values')]
            best = max(scored, key=lambda c: c['values']['net_sharpe_2x']) if scored else None
            attribution = None
            if best is not None:
                aligned = pd.concat([best['_returns_2x'].rename('s'), bench_returns.rename('b')], axis=1).dropna()
                attribution = run_attribution(best['config_id'], aligned['s'], aligned['b'])
            forfeits = None
            shares = [c['forfeit_share_of_final_nav'] for c in scored if c.get('forfeit_share_of_final_nav') is not None]
            if scored:
                forfeits = {'events': int(sum(c.get('forfeit_events') or 0 for c in scored)), 'value_share_mean': float(np.mean(shares)) if shares else 0.0}
            charts = [c['chart'] for c in configs if c.get('chart')]
            diag = output / 'diagnostics' / f'{name}.md'
            write_diagnostics(diag, name, family, status, reason, configs, best, attribution, entry['assumptions_sha256'], forfeits, charts)
            entry.update(status=status, reason=reason, configurations=counts, parameter_fit_scope='PER_DECISION_POINT_IN_TIME' if family == 'D5_VARIANT' else 'IN_SAMPLE_DEVELOPMENT_WINDOW',
                         best_configuration=({'config_id': best['config_id'], **best['values'], 'base_sharpe': best['base_sharpe'],
                                              'base_total_return': best['base_total_return'], 'stress_total_return': best['stress_total_return'],
                                              'stress_max_drawdown': best['stress_max_drawdown'], 'gates': best['gates']} if best else None),
                         attribution=attribution, forfeits=forfeits, charts=charts, diagnostics=str(diag.relative_to(ROOT).as_posix()),
                         candidates=[c['config_id'] for c in configs if c.get('candidate')],
                         failure_reasons={k: int(v) for k, v in pd.Series([c['reason'] for c in configs if c['status'] != 'COMPUTABLE']).value_counts().items()})
        else:
            entry.update(status=item.get('status') or item.get('execution_disposition'), reason=item.get('reason') or 'Deferred by scope in this change; no training or backtest was run.',
                         configurations=None, charts=[], candidates=[])
        strategies.append(entry)
    candidates = [{'strategy': r['strategy'], 'config_id': r['config_id'], 'family': r['family'], 'research_only': True,
                   'label': 'DIAGNOSTIC_ONLY', 'parameter_fit_scope': r['parameter_fit_scope'], **r['values'], 'gates': r['gates']}
                  for r in rows if r.get('candidate')]
    public_rows = [{k: v for k, v in r.items() if not k.startswith('_')} for r in rows]
    (output / 'screen_results.json').write_text(json.dumps({**LABELS, 'trial_sharpe_variance': trial_variance, 'research_trials': research_trials,
                                                             'certification_baseline_trials': 743, 'rows': public_rows}, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    with (output / 'screen_results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        fields = ['family', 'strategy', 'config_id', 'status', 'reason', 'screen_status', 'candidate', 'base_total_return', 'base_sharpe', 'base_max_drawdown',
                  'base_turnover', 'stress_total_return', 'stress_sharpe', 'stress_max_drawdown', 'stress_turnover', 'benchmark_total_return',
                  'forfeit_events', 'forfeit_share_of_final_nav', 'forced_exits', 'forced_exit_notional_share', 'blocking_date', 'blocking_stock', 'blocking_terms', 'parameter_fit_scope', 'chart']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(public_rows)
    (output / 'research_candidates.json').write_text(json.dumps({**LABELS, 'status': 'AVAILABLE' if candidates else 'EMPTY',
                                                                  'reason': None if candidates else 'No configuration passed all five preregistered criteria.',
                                                                  'count': len(candidates), 'candidates': candidates}, ensure_ascii=False, indent=1), encoding='utf-8')
    # factor charts
    factor_dir = output / 'factor_charts'
    if not factor_dir.exists():
        quotes = pd.DataFrame([{'trade_date': day, 'stock_id': stock, **{f: v[f] for f in ('open', 'high', 'low', 'close', 'volume', 'amount')}}
                               for day, daily in bars.items() for stock, v in daily.items()])
        quotes['trade_date'] = pd.to_datetime(quotes['trade_date'])
        frames = {f: quotes.pivot(index='trade_date', columns='stock_id', values=f).sort_index() for f in ('open', 'high', 'low', 'close', 'volume', 'amount')}
        universe = pd.DataFrame(_load(Path(prepared) / 'universe.json'))
        membership = universe.pivot(index='asof_date', columns='asset_id', values='member').fillna(False).astype(bool)
        membership.index = pd.to_datetime(membership.index)
        panels = {**search.compute_factor_panels(frames, MASTER), **raw_factor_panels(frames)}
        tables, scoreboard = factor_tables(frames, membership, panels)
        write_factor_charts(factor_dir, tables=tables, scoreboard=scoreboard, label='DIAGNOSTIC_ONLY', assumptions_sha=assumptions_sha(RESEARCH_ASSUMPTIONS_V6))
        tables['ic_summary'].to_csv(factor_dir / 'ic_summary.csv', index=False)
        tables['factor_correlation'].to_csv(factor_dir / 'factor_correlation.csv', index=False)
    factor_charts = sorted(str(p.relative_to(ROOT).as_posix()) for p in factor_dir.rglob('*.png'))
    batches = {}
    for family, label in (('DAY3', 'day3'), ('D5_VARIANT', 'd5_variant')):
        part = [r for r in rows if r['family'] == family]
        batches[label] = {'total': len(part), 'computable': sum(r['status'] == 'COMPUTABLE' for r in part),
                          'not_computable': sum(r['status'] == 'NOT_COMPUTABLE' for r in part),
                          'failed_or_interrupted': sum(r['status'] in ('FAILED', 'INTERRUPTED', 'UNVERIFIED_CHECKPOINT') for r in part),
                          'pass_screen': sum(r['screen_status'] == 'RESEARCH_RUN_PASS_SCREEN' for r in part),
                          'reasons': {k: int(v) for k, v in pd.Series([r['reason'] for r in part if r['status'] != 'COMPUTABLE']).value_counts().items()}}
    track = {**LABELS, 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6),
             'assumptions': {'DAY3': {'version': 'V6', 'sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6)},
                             'D5_VARIANT': {'version': 'D5-V5', 'sha256': assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V5)}},
             'held_event_policy': 'FORFEIT_UNKNOWN_ENTITLEMENTS', 'development_end': '2025-12-31',
             'benchmark': {'proxy': BENCHMARK, 'basis': 'equal-weight daily price return of all stocks in the same MI_INDEX files (small caps overweighted, dividends excluded); TAIEX and 0050 are not in the local raw cache'},
             'research_trials': research_trials, 'certification_baseline_trials': 743, 'dsr_trials': max(research_trials, 743),
             'trial_sharpe_variance': trial_variance, 'batches': batches, 'candidates': candidates,
             'candidate_status': 'AVAILABLE' if candidates else 'EMPTY', 'strategies': strategies,
             'strategy_count': len(strategies), 'factor_charts': factor_charts, 'equity_charts': len(charts_written),
             'report_dir': str(output.relative_to(ROOT).as_posix()),
             'certification_track_note': 'Research results never change certification status; all 22 strategies remain as the certification matrix states.'}
    (output / 'research_track.json').write_text(json.dumps(track, ensure_ascii=False, indent=1, allow_nan=False, default=str), encoding='utf-8')
    return track


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=RESEARCH / 'report_v6')
    parser.add_argument('--day3-runs', type=Path, default=RESEARCH / 'day3_runs_v6')
    parser.add_argument('--d5-runs', type=Path, default=RESEARCH / 'd5_runs_v5')
    parser.add_argument('--prepared', type=Path, default=RESEARCH / 'prepared_v6')
    args = parser.parse_args()
    result = build_report(args.output, args.day3_runs, args.d5_runs, args.prepared)
    print(json.dumps({'batches': result['batches'], 'candidates': result['candidate_status'], 'strategies': result['strategy_count']}))
