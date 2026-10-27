import importlib
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

import scripts.track_b_capture as tbc


@pytest.fixture
def fake_job(monkeypatch, tmp_path):
    calls = {}
    job = types.ModuleType('jobs.run_fundamental_strategy_validation')
    job.run_pipeline = lambda **kw: (calls.update(kw), kw['output_dir'].mkdir(parents=True, exist_ok=True))  # the real job creates its output folder
    monkeypatch.setitem(sys.modules, 'jobs.run_fundamental_strategy_validation', job)
    monkeypatch.setattr(importlib.import_module('jobs'), 'run_fundamental_strategy_validation', job, raising=False)  # `import a.b as x` reads the package attribute

    def capture(family, collector):
        returns = pd.Series([0.01, 0.02], index=pd.to_datetime(['2026-01-02', '2026-01-05']))
        collector.add('cfg', 'BASE_COST', returns)
        collector.add_orders('cfg', pd.DataFrame({c: [] for c in tbc.ORDER_COLUMNS}))
        return lambda: None

    monkeypatch.setattr(tbc, 'install_capture', capture)
    monkeypatch.setattr(tbc, 'OUT', tmp_path / 'default_out')
    monkeypatch.setattr(tbc, 'write', lambda path, obj: Path(path).write_text('{}'))
    monkeypatch.setattr(tbc, 'compare_outputs', lambda *a: {})
    monkeypatch.setitem(tbc.CANONICAL, 'G2G3', tmp_path)
    return calls


def test_rerun_defaults_keep_the_archived_layout(fake_job, tmp_path):
    (tmp_path / 'default_out').mkdir()
    tbc.rerun('G2G3')
    assert 'fundamental_matrix' not in fake_job and fake_job['output_dir'] == tmp_path / 'default_out/track_b_runs/g2g3'
    assert (tmp_path / 'default_out/returns_g2g3.parquet').exists()


def test_rerun_with_matrix_and_out_writes_only_to_out(fake_job, tmp_path):
    (tmp_path / 'default_out').mkdir()
    tbc.rerun('G2G3', fundamental_matrix='v31.parquet', out=tmp_path / 'diag')
    assert fake_job['fundamental_matrix'] == Path('v31.parquet') and fake_job['output_dir'] == tmp_path / 'diag/run'
    assert (tmp_path / 'diag/returns_g2g3.parquet').exists() and (tmp_path / 'diag/orders_g2g3.parquet').exists()
    assert not (tmp_path / 'default_out/returns_g2g3.parquet').exists()


# ---- Phase 4 / 5 ----
from scripts import post_archive_readiness as par  # noqa: E402

PERFORMANCE_COLUMNS = {'total_return', 'cagr', 'sharpe_net', 'excess_sharpe', 'max_drawdown', 'deflated_sharpe', 'median_net_sharpe', 'bootstrap_lower'}


def _release(gap=None, wall=None):
    return {'event': 'RELEASED', 'gate_gap_seconds': gap, 'gate_release_wall': wall}


def test_gate_verifier_passes_only_when_every_pair_is_recorded_and_at_least_3_2_seconds():
    assert par.verify_gate_gaps([_release(None, 100.0), _release(3.3, 103.3), _release(3.5, 106.8)])['status'] == 'PASS'
    assert par.verify_gate_gaps([_release(None, 100.0), _release(None, 103.3)])['status'] == 'PASS'  # first release of a new process: wall-clock difference
    below = par.verify_gate_gaps([_release(None, 100.0), _release(3.19, 103.19)])
    assert below['status'] == 'FAIL' and below['below_minimum'][0]['gap_seconds'] == pytest.approx(3.19)


def test_gate_verifier_never_assumes_compliance():
    assert par.verify_gate_gaps([_release(), _release(), _release()])['status'] == 'FAIL'  # nothing recorded
    assert par.verify_gate_gaps([{'event': 'SENT'}])['status'] == 'NO_DOWNLOADS'


def test_historical_ledger_stays_a_failure_and_is_read_without_change():
    import json
    ledger = par.DT / 'request_ledger.jsonl'
    events = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    assert par.verify_gate_gaps(events)['status'] == 'FAIL'
    assert par.timestamp_gaps(events)['smallest_logged_gap_seconds'] == pytest.approx(2.934, abs=0.001)  # the retained historical pair


