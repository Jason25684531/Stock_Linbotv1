import pytest
from scripts.research_mode_charts import write_equity_chart


def test_chart_requires_watermark_and_complete_cost_tracks(tmp_path):
    path = tmp_path / 'equity.png'
    with pytest.raises(ValueError, match='WATERMARK'):
        write_equity_chart(path, {}, label='UNLABELLED', assumptions_sha='a' * 64)
    assert not path.exists()
    daily = [{'date': '2025-01-02', 'nav': 100}, {'date': '2025-01-03', 'nav': 102}]
    result = {'status': 'COMPUTABLE', 'tracks': {'base': {'result': {'daily': daily}}, 'stress': {'result': {'daily': daily}}}}
    write_equity_chart(path, result, label='DIAGNOSTIC_ONLY', assumptions_sha='a' * 64)
    assert b'DIAGNOSTIC_ONLY' in path.read_bytes()
    with pytest.raises(FileExistsError):
        write_equity_chart(path, result, label='DIAGNOSTIC_ONLY', assumptions_sha='a' * 64)
