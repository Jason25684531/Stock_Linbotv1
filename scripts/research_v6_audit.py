"""Read-only integrity audit of the delivered V6 / D5-V5 research batch (Phase 7). Every output is DIAGNOSTIC_ONLY.

Reads the sealed run checkpoints, prepared inputs, registry, research ledger, report and snapshot; writes only to a new audit
directory. It recounts forfeits, forced exits and blocked configurations, reruns the independent reconciliation on every track,
labels each forced exit's fill basis, bounds the effect of the forced-exit and forfeit assumptions on returns, risk, r1-r5 and
ranking, decomposes r3 / r4, and checks that every artefact names the same assumption version and SHA.
"""
import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest.costs import CostModel
from core.research.repair_accounting import reconcile
from core.research.research_mode import REGISTERED_ASSUMPTIONS, assumptions_registration, assumptions_sha
from core.research.research_screen import screen
from scripts import research_mode_report as report
from scripts import research_workflow as workflow
from scripts import track_b_stats as stats

RESEARCH = ROOT / 'outputs/research_mode'
RUNS = {'DAY3': RESEARCH / 'day3_runs_v6', 'D5_VARIANT': RESEARCH / 'd5_runs_v5'}
VERSIONS = {'DAY3': 'V6', 'D5_VARIANT': 'D5-V5'}
PREPARED = RESEARCH / 'prepared_v6'
SIGNALS = {'DAY3': RESEARCH / 'day3_signals_v6', 'D5_VARIANT': RESEARCH / 'd5_signals_v5'}
REPORT = RESEARCH / 'report_v6'
SNAPSHOT = ROOT / 'outputs/research_workflow/research_mode_20261102_v3'
DELIVERED = {'computable': 230, 'blocked': 118, 'forfeits_stress': 2537, 'forced_exits_stress': 212}
FILL_BASIS = 'RESEARCH_ASSUMPTION_NO_FILL_EVIDENCE'  # the local cache has closing bars, never a per-trade record
UNCERTIFIED = 'UNCERTIFIED_EXECUTION_ASSUMPTION'
LABELS = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
          'executable_performance': False, 'note': 'Research-assumption accounting; not executable trading performance.'}
RTOL, ATOL = 1e-9, 1e-12
# Scenarios on the 2x track: (forfeit add-back, forced-exit price field or None).
SCENARIOS = {'DELIVERED': (False, None), 'FORFEIT_ADDBACK': (True, None), 'EXIT_AT_OPEN': (False, 'open'),
             'EXIT_AT_CLOSE': (False, 'close'), 'EXIT_AT_LOW': (False, 'low'), 'EXIT_AT_HIGH': (False, 'high'),
             'FAVORABLE': (True, 'high')}  # EXIT_AT_LOW is the adverse bound
SENSITIVITY = ('743_batch', '500_batch', '230_batch', 'neff_batch', '743_strategy', 'psr_1')


def _sha(path):
    return workflow.sha256(path)


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _close(a, b):
    return a is None and b is None or (a is not None and b is not None and math.isclose(a, b, rel_tol=RTOL, abs_tol=ATOL))


def manifest_folder(folder):
    """The sealed manifest, or the latest attempt_* rerun (the report's own rule)."""
    if (folder / 'manifest.json').is_file():
        return folder
    attempts = sorted(folder.glob('attempt_*/manifest.json'))
    return attempts[-1].parent if attempts else None


def configurations():
    for family, run_dir in RUNS.items():
        for folder in sorted(p for p in run_dir.iterdir() if p.is_dir() and p.name.isdigit()):
            yield family, folder, manifest_folder(folder)


def read_set():
    """Every file this audit reads, hashed before and after the run to prove it wrote nothing back."""
    files = [p for p in PREPARED.glob('*.json')] + [p for d in SIGNALS.values() for p in d.glob('*.json')] + [workflow.REGISTRY, workflow.RESEARCH_MODE_LEDGER,
                                                    workflow.RESEARCH_MODE_LEDGER.with_suffix('.head.json'),
                                                    REPORT / 'screen_results.json', REPORT / 'research_track.json',
                                                    SNAPSHOT / 'research_track.json']
    files += [p for p in SNAPSHOT.iterdir() if p.is_file()] + list((REPORT / 'charts').rglob('*.png')) + list((REPORT / 'diagnostics').glob('*.md'))
    for _, _, folder in configurations():
        if folder is not None:
            files += [folder / 'manifest.json'] + [folder / name for name in _load(folder / 'manifest.json').get('output_sha256', {})]
    return {Path(p).relative_to(ROOT).as_posix(): _sha(p) for p in sorted(set(files))}


# ---------------------------------------------------------------- forced exits

