"""22-strategy verdicts (design section 2), shadow selection day and forward-evidence status for 2026-10-24-establish-strategy-verdicts-and-shadow-selection.

Reads archived evidence only; writes only into OUT and outputs/shadow_selection. No backtest, no search, no held-out OOS, no MySQL, no request.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/goal_verdicts/2026-10-24'
DT = ROOT / 'outputs/dual_track_validation/2026-10-23'
NATIVE7 = ROOT / 'outputs/post_archive_readiness/2026-10-24/native7_training_readiness_matrix_v4.csv'
FRESH_LEDGER = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/FreshOOSLedger.csv'
SHADOW = ROOT / 'outputs/shadow_selection'
FRESH_MINIMUM = 252
SHADOWED = 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY'  # the only strategy with a frozen prospective ledger (G2+G3 top5, reb60)


def verdict(*, data, model, any_config_passes, data_gaps, frozen) -> tuple[str, str]:
    """Design section 2, first rule that holds."""
    if data != 'DATA_PASS' or model == 'MODEL_BLOCKED':
        return 'BLOCKED', f'DATA={data}, MODEL={model}'
    if not any_config_passes:
        return ('NEED_MORE_EVIDENCE', 'no configuration passes all OD-24 gates; data has known gaps (total return / non-official prices), so not REJECTED') if data_gaps \
            else ('REJECTED', 'complete data and no configuration passes all OD-24 gates')
    if data_gaps or not frozen:
        return 'NEED_MORE_EVIDENCE', 'a configuration passes OD-24 but ' + ('data has known gaps' if data_gaps else 'it is not frozen')
    return 'QUALIFIED_FOR_FORWARD', 'a frozen configuration passes all OD-24 gates on complete data'


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # new evidence only
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


DATA_R2 = ROOT / 'outputs/p0_p4/2026-10-24/p0_strategy_data_status_r2.csv'  # newer DATA evidence than strategy_status_matrix.csv


def verdicts(name='strategy_verdicts.csv', data_r2=False):
    """data_r2=True: DATA for Day3/D5 comes from p0_strategy_data_status_r2.csv (2026-10-24), which supersedes the 2026-10-23 matrix."""
    import pandas as pd
    status = pd.read_csv(DT / 'strategy_status_matrix.csv', encoding='utf-8-sig')
    if data_r2:
        r2 = pd.read_csv(DATA_R2, encoding='utf-8-sig').set_index('family_name')['DATA']
        missing = set(r2.index) - set(status.strategy)
        if missing:
            raise ValueError(f'r2 families not in the status matrix (no silent fallback): {sorted(missing)}')
        status['DATA'] = [r2.get(s, d) for s, d in zip(status.strategy, status.DATA)]
    results = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    native = pd.read_csv(NATIVE7, encoding='utf-8-sig').set_index('strategy')
    rows = []
    for r in status.itertuples():
        configs = results[results.family_name == r.strategy]
        track_a = r.group == 'A_NATIVE_AI'
        data = 'DATA_BLOCKED' if track_a and native.loc[r.strategy, 'VERDICT'] != 'READY_FOR_OWNER_APPROVAL' else r.DATA  # Track A: the training readiness verdict governs
        gaps = not len(configs) or bool(  # no backtest at all is missing evidence, never grounds for REJECTED
(configs.total_return_status != 'TOTAL_RETURN_PASS').any() | (configs.held_on_nonofficial_prices > 0).any())
        result, why = verdict(data=data, model=r.MODEL, any_config_passes=bool(configs.statistical_gates_pass.any()), data_gaps=gaps or track_a, frozen=False)
        rows.append({'group': r.group, 'strategy': r.strategy, 'VERDICT': result, 'why': why, 'ENGINE': r.ENGINE, 'DATA': data, 'MODEL': r.MODEL,
                     'configurations': len(configs), 'configurations_passing_all_od24': int(configs.statistical_gates_pass.sum()),
                     'max_bootstrap_lower': r.max_bootstrap_lower, 'max_deflated_sharpe': r.max_deflated_sharpe,
                     'track_a_readiness': native.loc[r.strategy, 'VERDICT'] if track_a else 'NOT_APPLICABLE',
                     'evidence': 'strategy_status_matrix.csv; backtest_results.csv' + ('; native7_training_readiness_matrix_v4.csv' if track_a else '')})
    frame = pd.DataFrame(rows)
    frame.to_csv(out(name), index=False, encoding='utf-8-sig')
    print(frame.VERDICT.value_counts().to_dict())


def load_verdicts():
    import pandas as pd
    frame = pd.read_csv(OUT / 'strategy_verdicts.csv', encoding='utf-8-sig')
    return {r.strategy: (r.VERDICT, r.why) for r in frame.itertuples()}


def shadow(date):
    from core.research import shadow_selection
    record = shadow_selection.record_day(date, load_verdicts(), {}, SHADOW)  # no qualified strategy produces picks this round
    print(record['status'], len(record['rejected']), shadow_selection.verify(SHADOW))


def forward():
    import pandas as pd
    sessions = pd.read_csv(FRESH_LEDGER, usecols=['date']).date.nunique()
    rows = [{'strategy': s, 'verdict': v, 'post_freeze_sessions_recorded': sessions if s == SHADOWED else 0,
             'source': 'FreshOOSLedger.csv (fundamental_g2g3_top5_reb60_score_weighted_v1)' if s == SHADOWED else 'no frozen prospective record'}
            for s, (v, _) in load_verdicts().items()]
    frame = pd.DataFrame(rows).assign(minimum=FRESH_MINIMUM)
    frame['remaining'] = (FRESH_MINIMUM - frame.post_freeze_sessions_recorded).clip(lower=0)
    frame['status'] = 'PENDING_FORWARD_EVIDENCE'  # sessions are counted, never their returns (design section 4)
    frame.to_csv(out('forward_evidence_status_r2.csv'), index=False, encoding='utf-8-sig')  # r1 attributed the G2+G3 ledger to G2 and G3 alone too
    print(frame[['strategy', 'post_freeze_sessions_recorded', 'remaining']].to_string(index=False))


# ----- seven-layer qualification matrix (2026-10-26-complete-end-to-end-quant-acceptance, design D5) -----
MATRIX_OUT = ROOT / 'outputs/research_workflow/2026-10-26'
P1_R2 = ROOT / 'outputs/p0_p4/2026-10-24/p1_day3_d5_diagnostics_r2.csv'
RESEARCH_SHADOW = ROOT / 'outputs/research_shadow/ledger.jsonl'
PAPER_ROOT = ROOT / 'outputs/paper_trading'
VECTORBT_FAMILIES = ('DAY3', 'D5')  # mask-only fill rule: never better than DIAGNOSTIC_ONLY (design D2)
SIGNAL_OVERRIDES = {'D5_PORTFOLIO_5_FACTORS': 'PAUSE', 'A191_132': 'NEED_MORE_EVIDENCE'}  # risk-gatekeeper review 2026-10-25 (RISK_REVIEW_D5_A191_132.md)
STAGES = ('DATA_STATUS', 'SIGNAL_STATUS', 'BACKTEST_STATUS', 'STATISTICAL_STATUS', 'FORWARD_STATUS', 'PAPER_STATUS')
PASSING = {'DATA_STATUS': {'DATA_CERTIFIED'}, 'SIGNAL_STATUS': {'KEEP'}, 'BACKTEST_STATUS': {'CREDIBLE'}, 'STATISTICAL_STATUS': {'PASS'},
           'FORWARD_STATUS': {'FRESH_OOS_PASS'}, 'PAPER_STATUS': {'PAPER_PASSED'}}


CERTIFICATION_ITEMS = ('prices', 'corporate_actions', 'pit_fundamentals', 'universe', 'calendar')
PIT_TEXT = re.compile(r'(financials|revenue)": "DATA_BLOCKED|fundamentals values unverified|fundamentals_sample_only|(FINANCIALS|REVENUE)_DATA_BLOCKED|'
                      r'FUNDAMENTALS_VALUES=DATA_BLOCKED|REVENUE_TIMING=DATA_BLOCKED')
# (blocker_id, evidence pattern, affected fields, minimum release condition, owner_required); design D1
BLOCKERS = (
    ('OD-P6', re.compile(r'TOTAL_RETURN_BLOCKED|mixed|rights', re.I), 'cash/stock dividend split, rights-offering terms (adjusted close, shares)',
     'Owner grants official_fetch: TWT49U detail, 385 requests, through RequestGate', 'YES (Level 1 official_fetch)'),
    ('OD-P5', re.compile(r'NONOFFICIAL|STALE_VALUATION', re.I), 'valuation price on sessions without an official quote',
     'Owner chooses the valuation rule (last official close, no trading while unquoted)', 'YES (Owner decision)'),
    ('T1b', re.compile(r'SPLIT_CAPITAL_REDUCTION'), 'capital reduction / par change reference price and share count',
     'Owner grants official_fetch: resumption reference prices, at most 10 requests', 'YES (Level 1 official_fetch)'),
    ('OD-P4', PIT_TEXT, 'financial-statement values and versions, monthly revenue announcement time',
     'a trusted historical-version (point-in-time) source; none is known', 'YES (Level 2 source)'),
)


def _data_status(strategy, sources, evidence=(), certification=None):
    """Five states, fail closed (design D1). DATA_CERTIFIED needs a positive certification record (every CERTIFICATION_ITEMS entry
    PASS with an evidence path); sources that merely report DATA_PASS, or no detected blocker, never certify."""
    seen = [(name, value) for name, value in sources if value is not None]
    values = {v for _, v in seen}
    conflicts = [f'{strategy}: DATA ' + ' vs '.join(f'{n}={v}' for n, v in seen)] if len(values) > 1 else []
    detail = '; '.join(f'{n}={v}' for n, v in seen) or 'no data-status source'
    if not seen and not evidence:
        return 'MISSING_DATA', [], detail
    if any(PIT_TEXT.search(str(text)) for _, text in evidence):
        return 'PIT_UNVERIFIED', conflicts, detail
    if 'DATA_BLOCKED' in values:
        return 'DATA_BLOCKED', conflicts, detail
    if len(values) > 1:
        return 'SOURCE_CONFLICT', conflicts, detail
    items = certification or {}
    if all(str(items.get(k, ('', ''))[0]) == 'PASS' and items[k][1] for k in CERTIFICATION_ITEMS):
        return 'DATA_CERTIFIED', conflicts, detail + '; certification: ' + ', '.join(f'{k}={items[k][1]}' for k in CERTIFICATION_ITEMS)
    return 'MISSING_DATA', conflicts, detail + '; no positive certification record'


def data_blockers(strategy, status, evidence):
    """One row per (strategy, blocker) for a non-certified strategy; unknown dates stay UNKNOWN (never guessed)."""
    if status == 'DATA_CERTIFIED':
        return []
    rows = [{'strategy': strategy, 'blocker_id': bid, 'date_range': 'UNKNOWN', 'fields': fields,
             'evidence': ' | '.join(f'{src}: {text}' for src, text in evidence if pattern.search(str(text))),
             'release_condition': release, 'owner_required': owner}
            for bid, pattern, fields, release, owner in BLOCKERS if any(pattern.search(str(text)) for _, text in evidence)]
    return rows or [{'strategy': strategy, 'blocker_id': 'NO_CERTIFICATION', 'date_range': 'UNKNOWN', 'fields': ', '.join(CERTIFICATION_ITEMS),
                     'evidence': ' | '.join(f'{src}: {text}' for src, text in evidence) or 'no data-status source',
                     'release_condition': 'a positive certification record for every item', 'owner_required': 'DEPENDS on the missing item'}]


def build_matrix(status, data_r2, verdicts_r2, verdicts_r1, diagnostics, forward, shadow_prospective, paper_accounts, corrected_reruns=None,
                 evidence=None, certifications=None):
    """Pure: frames in, (matrix, conflicts) out. Stage order DATA -> SIGNAL -> BACKTEST -> STATISTICAL -> FORWARD -> PAPER; PAPER never feeds back.

    status: strategy_status_matrix.csv; data_r2: p0_strategy_data_status_r2.csv; verdicts_r2 / verdicts_r1: goal verdict files;
    diagnostics: p1_day3_d5_diagnostics_r2.csv; forward: forward_evidence_status_r2.csv; shadow_prospective: {strategy: n PROSPECTIVE records};
    paper_accounts: {strategy: 'PASS' | 'FAIL' | 'REHEARSAL'}.
    corrected_reruns: {strategy: {'execution_audit_pass': bool, 'configurations_passing_all_od24': int, 'fill_rule': str}} from a rerun file.
    evidence: {strategy: [(source, blocker text)]}; certifications: {strategy: {item: (PASS, evidence path)}} (design D1).
    Returns (matrix, conflicts, blocker rows).
    Statistics from strategy_verdicts_r2.csv (old fill rule) always carry _OLD_FILL_RULE; only a rerun's own OD-24 result drops it, and
    BACKTEST is CREDIBLE only when that rerun's execution audit passed (vectorbt families stay DIAGNOSTIC_ONLY).
    """
    corrected_reruns, evidence, certifications = corrected_reruns or {}, evidence or {}, certifications or {}
    import pandas as pd
    r2 = data_r2.set_index('family_name')['DATA'] if len(data_r2) else {}
    v2 = verdicts_r2.set_index('strategy') if len(verdicts_r2) else None
    v1 = verdicts_r1.set_index('strategy') if len(verdicts_r1) else None
    diag = diagnostics.set_index('strategy') if len(diagnostics) else None
    fwd = forward.set_index('strategy') if len(forward) else None
    rows, conflicts, blocker_rows = [], [], []
    for r in status.itertuples():
        s, reasons, src = r.strategy, [], {}
        data, found, detail = _data_status(s, [('p0_strategy_data_status_r2.csv', r2.get(s)),
                                               ('strategy_verdicts_r2.csv', v2.DATA.get(s) if v2 is not None else None),
                                               ('strategy_verdicts.csv', v1.DATA.get(s) if v1 is not None else None)],
                                           evidence.get(s, ()), certifications.get(s))
        conflicts += found
        blocker_rows += data_blockers(s, data, evidence.get(s, ()))
        src['DATA_STATUS'] = detail
        if data != 'DATA_CERTIFIED':
            blockers = data_r2.set_index('family_name').blockers.get(s, '') if len(data_r2) and 'blockers' in data_r2 else ''
            reasons.append(f'DATA {data}: {blockers or "see strategy_status_matrix DATA_detail"}')
        if s in SIGNAL_OVERRIDES:
            signal, src['SIGNAL_STATUS'] = SIGNAL_OVERRIDES[s], 'RISK_REVIEW_D5_A191_132.md'
        elif diag is not None and s in diag.index:
            signal, src['SIGNAL_STATUS'] = 'NEED_MORE_EVIDENCE', f'p1_day3_d5_diagnostics_r2.csv ({diag.loc[s, "factor_ic_60d"]})'
        else:
            signal, src['SIGNAL_STATUS'] = 'UNKNOWN', 'no factor diagnostics'
        if signal != 'KEEP':
            reasons.append(f'SIGNAL {signal}')
        configs = int(v2.configurations.get(s, 0)) if v2 is not None and s in v2.index else 0
        family = diag.loc[s, 'family'] if diag is not None and s in diag.index else None
        rerun = corrected_reruns.get(s)
        if configs == 0 and rerun is None:
            backtest = 'NOT_RUN'
        elif rerun is None:
            backtest = 'PENDING_CORRECTED_RERUN'
        elif family is None or family in VECTORBT_FAMILIES or not rerun.get('execution_audit_pass') or rerun.get('fill_rule') != 'CORRECTED_FILL_RULE':
            backtest = 'DIAGNOSTIC_ONLY'
        else:
            backtest = 'CREDIBLE'
        src['BACKTEST_STATUS'] = f'strategy_verdicts_r2.csv configurations={configs}; corrected rerun: ' + (f'audit_pass={rerun.get("execution_audit_pass")}' if rerun else 'none')
        if backtest != 'CREDIBLE':
            reasons.append(f'BACKTEST {backtest}' + (' (vectorbt mask: never per-order credible)' if family in VECTORBT_FAMILIES and rerun else ''))
        if rerun is not None:
            stat = 'PASS' if int(rerun.get('configurations_passing_all_od24', 0)) else 'FAIL'
            src['STATISTICAL_STATUS'] = f'corrected rerun OD-24 g1..g5 passing={int(rerun.get("configurations_passing_all_od24", 0))}'
        elif configs == 0:
            stat, src['STATISTICAL_STATUS'] = 'NOT_EVALUATED', 'no configuration'
        else:
            stat = ('PASS' if int(v2.configurations_passing_all_od24.get(s, 0)) else 'FAIL') + '_OLD_FILL_RULE'
            src['STATISTICAL_STATUS'] = f'strategy_verdicts_r2.csv OD-24 g1..g5 passing={int(v2.configurations_passing_all_od24.get(s, 0))} (old fill rule)'
        if stat != 'PASS':
            reasons.append(f'STATISTICAL {stat}')
        sessions = int(fwd.post_freeze_sessions_recorded.get(s, 0)) if fwd is not None and s in fwd.index else 0
        n = int(shadow_prospective.get(s, 0))
        if sessions >= FRESH_MINIMUM and fwd is not None and str(fwd.status.get(s)) == 'FRESH_OOS_PASS':
            forward_status = 'FRESH_OOS_PASS'
        elif n:
            forward_status = f'RESEARCH_SHADOW_ACCUMULATING({n})'
        else:
            forward_status = 'NONE'
        src['FORWARD_STATUS'] = f'FreshOOSLedger sessions={sessions} (min {FRESH_MINIMUM}); research_shadow PROSPECTIVE={n} (never Fresh OOS)'
        if forward_status != 'FRESH_OOS_PASS':
            reasons.append(f'FORWARD {forward_status}')
        five_pass = all(v in PASSING[k] for k, v in zip(STAGES[:5], (data, signal, backtest, stat, forward_status)))
        account = paper_accounts.get(s)
        if five_pass and account == 'PASS':
            paper = 'PAPER_PASSED'
        elif account in ('REHEARSAL', 'PASS', 'FAIL'):
            paper = 'PAPER_REHEARSAL'
        else:
            paper = 'NOT_ELIGIBLE'
        src['PAPER_STATUS'] = f'paper account: {account or "none"}; decided after the five prior stages'
        if paper != 'PAPER_PASSED':
            reasons.append(f'PAPER {paper}')
        final = 'QUALIFIED' if five_pass and paper == 'PAPER_PASSED' else 'NOT_QUALIFIED'
        rows.append({'group': r.group, 'strategy': s, 'DATA_STATUS': data, 'SIGNAL_STATUS': signal, 'BACKTEST_STATUS': backtest, 'STATISTICAL_STATUS': stat,
                     'FORWARD_STATUS': forward_status, 'PAPER_STATUS': paper, 'FINAL_QUALIFICATION': final, 'reasons': '; '.join(reasons),
                     **{f'source_{k}': v for k, v in src.items()}})
    return pd.DataFrame(rows), conflicts, blocker_rows


def shadow_prospective_counts(path=RESEARCH_SHADOW) -> dict:
    """Signal records (no 'kind') whose annotations do not mark them BACKFILLED. A BLOCKED or ANNOTATION record is never a signal."""
    import json
    records = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()] if Path(path).is_file() else []
    backfilled = {r['refers_to_sha256'] for r in records if r.get('kind') == 'ANNOTATION' and 'BACKFILLED' in str(r.get('status'))}
    counts = {}
    for r in records:
        if 'kind' not in r and r['sha256'] not in backfilled:
            counts[r['strategy']] = counts.get(r['strategy'], 0) + 1
    return counts


def paper_account_states(root=PAPER_ROOT) -> dict:
    import json
    out = {}
    for state in Path(root).glob('*/state.json') if Path(root).is_dir() else []:
        s = json.loads(state.read_text(encoding='utf-8'))
        out[s['strategy']] = 'REHEARSAL' if s.get('label') == 'PAPER_REHEARSAL' else 'FAIL'
    return out


def matrix():
    import pandas as pd
    MATRIX_OUT.mkdir(parents=True, exist_ok=True)
    read = lambda p: pd.read_csv(p, encoding='utf-8-sig') if Path(p).is_file() else pd.DataFrame()
    status, data_r2, native = read(DT / 'strategy_status_matrix.csv'), read(DATA_R2), read(NATIVE7)
    evidence = {}
    for frame_, name, columns in ((status, 'strategy_status_matrix.csv', ('DATA_detail', 'blockers')), (data_r2, 'p0_strategy_data_status_r2.csv', ('blockers',)),
                                  (native, 'native7_training_readiness_matrix_v4.csv', ('DATA_blockers',))):
        key = 'family_name' if 'family_name' in frame_ else 'strategy'
        for r in frame_.to_dict('records'):
            evidence.setdefault(r[key], []).extend((f'{name}:{c}', r[c]) for c in columns if isinstance(r.get(c), str) and r[c])
    frame, conflicts, blockers = build_matrix(status, data_r2, read(OUT / 'strategy_verdicts_r2.csv'), read(OUT / 'strategy_verdicts.csv'),
                                              read(P1_R2), read(OUT / 'forward_evidence_status_r2.csv'), shadow_prospective_counts(), paper_account_states(),
                                              evidence=evidence, certifications={})  # no certification record exists yet (OD-P6 / T1b open)
    pd.DataFrame(blockers).to_csv(MATRIX_OUT / 'data_blockers.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
    frame.to_csv(MATRIX_OUT / 'strategy_qualification_matrix.csv', index=False, encoding='utf-8-sig', lineterminator='\n')
    (MATRIX_OUT / 'qualification_conflicts.md').write_text('# Source conflicts (fail-closed: the conservative value was used; originals untouched)\n\n'
                                                           + ('\n'.join(f'- {c}' for c in conflicts) if conflicts else '- none'), encoding='utf-8')
    print(frame.FINAL_QUALIFICATION.value_counts().to_dict(), f'{len(conflicts)} conflicts')
    print(frame[['strategy', *STAGES, 'FINAL_QUALIFICATION']].to_string(index=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verdicts', 'verdicts-r2', 'shadow', 'forward', 'verify-shadow', 'matrix'))
    parser.add_argument('--date')
    args = parser.parse_args()
    if args.command == 'shadow':
        shadow(args.date)
    elif args.command == 'verify-shadow':
        from core.research.shadow_selection import verify
        print(verify(SHADOW))
    else:
        {'verdicts': verdicts, 'verdicts-r2': lambda: verdicts('strategy_verdicts_r2.csv', data_r2=True), 'forward': forward, 'matrix': matrix}[args.command]()
