"""Read-only browser acceptance: filters, preview, actions, mobile sources."""
from pathlib import Path
from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:5059/strategies')
    page.get_by_role('button', name='技術', exact=True).click()
    assert page.get_by_role('button', name='技術', exact=True).get_attribute('aria-pressed') == 'true'
    assert page.locator('label:visible', has_text='品質成長策略').count() == 0
    page.locator('label:visible', has_text='MACD 黃金交叉').click()
    assert page.get_by_role('button', name='套用到每日篩選', exact=True).is_enabled()
    assert page.get_by_role('button', name='執行組合回測', exact=True).is_enabled()
    page.wait_for_function("document.querySelector('[data-testid=strategy-results]').textContent.includes('MACD')")
    page.get_by_role('button', name='套用到每日篩選', exact=True).click()
    assert page.get_by_role('dialog').is_visible()
    page.get_by_role('button', name='取消', exact=True).click()
    page.get_by_role('button', name='執行組合回測', exact=True).click()
    page.wait_for_url('**/backtest?strategies=**')
    assert 'macd_cross' in page.url
    page.goto('http://127.0.0.1:5059/strategies')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='技術', exact=True).click()
    card = page.locator('article:visible').filter(has=page.locator('label', has_text='MACD 黃金交叉'))
    assert card.get_by_text('來源：TQ_MACD交易策略.ipynb', exact=True).is_visible()
    assert card.get_by_text('持股數、再平衡和出場規則依本專案引擎，績效和原 notebook 不同。', exact=True).is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    Path('outputs/tquant_change').mkdir(exist_ok=True, parents=True)
    page.screenshot(path='outputs/tquant_change/mobile.png', full_page=True)
    assert not errors, errors
    browser.close()
    print('BROWSER ACCEPTANCE PASSED')