def exit_rows(track, bars, days, cost, family, config_id, track_name):
    """One row per FORCED_EXIT with accounting checks, resumption-day market facts and the fill basis."""
    result, rows = track['result'], []
    daily = {row['date']: i for i, row in enumerate(result['daily'])}
    actions = {(e['date'], e['stock_id']): e for e in result['ledger'] if e['kind'] == 'ACTION' and e.get('action_type') == 'reference_change_exit'}
    for event in (e for e in result['ledger'] if e['kind'] == 'FORCED_EXIT'):
        day, asset = event['date'], event['stock_id']
        action = actions.get((day, asset), {})
        prev = result['daily'][daily[day] - 1] if daily.get(day, 0) > 0 else {'positions': {}, 'stock_receivable': []}
        held = prev['positions'].get(asset, 0) + sum(c['shares'] for c in prev['stock_receivable'] if c['stock_id'] == asset)
        bar = bars.get(day, {}).get(asset)
        ref = action.get('reference_price')
        refund = 'refund' if '退還' in str(event.get('reason', '')) else 'value_neutral'  # cash-return reductions only bound the move
        row = {'family': family, 'config_id': config_id, 'track': track_name, 'date': day, 'stock_id': asset, 'shares': event['shares'],
               'price': event['price'], 'notional': event['notional'], 'fee': event['fee'], 'tax': event['tax'],
               'reason_kind': refund, 'pre_close': action.get('pre_close'), 'reference_price': ref,
               'check_price_is_pre_close': action.get('pre_close') == event['price'],
               'check_notional': math.isclose(event['notional'], event['shares'] * event['price'], rel_tol=RTOL, abs_tol=1e-6),
               'check_fee': event['fee'] == cost.buy_cost(event['notional']),
               'check_tax': event['tax'] == int(event['notional'] * cost.tax_rate),
               'check_shares_equal_prior_holding': held == event['shares'],
               'has_resumption_bar': bar is not None, 'fill_basis': FILL_BASIS, 'slippage_applied': False}
        if bar:
            # The exit is priced per pre-action share; the resumption bar trades on the post-action basis, where the same value per
            # share is the reference price. Compare on that basis. Even inside the range, a closing bar is never a fill record.
            row.update(open=bar['open'], high=bar['high'], low=bar['low'], close=bar['close'], volume=bar['volume'], amount=bar['amount'],
                       post_action_equivalent_price=ref,
                       price_within_limits=bool(ref) and bar['low'] <= ref <= bar['high'],
                       volume_cover_ratio=event['notional'] / bar['amount'] if bar['amount'] else None,
                       limit_locked=bar['open'] == bar['high'] == bar['low'] == bar['close'])
            # One price below the reference: sellers queued at the lower limit, a sale that day is not plausible. The adverse
            # bound then uses the first later session with a price range; even that remains an execution assumption.
            row['sell_side_locked'] = row['limit_locked'] and bool(ref) and bar['close'] < ref
            if row['sell_side_locked']:
                later = next(((d, bars[d][asset]) for d in days[days.index(day) + 1:days.index(day) + 21]
                              if asset in bars[d] and bars[d][asset]['high'] > bars[d][asset]['low']), None)
                row['first_tradable_date'], row['first_tradable_close'] = (later[0], later[1]['close']) if later else (None, None)
        row['execution_status'] = UNCERTIFIED if row.get('sell_side_locked') or not bar else 'QUANTIFIED_BY_PRICE_BOUNDS'
        rows.append(row)
    return rows


def exit_delta(row, price_field, cost):
    """Net cash difference if the same position had been sold on the post-action basis at the resumption-day price instead of the
    pre-suspension close. Value-neutral reductions map shares*pre_close to notional*P/reference exactly; refunds only bound it."""
    if not row['has_resumption_bar'] or not row['reference_price']:
        return None
    price = row[price_field]
    if row.get('sell_side_locked') and price_field == 'low':  # adverse bound: the first session that could trade
        if row.get('first_tradable_close') is None:
            return None
        price = row['first_tradable_close']
    gross = row['notional'] * price / row['reference_price']
    return (gross - cost.sell_cost(gross)) - (row['notional'] - row['fee'] - row['tax'])


def adjusted_track(track, credits):
    """A copy of the 2x track with extra cash credited on each date (kept as cash: no reinvestment, a first-order bound)."""
    if not credits:
        return track
    out = {**track, 'result': {**track['result']}}
    by_date = {}
    for day, asset, amount in credits:
        by_date[day] = by_date.get(day, 0.0) + amount
    running, daily = 0.0, []
    for row in track['result']['daily']:
        running += by_date.get(row['date'], 0.0)
        daily.append({**row, 'nav': row['nav'] + running})
    out['result']['daily'] = daily
    out['result']['ledger'] = track['result']['ledger'] + [{'date': d, 'kind': 'CASH_CREDIT', 'stock_id': a, 'amount': x} for d, a, x in credits]
    return out


# ---------------------------------------------------------------- r3 / r4

def dsr_parts(x, n_trials, variance):
    """Independent re-derivation of Bailey & Lopez de Prado (2014) in per-session units; returns its pieces."""
    x = np.asarray(x, dtype=float)
    n, sd = len(x), x.std(ddof=1)
    sr = x.mean() / sd
    z = (x - x.mean()) / sd
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    e_max = stats.expected_max_sharpe(n_trials, variance)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    value = float(norm.cdf((sr - e_max) * math.sqrt(n - 1) / math.sqrt(denom))) if denom > 0 else None
    lo, hi = e_max, e_max + 2.0  # smallest per-session Sharpe that reaches 0.95 with this sample's moments
    for _ in range(80):
        mid = (lo + hi) / 2
        d = 1 - skew * mid + (kurt - 1) / 4 * mid ** 2
        lo, hi = (mid, hi) if d <= 0 or (mid - e_max) * math.sqrt(n - 1) / math.sqrt(d) < norm.ppf(0.95) else (lo, mid)
    return {'sessions': n, 'sr_daily': float(sr), 'sr_annual': float(sr * math.sqrt(stats.PERIODS)), 'skew': skew, 'kurtosis': kurt,
            'expected_max_sr_daily': e_max, 'expected_max_sr_annual': e_max * math.sqrt(stats.PERIODS), 'dsr': value,
            'required_sr_annual_for_0_95': hi * math.sqrt(stats.PERIODS)}


