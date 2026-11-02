from core.validation.param_scan import parameter_scan
from core.validation.rolling import rolling_metrics


def test_rolling_metrics_window_count_and_first_record():
    equity = [100.0 + index for index in range(70)]
    result = rolling_metrics(equity, windows=(63,))
    assert list(result) == [63]
    assert len(result[63]) == 8
    first = result[63][0]
    assert first['total_return'].value == equity[62] / equity[0] - 1


def test_parameter_scan_keeps_every_combination_in_product_order():
    rows = parameter_scan({'a': [1, 2], 'b': [10, 20]}, lambda a, b: {'score': a * b})
    assert rows == [
        {'a': 1, 'b': 10, 'score': 10},
        {'a': 1, 'b': 20, 'score': 20},
        {'a': 2, 'b': 10, 'score': 20},
        {'a': 2, 'b': 20, 'score': 40},
    ]
