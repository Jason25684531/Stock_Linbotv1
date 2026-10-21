from scripts.audit_strategy_history import digest, row_matches, verify_manifest, write


def test_history_rejects_same_id_with_changed_parameters():
    original = {'configuration_fingerprint': 'frozen', 'parameters': {'top_n': '5'}}
    assert not row_matches(original, {'config_hash': 'changed', 'top_n': '5'})
    assert not row_matches(original, {'top_n': '10'})
    assert row_matches(original, {'config_hash': 'frozen', 'top_n': '5'})


def test_manifest_verification_distinguishes_missing_and_corrupt_artifacts(tmp_path):
    score = tmp_path / 'score.csv'
    score.write_text('id,value\na,1\n')
    manifest = tmp_path / 'run_manifest.json'
    write(manifest, {'artifact_hashes': {'score.csv': digest(score), 'missing.csv': 'a' * 64}})
    hashes = {p.name: digest(p) for p in (score, manifest)}
    result = verify_manifest(manifest, tmp_path, {}, hashes)
    assert result['status'] == 'BLOCKED'
    assert [c['status'] for c in result['checks']] == ['PASS', 'BLOCKED']
    hashes['score.csv'] = 'b' * 64
    assert verify_manifest(manifest, tmp_path, {}, hashes)['status'] == 'FAIL'