def pnl_components(track):
    """r4 numerator and denominator by cash-flow kind. Traded notional is reported apart: it is volume, not profit."""
    comp = {}
    def add(asset, kind, value):
        comp.setdefault(asset, {'fills': 0.0, 'forced_exit': 0.0, 'cash_credit': 0.0, 'terminal': 0.0})[kind] += value
    traded = 0.0
    for row in track['result']['ledger']:
        if row['kind'] == 'FILL':
            traded += row['notional']
            add(row['stock_id'], 'fills', -(row['notional'] + row['fee']) if row['side'] == 'buy' else row['notional'] - row['fee'] - row['tax'])
        elif row['kind'] == 'FORCED_EXIT':
            add(row['stock_id'], 'forced_exit', row['notional'] - row['fee'] - row['tax'])
        elif row['kind'] == 'CASH_CREDIT':
            add(row['stock_id'], 'cash_credit', row['amount'])
    last = track['result']['daily'][-1]
    for asset, shares in last['positions'].items():
        add(asset, 'terminal', shares * last['marks'][asset])
    for claim in last['stock_receivable']:
        add(claim['stock_id'], 'terminal', claim['shares'] * last['marks'][claim['stock_id']])
    for claim in last['cash_receivable_claims']:
        add(claim['stock_id'], 'terminal', claim['amount'])
    per_asset = pd.Series({a: sum(v.values()) for a, v in comp.items()}, dtype=float)
    totals = {k: float(sum(v[k] for v in comp.values())) for k in ('fills', 'forced_exit', 'cash_credit', 'terminal')}
    net = float(per_asset.sum())
    top5 = float(per_asset.sort_values(ascending=False).head(5).sum())
    gross_profit = float(per_asset[per_asset > 0].sum())
    return {'components': totals, 'net_profit': net, 'gross_profit': gross_profit, 'gross_loss': float(per_asset[per_asset < 0].sum()),
            'assets': int(len(per_asset)), 'top5_profit': top5, 'top5_share_of_net': top5 / net if net > 0 else None,
            'top5_share_of_gross_profit_diagnostic': top5 / gross_profit if gross_profit > 0 else None, 'traded_notional': traded}


def days_share_parts(net):
    """Observed best-5%-days share and its normal approximation 0.05 + phi(z95) * sigma / mu (sigma/mu per session)."""
    net = np.asarray(net, dtype=float)
    mu, sd = float(net.mean()), float(net.std(ddof=1))
    return {'observed': stats.best_days_share(net), 'sum_best_5pct': float(np.sort(net)[::-1][:max(1, int(len(net) * stats.TOP_DAY_SHARE))].sum()),
            'sum_all_days': float(net.sum()), 'normal_approximation': 0.05 + float(norm.pdf(norm.ppf(0.95))) * sd / mu if mu > 0 else None}


def effective_trials(series):
    """Participation ratio of the correlation spectrum of the trial return series: (sum lambda)^2 / sum lambda^2."""
    frame = pd.concat(series, axis=1).fillna(0.0)
    frame = frame.loc[:, frame.std() > 0]
    eig = np.clip(np.linalg.eigvalsh(np.corrcoef(frame.to_numpy().T)), 0, None)
    return float(eig.sum() ** 2 / (eig ** 2).sum())


# ---------------------------------------------------------------- consistency

