"""Public Research CLI. Fetch commands require the Owner-applied Guard patch; status/initialize perform no HTTP."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.runtime import operation_policy as policy, public_data, research_budget as budget


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('initialize')
    calendar = sub.add_parser('calendar')
    calendar.add_argument('--year', type=int, choices=(2026,), default=2026)
    detail = sub.add_parser('fetch-detail')
    detail.add_argument('--stock', required=True)
    detail.add_argument('--date', required=True)
    args = parser.parse_args(argv)
    policy.announce('research_public_data')
    try:
        if args.command == 'initialize':
            report = budget.initialize()
            report['legacy_cache_imported'] = public_data.import_verified_t1b_cache()
        elif args.command == 'status':
            report = budget.status()
        else:
            if args.command == 'calendar':
                response = public_data.request('GET', 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule', params={'year': str(args.year)})
            else:
                response = public_data.request('GET', 'https://www.twse.com.tw' + public_data.DETAIL_PATHS[0],
                                               params={'STK_NO': args.stock, 'T': args.date, 'response': 'json'})
            report = {'mode': policy.current_mode().value, 'status': response.status_code, 'cache_hit': response.research_cache_hit,
                      'budget': budget.status()}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (PermissionError, OSError, ValueError, TimeoutError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
