from pathlib import Path

import pandas as pd

from core.research.factors import factor_result


def test_factor_result_reports_zero_dispersion_from_the_factor_stage_without_io():
    result = factor_result(pd.DataFrame({"2330": [1.0, 1.0]}), window=2)

    assert result.diagnostics == [{"stage": "factor", "code": "W012_zero_dispersion", "stock_id": "2330"}]
    source = Path(factor_result.__code__.co_filename).read_text(encoding="utf-8")
    assert "to_csv" not in source
    assert "Path.write" not in source