def consistency(manifests, prepared_sha):
    findings, checked = [], {}
    registry = {row['hypothesis_id']: row for row in csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig', newline=''))}
    for family, version in VERSIONS.items():
        sha = assumptions_sha(REGISTERED_ASSUMPTIONS[version])
        expected = assumptions_registration(version)
        row = registry.get(expected['hypothesis_id'])
        checked[version] = sha
        if row is None or any(row[k] != v for k, v in expected.items()):
            findings.append(f'registry row {expected["hypothesis_id"]} differs from the code identity')
        bad = [m['config']['config_id'] for f, m in manifests if f == family and (m['assumptions_sha256'] != sha or m['assumptions_version'] != version
                                                                               or m['config'].get('assumptions_sha256') != sha)]
        if bad:
            findings.append(f'{len(bad)} {family} manifests do not carry {version} {sha[:12]}')
    # bars/actions come from prepared_v6; scores/universe from the family's signal checkpoint.
    signal_sha = {_sha(p) for d in SIGNALS.values() for p in d.glob('*.json')}
    stale = [m['config']['config_id'] for _, m in manifests
             if any(m['inputs_sha256'].get(k) != v for k, v in prepared_sha.items())
             or any(m['inputs_sha256'].get(k) not in signal_sha for k in ('scores', 'universe'))]
    if stale:
        findings.append(f'{len(stale)} manifests reference inputs outside prepared_v6 and the V6 signal checkpoints')
    track = _load(REPORT / 'research_track.json')
    for family, version in VERSIONS.items():
        if track['assumptions'][family] != {'version': version, 'sha256': checked[version]}:
            findings.append(f'report research_track assumptions for {family} differ')
    snap = _load(SNAPSHOT / 'research_track.json')
    if snap.get('assumptions') != track['assumptions'] or snap.get('trial_sharpe_variance') != track.get('trial_sharpe_variance'):
        findings.append('snapshot research_track differs from report_v6 research_track')
    from app.dashboard_payloads import build_research_status_payload
    payload = build_research_status_payload(SNAPSHOT).get('research_track') or {}
    if payload.get('assumptions') != track['assumptions'] or payload.get('label') != 'DIAGNOSTIC_ONLY':
        findings.append('dashboard payload research_track does not carry the report assumptions')
    screen_rows = _load(REPORT / 'screen_results.json')['rows']
    bad = [r['config_id'] for r in screen_rows if r.get('assumptions_version') not in VERSIONS.values()]
    if bad:
        findings.append(f'{len(bad)} screen rows lack a current assumptions_version')
    from PIL import Image
    charts = {'day3': checked['V6'], 'd5_variant': checked['D5-V5']}
    counts = {}
    for sub, sha in charts.items():
        files = sorted((REPORT / 'charts' / sub).glob('*.png'))
        counts[sub] = len(files)
        bad = [p.name for p in files if Image.open(p).info.get('Description') != f'DIAGNOSTIC_ONLY assumptions_sha256={sha}']
        if bad:
            findings.append(f'{len(bad)} {sub} charts do not carry DIAGNOSTIC_ONLY {sha[:12]}')
    diag = sorted((REPORT / 'diagnostics').glob('*.md'))
    bad = [p.name for p in diag if not any(s in p.read_text(encoding='utf-8') for s in checked.values())]
    if bad:
        findings.append(f'{len(bad)} diagnostics files name neither current SHA')
    return {'status': 'PASS' if not findings else 'FINDINGS', 'findings': findings, 'current': checked, 'charts': counts,
            'diagnostics_files': len(diag), 'screen_rows': len(screen_rows), 'manifests': len(manifests)}


# ---------------------------------------------------------------- main audit

def audit(output, reconcile_tracks=True):
    output = Path(output).resolve()
    if any(output.is_relative_to(d.resolve()) for d in RUNS.values()) or output.is_relative_to(PREPARED.resolve()):
        raise ValueError('AUDIT_OUTPUT_INSIDE_V6_INPUTS')
    output.mkdir(parents=True, exist_ok=True)
    before = read_set()
    market = _load(PREPARED / 'bars.json')['bars']
    actions = _load(PREPARED / 'actions.json')
    bars = {d: r for d, r in market.items() if d >= '2023-01-03'}
    days = sorted(bars)
    bench = report.benchmark_returns(market)
    regime = stats.regimes((1 + bench).cumprod())
    act = [e for e in actions['actions'] if e['ex_date'] in bars]
    unresolved = [e for e in actions['unresolved_events'] if e['ex_date'] in bars]
    admitted = {r['experiment_id'] for r in workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)}
    delivered_rows = {r['config_id']: r for r in _load(REPORT / 'screen_results.json')['rows']}
    delivered_variance = _load(REPORT / 'screen_results.json')['trial_sharpe_variance']

    manifests, findings, exits, blocked, configs = [], [], [], [], []
    counts = {'computable': 0, 'blocked': 0, 'missing': 0, 'forfeits': {'base': 0, 'stress': 0}, 'forced_exits': {'base': 0, 'stress': 0},
              'forfeits_ledger': {'base': 0, 'stress': 0}, 'forced_exits_ledger': {'base': 0, 'stress': 0}, 'reconcile_pass': 0, 'reconcile_run': 0}
    for family, folder, mfolder in configurations():
        if mfolder is None:
            counts['missing'] += 1
            findings.append(f'{folder}: no manifest')
            continue
        m = _load(mfolder / 'manifest.json')
        manifests.append((family, m))
        cid = m['config']['config_id']
        if m['status'] != 'COMPUTABLE':
            counts['blocked'] += 1
            blocked.append({'family': family, 'strategy': m['config']['strategy'], 'config_id': cid, 'top_n': m['config'].get('top_n'),
                            'rebalance_days': m['config'].get('rebalance_days'), 'reason': m.get('reason'), 'date': m.get('date'),
                            'stock_id': m.get('stock_id'), 'holdings': m.get('holdings'), 'missing_terms': m.get('missing_terms'),
                            'cost_track': m.get('cost_track'), 'admitted': m['experiment_id'] in admitted})
            continue
        counts['computable'] += 1
        tracks = {n: _load(mfolder / f'{n}.json') for n in ('base', 'stress')}
        if any(_sha(mfolder / n) != d for n, d in m['output_sha256'].items()) or m['experiment_id'] not in admitted:
            findings.append(f'{cid}: output SHA or ledger admission mismatch')
        for name, track in tracks.items():
            result = track['result']
            cost = CostModel(**result['cost'])
            f_rows = [e for e in result['ledger'] if e['kind'] == 'ENTITLEMENT_FORFEITED']
            x_rows = [e for e in result['ledger'] if e['kind'] == 'FORCED_EXIT']
            counts['forfeits'][name] += m['forfeits'][name]['events']
            counts['forced_exits'][name] += m['forfeits'][name]['forced_exits']
            counts['forfeits_ledger'][name] += len(f_rows)
            counts['forced_exits_ledger'][name] += len(x_rows)
            if reconcile_tracks:
                counts['reconcile_run'] += 1
                check = reconcile(m['config']['initial_cash'], result, bars=bars, orders=result['generated_orders'], actions=act,
                                  action_coverage=actions['coverage'], cost=cost, slippage=result['slippage'],
                                  cancellations=result['cancellations'], fractional_policy='DROP', unresolved_events=unresolved,
                                  unresolved_policy=m['held_event_policy'], overlap_policy=m['overlap_policy'])
                counts['reconcile_pass'] += check['status'] == 'PASS'
                if check['status'] != 'PASS':
                    findings.append(f'{cid}/{name}: reconcile {check}')
            exits += exit_rows(track, bars, days, cost, family, cid, name)
        # r4 identity: per-asset profit sums to the NAV change, so a non-positive 2x return is exactly an undefined share.
        stress = tracks['stress']
        pnl = pnl_components(stress)
        nav_change = stress['result']['daily'][-1]['nav'] - m['config']['initial_cash']
        if not math.isclose(pnl['net_profit'], nav_change, rel_tol=1e-9, abs_tol=1e-4):
            findings.append(f'{cid}: asset profit {pnl["net_profit"]} != NAV change {nav_change}')
        cost = CostModel(**stress['result']['cost'])
        stress_exits = [r for r in exits if r['config_id'] == cid and r['track'] == 'stress']
        forfeit_credits = [(e['date'], e['stock_id'], e['forfeited_value']) for e in stress['result']['ledger']
                           if e['kind'] == 'ENTITLEMENT_FORFEITED' and e.get('forfeited_value') is not None]
        unvalued_forfeits = sum(1 for e in stress['result']['ledger'] if e['kind'] == 'ENTITLEMENT_FORFEITED' and e.get('forfeited_value') is None)
        scenario_results = {}
        for scenario, (addback, field) in SCENARIOS.items():
            credits = list(forfeit_credits) if addback else []
            if field:
                credits += [(r['date'], r['stock_id'], exit_delta(r, field, cost)) for r in stress_exits if exit_delta(r, field, cost) is not None]
            record = {'_tracks': {'base': tracks['base'], 'stress': adjusted_track(stress, credits)}, 'config': m['config']}
            scenario_results[scenario] = report.build_result(record, bench, regime)
        unquantified = sum(1 for r in stress_exits if exit_delta(r, 'close', cost) is None or exit_delta(r, 'low', cost) is None)
        uncertified = sum(1 for r in stress_exits if r['execution_status'] == UNCERTIFIED)
        configs.append({'family': family, 'strategy': m['config']['strategy'], 'config_id': cid, 'top_n': m['config'].get('top_n'),
                        'rebalance_days': m['config'].get('rebalance_days'), 'results': scenario_results, 'pnl': pnl,
                        'forced_exits_stress': len(stress_exits), 'forfeits_stress': m['forfeits']['stress']['events'],
                        'unvalued_forfeits': unvalued_forfeits, 'unquantified_exits': unquantified, 'uncertified_exits': uncertified,
                        'stress_total_return': float(stress['result']['daily'][-1]['nav'] / m['config']['initial_cash'] - 1),
                        'forced_exit_notional_share': m['forfeits']['stress'].get('forced_exit_notional_share_of_final_nav'),
                        'forfeit_share': m['forfeits']['stress'].get('forfeited_share_of_final_nav')})
        print(f'{family} {folder.name} {cid}', flush=True)

    # Trial variance exactly as the report computed it, then the delivered screen reproduced before any scenario is read.
    originals = [c['results']['DELIVERED'] for c in configs]
    sharpes = [stats.sharpe(r['excess_returns_2x'], periods=1) for r in originals if len(r['excess_returns_2x']) > 1]
    variance = float(np.var([s for s in sharpes if math.isfinite(s)], ddof=1))
    if not _close(variance, delivered_variance):
        findings.append(f'trial variance {variance} != delivered {delivered_variance}')
    for c in configs:
        c['screens'] = {s: screen(r, trial_sharpe_variance=variance) for s, r in c['results'].items()}
        got, want = c['screens']['DELIVERED'], delivered_rows.get(c['config_id'], {})
        if got['status'] != want.get('screen_status') or got.get('gates') != want.get('gates') or any(
                not _close(v, (want.get('values') or {}).get(k)) for k, v in (got.get('values') or {}).items() if k != 'regime'):
            findings.append(f'{c["config_id"]}: delivered screen not reproduced')

    impact = scenario_impact(configs)
    diagnostics = screen_diagnostics(configs, variance)
    checked = consistency(manifests, {k: _sha(PREPARED / f'{k}.json') for k in ('bars', 'actions')})
    if checked['findings']:
        findings += ['consistency: ' + f for f in checked['findings']]
    after = read_set()
    changed = sorted(k for k in before if before[k] != after.get(k))
    if changed:
        findings.append(f'{len(changed)} read files changed during the audit')
    reproduced = {'computable': counts['computable'] == DELIVERED['computable'], 'blocked': counts['blocked'] == DELIVERED['blocked'],
                  'forfeits_stress': counts['forfeits']['stress'] == counts['forfeits_ledger']['stress'] == DELIVERED['forfeits_stress'],
                  'forced_exits_stress': counts['forced_exits']['stress'] == counts['forced_exits_ledger']['stress'] == DELIVERED['forced_exits_stress']}
    findings += [f'delivered count not reproduced: {k}' for k, ok in reproduced.items() if not ok]
    exit_summary = summarize_exits([r for r in exits if r['track'] == 'stress'])
    affected = [c for c in configs if c['forced_exits_stress']]
    summary = {**LABELS, 'counts': counts, 'delivered': DELIVERED,
               'reproduced': reproduced,
               'forced_exits': exit_summary,
               'affected_by_forced_exit': {'configs': len(affected), 'of_computable': len(configs),
                                           'unquantified_configs': sum(1 for c in affected if c['unquantified_exits']),
                                           'uncertified_execution_configs': sorted(c['config_id'] for c in affected if c['uncertified_exits']),
                                           'by_strategy': pd.Series([c['strategy'] for c in affected]).value_counts().to_dict()},
               'affected_by_forfeit': {'configs': sum(1 for c in configs if c['forfeits_stress']),
                                       'unvalued_events': sum(c['unvalued_forfeits'] for c in configs)},
               'blocked': {'configs': len(blocked), 'reasons': pd.Series([b['reason'] for b in blocked]).value_counts().to_dict(),
                           'stocks': pd.Series([b['stock_id'] for b in blocked]).value_counts().to_dict(),
                           'dates': pd.Series([b['date'] for b in blocked]).value_counts().to_dict(),
                           'missing_terms': pd.Series([','.join(b['missing_terms'] or []) for b in blocked]).value_counts().to_dict()},
               'selection_bias': selection_bias(configs, blocked), 'scenario_impact': impact,
               'trial_sharpe_variance': {'recomputed': variance, 'delivered': delivered_variance},
               'consistency': checked,
               'read_only': {'files_hashed': len(before), 'changed_during_audit': changed},
               'audit_script_sha256': _sha(Path(__file__)),
               'findings': findings, 'status': 'PASS' if not findings else 'FINDINGS'}
    write(output, summary, exits, blocked, diagnostics)
    return summary


