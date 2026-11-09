"""Static inventory of Strategy_example/*.ipynb (code cells only, never executed).

Outputs notebook_inventory.csv / .md and credential_actions.md. Detected credential
literals are never written or printed; each output is checked before writing.
Knowledge source: openspec/changes/archive/2026-10-19-2026-10-19-add-tquant-strategies/design.md
(notebook table), scripts/strategy_family_review.json and core/strategy_manager.py.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / 'Strategy_example'
OUT_DIR = ROOT / 'docs' / 'research' / '2026-11-06-multi-strategy-validation-and-composite-v1'
QUARANTINE_DIR = ROOT / 'outputs' / 'credential_quarantine'

CONCLUSIONS = ('PORTED', 'DUPLICATE', 'BLOCKED_DATA', 'BLOCKED_LICENSE', 'BLOCKED_PIT',
               'ETF_SEPARATE', 'FUTURES_SEPARATE')
COLUMNS = ['name', 'sha256', 'sha256_original', 'source', 'license_statement', 'asset_class',
           'factors', 'data_tables_fields', 'backtest_rules', 'duplicate_of', 'tej_dependency',
           'credential_finding', 'risks', 'reproducible_with_local_data',
           'notebook_result_independently_verifiable', 'conclusion', 'in_user_list']

# User list provided 2026-11-06 (exact stems; trailing spaces in file names are stripped).
USER_LIST = [
    'TQ_安東尼·賣利亞反向操作策略', 'TQ_乖離率策略', 'TQ_延伸量能回測策略', 'TQ_David_Dreman_逆向投資策略',
    'TQ_ETF資產配置模型', 'TQ_Future_波動率調整持倉動能期貨策略', 'TQ_IMQ_IndMom_Quality', 'TQ_iValue',
    'TQ_James_OShaughnessy_Value_v1', 'TQ_KD指標回測實戰', 'TQ_LTR_LambdaMART截面排名', 'TQ_MACD交易策略',
    'TQ_MAD_移動均線距離策略', 'TQ_VAM_波動率調整截面動能_TWN100', 'TQ_大師策略＿麥克．喜偉收益型選股法',
    'TQ_尤金·法瑪(Eugene_Fama)三因子模型策略', 'TQ_月營收成長率策略', 'TQ_月營收成長率策略_simple_algo',
    'TQ_布林通道交易策略',
]

FIX = 'fixed ticker list chosen ex post (survivorship/selection bias)'
ONE = 'single stock chosen ex post (selection bias)'
FUT_REPRO = 'NO: no local futures bundle; engine supports neither futures nor shorting'
FUND = 'NO: TEJ fundamental/attribute tables not available locally'
# stem -> (asset_class, factors, backtest_rules, conclusion, duplicate_of, risks, reproducible)
KNOWLEDGE = {
    # --- ported (core/strategy_manager.py, source='tquant') ---
    'TQ_MACD交易策略': ('stock', 'MACD 12/26/9 (SMA-seeded EMA) cross', '2330 only, 35-day close; long on golden cross, exit on death cross',
                     'PORTED', 'macd_cross', ONE, 'YES: price-only, 2330 OHLCV local'),
    'TQ_KD指標回測實戰': ('stock', 'Fast Stochastic K (10-day)', 'IX0002 universe; buy oversold K, sell overbought',
                      'PORTED', 'kd_oversold', 'universe = historical TWN50 membership (IX0002) via get_universe',
                      'REPRODUCTION_DIFFERS: IX0002 membership history not local; port uses local market universe'),
    'TQ_MAD_移動均線距離策略': ('stock', 'MRAT moving-average distance, 20-day high/low, cross-sectional rank and std',
                         'TSE common-stock pool; rank by MAD, periodic rebalance', 'PORTED', 'mad_distance',
                         "TejToolAPI raw prices with include_self_acc='Y'; get_universe pool",
                         'REPRODUCTION_DIFFERS: notebook uses TEJ raw TSE prices and TEJ universe; port uses local adjusted data'),
    'TQ_乖離率策略': ('stock', 'BIAS vs 7-day weighted MA, low of prior closes', '2330 only; buy on negative bias, exit on reversion',
                   'PORTED', 'bias_reversion', ONE, 'YES: price-only, 2330 OHLCV local'),
    'TQ_布林通道交易策略': ('stock', 'Bollinger 20-day band (population std)', '2409 only; buy at lower band, sell at upper band',
                       'PORTED', 'bollinger_reversion', ONE, 'YES: price-only, 2409 OHLCV local'),
    'TQ_動量策略': ('stock', '252-day log-price regression momentum, average dollar volume', 'fixed ~90-stock list, top 20 by momentum',
                  'PORTED', 'regression_momentum', FIX + '; TQDataSet.Industry used only for a plot',
                  'YES: price/volume only on a fixed list available locally'),
    'TQ_量能回測實戰': ('stock', '4-day volume surge', 'tickers 2330 3443 2337; buy on volume breakout',
                    'PORTED', 'volume_surge', FIX, 'YES: price/volume only'),
    'TQ_阿隆指標交易策略': ('stock', 'Aroon 25-day up/down', 'fixed 11 stocks; buy on Aroon-up cross',
                       'PORTED', 'aroon_trend', FIX, 'YES: price-only'),
    'TQ_趨勢跟蹤策略': ('stock', '40/80 EMA, 50-day high breakout', 'fixed ~50 large caps', 'PORTED', 'trend_following', FIX,
                    'YES: price-only'),
    'TQ_逆勢交易策略': ('stock', '40/80 EMA, daily-return std, pullback from high', 'fixed ~50 large caps', 'PORTED',
                    'contrarian_pullback', FIX, 'YES: price-only'),
    'TQ_長期趨勢策略': ('stock', 'close vs close 250 and 125 bars ago', 'fixed ~50 large caps', 'PORTED', 'long_term_trend', FIX,
                    'YES: price-only'),
    'TQ_VAM_波動率調整截面動能_TWN100': ('stock', 'volatility-adjusted cross-sectional momentum (return / vol)',
                                 'IX0003 (TWN100) universe; long top ranks, periodic rebalance', 'PORTED',
                                 'volatility_adjusted_momentum', 'universe = historical TWN100 membership (IX0003)',
                                 'REPRODUCTION_DIFFERS: TWN100 membership history not local; port uses local market universe'),
    # --- futures ---
    'TQ-Future_Impluse_MACD_期貨交易策略': ('futures', 'Impulse MACD cross, ATR trailing stop', 'TX continuous future, long/short',
                                     'FUTURES_SEPARATE', 'none', 'continuous-future roll; short side', FUT_REPRO),
    'TQ-Future_三大法人籌碼面台指期貨交易策略': ('futures', 'institutional net open interest, ATR quantile filter',
                                        'TX continuous future, long/short', 'FUTURES_SEPARATE', 'none',
                                        'institutional OI timing (published after close)', FUT_REPRO + '; no OI data'),
    'TQ-Future_黃金交叉期貨交易策略': ('futures', 'moving-average golden cross', 'continuous future, long/short',
                                  'FUTURES_SEPARATE', 'none', 'continuous-future roll', FUT_REPRO),
    'TQ_Future_波動率調整持倉動能期貨策略': ('futures', 'multi-lookback return sign, Yang-Zhang vol-target sizing',
                                      'TX/TE/TF/CDF/TGF continuous futures, long/short', 'FUTURES_SEPARATE',
                                      'none (same logic as TQ_波動率調整持倉動能期貨策略)', 'continuous-future roll', FUT_REPRO),
    'TQ_波動率調整持倉動能期貨策略': ('futures', 'multi-lookback return sign, Yang-Zhang vol-target sizing',
                                 'continuous futures, long/short', 'FUTURES_SEPARATE',
                                 'none (same logic as TQ_Future_波動率調整持倉動能期貨策略)', 'continuous-future roll', FUT_REPRO),
    'TQ_期貨策略範例': ('futures', 'futures strategy examples', 'continuous futures', 'FUTURES_SEPARATE', 'none',
                    'continuous-future roll', FUT_REPRO),
    # --- ETF ---
    'TQ_ETF資產配置模型': ('ETF', 'ETF asset-allocation target weights', 'multi-asset target-weight rebalance',
                       'ETF_SEPARATE', 'none', 'ETF universe from TEJ get_universe (survivorship)',
                       'NO: multi-asset target-weight rebalance not in stock engine'),
    'TQ_造市商與一般投資人在 ETF 折溢價套利中的實作績效探討': ('ETF', '0050 NAV premium/discount', 'analysis only, no trading strategy',
                                              'ETF_SEPARATE', 'none', 'n/a (analysis)', 'NO: TWN/ANAV NAV data not local'),
    'TQ_週期投資': ('ETF', 'business-cycle signal switching 0050 vs bond/inverse ETF', 'monthly regime switch between ETFs',
                  'ETF_SEPARATE', 'none', 'signal val_shifted publication lag must be verified',
                  'NO: cycle signal (GLOBAL/ANMAR) not local'),
    # --- duplicate ---
    'TQ_月營收成長率策略_simple_algo': ('stock', 'monthly revenue YoY + profit growth', 'same logic as TQ_月營收成長率策略',
                                   'DUPLICATE', 'notebook:TQ_月營收成長率策略 (no repo key)', 'same as TQ_月營收成長率策略', FUND),
    # --- blocked stock notebooks ---
    'TQ_David_Dreman_逆向投資策略': ('stock', 'low PER/PBR/PCF, dividend yield, current ratio, D/E, ROE, pretax margin, EPS growth',
                                'TSE common stock; score extra conditions, top picks', 'BLOCKED_PIT', 'none',
                                "include_self_acc='Y' without require_annd: financials may be used before announcement (PIT unverifiable)",
                                FUND + ' (mktcap, balance sheet, ROE)'),
    'TQ_James_OShaughnessy_Value_v1': ('stock', 'value composite: PCF, PSR, dividend yield, mktcap', 'TSE/OTC common stock; value rank',
                                     'BLOCKED_PIT', 'none', 'same-day mktcap used for trades at the open = look-ahead',
                                     FUND + ' (mktcap, cash flow, shares, sales per share)'),
    'TQ_機器學習算法 XGBoost 提升技術指標一目均衡表的投資績效': ('stock', 'Ichimoku features + XGBoost classifier',
                                               'ML signal mixed with raw Ichimoku signal', 'BLOCKED_PIT', 'none',
                                               'Chikou_Span = close.shift(-26) uses future prices; random train_test_split = training leakage',
                                               'NO: needs training pipeline; feature set leaks the future'),
    'TQ_IMQ_IndMom_Quality': ('stock', 'industry momentum (mktcap weighted) + GP/A quality', 'TSE common stock; monthly rebalance',
                              'BLOCKED_DATA', 'none', "PIT-aware (require_annd=True, include_self_acc='N') but needs versioned financials",
                              FUND + ' (historical industry, total assets, mktcap, announcement versions)'),
    'TQ_iValue': ('stock', 'intangible-adjusted B/M (R&D and SG&A capitalised)', 'TSE/OTC common stock; value rank',
                  'BLOCKED_DATA', 'none', 'notebook notes TQuant Lab license excludes TWN/AIM1A; restated values without announcement versions',
                  FUND + ' (equity, R&D, opex, announcement versions); PBR is not iB/M'),
    'TQ_LTR_LambdaMART截面排名': ('stock', 'LambdaMART learning-to-rank on price features', 'IX0002 universe; rank and hold top names',
                               'BLOCKED_DATA', 'none', '1-day feature/execution leak; training leakage risk',
                               'NO: TWN50 membership history (IX0002) not local; needs training pipeline'),
    'TQ_延伸量能回測策略': ('stock', '20-day log-price regression momentum within TWN50', 'TWN50 daily flag universe',
                       'BLOCKED_DATA', 'none (logic near regression_momentum)', 'membership flag must be PIT',
                       'NO: Component_Stock_of_TWN50_Fg daily history not local'),
    'TQ_大師策略＿麥克．喜偉收益型選股法': ('stock', 'industry, PER, current ratio, D/E, cash dividend yield, annual revenue growth',
                                  'TSE/OTC common stock; screen', 'BLOCKED_DATA', 'none', "fin_type='A' annual data timing",
                                  FUND + ' (industry, current ratio, debt, equity)'),
    'TQ_安東尼·賣利亞反向操作策略': ('stock', 'PER/PFCF/PBR/PSR + foreign/trust/insider holdings', 'TSE/OTC/TIB common stock; contrarian screen',
                               'BLOCKED_DATA', 'none', 'TTM cash flow timing', FUND + ' (adjust factor, holdings, cash flow, shares)'),
    'TQ_尋找Alpha': ('stock', 'regression alpha vs IR0001 + PER rank z-score', 'TWSE common stock', 'BLOCKED_DATA', 'none',
                   'historical market classification', FUND + ' (TEJ ROI, market classification)'),
    'TQ_尤金·法瑪(Eugene_Fama)三因子模型策略': ('stock', 'size (prior-year mktcap) and book-to-market', 'TSE general-industry common stock; annual sort',
                                        'BLOCKED_DATA', 'none', 'historical eligibility', FUND + ' (mktcap, adjust factor, eligibility)'),
    'TQ_從羅伯．加迪納的選股法出發：在台股尋找小型成長黑馬': ('stock', 'small-cap growth: mktcap, NI growth, TTM GM, insider holdings, PEG',
                                           'TSE/OTC common stock; screen', 'BLOCKED_DATA', 'none', 'TTM timing',
                                           FUND + ' (mktcap, net income, industry, insider holdings)'),
    'TQ_德伍．卻斯動能策略': ('stock', 'mktcap, 5y EPS CAGR, TTM EPS, debt ratio, PER, 6m relative strength', 'TSE/OTC; screen',
                        'BLOCKED_DATA', 'none', 'TTM timing', FUND + ' (mktcap, debt ratio, TTM EPS)'),
    'TQ_月營收成長率策略': ('stock', 'monthly revenue YoY + gross/operating/net profit growth', 'TWSE/OTC common stock; screen',
                       'BLOCKED_DATA', 'none', 'revenue publication lag', FUND + ' (net income growth); revenue_yoy cannot replace profit conditions'),
    'TQ_柏頓．墨基爾成功選股法則': ('stock', 'industry, multi-year TTM revenue/NI growth, PER history', 'screen within industries',
                              'BLOCKED_DATA', 'none', 'TTM timing', FUND + ' (industry, net income, long history)'),
    'TQ_查爾士．布蘭帝 價值型選股法則': ('stock', 'D/E, insider holdings, PER, PCF, PBR', 'TSE/OTC common stock; value screen',
                                'BLOCKED_DATA', 'none', "fin_type='Q' accumulated data timing", FUND + ' (debt, equity, holdings, cash flow)'),
    'TQ_霍華羅斯曼_審慎致富投資法': ('stock', 'mktcap, current ratio, 4Q ROE, 5y positive FCF, revenue/NI growth', 'TSE/OTC common stock; screen',
                               'BLOCKED_DATA', 'none', "fin_type='Q' accumulated data timing", FUND + ' (mktcap, ROE, FCF)'),
    'TQ_融資維持率': ('stock', 'turnover value and margin balance', 'screen on value/margin changes', 'BLOCKED_DATA', 'none',
                   'n/a', 'NO: actual turnover value and margin maintenance ratio not local'),
    'TQ_跟隨大戶策略': ('stock', 'large-holder shares and ratio changes', 'follow large holders', 'BLOCKED_DATA', 'none',
                    'holding data publication lag', 'NO: large-holder holdings data not local'),
}

KEY_NAME = re.compile(r'TEJAPI_KEY|api_key|\btoken\b|tej_key', re.I)
QUOTED = re.compile(r'([\'"])(.*?)\1')
LITERAL = re.compile(r'[A-Za-z0-9]{20,}')
ENV_READ = re.compile(r'environ\.get|getenv|config\[')
PLACEHOLDER = re.compile(r'=\s*([\'"])(.*?)\1')
CRED_RANK = {'NONE': 0, 'ENV_READ': 1, 'PLACEHOLDER': 2, 'LITERAL_REDACTED': 3}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_nb(path: Path) -> dict:
    return json.loads(Path(path).read_bytes().decode('utf-8'))


def code_cells(nb: dict):
    for i, c in enumerate(nb.get('cells', [])):
        if c.get('cell_type') == 'code':
            src = c.get('source', '')
            yield i, ''.join(src) if isinstance(src, list) else src


def scan_credentials(nb: dict) -> list[dict]:
    """Findings per key line. 'literal' is internal only and must never be output."""
    out = []
    for i, src in code_cells(nb):
        for line in src.splitlines():
            if not KEY_NAME.search(line):
                continue
            lits = [m.group(2) for m in QUOTED.finditer(line) if LITERAL.fullmatch(m.group(2))]
            code_part = re.sub(r'\s#.*$', '', line)
            if lits:
                kind = 'ENV_DEFAULT_LITERAL' if ENV_READ.search(code_part) else 'ASSIGN_LITERAL'
                out += [{'cell': i, 'finding': 'LITERAL_REDACTED', 'kind': kind, 'literal': s} for s in lits]
            elif ENV_READ.search(code_part):
                out.append({'cell': i, 'finding': 'ENV_READ', 'kind': 'ENV_READ', 'literal': None})
            elif PLACEHOLDER.search(code_part):
                out.append({'cell': i, 'finding': 'PLACEHOLDER', 'kind': 'PLACEHOLDER', 'literal': None})
    return out


def extract_data(nb: dict) -> tuple[str, bool, str]:
    """(data_tables_fields, uses_tej_api, license_statement) from code cells."""
    tables, fields, flags, lic = [], [], [], []
    tej = False
    cells = [src for _, src in code_cells(nb)]
    # columns=<var> inside a get_history_data cell -> resolve '<var> = [...]' in any code cell
    col_vars = {v for src in cells if 'get_history_data' in src
                for v in re.findall(r'columns\s*=\s*([A-Za-z_]\w*)', src)}
    for src in cells:
        for v in col_vars:
            for lst in re.findall(r'^\s*' + v + r'\s*=\s*\[([^\]]*)\]', src, re.M):
                fields += re.findall(r'[\'"]([^\'"]+)[\'"]', lst)
    for src in cells:
        tables += re.findall(r'tejapi\.get\(\s*[\'"]([^\'"]+)', src)
        if re.search(r'tejapi\.get\(|get_history_data\(|get_universe\(|get_fundamentals\(', src):
            tej = True
        if 'get_history_data' in src:
            for lst in re.findall(r'columns\s*=\s*\[([^\]]*)\]', src):
                fields += re.findall(r'[\'"]([^\'"]+)[\'"]', lst)
        for f in ('require_annd', 'include_self_acc', 'fin_type', 'idx_id'):
            flags += [f'{f}={v}' for v in re.findall(f + r'\s*=\s*([^,\)\n]+)', src)]
        if 'get_universe(' in src:
            flags.append('get_universe')
        for line in src.splitlines():
            if re.search(r'授權|licen[cs]e', line, re.I):
                lic.append(re.sub(r'^[#\s]+', '', line.strip())[:160])
    uniq = lambda xs: list(dict.fromkeys(x.strip() for x in xs))
    parts = []
    if tables:
        parts.append('tejapi tables: ' + ', '.join(uniq(tables)))
    if fields:
        parts.append('TejToolAPI fields: ' + ', '.join(uniq(fields)))
    if flags:
        parts.append('flags: ' + ', '.join(uniq(flags)))
    return ('; '.join(parts) or 'zipline bundle only'), tej, (' | '.join(uniq(lic)) or 'NONE_FOUND')


def build_rows(nb_dir: Path, quarantine_dir: Path, knowledge: dict = KNOWLEDGE) -> tuple[list[dict], list[dict], set]:
    """Return (rows, credential findings with file/status, set of literals)."""
    rows, creds, literals = [], [], set()
    for p in sorted(Path(nb_dir).glob('*.ipynb')):
        stem = p.stem.strip()
        nb = load_nb(p)
        q = Path(quarantine_dir) / (p.name + '.orig')
        found = scan_credentials(nb)
        orig_found = scan_credentials(load_nb(q)) if q.exists() else []
        current_lits = {f['literal'] for f in found if f['literal']}
        for f in found + orig_found:
            if f['literal']:
                literals.add(f['literal'])
        for f in found:
            if f['literal']:
                creds.append({'file': p.name, 'cell': f['cell'], 'kind': f['kind'],
                              'status': 'NOT_REDACTED: literal still in notebook; redaction needs user authorization'})
        for f in orig_found:
            if f['literal'] and f['literal'] not in current_lits:
                creds.append({'file': p.name, 'cell': f['cell'], 'kind': f['kind'],
                              'status': f'REDACTED_IN_PLACE: original quarantined as outputs/credential_quarantine/{q.name}'})
        best = max([f['finding'] for f in found] or ['NONE'], key=CRED_RANK.get)
        cells = sorted({f['cell'] for f in found if f['finding'] == best})
        cred = best
        if best == 'LITERAL_REDACTED':
            kinds = sorted({f['kind'] for f in found if f['literal']})
            cred = f"LITERAL_REDACTED: cell {','.join(map(str, cells))} {'/'.join(kinds)} REDACTED"
        elif orig_found and any(f['literal'] for f in orig_found):
            cred = f'{best} (literal removed; see credential_actions.md)'
        data, tej, lic = extract_data(nb)
        k = knowledge.get(stem)
        if k is None:
            k = ('stock', 'UNREVIEWED', 'UNREVIEWED', 'BLOCKED_DATA', 'none', 'UNREVIEWED: no decision recorded',
                 'NO: not reviewed')
        asset, factors, rules, concl, dup, risks, repro = k
        assert concl in CONCLUSIONS, (stem, concl)
        verif = ('NO: port acceptance used fixed fixtures, not notebook output; notebook charts are never gate evidence'
                 if concl == 'PORTED' else
                 'NO: notebook output comes from TEJ cloud data with no local re-run; notebook charts are never gate evidence')
        rows.append({
            'name': p.name, 'sha256': sha256(p), 'sha256_original': sha256(q) if q.exists() else sha256(p),
            'source': 'TQuant Lab (TEJ)', 'license_statement': lic, 'asset_class': asset, 'factors': factors,
            'data_tables_fields': data, 'backtest_rules': rules, 'duplicate_of': dup,
            'tej_dependency': 'TEJ_DATA' if tej else 'BUNDLE_ONLY', 'credential_finding': cred, 'risks': risks,
            'reproducible_with_local_data': repro, 'notebook_result_independently_verifiable': verif,
            'conclusion': concl, 'in_user_list': 'YES' if stem in USER_LIST else 'NO',
        })
    return rows, creds, literals


def safe_write(path: Path, text: str, literals: set) -> None:
    for lit in literals:
        assert lit not in text, f'credential literal would leak into {Path(path).name}'
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding='utf-8', newline='\n')


def _md_cell(v) -> str:
    return str(v).replace('|', '/').replace('\n', ' ')


def _table(rows: list[dict], cols: list[str]) -> str:
    lines = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    lines += ['| ' + ' | '.join(_md_cell(r[c]) for c in cols) + ' |' for r in rows]
    return '\n'.join(lines)


def render(rows: list[dict], creds: list[dict]) -> dict[str, str]:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator='\n')
    w.writeheader()
    w.writerows(rows)

    stems = {Path(r['name']).stem.strip(): r for r in rows}
    missing = [s for s in USER_LIST if s not in stems]
    user_rows = [stems[s] for s in USER_LIST if s in stems]
    counts = Counter(r['conclusion'] for r in rows)
    md = ['# TQuant notebook inventory', '',
          'Generated by `scripts/notebook_inventory.py` from code cells only; no notebook was executed.',
          'Notebook charts and printed results are never gate evidence.', '',
          f'Notebooks: {len(rows)}. Conclusions: ' + ', '.join(f'{c}={counts[c]}' for c in CONCLUSIONS if counts[c]), '',
          f'## User list (19 provided, {len(user_rows)} found)', '']
    md += [f'MISSING: {s}' for s in missing] + ([''] if missing else [])
    md += [_table(user_rows, ['name', 'sha256', 'asset_class', 'conclusion', 'duplicate_of', 'reproducible_with_local_data',
                              'credential_finding']), '',
           f'## All notebooks ({len(rows)})', '',
           _table(rows, ['name', 'asset_class', 'conclusion', 'tej_dependency', 'credential_finding', 'in_user_list']), '',
           '## Details', '']
    for r in rows:
        md.append(f"### {r['name']}")
        md += [f'- {c}: {_md_cell(r[c])}' for c in COLUMNS if c != 'name'] + ['']

    ca = ['# Credential actions', '',
          'Literal credentials found in notebook code cells. Values are never reproduced: REDACTED.', '']
    if creds:
        ca += [_table([{**c, 'value': 'REDACTED',
                        'owner_action': 'Revoke this TEJ API key in the TEJ account and reissue; store the new key only in .env'}
                       for c in creds], ['file', 'cell', 'kind', 'value', 'status', 'owner_action'])]
    else:
        ca.append('No literal credentials found.')
    other = Counter(r['credential_finding'].split(':')[0].split(' ')[0] for r in rows)
    ca += ['', 'Credential finding per notebook (current files): ' +', '.join(f'{k}={v}' for k, v in sorted(other.items())), '']
    return {'notebook_inventory.csv': buf.getvalue(), 'notebook_inventory.md': '\n'.join(md),
            'credential_actions.md': '\n'.join(ca)}


def generate(nb_dir: Path = NB_DIR, out_dir: Path = OUT_DIR, quarantine_dir: Path = QUARANTINE_DIR) -> list[dict]:
    rows, creds, literals = build_rows(nb_dir, quarantine_dir)
    for name, text in render(rows, creds).items():
        safe_write(Path(out_dir) / name, text, literals)
    return rows


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    rows = generate()
    print(len(rows), 'notebooks;', dict(Counter(r['conclusion'] for r in rows)))
