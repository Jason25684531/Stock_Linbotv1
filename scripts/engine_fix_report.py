"""FINAL_ENGINE_AND_HISTORY_VALIDATION_REPORT.md tables, generated from stored files only (change 2026-11-10-engine-accounting-and-history-gaps-v1).

    python -m scripts.engine_fix_report

Reads outputs/engine_history_fix/{state,evidence,legacy_baseline}.json, the latest cross-regime report, the acceptance logs under
outputs/engine_history_fix/acceptance/ and the Codex review files, and writes outputs/engine_history_fix/REPORT_TABLES.md. The final report
quotes these tables; nothing in it is typed by hand from memory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/engine_history_fix'


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _pct(value) -> str:
    return 'None' if value is None else f'{value * 100:.2f}%'


def _num(value) -> str:
    return 'None' if value is None else f'{value:.3f}'


def _money(value) -> str:
    return '-' if value is None else f'{value:,.0f}'


def annualized(run_id) -> float | None:
    """Whole-window annualized base return from the stored NAV (252 sessions a year)."""
    import pandas as pd
    from scripts import historical_foundation as hf
    nav = pd.read_csv(hf.RUNS / run_id / 'nav.csv').nav_base.astype(float)
    return None if len(nav) < 2 or nav.iloc[0] <= 0 else float((nav.iloc[-1] / nav.iloc[0]) ** (252 / (len(nav) - 1)) - 1)


def cross_report() -> dict:
    from scripts import historical_foundation as hf
    pointer = _json(hf.REPORTS / 'LATEST.json', {})
    return _json(hf.REPORTS / pointer['cross_regime'], {}) if pointer.get('cross_regime') else {}


def strategy_table(cross: dict) -> list[str]:
    lines = ['| 策略 | 新版狀態 | 會計 | 視窗 | VERIFIED 年 | PARTIAL 年 | 累積報酬 基礎／2x | 年化（基礎） | Sharpe | MDD | 交易數 | 未平倉市值 | 公司行動入帳／現金 | '
             '共同年份 | 舊版／新版（共同年份連乘） | 差異 | 研究判定／優先順序 |', '|' + '---|' * 17]
    for s in cross.get('strategies', []):
        a = s.get('analysis')
        c = s.get('legacy_comparison') or {}
        impact = s.get('corporate_action_impact') or {}
        if a:
            years = a['years']
            verified = [y for y, v in years.items() if v['trust'] == 'VERIFIED']
            partial = [y for y, v in years.items() if v['trust'] == 'PARTIAL']
            new, common = c.get('new') or {}, c.get('common_years') or []
            ann = annualized(s['run_id'])
            open_value = (s.get('open_positions') or {}).get('value')
            lines.append(f"| {s['strategy_id']} | {s['final_state']} | {s.get('accounting')} | {'～'.join(s['window'])} | {','.join(verified) or '-'} | {','.join(partial) or '-'} | "
                         f"{_pct(new.get('base_total_return'))}／{_pct(new.get('x2_total_return'))} | {_pct(ann)} | "
                         f"{_num(new.get('sharpe_base'))} | {_pct(new.get('max_drawdown_base'))} | {new.get('trades', '-')} | {_money(open_value)} | "
                         f"{impact.get('events_booked', 0)}／{impact.get('cash_credited', 0):,.0f} | {f'{common[0]}～{common[-1]}' if common else '-'} | "
                         f"{_pct((c.get('legacy') or {}).get('common_years_return'))}／{_pct(new.get('common_years_return'))} | "
                         f"{_pct((c.get('delta') or {}).get('common_years_return'))} | {s['decision']['state']}／{s['decision']['priority']} |")
        else:
            lines.append(f"| {s['strategy_id']} | {s['final_state']} | - | - | - | - | - | - | - | - | - | - | - | - | - | - | {s['decision']['state']}：{s.get('reason')} |")
    return lines


def yearly_table(cross: dict) -> list[str]:
    years = [str(y) for y in range(2015, 2026)]
    lines = ['| 策略 | ' + ' | '.join(years) + ' |', '|' + '---|' * (len(years) + 1)]
    from scripts.historical_report import _v
    for s in cross.get('strategies', []):
        a = s.get('analysis')
        if not a:
            continue
        cells = []
        for y in years:
            v = a['years'].get(y)
            if v is None:
                cells.append('-')
            else:
                value = _v(v['base']['cumulative_return'])
                cells.append(f"{v['trust'][0]} {_pct(value)}" if value is not None else v['trust'])
        lines.append(f"| {s['strategy_id']} | " + ' | '.join(cells) + ' |')
    return lines


def regime_table(cross: dict) -> list[str]:
    from scripts.historical_report import _v
    lines = ['| 策略 | 多頭（交易日／基礎） | 空頭 | 盤整 | 高波動 | 低波動 | 未標環境日 |', '|---|---|---|---|---|---|---|']
    for s in cross.get('strategies', []):
        a = s.get('analysis')
        if not a:
            continue
        r = a['regimes']['VERIFIED_AND_PARTIAL']
        cell = lambda k: f"{r[k]['sessions']}／{_pct(_v(r[k]['base']['cumulative_return']))}"
        lines.append(f"| {s['strategy_id']} | {cell('BULL')} | {cell('BEAR')} | {cell('SIDEWAYS')} | {cell('HIGH_VOL')} | {cell('LOW_VOL')} | {a.get('unlabelled_sessions')} |")
    return lines


def drop_scan(cross: dict, threshold: float = -0.15) -> list[str]:
    """Days a NAV track fell more than 15 %: the signature of an unbooked corporate action. Legacy runs against the new runs, both tracks."""
    import pandas as pd
    from scripts import historical_foundation as hf
    lines = ['| 策略 | 舊版 基礎／2x 單日跌幅 > 15% 天數 | 新版 基礎／2x 天數 | 新版最差單日 基礎／2x |', '|---|---|---|---|']
    count = lambda nav, col: int((nav[col].astype(float).pct_change() < threshold).sum())
    for s in cross.get('strategies', []):
        c = s.get('legacy_comparison')
        if not s.get('run_id') or not c:
            continue
        old, new = pd.read_csv(hf.RUNS / c['legacy_run_id'] / 'nav.csv'), pd.read_csv(hf.RUNS / s['run_id'] / 'nav.csv')
        worst = lambda col: f"{new[col].astype(float).pct_change().min() * 100:.2f}%（{new.date[new[col].astype(float).pct_change().idxmin()]}）"
        lines.append(f"| {s['strategy_id']} | {count(old, 'nav_base')}／{count(old, 'nav_stress')} | {count(new, 'nav_base')}／{count(new, 'nav_stress')} | "
                     f"{worst('nav_base')}／{worst('nav_stress')} |")
    return lines


def task_table(state: dict) -> list[str]:
    lines = ['| 工作 | 標題 | 狀態 | 修復輪數 | 錯誤分類 | 可重試 | 備註 |', '|---|---|---|---|---|---|---|']
    for task_id, t in state.get('tasks', {}).items():
        lines.append(f"| {task_id} | {t.get('title') or '-'} | {t['status']} | {t.get('repairs', 0)} | {t.get('error_class') or '-'} | {t.get('retryable')} | {(t.get('note') or '-')[:120]} |")
    return lines


def main() -> int:
    cross = cross_report()
    state = _json(OUT / 'state.json', {})
    evidence = _json(OUT / 'evidence.json', {})
    sat = evidence.get('saturdays', {})
    lines = ['# 自動產生的表格（scripts/engine_fix_report.py；只讀存檔）', '',
             f"- Snapshot：舊 `{evidence.get('snapshot', {}).get('legacy')}` → 新 `{evidence.get('snapshot', {}).get('new')}`；Evaluation `{cross.get('evaluation_id')}`；會計 `{cross.get('accounting')}`。", '',
             '## 週六交易日', '', '| 日期 | 檔案 SHA256（前 12） | 列數／有成交 | 官方日期相符 | FinLab 收盤對帳 | 納入快照 |', '|---|---|---|---|---|---|']
    for day, v in sat.items():
        fl = v.get('finlab') or {}
        lines.append(f"| {day} | {(v.get('sha256') or '-')[:12]} | {v.get('rows')}／{v.get('traded_rows')} | {v.get('date_matches')} | {fl.get('agreed')}/{fl.get('compared')} | {v.get('in_snapshot')} |")
    lines += ['', '## 21 策略（新版）', '', *strategy_table(cross), '', '## 逐年基礎報酬（V=VERIFIED、P=PARTIAL）', '', *yearly_table(cross), '',
              '## 市場環境（VERIFIED_AND_PARTIAL）', '', *regime_table(cross), '', '## 單日異常跌幅掃描（未入帳公司行動的特徵）', '', *drop_scan(cross), '',
              '## 工作狀態', '', *task_table(state), '']
    (OUT / 'REPORT_TABLES.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'written': str(OUT / 'REPORT_TABLES.md'), 'strategies': len(cross.get('strategies', [])), 'tasks': len(state.get('tasks', {}))}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