def summarize_exits(rows):
    frame = pd.DataFrame(rows)
    priced = frame[frame['has_resumption_bar']]
    return {'events': int(len(frame)), 'fill_basis': frame['fill_basis'].value_counts().to_dict(),
            'stocks': int(frame['stock_id'].nunique()), 'configs': int(frame['config_id'].nunique()),
            'reason_kind': frame['reason_kind'].value_counts().to_dict(),
            'accounting_checks_failed': {c: int((~frame[c]).sum()) for c in frame.columns if c.startswith('check_')},
            'has_resumption_bar': int(frame['has_resumption_bar'].sum()),
            'price_within_limits': int(priced['price_within_limits'].sum()),  # post-action equivalent inside the day's traded range
            'price_within_limits_stock_dates': priced[priced['price_within_limits']][['stock_id', 'date']].drop_duplicates().astype(str).agg(' '.join, axis=1).tolist(),
            'limit_locked': int(priced['limit_locked'].sum()), 'sell_side_locked': int(priced['sell_side_locked'].sum()),
            'execution_status': frame['execution_status'].value_counts().to_dict(),
            'locked_events': priced[priced['sell_side_locked']][['stock_id', 'date', 'close', 'reference_price', 'first_tradable_date', 'first_tradable_close']].drop_duplicates().to_dict(orient='records'),
            'volume_cover_ratio': {'median': float(priced['volume_cover_ratio'].median()), 'max': float(priced['volume_cover_ratio'].max())},
            'resumption_close_vs_reference': {'median': float((priced['close'] / priced['reference_price'] - 1).median()),
                                              'min': float((priced['close'] / priced['reference_price'] - 1).min()),
                                              'max': float((priced['close'] / priced['reference_price'] - 1).max())},
            'slippage_applied': int(frame['slippage_applied'].sum())}


