from scripts.research_failure_review import NA, base, parts, screened

VALUES = {'net_sharpe_2x': 1.0, 'excess_sharpe_2x': .5, 'bootstrap_lower': -.1, 'deflated_sharpe': .01, 'top5_assets_profit_share': .7,
          'best5pct_days_return_share': 2.0, 'regime': {'bull': {'annualized_excess': .1}, 'bear': {'annualized_excess': -.1}}}


def test_first_failing_gate_is_primary_and_rest_secondary():
    row = screened(base(), {'r1': True, 'r2': False, 'r3': False, 'r4': False, 'r5': True}, VALUES, 'V1')
    assert row['status'] == 'SCREEN_FAIL' and row['primary_failure'].startswith('r2') and row['secondary_reasons'].startswith('r3')
    assert screened(base(), dict.fromkeys(('r1', 'r2', 'r3', 'r4', 'r5'), True), VALUES, 'V2')['status'] == 'PASS'


def test_unscreened_rows_keep_metrics_not_available():
    row = base(status='DATA_BLOCKED')
    assert row['r1'] == NA and row['total_return_2x'] == NA
    assert parts('X__EQUAL__TOP20__REB60__SCORE_WEIGHTED') == (20, 60, 'score_weighted') and parts(None) == (NA, NA, NA)
