"""Browser acceptance of the 2026-11-10 engine-accounting round on the existing web (no new site, no new framework).

    python -m scripts.verify_engine_fix_browser

Starts the Flask app on a free port, walks /strategies, /reference, /admin/data (guest, then admin) and one data-trust page at 1280x800
and 390x844, and stores PNGs, aria snapshots and summary.json under outputs/verify_runs/<UTC stamp>/. Exit 1 when the server does not
start, a page does not answer 200, a page error fires, or a server-rendered testid is missing. It presses no update or batch button.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'local-review-token'
VIEWPORTS = {'desktop': (1280, 800), 'mobile': (390, 844)}
GUEST_PAGES = {  # path: required testids; Alpine-rendered ones are awaited until attached
    '/strategies': ('strategy-center', 'history-trust', 'engine-compare'),
    '/reference': ('reference', 'cross-regime'),
    '/admin/data': ('historical-dashboard', 'fix-evidence', 'fix-tasks'),
}
COUNTED = ('engine-compare', 'history-trust', 'legacy-compare', 'compare-table', 'compare-row', 'fix-task-row', 'history-run', 'history-refresh')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start(port):
    code = "import os, sys; sys.path.insert(0, r'%s'); os.chdir(r'%s'); from app import app; app.run(host='127.0.0.1', port=%d)" % (ROOT, ROOT, port)
    env = {**os.environ, 'STOCK_LINBOT_ADMIN_TOKEN': TOKEN, 'FLASK_SECRET_KEY': 'local-review-secret', 'PYTHONIOENCODING': 'utf-8'}
    log = open(OUT / 'server.log', 'w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(180):
        if proc.poll() is not None:
            break
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            time.sleep(1)
            return proc
        except OSError:
            time.sleep(1)
    proc.kill()
    raise RuntimeError(f'server did not start; see {OUT / "server.log"}')


def visit(page, base, path, name, required, errors):
    """One page at both viewports: status, required and counted testids, PNG and aria snapshot per viewport."""
    result = {'path': path, 'status': None, 'missing': [], 'counts': {}, 'mobile_fits': None}
    for label, (width, height) in VIEWPORTS.items():
        page.set_viewport_size({'width': width, 'height': height})
        response = page.goto(base + path, wait_until='networkidle', timeout=120000)
        result['status'] = response.status if response else None
        if label == 'desktop':
            for testid in required:
                try:
                    page.locator(f'[data-testid="{testid}"]').first.wait_for(state='attached', timeout=15000)
                except Exception:
                    result['missing'].append(testid)
            result['counts'] = {t: page.locator(f'[data-testid="{t}"]').count() for t in COUNTED}
        else:
            result['mobile_fits'] = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        page.screenshot(path=str(OUT / f'{name}_{label}.png'), full_page=True)
        (OUT / f'{name}_{label}.aria.txt').write_text(page.locator('body').aria_snapshot(), encoding='utf-8')
    result['ok'] = result['status'] == 200 and not result['missing']
    result['page_errors'] = list(errors)
    errors.clear()
    return result


def main():
    global OUT
    OUT = ROOT / 'outputs/verify_runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-engine-fix')
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    summary = {'base': base, 'pages': {}, 'started_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    try:
        proc = start(port)
    except RuntimeError as exc:
        summary['error'] = str(exc)
        (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 1
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 800})
            page.on('pageerror', lambda e: errors.append(str(e)))
            for index, (path, required) in enumerate(GUEST_PAGES.items(), start=1):
                summary['pages'][path] = visit(page, base, path, f'{index:02d}_{path.strip("/").replace("/", "_")}_guest', required, errors)
            page.goto(base + '/strategies', wait_until='networkidle')
            link = page.locator('a[href$="/data-trust"]').first
            trust_path = link.get_attribute('href') if link.count() else '/strategies/bollinger_reversion/data-trust'
            summary['pages'][trust_path] = visit(page, base, trust_path, '04_data_trust', ('strategy-data-trust',), errors)
            page.goto(base + '/admin/login')
            page.fill('input[name=token]', TOKEN)
            page.click('form[data-testid=admin-login] button')
            page.wait_for_url('**/admin/data', timeout=60000)
            admin = visit(page, base, '/admin/data', '05_admin_data_admin', ('fix-evidence', 'fix-tasks', 'data-actions'), errors)
            admin['admin_forms'] = page.locator('[data-testid=data-actions] form').count()
            summary['pages']['/admin/data (admin)'] = admin
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    summary['ok'] = all(p['ok'] and not p['page_errors'] for p in summary['pages'].values())
    summary['guest_sees_admin_buttons'] = any(summary['pages'][p]['counts'].get(t) for p in ('/admin/data',) for t in ('history-run', 'history-refresh'))
    summary['ok'] = summary['ok'] and not summary['guest_sees_admin_buttons']
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f'artifacts: {OUT}')
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
