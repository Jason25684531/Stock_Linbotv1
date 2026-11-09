import json

from app import web_server


def test_recovery_status_requires_a_verified_run(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, 'LEGACY_SUMMARY', tmp_path / 'old.json')
    monkeypatch.setattr(web_server, 'LEGACY_ITEMS', tmp_path / 'old_items')
    monkeypatch.setattr(web_server, 'LEGACY_RECOVERY', tmp_path)
    (tmp_path / 'mean_reversion.json').write_text(json.dumps({'status': 'SUCCESS', 'run_id': 'bad'}), encoding='utf-8')
    monkeypatch.setattr(web_server.run_store, 'load_run', lambda _: (_ for _ in ()).throw(ValueError('bad manifest')))
    result = web_server._legacy_status('mean_reversion')
    assert result['recovery_baseline']['status'] == 'UNVERIFIED'
    assert result['recommendation'] == 'NOT_EVALUATED'


def test_wrong_valid_run_is_not_labelled_as_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, 'LEGACY_SUMMARY', tmp_path / 'old.json')
    monkeypatch.setattr(web_server, 'LEGACY_ITEMS', tmp_path / 'old_items')
    monkeypatch.setattr(web_server, 'LEGACY_RECOVERY', tmp_path)
    (tmp_path / 'mean_reversion.json').write_text(json.dumps({'run_id': 'valid', 'experiment_id': 'trial'}), encoding='utf-8')
    monkeypatch.setattr(web_server.run_store, 'load_run', lambda _: {'run': {'strategy_id': 'mean_reversion', 'item_id': 'OTHER', 'trial_id': 'trial'}})
    assert web_server._legacy_status('mean_reversion')['recovery_baseline']['status'] == 'UNVERIFIED'


def test_missing_trial_id_is_not_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, 'LEGACY_SUMMARY', tmp_path / 'old.json')
    monkeypatch.setattr(web_server, 'LEGACY_ITEMS', tmp_path / 'old_items')
    monkeypatch.setattr(web_server, 'LEGACY_RECOVERY', tmp_path)
    (tmp_path / 'mean_reversion.json').write_text(json.dumps({'run_id': 'valid'}), encoding='utf-8')
    monkeypatch.setattr(web_server.run_store, 'load_run', lambda _: {'run': {'strategy_id': 'mean_reversion', 'item_id': 'OD29:mean_reversion'}})
    assert web_server._legacy_status('mean_reversion')['recovery_baseline']['status'] == 'UNVERIFIED'
