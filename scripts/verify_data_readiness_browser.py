"""Read-only desktop (1280x800) and mobile (390x844) acceptance for change 2026-11-09-autonomous-data-readiness-v1.

Reuses scripts/verify_platform_browser.py's local server; never logs in, submits a backtest or updates market data.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from scripts import verify_platform_browser as browser_check

OUT = ROOT / 'docs/research/2026-11-09-autonomous-data-readiness-v1/screenshots'
V38_RUN = '20261106T173926Z-f0589c22'
PAGES = {  # page -> (url, selectors that must be visible)
    'admin': ('/admin/data', ['[data-testid=calendar-state]', '[data-testid=benchmark-state]', '[data-testid=data-sources]', '[data-testid=scheduler-state]']),
    'runs': ('/backtest/runs', ['[data-testid=run-evidence]']),
    'run_detail': (f'/backtest/runs/{V38_RUN}', ['[data-testid=evidence-status]', '[data-testid=reproducible]']),
    'strategies': ('/strategies', ['[data-testid=recovery-evidence]']),
    'lab': ('/lab/combinations', []),
    'latest': ('/selection/latest', []),
}
VIEWPORTS = {'desktop': (1280, 800), 'mobile': (390, 844)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results, errors = {}, []
    proc = browser_check.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            for name, (url, selectors) in PAGES.items():
                for label, (width, height) in VIEWPORTS.items():
                    page.set_viewport_size({'width': width, 'height': height})
                    page.goto(browser_check.BASE + url, wait_until='networkidle')
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(OUT / f'{name}_{label}.png'), full_page=True)
                    results[f'{name}_{label}'] = {
                        'fits': page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'),
                        'selectors': {s: any(page.locator(s).nth(i).is_visible() for i in range(page.locator(s).count())) for s in selectors}}
                results[f'{name}_text'] = page.locator('body').inner_text()[:600]
            page.goto(browser_check.BASE + f'/backtest/runs/{V38_RUN}', wait_until='networkidle')
            results['evidence_status'] = page.locator('[data-testid=evidence-status]').inner_text()
            page.goto(browser_check.BASE + '/selection/latest', wait_until='networkidle')
            results['latest_signal_disabled'] = 'LATEST_SIGNAL_DISABLED' in page.locator('body').inner_text()
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    results['page_errors'] = errors
    checks = [v for k, v in results.items() if isinstance(v, dict) and 'fits' in v]
    results['passed'] = (all(c['fits'] and all(c['selectors'].values()) for c in checks) and not errors
                         and results['evidence_status'] == 'POST_RUN_NOT_ADMISSION_VERIFIED' and results['latest_signal_disabled'])
    (OUT.parent / 'browser_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in results.items() if not k.endswith('_text')}, ensure_ascii=False))
    return 0 if results['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
