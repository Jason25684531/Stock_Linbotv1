"""Local browser acceptance, isolated ephemeral port; never submits jobs or deploys."""
import json
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from services import strategy_v4 as service


def main():
    out = Path('outputs/strategy_v4/acceptance'); images = out / 'screenshots'; images.mkdir(exist_ok=True)
    rows = [service.load(sid) for sid in service.NAMES]
    paths = ['/platform/research/v4'] + ['/platform/research/v4/' + sid for sid in service.NAMES]
    paths += ['/platform', '/platform/strategies', '/platform/backtest', '/platform/selection', '/platform/predictions', '/platform/portfolio', '/platform/system']
    paths += ['/platform/backtest/' + row['original']['run_id'] for row in rows if row['status'] == 'COMPARABLE']
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    evidence = {'base': f'http://127.0.0.1:{server.server_port}', 'pages': []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for device, width, height in [('desktop',1280,800), ('mobile',390,844)]:
                page = browser.new_page(viewport={'width':width,'height':height})
                for path in paths:
                    errors = []; console = []
                    on_error = lambda error: errors.append(str(error))
                    on_console = lambda msg: console.append(msg.text) if msg.type == 'error' else None
                    page.on('pageerror',on_error); page.on('console',on_console)
                    item = {'path':path,'viewport':device}
                    try:
                        response = page.goto(evidence['base'] + path, wait_until='networkidle', timeout=60000)
                        assert response.status == 200
                        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
                        if '/research/v4' in path:
                            assert 'RESEARCH_ONLY / NOT_CERTIFIED' in page.inner_text('body')
                            for row in rows:
                                if path.endswith('/v4') or path.endswith(row['strategy_id']):
                                    if row['status'] == 'COMPARABLE':
                                        for version in ['original','fixed']:
                                            for metric in ['cagr','max_drawdown','cagr_stress']:
                                                locator = f'[data-metric="{row["strategy_id"]}-{version}-{metric}"]'
                                                assert page.locator(locator).inner_text() == service.percentage(row[version]['summary'].get(metric))
                            if page.locator('#v4-chart-data').count():
                                row = next(r for r in rows if path.endswith(r['strategy_id']))
                                payload = json.loads(page.locator('#v4-chart-data').text_content())
                                assert payload == {v:row[v]['series'] for v in ['original','fixed']}
                                assert page.locator('#v4-curve path').count() == 3
                                before = page.locator('#v4-curve path').first.get_attribute('d')
                                page.select_option('#v4-cost','stress')
                                assert page.locator('#v4-curve path').first.get_attribute('d') != before
                                item['numeric_payload'] = 'EXACT_SAVED_MATCH'; item['cost_switch'] = 'PASS'
                        assert not errors and not console
                        page.screenshot(path=str(images / f'{path.strip("/").replace("/","_")}-{device}.png'), full_page=True)
                        item['status'] = 'PASS'
                    except Exception as error:
                        item.update(status='FAIL',reason=str(error),errors=errors,console=console)
                        page.screenshot(path=str(images / f'failure-{len(evidence["pages"])}.png'),full_page=True)
                    page.remove_listener('pageerror',on_error); page.remove_listener('console',on_console)
                    evidence['pages'].append(item)
                page.close()
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    evidence['status'] = 'PASS' if all(i['status'] == 'PASS' for i in evidence['pages']) else 'FAIL'
    (out / 'playwright.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':evidence['status'],'pages':len(evidence['pages']),'failures':[i for i in evidence['pages'] if i['status'] != 'PASS']},ensure_ascii=False))
    return 0 if evidence['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
