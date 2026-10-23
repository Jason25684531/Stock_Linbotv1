"""The Track B reference-formula checks must all pass on the synthetic panel."""
from scripts import track_b_formulas as tf


def test_day3_factors_equal_their_reference_formulas():
    result = tf.day3_checks()
    assert {k: v['status'] for k, v in result.items()} == {'A101_003': 'PASS', 'A191_088': 'PASS', 'A191_132': 'PASS', 'RS_004_NEAR_HIGH_120D': 'PASS'}
    assert result['A101_003']['non_finite_correlations_mapped_to_missing'] > 0  # the documented rule is actually exercised


def test_d5_factors_equal_their_reference_formulas_and_need_the_declared_history():
    result = tf.d5_checks()
    assert all(v['status'] == 'PASS' for v in result.values())
    assert {k: v['required_history_sessions'] for k, v in result.items()} == {'momentum_20d': 21, 'momentum_60d': 61, 'momentum_12_1': 253, 'near_high_252d': 252, 'amihud_20d': 21}
