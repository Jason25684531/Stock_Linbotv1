"""Monthly revenue split (design D4, spec monthly-revenue-split-commit): characterization first, then the service commit path. SQLite only."""
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from core import update_monthly_revenue as rev

RAW = pd.DataFrame({'公司代號': ['1101', '2330.0', '2317'], '公司名稱': ['台泥', '台積電', '鴻海'],
                    '當月營收': ['1,234', '250,000,000', 'x'], '去年同月增減(%)': ['5.5', '-1.25', ''], 'year': 2026, 'month': 10})


def test_cleaning_characterization():
    clean = rev.clean_revenue_frame(RAW)
    assert clean.columns.tolist() == ['stock_id', 'year', 'month', 'revenue', 'revenue_yoy']
    assert clean['stock_id'].tolist() == ['1101', '2330', '2317']  # '.0' suffix removed
    assert clean['revenue'].tolist() == [1234000.0, 250000000000.0, 0.0]  # thousands to units, unparseable -> 0
    assert clean['revenue_yoy'].tolist() == [5.5, -1.25, 0.0]
    assert rev.clean_revenue_frame(RAW.drop(columns=['當月營收'])) is None


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'rev.db'}")
    monkeypatch.setattr(rev, 'get_db_engine', lambda: engine)
    return engine


def test_process_and_save_keeps_its_legacy_behaviour(engine, capsys):
    assert rev.process_and_save(RAW) is None
    stored = pd.read_sql(text('SELECT * FROM monthly_revenue ORDER BY stock_id'), engine)
    assert stored['stock_id'].tolist() == ['1101', '2317', '2330'] and '寫入成功 (3 筆)' in capsys.readouterr().out
    rev.process_and_save(RAW.drop(columns=['公司代號']))
    assert '找不到關鍵欄位' in capsys.readouterr().out


def test_write_error_is_swallowed_by_the_legacy_caller_only(engine, monkeypatch, capsys):
    monkeypatch.setattr(rev, 'write_revenue_frame', lambda conn, frame: (_ for _ in ()).throw(RuntimeError('disk')))
    assert rev.process_and_save(RAW) is None and '處理失敗: disk' in capsys.readouterr().out
    monkeypatch.undo()
    with pytest.raises(Exception), engine.begin() as conn:
        rev.write_revenue_frame(conn, pd.DataFrame({'bad column': [1]}))


def test_service_commits_revenue_per_period_through_the_safe_path(tmp_path, engine):
    from services import market_data_update as mdu
    with engine.begin() as conn:
        rev.write_revenue_frame(conn, rev.clean_revenue_frame(RAW.assign(month=9)))  # an older period, untouched by the update
    frame = rev.clean_revenue_frame(RAW)
    adapter = lambda day: {'frame': frame, 'rows': len(frame), 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': []}
    committer = mdu.COMMITTERS['monthly_revenue']
    committer._engine = lambda: engine
    service = mdu.MarketDataUpdateService(root=tmp_path / 'u', adapters={'monthly_revenue': adapter}, committers={'monthly_revenue': committer},
                                          enforce=lambda table: None, today='2026-11-06', calendar=lambda d: 'OPEN_OBSERVED')
    row = service.run('manual', dates=['2026-11-05'], datasets=['monthly_revenue'])['results'][0]
    assert row['status'] == 'PIT_UNVERIFIED' and row['committed_rows'] == 3 and row['reread']['match'] is True
    assert pd.read_sql(text('SELECT COUNT(*) AS n FROM monthly_revenue'), engine)['n'][0] == 6
    floor = mdu.MarketDataUpdateService(root=tmp_path / 'f', adapters={'monthly_revenue': lambda d: {**adapter(d), 'frame': rev.clean_revenue_frame(RAW.assign(year=2025, month=12))}},
                                        committers={'monthly_revenue': committer}, enforce=lambda t: None, today='2026-11-06', calendar=lambda d: 'OPEN_OBSERVED')
    assert floor.run('manual', dates=['2026-11-05'], datasets=['monthly_revenue'])['results'][0]['status'] == 'COMMIT_FLOOR_VIOLATION'
    assert mdu.SPLIT_MODE['monthly_revenue'] == 'SPLIT'
