"""Reuse V3/V4 browser checks while redirecting all evidence to this acceptance."""
import sys
import threading
from unittest.mock import patch

from scripts.system_acceptance import OUT, now, write
from scripts.system_operations_probe import readonly_sql


def main():
    readonly_sql()
    from app import app
    from scripts import verify_web_v3 as v3, verify_strategy_v4 as v4
    from werkzeug.serving import make_server
    (OUT / 'browser_v4').mkdir(parents=True, exist_ok=True)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = now()
    try:
        with patch.object(v3, 'ROOT', OUT / 'browser_v3'), patch.object(sys, 'argv', ['verify_web_v3', '--base', f'http://127.0.0.1:{server.server_port}']):
            code_v3 = v3.main()
        with patch.object(v4, 'Path', lambda name: OUT / 'browser_v4'):
            code_v4 = v4.main()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    write('checks/browser_results.json', {'started_at': started, 'ended_at': now(), 'v3_exit': code_v3, 'v4_exit': code_v4,
                                       'status': 'PASS' if code_v3 == code_v4 == 0 else 'FAIL',
                                       'server_scope': 'owned ephemeral local services, closed after test'})
    return int(code_v3 != 0 or code_v4 != 0)


if __name__ == '__main__':
    raise SystemExit(main())