def selection_bias(configs, blocked):
    survived = pd.DataFrame([{k: c[k] for k in ('family', 'strategy', 'top_n', 'rebalance_days')} for c in configs]).assign(set='computable')
    stopped = pd.DataFrame([{k: b[k] for k in ('family', 'strategy', 'top_n', 'rebalance_days')} for b in blocked]).assign(set='blocked')
    both = pd.concat([survived, stopped])
    return {key: both.groupby([key, 'set']).size().unstack(fill_value=0).to_dict(orient='index') for key in ('strategy', 'top_n', 'rebalance_days')}


def scenario_impact(configs):
    """How each assumption scenario moves 2x return, risk, the five gates and the ranking, against the delivered numbers."""
    def metrics(res):
        net = np.asarray(res['net_returns_2x'])
        return {'sharpe_2x': stats.sharpe(net), 'total_2x': float(np.prod(1 + net) - 1), 'mdd_2x': stats.max_drawdown(net),
                'excess_sharpe_2x': stats.sharpe(np.asarray(res['excess_returns_2x']))}
    base = pd.DataFrame([metrics(c['results']['DELIVERED']) for c in configs], index=[c['config_id'] for c in configs])
    strategies = pd.Series({c['config_id']: c['strategy'] for c in configs})
    out = {}
    for scenario in SCENARIOS:
        frame = pd.DataFrame([metrics(c['results'][scenario]) for c in configs], index=base.index)
        delta = frame - base
        gates = {g: int(sum(1 for c in configs if (c['screens'][scenario].get('gates') or {}).get(g))) for g in ('r1', 'r2', 'r3', 'r4', 'r5')}
        both = [c for c in configs if c['screens'][scenario].get('gates') and c['screens']['DELIVERED'].get('gates')]
        flips = {g: int(sum(1 for c in both if c['screens'][scenario]['gates'][g] != c['screens']['DELIVERED']['gates'][g])) for g in ('r1', 'r2', 'r3', 'r4', 'r5')}
        entered = int(sum(1 for c in configs if c['screens'][scenario].get('gates') and not c['screens']['DELIVERED'].get('gates')))
        left = int(sum(1 for c in configs if c['screens']['DELIVERED'].get('gates') and not c['screens'][scenario].get('gates')))
        best = lambda f: f.groupby(strategies)['sharpe_2x'].idxmax()
        top10 = lambda f: set(f['sharpe_2x'].nlargest(10).index)
        rows_affected = delta['sharpe_2x'].abs() > 1e-12
        out[scenario] = {'configs_changed': int(rows_affected.sum()),
                         'delta_sharpe_2x': {'median_changed': float(delta.loc[rows_affected, 'sharpe_2x'].median()) if rows_affected.any() else 0.0,
                                             'min': float(delta['sharpe_2x'].min()), 'max': float(delta['sharpe_2x'].max())},
                         'delta_total_return_2x': {'min': float(delta['total_2x'].min()), 'max': float(delta['total_2x'].max())},
                         'delta_max_drawdown_2x': {'min': float(delta['mdd_2x'].min()), 'max': float(delta['mdd_2x'].max())},
                         'delta_excess_sharpe_2x': {'min': float(delta['excess_sharpe_2x'].min()), 'max': float(delta['excess_sharpe_2x'].max())},
                         'gate_passes': gates, 'gate_flips_among_screened_in_both': flips,
                         'entered_screening': entered, 'left_screening': left,
                         'screen_status': pd.Series([c['screens'][scenario]['status'] for c in configs]).value_counts().to_dict(),
                         'candidates': int(sum(1 for c in configs if c['screens'][scenario].get('candidate'))),
                         'rank_spearman_sharpe_2x': float(spearmanr(base['sharpe_2x'], frame['sharpe_2x']).statistic),
                         'top10_overlap': len(top10(base) & top10(frame)),
                         'best_config_changed_strategies': int((best(base) != best(frame)).sum())}
    return out


