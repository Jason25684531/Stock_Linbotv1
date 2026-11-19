"""V8: the V7 revenue momentum book with a low-volatility selection step, one hypothesis in two registered variants. Research only.

python -m scripts.strategy_v8_program charter | admit <spec.json> | smoke <h> <v> | run <h> <v> [holdout] | finalize | report [--final] | status
It points the V7 program (and through it the V6 program) at outputs/strategy_v8, swaps the portfolio rule for the low-volatility one and
adds the V8 functions to the bound code; everything is restored on exit. Writes only under outputs/strategy_v8.
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts import strategy_v6_program as v6
from scripts import strategy_v7_program as v7
from scripts import strategy_v8_rules as rules8

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v8'
BUDGET = {'hypotheses': 1, 'variants': 2, 'finalists': 1, 'sourced_per_failure': 1}
V8_FUNCTIONS = ('low_vol_selection', 'low_vol_rule', 'v7_portfolio_rule')
CAUTION = '\n\n> 這裡的數字不能直接當作達標證據：這個假說是看過 V7 開發區間的持股之後才選的，所以開發區間的判定不是獨立檢驗，只有驗證區間才是；較小的回撤也可能只是現金較多（持股不足 40 檔時其餘留現金），平均持股數、現金比例與完整區間診斷見「持股數與現金，與完整區間診斷」；可信年度較短的格子不含被排除的年份，見「各格備註」.'
REPORT_FIXES = ((v7.REPORT_FIXES[0][0], '# V8 低波動組合研究：驗收報表'),
                (v7.REPORT_FIXES[1][0], '- V8 沒有代理格子：所有格子都用引擎回測，只有達標候選才會用到驗證區間.'),
                (v7.REPORT_FIXES[2][0], '- 每個格子都是 V7 的 A2 規則（每月 13 日當天或之後換股、不設停損與停利、新買入的倉位各取換股日淨值的 1/N），'
                                        '再加一步：只留下通過營收與價格濾網的名稱中，近 60 個交易日波動最低的一半（a）或三分之一（b）；'
                                        '以引擎的零股規則回測，2020-10 之前是規則診斷，不是可成交的歷史.'),
                (v7.CLOSEST, v7.CLOSEST + CAUTION))


@contextmanager
def installed():
    """V7 installed with the V8 folder and budget, the V8 rule in place of portfolio_rule, and the V8 functions bound; restored on exit."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(v7, 'OUT', OUT))
        stack.enter_context(patch.object(v7.rules, 'v7_portfolio_rule', rules8.V7_PORTFOLIO_RULE, create=True))
        stack.enter_context(patch.object(v6, 'ENGINE_BOUND_FILES', tuple(v6.ENGINE_BOUND_FILES) + ('scripts/strategy_v8_rules.py',)))
        stack.enter_context(patch.object(v7.rules, 'low_vol_selection', rules8.low_vol_selection, create=True))
        stack.enter_context(patch.object(v7.rules, 'low_vol_rule', rules8.low_vol_rule, create=True))
        stack.enter_context(patch.object(v7.rules, 'portfolio_rule', rules8.low_vol_rule))
        stack.enter_context(patch.dict(v7.V7_KINDS, {'portfolio': v7.V7_KINDS['portfolio'] + V8_FUNCTIONS}))
        stack.enter_context(v7.installed(BUDGET))
        yield


def _book_note(result):
    """Average names held and average cash share of the stored run over the counted years: a smaller drawdown may only be idle cash."""
    path = OUT / 'runs' / str(result.get('run_id')) / 'positions_daily.csv'
    if not path.is_file():
        return ''
    book = pd.read_csv(path, parse_dates=['date'])
    book = book[(book.date.dt.year >= result['span'][0]) & (book.date.dt.year <= result['span'][1])]
    names = book[book.stock_id != 'CASH'].groupby('date').stock_id.nunique().reindex(sorted(book.date.unique()), fill_value=0)
    total = book.groupby('date').value.sum()
    cash = book[book.stock_id == 'CASH'].set_index('date').value.reindex(total.index, fill_value=0)
    return f'平均持股 {names.mean():.1f} 檔；現金占淨值平均 {(cash / total).mean() * 100:.0f}%（持股不足 40 檔時其餘留現金，會同時壓低報酬與回撤）.'


def _window_note(result, windows):
    """For a cell whose verified span is shorter than the registered window: the same run's base and stress return and drawdown over the whole
    window. A diagnostic that counts years the data rule excludes, never adopted as the verdict."""
    window = windows[result['window']]
    nav_file = OUT / 'runs' / str(result.get('run_id')) / 'nav.csv'
    if not v7._left_out(result, windows) or not nav_file.is_file():
        return ''
    nav = pd.read_csv(nav_file, parse_dates=['date']).set_index('date')
    inside = nav[(nav.index.year >= window[0]) & (nav.index.year <= window[1])]
    years = (inside.index[-1] - inside.index[0]).days / 365.25
    parts = [f"{label} 年化 {((inside[col].iloc[-1] / inside[col].iloc[0]) ** (1 / years) - 1) * 100:+.1f}%、最大回撤 {(inside[col] / inside[col].cummax() - 1).min() * 100:.1f}%"
             for label, col in (('基本成本', 'nav_base'), ('兩倍成本', 'nav_stress'))]
    return f"診斷（不採計，含資料信任度不足的年份）：同一次執行在 {window[0]}-{window[1]} 全區間（以日曆日年化，比登錄度量保守），{'；'.join(parts)}."


def write_report(owner_items=(), final=False):
    """The V7 report with V8 wording, saved as V8_ACCEPTANCE_REPORT.md with the book note of every cell appended."""
    with patch.object(v7, 'REPORT_FIXES', REPORT_FIXES):
        end = v7.write_report(owner_items, final)
    report = OUT / 'V8_ACCEPTANCE_REPORT.md'
    (OUT / 'V7_ACCEPTANCE_REPORT.md').replace(report)
    results = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((OUT / 'results').glob('*.json'))]
    windows = v6.charter()['windows']
    lines = [f"- {r['hypothesis']}{r['variant']} {r['window']}：" + (' '.join(filter(None, (_book_note(r), _window_note(r, windows)))) or '無') for r in results]
    report.write_text(report.read_text(encoding='utf-8') + '\n## 持股數與現金，與完整區間診斷\n\n' + '\n'.join(lines) + '\n', encoding='utf-8')
    return end


def main(argv):
    command = argv[0] if argv else ''
    with installed():
        if command == 'smoke':
            print(json.dumps(v7.smoke(argv[1], argv[2]), ensure_ascii=False, default=str)[:600])
        elif command == 'admit':
            print(json.dumps(v7.admit(json.loads(Path(argv[1]).read_text(encoding='utf-8'))), ensure_ascii=False)[:300])
        elif command == 'report':
            items = json.loads((OUT / 'owner_items.json').read_text(encoding='utf-8')) if (OUT / 'owner_items.json').exists() else []
            print(write_report(items, final='--final' in argv))
        elif command in ('charter', 'rebind', 'run', 'finalize', 'status'):
            return v6.main(argv)
        else:
            print(__doc__)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