def test_strategy15_ranking_reads_no_performance_column_and_uses_no_forbidden_label():
    import pandas as pd
    assert not set(par.RANK_INPUT_COLUMNS) & PERFORMANCE_COLUMNS
    frame = pd.read_csv(par.OUT / 'strategy15_next_action_matrix.csv', encoding='utf-8-sig')
    assert len(frame) == 15 and sorted(frame.closest_to_trusted_development_rank) == list(range(1, 16))
    text = frame.to_csv()
    assert not any(label in text for label in par.FORBIDDEN_LABELS)
    assert (frame[frame.group == 'B1_G2_G3'].DATA == 'DATA_BLOCKED').all()  # YoY correctness never upgrades the gate


# ---- Phase 3: Track A fix 3 (listed codes only) had no executable test, only a description in track_a_readiness.json ----
def test_listed_universe_never_guesses_when_no_official_response_is_cached(tmp_path):
    from core.research.native_frame import listed_universe

    with pytest.raises(RuntimeError, match="LISTED_UNIVERSE_UNAVAILABLE"):
        listed_universe("2026-10-22", directories=(tmp_path,))


def test_listed_universe_equals_the_four_digit_official_bars_of_the_same_session():
    import pandas as pd
    from core.research.native_frame import listed_universe

    bars = pd.read_parquet(par.BARS, columns=['trade_date', 'stock_id'])
    expected = frozenset(bars[bars.trade_date == '2026-10-22'].stock_id.astype(str))
    universe = listed_universe("2026-10-22")
    assert universe == expected and all(len(code) == 4 and code.isdigit() for code in universe)


def test_spec_v2_draft_carries_every_key_the_training_builder_reads_and_stays_a_draft():
    import json
    from core.research import model_training
    from core.research.native_models import DERIVED_FROM

    spec = json.loads((par.OUT / 'retraining_spec_v2_draft.json').read_text(encoding='utf-8'))
    assert spec['status'] == 'DRAFT_PENDING_OWNER_APPROVAL' and spec['corporate_actions']['status'] == 'OWNER_DECISION_PENDING'
    assert spec['sha256'] == model_training.spec_hash(spec)
    assert {r for f in spec['feature_order'] for r in DERIVED_FROM.get(f.removesuffix('_z'), (f.removesuffix('_z'),))} <= set(spec['model_inputs'])
    for meta in spec['strategies'].values():  # keys read by build_training_frame / train
        assert {'look_ahead_sessions', 'target_return'} <= set(meta['label']) and meta['model_name']
    assert {'train', 'validation', 'embargo_sessions'} <= set(spec['split']) and 'eval_metric' in spec['hyperparameters']
    assert len(spec['feature_order']) == 17
    assert {c['change']: c['status'] for c in spec['code_changes_status']}[3] == 'PARTIAL'  # never claimed fully fixed from a description


def test_status_writer_window_compares_utc_rows_with_the_utc_mtime():
    from datetime import datetime, timezone
    from scripts.post_archive_readiness import written_inside
    rows = [{'started_at': '2026-10-24 10:31:51', 'finished_at': '2026-10-24 10:33:07'}]  # run_daily, naive UTC as core.db_helper writes it
    mtime = datetime(2026, 10, 24, 10, 33, 6, tzinfo=timezone.utc).timestamp()  # 18:33:06 in Taipei
    assert written_inside(rows, mtime)
    assert not written_inside(rows, mtime + 3600) and not written_inside([{**rows[0], 'finished_at': 'None'}], mtime)


def test_spec_r4_carries_the_owner_decisions_the_trainer_enforces_and_stays_a_draft():
    import json
    from core.research import model_training

    spec = json.loads((par.OUT / 'retraining_spec_v2_draft_r4.json').read_text(encoding='utf-8'))
    recheck = json.loads((par.OUT / 'native7_data_manifest_recheck.json').read_text(encoding='utf-8'))
    assert spec['status'] == 'DRAFT_PENDING_OWNER_APPROVAL' and spec['sha256'] == model_training.spec_hash(spec)
    assert spec['corporate_actions']['label_policy'] == model_training.VERIFIED_ONLY  # train() refuses anything else
    assert recheck['status'] == 'UNCHANGED' and spec['data_manifest_sha256'] == recheck['overall_sha256']  # guard 6 has a registered value
    assert spec['acceptance']['metrics'] == json.loads((par.OUT / 'retraining_spec_v2_draft_r3.json').read_text(encoding='utf-8'))['acceptance']['metrics']  # OD-T4: never lowered
