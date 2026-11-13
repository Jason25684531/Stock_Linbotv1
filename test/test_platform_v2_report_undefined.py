import json

from scripts import platform_v2_report


def test_model_report_preserves_undefined_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_v2_report, 'OUT', tmp_path)
    folder = tmp_path / 'models' / 'PRED1-20d-test'
    folder.mkdir(parents=True)
    evaluation = {
        'rows': 1, 'dates': 1, 'brier_m1': 0.0, 'brier_b0': 0.0,
        'brier_skill': None, 'brier_skill_ci95': [None, None],
        'yearly_skill': {'2020': None}, 'positive_skill_year_share': None,
        'calibration_slope': None, 'coverage_80': 0.8, 'median_abs_error': 0.0,
        'net_top_decile_mean': 0.0, 'net_top_decile_periods': 1, 'up_rate_test': 1.0,
        'reasons': ['BRIER_SKILL_UNDEFINED_ZERO_BASELINE'],
        'undefined_reasons': {'brier_skill': 'ZERO_BASELINE_BRIER',
                              'brier_skill_ci95': 'ZERO_BASELINE_BRIER',
                              'yearly_skill': {'2020': 'ZERO_BASELINE_BRIER'},
                              'positive_skill_year_share': 'UNDEFINED_YEARLY_BRIER_SKILL',
                              'calibration_slope': 'INSUFFICIENT_CALIBRATION_BINS'},
    }
    model = {'horizon': 20, 'model_version': folder.name, 'status': 'NOT_VALIDATED',
             'evaluation': evaluation, 'evidence': {'evaluation_id': 'test'},
             'snapshot_id': 'test', 'excluded_event_windows': 0}
    (folder / 'model.json').write_text(json.dumps(model), encoding='utf-8')

    platform_v2_report.model_report()
    text = (tmp_path / 'MODEL_VALIDATION_REPORT.md').read_text(encoding='utf-8')
    assert '不可計算：ZERO_BASELINE_BRIER' in text
    assert '不可計算：UNDEFINED_YEARLY_BRIER_SKILL' in text
    assert '不可計算：INSUFFICIENT_CALIBRATION_BINS' in text
    assert 'Brier M1 0.00000, B0 0.00000' in text


def test_model_report_handles_no_predictions(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_v2_report, 'OUT', tmp_path)
    folder = tmp_path / 'models' / 'PRED1-5d-test'
    folder.mkdir(parents=True)
    model = {'horizon': 5, 'model_version': folder.name, 'status': 'NOT_VALIDATED',
             'evaluation': {'horizon': 5, 'status': 'NOT_VALIDATED', 'reasons': ['NO_PREDICTIONS']}}
    (folder / 'model.json').write_text(json.dumps(model), encoding='utf-8')

    platform_v2_report.model_report()
    assert '不可計算：NO_PREDICTIONS' in (tmp_path / 'MODEL_VALIDATION_REPORT.md').read_text(encoding='utf-8')
