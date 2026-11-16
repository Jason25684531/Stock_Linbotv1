import pytest
import pandas as pd

from scripts.platform_v2_ledger_audit import daily_ledger_mismatch
from scripts.platform_v2_ledger_repair import reusable
from scripts.platform_v2_publication import native_two_track_verified


FILES = {'fills_stress.csv', 'daily_ledger_stress.csv', 'positions_daily_stress.csv',
         'corporate_actions_stress.csv', 'open_positions_stress.json'}
TRACK = {'status': 'PUBLISHABLE', 'missing_files': [], 'evidence_mode': 'NATIVE_SAVED_EVENTS'}
RECORD = {'publishable': True, 'reconciliation': {'base': {'status': 'PUBLISHABLE'}, 'stress': {'status': 'PUBLISHABLE'}}}
AUDIT = {'saved_publishable': True, 'manifest_sha256': 'manifest', 'tracks': {'base': TRACK, 'stress': TRACK}}


def test_repair_reuses_only_native_complete_reconciled_run():
    assert reusable(RECORD, FILES, AUDIT, 'manifest')


@pytest.mark.parametrize('changes', [
    {'files': FILES - {'corporate_actions_stress.csv'}},
    {'tracks': {'base': TRACK, 'stress': TRACK | {'evidence_mode': 'RECOVERED_FROM_BASE_EVENT_TERMS'}}},
    {'tracks': {'base': TRACK, 'stress': TRACK | {'missing_files': ['fills_stress:required_columns']}}},
    {'tracks': {'base': TRACK, 'stress': TRACK | {'status': 'NOT_PUBLISHABLE'}}},
    {'reconciliation': {'base': {'status': 'PUBLISHABLE'}, 'stress': {'status': 'NOT_PUBLISHABLE'}}},
    {'manifest_sha256': 'changed'},
    {'saved_publishable': False},
])
def test_repair_does_not_reuse_incomplete_or_stale_evidence(changes):
    changes = dict(changes)
    files = changes.pop('files', FILES)
    reconciliation = changes.pop('reconciliation', None)
    record = RECORD | ({'reconciliation': reconciliation} if reconciliation is not None else {})
    audit = AUDIT | changes
    assert not reusable(record, files, audit, 'manifest')


def test_audit_compares_saved_daily_ledger_to_independent_rebuild():
    nav = [('2020-01-02', 1000.0), ('2020-01-03', 1100.0)]
    expected = pd.DataFrame([
        {'date': day, 'cash': value / 2, 'positions_value': value / 2, 'nav': value, 'holdings': 1}
        for day, value in nav
    ])
    assert daily_ledger_mismatch(expected.copy(), expected.copy(), nav) is None
    damaged = expected.copy()
    damaged.loc[1, 'cash'] += 1
    assert daily_ledger_mismatch(damaged, expected, nav) == '2020-01-03'
    extra = pd.concat([expected, expected.tail(1).assign(date='2020-01-06')], ignore_index=True)
    assert daily_ledger_mismatch(extra, expected, nav) == '2020-01-06'
    assert daily_ledger_mismatch(expected.head(1), expected, nav) == '2020-01-03'


def test_publication_requires_native_events_on_both_tracks():
    row = {'saved_publishable': True, 'tracks': {'base': TRACK, 'stress': TRACK}}
    assert native_two_track_verified(row)
    recovered = row | {'tracks': {'base': TRACK, 'stress': TRACK | {'evidence_mode': 'RECOVERED_FROM_BASE_EVENT_TERMS',
                                                                     'missing_files': ['corporate_actions_stress']}}}
    assert not native_two_track_verified(recovered)
