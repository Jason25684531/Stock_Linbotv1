"""Presentation contracts; no changes to strategy or backtest execution."""
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from flask import render_template

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'


def render(name, **context):
    from app import app
    with app.test_request_context('/dashboard'):
        return render_template(name + '.html', shell_status={'state': 'unknown', 'label': 'UNKNOWN'}, **context)


@pytest.mark.parametrize('name,context', [
    ('dashboard', {}), ('stock_analysis', {'stock_id': '2330'}),
    ('strategy_center', {'strategy_catalog': []}),
    ('backtest', {'strategies': [], 'strategy_catalog': [], 'selected_strategies': []}),
    ('backtest_result', {'metrics': dict.fromkeys(['total_return', 'max_drawdown', 'sharpe_ratio', 'win_rate']), 'strategy_performance': {}, 'trades': [], 'equity_chart': 'null', 'drawdown_chart': 'null', 'monthly_chart': 'null'}),
    ('system', {'system_payload': {}, 'pipeline_steps': [], 'system_components': []}),
])
def test_primary_page_has_one_nonempty_heading(name, context):
    headings = re.findall(r'<h1\b[^>]*>(.*?)</h1>', render(name, **context), re.S)
    assert len(headings) == 1
    assert headings[0].strip()


def test_collapsed_navigation_has_distinct_named_svg_icons():
    html = render('dashboard')
    nav = re.search(r'<nav\b.*?</nav>', html, re.S)[0]
    links = re.findall(r'<a title="([^"]+)".*?(<svg.*?</svg>)', nav)
    assert len(links) == 5
    assert len({svg for _, svg in links}) == 5
    assert all(label and 'aria-hidden="true"' in svg for label, svg in links)


def test_taiwan_candles_use_shared_tokens_and_direction_symbols():
    base = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
    stock = (TEMPLATES / 'stock_analysis.html').read_text(encoding='utf-8')
    dashboard = (TEMPLATES / 'dashboard.html').read_text(encoding='utf-8')
    assert '--color-up:#ff5263' in base and '--color-down:#35e58a' in base
    assert "getPropertyValue('--color-up')" in stock
    assert "upColor: up, downColor: 'transparent'" in stock
    assert 'borderDownColor: down' in stock
    assert 'directionLabel(row.direction)' in dashboard
    assert "'▼ '" in dashboard and "'▲ '" in dashboard


def test_primary_templates_use_tokens_readable_sizes_and_numeric_font():
    for path in TEMPLATES.glob('*.html'):
        if path.name == 'login.html':
            continue
        source = path.read_text(encoding='utf-8')
        assert not re.search(r'-\[#|gray-|text-\[1[01]px\]|text-slate-[56]00', source), path.name
        assert 'chart.js@' not in source
    base = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
    assert base.index('tailwind.config') > base.index('cdn.tailwindcss.com')
    assert "font-family:'Noto Sans TC'" in base
    assert ".num { font-family:'JetBrains Mono'" in base
    assert 'font-variant-numeric:tabular-nums' in base

def test_navigation_copy_is_chinese_and_status_tokens_are_preserved(monkeypatch):
    html = render('dashboard')
    nav = re.search(r'<nav\b.*?</nav>', html, re.S)[0]
    assert re.findall(r'<span class="nav-label">(.*?)</span>', nav) == ['總覽', '個股分析', '策略中心', '回測分析', '系統狀態']
    assert 'BROKER' in html and 'DISABLED' in html
    assert 'LIVE TRADING' not in html
    import app.web_server as web
    from app import app
    monkeypatch.setattr(web, '_build_system_status_payload', lambda **kw: ({'status': 'ok'}, 200))
    with app.test_request_context('/dashboard'):
        assert 'SYSTEM ONLINE' in render_template('dashboard.html')


@pytest.mark.parametrize('today,start', [('2026-10-16', '2025-10-16'), ('2024-03-01', '2023-03-02')])
def test_backtest_get_defaults_to_365_days_and_preserves_opt_in(monkeypatch, today, start):
    import app.web_server as web
    from app import app
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(today)
    monkeypatch.setattr(web, 'date', FixedDate)
    monkeypatch.setattr(web, '_build_strategy_catalog', lambda: [])
    monkeypatch.setattr(web, '_build_system_status_payload', lambda **kw: ({'status': 'unknown'}, 200))
    html = app.test_client().get('/backtest').get_data(as_text=True)
    assert f'name="start_date" value="{start}"' in html
    assert f'name="end_date" value="{today}"' in html
    assert ':disabled="submitting"' in html and '回測中…' in html
    assert 'aria-describedby="weights-help"' in html
    assert '儲存結果到資料庫' in html
    persist = re.search(r'<input[^>]*name="persist"[^>]*>', html)[0]
    assert 'checked' not in persist


@pytest.mark.parametrize('value,color', [(4.5, 'text-up'), (-2.0, 'text-down'), (0, 'text-ink'), ({'value': None, 'reason': '樣本不足'}, 'text-ink')])
def test_result_return_sign_and_undefined_reason(value, color):
    html = render('backtest_result', metrics={'total_return': value, 'max_drawdown': None, 'sharpe_ratio': None, 'win_rate': None}, strategy_performance={}, trades=[], equity_chart='null', drawdown_chart='null', monthly_chart='null')
    assert re.search(r'總報酬</div><div class="[^"]*' + color, html)
    if isinstance(value, dict):
        assert '樣本不足' in html and '資料不可用' in html
    assert 'role="tablist"' in html and 'role="tab"' in html
    assert ':aria-selected=' in html and 'Plotly.Plots.resize(plot)' in html
    assert "token('--bg-primary')" in html


def test_stock_direction_formats_positive_negative_zero_and_missing_values():
    html = render('stock_analysis', stock_id='2330')
    script = next(s for s in re.findall(r'<script>(.*?)</script>', html, re.S) if "Alpine.data('stockAnalysisPage'" in s)
    harness = """
const assert = require('node:assert/strict');
let factory;
global.document = {addEventListener: (_, fn) => fn()};
global.Alpine = {data: (_, fn) => factory = fn};
""" + script + """
const page = factory();
assert.equal(page.changeClass(3), 'text-up');
assert.equal(page.changeClass(-3), 'text-down');
for (const value of [0, null, undefined, '', NaN]) assert.equal(page.changeClass(value), 'text-muted');
assert.match(page.formatSigned(3), /^▲/);
assert.match(page.formatSigned(-3), /^▼/);
assert.equal(page.formatSigned(0), '0');
assert.equal(page.formatSigned(null), '資料不可用');
assert.equal(page.quoteItems().length, 4);
"""
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr or result.stdout
    assert not re.search(r'MACD|>KD<|Turnover|命中 Strategy', html)