def screen_diagnostics(configs, variance):
    rows, series = [], []
    for c in configs:
        res = c['results']['DELIVERED']
        series.append(pd.Series(res['excess_returns_2x']))
    neff = effective_trials(series)
    per_strategy = {}
    for c in configs:
        s = stats.sharpe(c['results']['DELIVERED']['excess_returns_2x'], periods=1)
        if math.isfinite(s):
            per_strategy.setdefault(c['strategy'], []).append(s)
    strategy_variance = {k: float(np.var(v, ddof=1)) if len(v) > 1 else None for k, v in per_strategy.items()}
    trials = {'743_batch': (743, variance), '500_batch': (500, variance), '230_batch': (230, variance),
              'neff_batch': (max(2, round(neff)), variance), 'psr_1': (1, variance)}
    for c in configs:
        res, verdict = c['results']['DELIVERED'], c['screens']['DELIVERED']
        x = np.asarray(res['excess_returns_2x'])
        row = {'family': c['family'], 'strategy': c['strategy'], 'config_id': c['config_id'], 'screen_status': verdict['status'],
               'stress_total_return': c['stress_total_return'], **dsr_parts(x, 743, variance)}
        official = (verdict.get('values') or {}).get('deflated_sharpe')
        row['dsr_official'] = official
        row['dsr_matches_official'] = _close(row['dsr'], official) if official is not None else None
        row['sharpe_annual_matches_daily_x_sqrt252'] = _close(stats.sharpe(x), row['sr_annual'])
        for key, (n, v) in trials.items():
            row['dsr_' + key] = dsr_parts(x, n, v)['dsr']
        sv = strategy_variance.get(c['strategy'])
        row['dsr_743_strategy'] = dsr_parts(x, 743, sv)['dsr'] if sv is not None else None
        row.update({'r4_' + k: v for k, v in c['pnl'].items() if k != 'components'})
        row.update({'r4_component_' + k: v for k, v in c['pnl']['components'].items()})
        row.update({'r4_days_' + k: v for k, v in days_share_parts(res['net_returns_2x']).items()})
        row['gates'] = verdict.get('gates')
        rows.append(row)
    frame = pd.DataFrame(rows)
    screened = frame[frame['screen_status'].str.startswith('RESEARCH_RUN')]
    undefined = frame[frame['screen_status'] == 'NOT_COMPUTABLE']
    sensitivity = {key: {'r3_pass': int((screened['dsr_' + key] > 0.95).sum()), 'max_dsr': float(screened['dsr_' + key].max())}
                   for key in SENSITIVITY if 'dsr_' + key in screened}
    summary = {**LABELS, 'trial_sharpe_variance': variance, 'trial_sharpe_sd_annual': math.sqrt(variance * stats.PERIODS),
               'effective_independent_trials_participation_ratio': neff, 'strategy_variance': strategy_variance,
               'screened': int(len(screened)), 'undefined_r4': int(len(undefined)),
               'r3': {'pass': int((screened['dsr'] > 0.95).sum()), 'dsr_formula_matches_official': bool(screened['dsr_matches_official'].all()),
                      'annualization_consistent': bool(frame['sharpe_annual_matches_daily_x_sqrt252'].all()),
                      'expected_max_sr_annual_743': float(frame['expected_max_sr_annual'].iloc[0]),
                      'required_sr_annual_for_0_95': {'min': float(screened['required_sr_annual_for_0_95'].min()),
                                                     'median': float(screened['required_sr_annual_for_0_95'].median())},
                      'observed_sr_annual': {'max': float(screened['sr_annual'].max()), 'median': float(screened['sr_annual'].median())},
                      'max_dsr': float(screened['dsr'].max()), 'sensitivity': sensitivity},
               'r4': {'stock_pass': int((screened['r4_top5_share_of_net'] < 0.5).sum()), 'day_pass': int((screened['r4_days_observed'] < 0.5).sum()),
                      'stock_share': {'min': float(screened['r4_top5_share_of_net'].min()), 'median': float(screened['r4_top5_share_of_net'].median())},
                      'stock_share_above_1': int((screened['r4_top5_share_of_net'] > 1).sum()),
                      'stock_share_of_gross_profit_diagnostic': {'min': float(screened['r4_top5_share_of_gross_profit_diagnostic'].min()),
                                                                 'median': float(screened['r4_top5_share_of_gross_profit_diagnostic'].median())},
                      'day_share': {'min': float(screened['r4_days_observed'].min()), 'median': float(screened['r4_days_observed'].median())},
                      'day_share_normal_approximation_median': float(screened['r4_days_normal_approximation'].median()),
                      # 0.05 + phi(z95) * sigma / mu < 0.5  <=>  mu / sigma > phi(z95) / 0.45 per session
                      'sr_annual_needed_for_day_share_below_0_5_normal': float(norm.pdf(norm.ppf(0.95)) / 0.45 * math.sqrt(stats.PERIODS)),
                      'net_to_traded_notional_median': float((screened['r4_net_profit'] / screened['r4_traded_notional']).median())},
               'undefined': {'configs': int(len(undefined)), 'all_non_positive_2x_return': bool((undefined['stress_total_return'] <= 0).all()),
                             'all_non_positive_net_profit': bool((undefined['r4_net_profit'] <= 0).all()),
                             'screened_all_positive_2x_return': bool((screened['stress_total_return'] > 0).all())}}
    return summary, frame


