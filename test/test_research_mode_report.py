"""The report's per-stock profit must account for every ledger cash flow so that r4 shares use the true total."""
from scripts.research_mode_report import asset_pnl


def test_asset_pnl_includes_forced_exits_and_dividends_and_matches_nav_change():
    ledger = [
        {'kind': 'FILL', 'stock_id': 'A', 'side': 'buy', 'notional': 1000.0, 'fee': 20, 'tax': 0},
        {'kind': 'FILL', 'stock_id': 'B', 'side': 'buy', 'notional': 500.0, 'fee': 20, 'tax': 0},
        {'kind': 'CASH_CREDIT', 'stock_id': 'A', 'amount': 30.0},
        {'kind': 'FORCED_EXIT', 'stock_id': 'B', 'notional': 600.0, 'fee': 20, 'tax': 1},
        {'kind': 'ORDER_STATUS', 'stock_id': 'A'},
    ]
    last = {'positions': {'A': 100}, 'marks': {'A': 11.0}, 'stock_receivable': [], 'cash_receivable_claims': []}
    pnl = asset_pnl({'result': {'ledger': ledger, 'daily': [last]}})
    assert pnl == {'A': -1020.0 + 30.0 + 1100.0, 'B': -520.0 + 579.0}
    initial, final_cash = 10_000.0, 10_000.0 - 1020.0 - 520.0 + 30.0 + 579.0
    assert sum(pnl.values()) == (final_cash + 100 * 11.0) - initial


def test_load_runs_marks_tampered_or_unadmitted_checkpoints_and_status_reasons(tmp_path, monkeypatch):
    import json
    from scripts import research_mode_report as report
    from scripts import research_workflow as workflow
    folder = tmp_path / '000'
    folder.mkdir()
    for name in ('targets.csv', 'base.json', 'stress.json'):
        (folder / name).write_text(json.dumps({'reconciliation': {'status': 'PASS'}}) if name.endswith('.json') else 'a', encoding='utf-8')
    digests = {name: workflow.sha256(folder / name) for name in ('targets.csv', 'base.json', 'stress.json')}
    manifest = {'status': 'COMPUTABLE', 'experiment_id': 'exp-1', 'output_sha256': digests, 'config': {'config_id': 'c', 'strategy': 's'}}
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(workflow, 'verify_trials', lambda ledger=None: 'PASS')
    monkeypatch.setattr(workflow, '_trial_rows', lambda ledger=None: [{'experiment_id': 'exp-1'}])
    assert report.load_runs(tmp_path, 'DAY3')[0]['_tracks'] is not None
    monkeypatch.setattr(workflow, '_trial_rows', lambda ledger=None: [])  # not admitted in the research ledger
    row = report.load_runs(tmp_path, 'DAY3')[0]
    assert row['status'] == 'UNVERIFIED_CHECKPOINT' and row['reason'] == 'OUTPUT_SHA_OR_LEDGER_MISMATCH'
    monkeypatch.setattr(workflow, '_trial_rows', lambda ledger=None: [{'experiment_id': 'exp-1'}])
    (folder / 'stress.json').write_text(json.dumps({'reconciliation': {'status': 'FAIL'}}), encoding='utf-8')
    assert report.load_runs(tmp_path, 'DAY3')[0]['reason'] == 'OUTPUT_SHA_OR_LEDGER_MISMATCH'  # tampered file changes its digest
    status, reason = report.strategy_status([{'screen_status': 'RESEARCH_RUN_FAIL_SCREEN', 'gates': {'r1': True, 'r2': False, 'r3': False, 'r4': False, 'r5': True}},
                                             {'screen_status': 'NOT_COMPUTABLE', 'status': 'NOT_COMPUTABLE', 'reason': 'UNRESOLVED_CORPORATE_ACTION_HELD'}])
    assert status == 'RESEARCH_RUN_FAIL_SCREEN' and 'r2' in reason and 'r3' in reason
    status, reason = report.strategy_status([{'screen_status': 'NOT_COMPUTABLE', 'status': 'COMPUTABLE', 'screen_reason': 'UNDEFINED_SCREEN_METRIC', 'reason': None}])
    assert status == 'RESEARCH_NOT_COMPUTABLE' and 'UNDEFINED_SCREEN_METRIC x1' in reason
