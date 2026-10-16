"""Presentation contracts; no changes to strategy or backtest execution."""
import re
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

