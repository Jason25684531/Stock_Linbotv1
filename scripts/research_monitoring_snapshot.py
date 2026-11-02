"""Write the daily-market monitoring snapshot from the observed official calendar; no network, no inference."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

OUTPUT = ROOT / 'outputs/research_workflow/research_monitoring.json'
STATUS = ROOT / 'outputs/research_workflow/research_status_snapshot/research_status.json'


def build(days, closures, years, as_of, strategies=None):
    """Sessions are observed trading days plus scheduled-open days of an imported year.

    The calendar is complete only up to the day before the first UNVERIFIED or CONFLICT day,
    so staleness past an unimported annual schedule stays unknown instead of guessed.
    """
    import pandas as pd
    observed = sorted(day.date() for day, (trading, *_) in days.items() if trading)
    if not observed:
        raise ValueError('NO_OBSERVED_SESSIONS')
    latest = observed[-1]
    sessions, complete_through, blocker = list(observed), as_of, None
    day = max(days).date() + timedelta(days=1)
    while day <= as_of:
        status = workflow.session_status(pd.Timestamp(day), days, closures, years)
        if status in ('EXPECTED_OPEN_UNVERIFIED', 'CONFLICT'):
            complete_through, blocker = day - timedelta(days=1), workflow.UNVERIFIED_SCHEDULE if status != 'CONFLICT' else status
            break
        if status == 'EXPECTED_OPEN':
            sessions.append(day)
        day += timedelta(days=1)
    market = {'latest_official_session': latest.isoformat(), 'as_of': as_of.isoformat(),
              'calendar_complete_through': complete_through.isoformat(),
              'official_sessions': [d.isoformat() for d in sessions], 'calendar_source': str(workflow.MI_INDEX.relative_to(ROOT))}
    if blocker:
        market['calendar_blocker'] = blocker
    return {'daily_market': market, **degradation(strategies)}


def degradation(strategies):
    """Only a strategy at OOS_PASSED or above with registered rolling thresholds can degrade; otherwise there is nothing to evaluate.
    `None` means the status snapshot is missing, which is missing evidence rather than proof that no strategy is verified."""
    if strategies is None:
        return {'degradation_status': 'NOT_EVALUABLE', 'degradation_reason': 'STATUS_SNAPSHOT_MISSING'}
    verified = [s for s in strategies if s.get('status') in ('OOS_PASSED', 'PAPER_ELIGIBLE')]
    if not verified:
        return {'degradation_status': 'NOT_EVALUABLE', 'degradation_reason': 'NO_VERIFIED_STRATEGY'}
    # ponytail: no threshold registry exists yet; a verified strategy without one stays NOT_EVALUABLE, add rolling_metrics when one is registered.
    return {'degradation_status': 'NOT_EVALUABLE', 'degradation_reason': 'NO_REGISTERED_THRESHOLDS',
            'verified_strategies': [s['strategy'] for s in verified]}


def main(as_of=None, output=OUTPUT):
    closures, years = workflow.load_closures()
    status = json.loads(STATUS.read_text(encoding='utf-8'))['strategies'] if STATUS.is_file() else None
    snapshot = build(workflow.load_sessions(), closures, years, date.fromisoformat(as_of) if as_of else date.today(), status)
    output = Path(output)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    os.replace(temporary, output)
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of')
    parser.add_argument('--output', default=str(OUTPUT))
    print(json.dumps(main(parser.parse_args().as_of, parser.parse_args().output)['daily_market'], indent=1))
