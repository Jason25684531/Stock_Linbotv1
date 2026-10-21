from scripts.audit_strategy_history import digest, row_matches, verify_manifest, write


def test_history_rejects_same_id_with_changed_parameters():
    original = {'configuration_fingerprint': 'frozen', 'parameters': {'top_n': '5'}}
    assert not row_matches(original, {'config_hash': 'changed', 'top_n': '5'})
    assert not row_matches(original, {'top_n': '10'})
    assert row_matches(original, {'config_hash': 'frozen', 'top_n': '5'})
    assert not row_matches(original, {'config_hash': 'frozen', 'top_n': '10'})
    assert not row_matches({'parameters': {'stock_weighting': 'equal'}}, {'stock_weighting': 'score_weighted'})


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


def test_input_manifest_does_not_resolve_to_own_run_manifest(tmp_path):
    prior = tmp_path / 'prior'
    prior.mkdir()
    source = prior / 'run_manifest.json'
    write(source, {'source': True})
    current = tmp_path / 'run_manifest.json'
    write(current, {'input_hashes': {'run_manifest.json': digest(source)}})
    hashes = {p.relative_to(tmp_path).as_posix(): digest(p) for p in (source, current)}
    result = verify_manifest(current, tmp_path, {'run_manifest.json': [source, current]}, hashes)
    assert result['status'] == 'PASS'
    assert result['checks'][0]['paths'] == ['prior/run_manifest.json']
