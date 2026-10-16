from __future__ import annotations

import re
import subprocess

from flask import render_template


def _render_chart_script() -> str:
    from app import app as flask_app

    with flask_app.test_request_context('/stock-analysis?id=2330'):
        html = render_template('stock_analysis.html', stock_id='2330')
    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    return match.group(1)


def test_stock_analysis_chart_constructs_all_required_series_for_normal_and_fallback_data():
    script = _render_chart_script()
    harness = r"""
const assert = require('node:assert/strict');
const factories = {};
const calls = {create: 0, series: [], setData: [], margins: []};
const priceScale = () => ({applyOptions(value) { calls.margins.push(value.scaleMargins); }});
const chart = {
  addCandlestickSeries() { calls.series.push('Candlestick'); return {priceScale, setData(value) { calls.setData.push(['Candlestick', value]); }}; },
  addLineSeries() { const name = ['MA5', 'MA20', 'MA60'][calls.series.filter(item => String(item).startsWith('MA')).length % 3]; calls.series.push(name); return {setData(value) { calls.setData.push([name, value]); }}; },
  addHistogramSeries() { calls.series.push('Volume'); return {priceScale, setData(value) { calls.setData.push(['Volume', value]); }}; },
  timeScale() { return {fitContent() {}}; },
  applyOptions() {},
  remove() {}
};
global.window = {LightweightCharts: {createChart() { calls.create += 1; return chart; }}};
global.document = {getElementById() { return {clientWidth: 900}; }, addEventListener(name, callback) { if (name === 'alpine:init') callback(); }};
global.ResizeObserver = class { observe() {} disconnect() {} };
global.Alpine = {data(name, factory) { factories[name] = factory; }};
__SCRIPT__
const page = factories.stockAnalysisPage({initialStockId: '2330'});
page.payload = {quote: {price_change: 15, change_percent: 1.46}};
assert.equal(page.changeValue(), '+15');
assert.equal(page.changePercentValue(), '+1.46%');
page.payload = {quote: {}};
assert.equal(page.changeValue(), 'Unavailable');
assert.equal(page.changePercentValue(), 'Unavailable');
const series = {candles: [{time: '2026-10-05', open: 100, high: 105, low: 99, close: 104}, {time: '2026-10-06', open: 104, high: 108, low: 103, close: 107}], volume: [{time: '2026-10-05', value: 1000}, {time: '2026-10-06', value: 1200}], ma5: [{time: '2026-10-05', value: 101}, {time: '2026-10-06', value: 103}], ma20: [{time: '2026-10-05', value: 98}, {time: '2026-10-06', value: 100}], ma60: [{time: '2026-10-05', value: 95}, {time: '2026-10-06', value: 97}]};
for (const status of ['ok', 'degraded']) { page.payload = {status, kline_ma: series, metadata: {fallback_used: status === 'degraded'}}; page.renderChart(); }
assert.equal(calls.create, 2);
assert.deepEqual(calls.series, ['Candlestick', 'MA5', 'MA20', 'MA60', 'Volume', 'Candlestick', 'MA5', 'MA20', 'MA60', 'Volume']);
assert.equal(calls.setData.length, 10);
assert.deepEqual(calls.margins, [{top: .05, bottom: .25}, {top: .8, bottom: 0}, {top: .05, bottom: .25}, {top: .8, bottom: 0}]);
page.payload = {chip: {institutional: {foreign_buy: -5, trust_buy: 3, dealer_buy: 4}}};
assert.equal(page.institutionItems()[3].value, '2');
page.payload = {chip: {institutional: {foreign_buy: -5, trust_buy: null, dealer_buy: 4}}};
assert.equal(page.institutionItems()[3].value, 'Unavailable');
assert.deepEqual(calls.setData.map(item => item[0]), calls.series);
console.log('chart-ok');
""".replace('__SCRIPT__', script)
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'chart-ok' in result.stdout
