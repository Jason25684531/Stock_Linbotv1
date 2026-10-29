"""Read-only check of the Owner's production authorization setup. Creates, edits and grants nothing.

Reports, for the grant file in use (STOCK_LINBOT_GRANTS or .claude/owner_grants.json): whether grant_trusted() accepts it and why not, which
operations it grants, whether production_mode is present, and the effective execution mode a process would get. Exit 0 only when the file is
trusted and the effective mode with STOCK_LINBOT_MODE=production would be PRODUCTION; otherwise 1 (Production stays denied, Research is unaffected).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import task_controller as tc  # noqa: E402


def untrusted_reason(path: Path) -> str:
    """Mirror of grant_trusted() with the reason; the decision itself is always tc.grant_trusted()."""
    import stat
    if not path.is_file():
        return 'file does not exist'
    if path.resolve().is_relative_to(tc.ROOT):
        return 'file is inside the repository (move it outside and point STOCK_LINBOT_GRANTS at it)'
    if getattr(path.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY:
        return 'read-only attribute set (this account could clear it); use an ACL instead'
    try:
        if tc.file_owner(path) == tc.current_user():
            return 'file is owned by the account that runs the agent; the Owner account must own it'
        with path.open('r+b'):
            return 'this account can write the file; remove its write permission (ACL)'
    except PermissionError:
        return ''
    except OSError as exc:
        return f'cannot inspect: {exc}'
    return ''


def main() -> int:
    path = Path(tc.GRANTS)
    reason = untrusted_reason(path)
    trusted = tc.grant_trusted(path)
    print(f'grant file : {path}')
    print(f'trusted    : {trusted}' + (f' ({reason})' if not trusted else ''))
    operations = []
    try:
        now = datetime.now(timezone.utc)
        for grant in json.loads(path.read_text(encoding='utf-8'))['grants']:
            live = datetime.fromisoformat(grant['expires_at']) > now
            operations.append(f"{grant['operation']} (max_uses {grant.get('max_uses', 1)}, {'valid' if live else 'EXPIRED'})")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'grants     : unreadable ({type(exc).__name__})')
    else:
        print('grants     : ' + ('; '.join(operations) or 'none'))
    os.environ['STOCK_LINBOT_MODE'] = 'production'  # this process only, to show what a Production process would resolve to
    from core.runtime.operation_policy import current_mode
    mode = current_mode(path)
    print(f'mode if STOCK_LINBOT_MODE=production: {mode.value}')
    if mode.value != 'PRODUCTION':
        print('Production operations stay DENIED. Research work is not affected. Setup steps: docs/research/production_grant_setup.md')
    return 0 if trusted and mode.value == 'PRODUCTION' else 1


if __name__ == '__main__':
    raise SystemExit(main())
