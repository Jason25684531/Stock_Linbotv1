"""Browser acceptance of round 2026-11-06-msv (task 14.2): starts a local server, walks the pages, restarts it, re-checks persistence.

It never presses the market-data update buttons (an external fetch is an Owner action); the admin page is checked logged in and read-only.
"""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/research/2026-11-06-multi-strategy-validation-and-composite-v1/screenshots'
PORT = 5071
BASE = f'http://127.0.0.1:{PORT}'
TOKEN = 'local-review-token'
SERVER = ("import os, sys; sys.path.insert(0, r'%s'); os.chdir(r'%s'); from app import app; app.run(host='127.0.0.1', port=%d)" % (ROOT, ROOT, PORT))


def _port_open():
    try:
        socket.create_connection(('127.0.0.1', PORT), timeout=2).close()
        return True
    except OSError:
        return False


def start():
    for _ in range(60):  # the previous server must be gone before the new one is probed
        if not _port_open():
            break
        time.sleep(1)
    env = {**os.environ, 'STOCK_LINBOT_ADMIN_TOKEN': TOKEN, 'FLASK_SECRET_KEY': 'local-review-secret', 'PYTHONIOENCODING': 'utf-8'}
    proc = subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        if proc.poll() is None and _port_open():  # port probe only; pages are loaded by the browser
            time.sleep(2)
            return proc
        time.sleep(1)
    proc.kill()
    raise RuntimeError('server did not start')


MOBILE_FIT = {}


def shot(page, name):
    for width, label in ((1440, 'desktop'), (390, 'mobile')):
        page.set_viewport_size({'width': width, 'height': 900 if width > 400 else 844})
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f'{name}_{label}.png'), full_page=True)
        if width < 400:
            MOBILE_FIT[name] = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results, errors = {}, []
    proc = start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(BASE + '/admin/data')
            results['admin_readonly_before_login'] = page.locator('[data-testid=data-actions]').inner_text()
            page.goto(BASE + '/admin/login')
            page.fill('input[name=token]', TOKEN)
            page.click('form[data-testid=admin-login] button')
            page.wait_for_url('**/admin/data')
            results['scheduler_state'] = page.locator('[data-testid=scheduler-state]').inner_text()
            results['admin_actions_visible'] = page.locator('[data-testid=data-actions] form').count()
            shot(page, '01_admin_data')
            page.goto(BASE + '/strategies')
            text = page.locator('body').inner_text()
            results['strategy_names'] = {n: n in text for n in ('趨勢混合選股', '低波動選股', '雙渦輪成長', '經營效益', '籌碼動能', '均值回歸', '品質價值低波動')}
            results['strategy_center_mentions_ai_unavailable'] = 'AI 模型不可用' in text
            shot(page, '02_strategy_center')
            page.goto(BASE + '/backtest')
            results['backtest_links_to_runs'] = page.locator('a[href*="/backtest/runs/"]').count()
            shot(page, '03_backtest')
            # one real legacy web backtest (live MySQL data, EXPERIMENTAL, not a research trial)
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.evaluate("document.querySelectorAll('input[name=strategies]').forEach(box => { box.checked = false; box.dispatchEvent(new Event('change', {bubbles: true})); })")
            page.check('input[name=strategies][value=mean_reversion]')
            page.fill('input[name=start_date]', '2025-10-01')
            page.fill('input[name=end_date]', '2025-10-31')
            page.locator('form button[type=submit]').last.click()
            page.wait_for_load_state('networkidle', timeout=600000)
            status = page.locator('[data-testid=save-status]')
            results['web_backtest'] = status.inner_text() if status.count() else page.locator('body').inner_text()[:300]
            shot(page, '03b_backtest_result')
            page.goto(BASE + '/lab/combinations')
            results['lab_heatmap_rendered'] = page.locator('#heatmap .main-svg').count() > 0
            results['lab_banner'] = page.locator('[data-testid=lab-banner]').inner_text()[:200]
            shot(page, '04_lab')
            page.goto(BASE + '/backtest/runs')
            runs = page.locator('[data-testid=run-row]').count()
            results['runs_before_restart'] = runs
            first = page.locator('[data-testid=run-row] a').first
            run_url = first.get_attribute('href') if runs else None
            shot(page, '05_runs')
            if run_url:
                page.goto(BASE + run_url)
                results['run_detail_status'] = page.locator('[data-testid=run-status]').inner_text()[:160]
                shot(page, '06_run_detail')
            page.goto(BASE + '/selection/latest')
            page.select_option('select', 'mean_reversion')
            page.wait_for_timeout(800)
            results['latest_selection'] = page.locator('[data-testid=latest-selection]').inner_text()[:300]
            shot(page, '07_latest_selection')
            results['mobile_no_horizontal_scroll'] = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    proc = start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.goto(BASE + '/backtest/runs')
            results['runs_after_restart'] = page.locator('[data-testid=run-row]').count()
            page.goto(BASE + '/strategies')
            results['latest_links_after_restart'] = page.locator('a[href*="/backtest/runs/"]').count()
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    results['page_errors'] = errors
    results['mobile_fit_per_page'] = MOBILE_FIT
    results['persistence_after_restart'] = results['runs_after_restart'] == results['runs_before_restart'] and results['runs_before_restart'] > 0
    (OUT.parent / 'e2e_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