def write(output, summary, exits, blocked, diagnostics):
    diag_summary, frame = diagnostics
    (output / 'v6_integrity_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (output / 'screen_diagnostics.json').write_text(json.dumps(diag_summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    pd.DataFrame(exits).to_csv(output / 'forced_exits.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(blocked).to_csv(output / 'blocked_configurations.csv', index=False, encoding='utf-8-sig')
    frame.drop(columns=['gates']).to_csv(output / 'screen_diagnostics.csv', index=False, encoding='utf-8-sig')
    (output / 'consistency.json').write_text(json.dumps(summary['consistency'], ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(REPORT / 'audit'))
    parser.add_argument('--skip-reconcile', action='store_true', help='skip the independent reconciliation rerun (fast iteration only)')
    parser.add_argument('--check-consistency', action='store_true', help='only compare SHA and version across registry, manifests, report, charts and dashboard')
    args = parser.parse_args()
    if args.check_consistency:
        result = consistency([(f, _load(m / 'manifest.json')) for f, _, m in configurations() if m is not None],
                             {k: _sha(PREPARED / f'{k}.json') for k in ('bars', 'actions')})
        Path(args.output).mkdir(parents=True, exist_ok=True)
        (Path(args.output) / 'consistency.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'findings': result['findings']}, ensure_ascii=False, indent=1))
    else:
        result = audit(args.output, reconcile_tracks=not args.skip_reconcile)
        print(json.dumps({'status': result['status'], 'reproduced': result['reproduced'], 'findings': result['findings'][:20]}, ensure_ascii=False, indent=1))
    sys.exit(0 if result['status'] == 'PASS' else 1)  # a finding is a failed audit, never a silent pass
