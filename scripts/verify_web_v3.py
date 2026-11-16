"""Read-only browser acceptance against a local V3 server; never submits real jobs."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:18783')
    args = parser.parse_args()
    from app import platform_v2_routes as routes, intelligence_routes
    from services import web_dashboard
    from app import app
    with app.test_request_context('/'):
        rows = web_dashboard.strategies(routes, intelligence_routes)
    reports = [r['selected']['run_id'] for r in rows if r['selected']]
    out = ROOT / 'outputs/web_v3/after'
    out.mkdir(parents=True, exist_ok=True)
    checks = {'at': datetime.now(timezone.utc).isoformat(), 'base': args.base, 'pages': [], 'interactions': []}
    paths = ['/platform', '/platform/strategies', '/platform/backtest', '/platform/selection',
             '/platform/predictions', '/platform/portfolio', '/platform/system']
    paths += ['/platform/strategies/' + r['strategy_id'] for r in rows]
    paths += ['/platform/backtest/' + run for run in reports]
    folios = [r for r in routes._runs(routes.PORTFOLIOS) if r.get('publishable')]
    paths += ['/platform/portfolio/' + r['run_id'] for r in folios]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, width, height in [('desktop', 1280, 800), ('mobile', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            for path in paths:
                errors, console = [], []
                on_error = lambda e: errors.append(str(e))
                on_console = lambda m: console.append(m.text) if m.type == 'error' else None
                page.on('pageerror', on_error)
                page.on('console', on_console)
                record = {'path': path, 'viewport': label}
                try:
                    response = page.goto(args.base + path, wait_until='networkidle', timeout=90000)
                    record.update(status=response.status, errors=errors, console=console,
                                  overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                    if path == '/platform/strategies':
                        assert page.locator('[data-strategy-id]').count() == 21
                        page.locator('input[name=q]').fill('H4')
                        page.get_by_role('button', name='套用', exact=True).click()
                        assert page.locator('[data-strategy-id]').count() == 1
                        page.get_by_role('link', name='清除', exact=True).click()
                        assert page.locator('[data-strategy-id]').count() == 21
                        checks['interactions'].append({'viewport': label, 'search_clear': 'PASS'})
                    if path.startswith('/platform/backtest/') and page.locator('#v3-report-data').count():
                        payload = json.loads(page.locator('#v3-report-data').text_content())
                        run_id = path.rsplit('/', 1)[-1]
                        saved = routes.analysis(run_id)['result']['performance']
                        assert payload['series'] == web_dashboard.clean(saved['series'])
                        assert payload['base'] == web_dashboard.clean(saved['provisional'])
                        record['numeric_payload'] = 'EXACT_STORED_MATCH'
                        if page.locator('#report-cost').count():
                            page.select_option('#report-cost', 'stress')
                            assert '兩倍成本' in page.locator('#cost-basis').inner_text()
                            assert not page.locator('#run-dd').is_visible()
                            value = payload['base']['cagr_stress']['value']
                            if value is not None:
                                assert f'{value * 100:.1f}%' in page.locator('[data-testid=kpi-cagr]').inner_text()
                            page.select_option('#report-cost', 'base')
                            assert page.locator('#run-dd').is_visible()
                            record['cost_switch'] = 'PASS'
                    name = path.strip('/').replace('/', '_').replace(':', '_') or 'root'
                    page.screenshot(path=str(out / f'{name}-{label}.png'), full_page=True)
                    record['pass'] = record['status'] == 200 and not errors and not console and not record['overflow']
                except Exception as error:
                    record.update(pass_=False, exception=str(error))
                    record['pass'] = False
                checks['pages'].append(record)
                page.remove_listener('pageerror', on_error)
                page.remove_listener('console', on_console)
            page.close()
        browser.close()
    checks['status'] = 'PASS' if all(r['pass'] for r in checks['pages']) else 'FAIL'
    destination = ROOT / 'outputs/web_v3/acceptance/playwright.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': checks['status'], 'pages': len(checks['pages']), 'failures': [r for r in checks['pages'] if not r['pass']]}, ensure_ascii=False))
    return 0 if checks['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
