"""Read-only desktop/mobile acceptance; never submits a backtest or updates market data."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from scripts import verify_platform_browser as browser_check

OUT = ROOT / 'docs/research/legacy-od29-recovery-and-daily-ops-v1/screenshots'
PAGES = {'strategies': '/strategies', 'runs': '/backtest/runs', 'lab': '/lab/combinations',
         'latest': '/selection/latest', 'admin_readonly': '/admin/data'}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    proc = browser_check.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(browser_check.BASE + '/backtest/runs', wait_until='networkidle')
            links = page.locator('[data-testid=run-row] a')
            if links.count():
                PAGES['run_detail'] = links.first.get_attribute('href')
            for name, url in PAGES.items():
                page.goto(browser_check.BASE + url, wait_until='networkidle')
                if name == 'strategies':
                    evidence_dir = ROOT / 'outputs/research_mode/legacy_od29_recovery_v1'
                    run_ids = [json.loads((evidence_dir / f'{sid}.json').read_text(encoding='utf-8'))['run_id'] for sid in
                               ('hybrid_trend_rank', 'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility')]
                    results['recovery_links'] = {run_id: page.locator(f'a[href="/backtest/runs/{run_id}"]').count() > 0 for run_id in run_ids}
                for width, label in ((1440, 'desktop'), (390, 'mobile')):
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(OUT / f'{name}_{label}.png'), full_page=True)
                    results[f'{name}_{label}_fits'] = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                results[f'{name}_text'] = page.locator('body').inner_text()[:1000]
            results['page_errors'] = errors
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    proc = browser_check.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.goto(browser_check.BASE + '/backtest/runs', wait_until='networkidle')
            results['runs_after_restart'] = {run_id: page.locator(f'a[href="/backtest/runs/{run_id}"]').count() > 0 for run_id in run_ids}
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    (OUT.parent / 'browser_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in results.items() if not k.endswith('_text')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
