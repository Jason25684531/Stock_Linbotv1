"""Read-only trust check of the production status file (OD-32). Never writes, restores or touches the production folder.

Result is UNTRUSTED when the content is a known test output or older than the latest official session; otherwise UNVERIFIED (never TRUSTED): a new
mtime is not evidence. Trust needs the evening pipeline's pipeline_runs row (run_daily success) plus asof_date equal to the latest session.
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OPERATION = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1'
OUT = ROOT / 'outputs/dual_track_validation/2026-10-23/final_closure'
KNOWN_TEST_OUTPUT = {'bb561a27c5559ea248c059fa63a908bdaa4eaf76db7e524e63efa23e39b011b1': 'test_run_daily_persistence.py::test_run_daily_for_date_persists_every_persistence_strategy (asof 2026-04-28)'}
EVIDENCE_FILES = {'runtime_validation_manifest': 'runtime_validation_manifest.json', 'promotion_review': 'promotion_review.json', 'fresh_oos_availability': 'FreshOOSAvailabilityReport.json',
                  'fundamental_runtime_spec': 'FundamentalRuntimeSpec.json'}
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(latest_session=None):
    path = OPERATION / 'fundamental_production_status.json'
    stat, content = path.stat(), json.loads(path.read_text(encoding='utf-8'))
    latest = latest_session or str(pd.read_parquet(ROOT / 'outputs/dual_track_validation/2026-10-23/official_bars.parquet', columns=['trade_date']).trade_date.max().date())
    reasons = []
    if sha(path) in KNOWN_TEST_OUTPUT:
        reasons.append(f'content is byte-identical to a test output: {KNOWN_TEST_OUTPUT[sha(path)]}')
    if content['asof_date'] < latest:
        reasons.append(f"asof_date {content['asof_date']} is older than the latest official session {latest}")
    fail_closed = {'production_eligibility': content['production_eligibility'], 'production_enable_flag': content['production_enable_flag'], 'production_ready': content['production_ready'],
                   'broker_submission_status': content['broker_submission_status'], 'recommendation_count': content['recommendation_count']}
    return {'checked_at': datetime.now().isoformat(timespec='seconds'), 'path': str(path.resolve()), 'sha256': sha(path), 'mtime': datetime.fromtimestamp(stat.st_mtime).isoformat(), 'size': stat.st_size,
            'asof_date': content['asof_date'], 'latest_official_session': latest, 'classification': 'PRODUCTION_STATUS_UNTRUSTED' if reasons else 'PRODUCTION_STATUS_UNVERIFIED',
            'reasons': reasons or ['content is not a known test output and is not older than the latest session, but trust still needs the pipeline_runs run_daily row; mtime alone is not evidence'],
            'evidence_hashes_match_current_files': {k: content['artifact_hashes'][k] == sha(OPERATION / f) for k, f in EVIDENCE_FILES.items()},
            'content_fail_closed_fields': fail_closed, 'content_is_blocking': content['production_eligibility'] == 'BLOCKED' and not content['production_enable_flag'] and content['broker_submission_status'] == 'DISABLED',
            'wrote_anything': False}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    result = check()
    if len(sys.argv) > 1 and sys.argv[1] == 'record':
        target = OUT / f"production_status_check_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        target.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('classification', 'sha256', 'mtime', 'asof_date', 'latest_official_session', 'reasons', 'content_is_blocking')}, ensure_ascii=False, indent=1))
