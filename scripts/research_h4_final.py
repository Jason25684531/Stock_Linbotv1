"""h4-final-resolution-v1 (design.md): baseline, ROUTE A probe through the one public transport, ROUTE B inventory.

Commands: baseline NAME | design-sha | probe | routeb. Every attempt is logged with form, time, result and SHA256."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import pandas as pd
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

OUT = ROOT / 'outputs/research_mode/h4_final_resolution_v1'
CHANGE = ROOT / 'openspec/changes/2026-11-05-h4-final-resolution-v1'
RESEARCH = ROOT / 'outputs/research_mode'
SEALED = ('prepared_v6', 'h4_signals_v1', 'h4_runs_v1', 'report_h4_v2', 'h4_attribution_v1', 'h4_hist_signals_v1',
          'prepared_hist_v1', 'h4_historical_v1', 'h4_unblock_v1', 'g2g3_factor_dev_v2')
DATA = ('data/processed/fundamental_pit_v3_2_verified', 'data/processed/fundamental_pit_v3_3_hist')
T57 = 'https://doc.twse.com.tw/server-java/t57sb01'
T164SB01 = 'https://mopsov.twse.com.tw/server-java/t164sb01'
PROBE = ('2448', '2456', '5305')
PROBE_CAP = 30


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return value


def recommendation_rows():
    """Read-only row count of the production recommendation table; an unreachable DB is recorded, not guessed."""
    try:
        from sqlalchemy import text
        from core.db_helper import get_db_engine
        with get_db_engine(max_retries=1).connect() as conn:
            return int(conn.execute(text('SELECT COUNT(*) FROM daily_recommendations')).scalar())
    except Exception as exc:  # ponytail: any failure means "not observed", recorded with its reason
        return f'UNREACHABLE: {type(exc).__name__}: {str(exc)[:120]}'


def baseline(name):
    status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, capture_output=True, text=True).stdout
    files = sorted(line[3:].strip('"') for line in status.splitlines() if line.strip())
    from core.runtime import research_budget
    return _write(OUT / f'baseline_{name}.json', {
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'git_head': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        'worktree_files_sha256': {f: workflow.sha256(ROOT / f) for f in files if (ROOT / f).is_file()},
        'sealed_tree_sha256': {d: workflow._tree_sha(RESEARCH / d) for d in SEALED},
        'data_tree_sha256': {d: workflow._tree_sha(ROOT / d) for d in DATA},
        'registry': workflow.verify_registry(), 'registry_rows': sum(1 for _ in csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig'))),
        'results_rows': sum(1 for _ in csv.DictReader(workflow.results_path().open(encoding='utf-8-sig'))),
        'formal_ledger': workflow.verify_trials(), 'formal_effective_trials': workflow.effective_trials(),
        'research_ledger': workflow.verify_trials(workflow.RESEARCH_MODE_LEDGER),
        'research_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)),
        'daily_recommendations_rows': recommendation_rows(),
        'budget_h4_fund_recovery': research_budget.status()['jobs']['h4_fund_recovery']})


def design_sha():
    target = OUT / 'design_sha_before_fetch.txt'
    if target.exists():
        return target.read_text(encoding='utf-8')
    line = f"{workflow.sha256(CHANGE / 'design.md')}  design.md  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(line, encoding='utf-8')
    return line


LOG = OUT / 'recovery_attempts.jsonl'
RAW = OUT / 'raw'


def attempts():
    return [json.loads(line) for line in LOG.open(encoding='utf-8')] if LOG.exists() else []


def fetch(kind, ticker, year, season='', stage='A2'):
    """One logged attempt. kind t164: XBRL viewer (year = AD, season 1-4); t57: filing list (year = ROC)."""
    from core.runtime import public_data
    if stage == 'A2' and ticker not in PROBE:
        raise ValueError('A2 probes only the three representative companies')
    if stage == 'A2' and sum(1 for a in attempts() if a['stage'] == 'A2' and not a.get('cache_hit')) >= PROBE_CAP:
        raise ValueError('A2_PROBE_CAP_REACHED')
    if kind == 't164':
        method, url, form = 'GET', T164SB01, {'step': '1', 'CO_ID': ticker, 'SYEAR': str(year), 'SSEASON': str(season), 'REPORT_ID': 'C'}
    else:
        method, url, form = 'POST', T57, {'id': '', 'key': '', 'step': '1', 'co_id': ticker, 'year': str(year), 'seamon': '', 'mtype': 'A', 'dtype': 'AI1'}
    name = f'{kind}_{ticker}_{year}{"_" + str(season) if season else ""}.html'
    entry = {'stage': stage, 'source': 'S1_XBRL_VIEWER' if kind == 't164' else 'S2_FILING_LIST', 'ticker': ticker, 'year': year,
             'season': season, 'url': url, 'form': form, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    try:
        response = public_data.request(method, url, **({'params': form} if method == 'GET' else {'data': form}))
        RAW.mkdir(parents=True, exist_ok=True)
        if not (RAW / name).exists():
            (RAW / name).write_bytes(response.content)
        entry.update(result='VERIFIED', status=response.status_code, file=name, sha256=hashlib.sha256(response.content).hexdigest(),
                     cache_hit=bool(getattr(response, 'research_cache_hit', False)))
    except Exception as exc:  # ponytail: the transport already debited and audited the attempt; keep the reason
        entry.update(result='FAILED', error=f'{type(exc).__name__}: {str(exc)[:300]}')
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def _rows(raw):
    import re
    text = raw.decode('big5', errors='replace') if isinstance(raw, bytes) else raw
    cell = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S | re.I)
    return [[re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', c)).replace('&nbsp;', '').strip() for c in cell.findall(r)]
            for r in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S | re.I)]


def _number(text):
    text = text.replace(',', '').strip()
    if not text:
        return None
    negative = text.startswith('(') and text.endswith(')')
    return -float(text.strip('()')) if negative else float(text)


def parse_statement(raw, year, season):
    """Current-year cumulative operating income (code 6900) and basic EPS (code 9750) from the comprehensive income table."""
    import re
    rows = _rows(raw)
    start = next((i for i, r in enumerate(rows) if r and 'Statement of Comprehensive Income' in r[0]), None)
    if start is None:
        return _parse_flat_statement(rows, year, season)
    header = next(r for r in rows[start + 1:] if len(r) > 2 and 'Accounting Title' in r[1])
    end_month = 3 * int(season)
    column = None
    for i, h in enumerate(header[2:]):
        m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})To(\d{1,2})/(\d{1,2})', h.replace(' ', ''))
        if m and int(m[1]) == int(year) and (int(m[2]), int(m[3]), int(m[4])) == (1, 1, end_month):
            column = i
    if column is None:
        return None
    values = {}
    for r in rows[start + 1:]:
        if r and r[0] in ('6900', '9750') and r[0] not in values and len(r) > 2 + column:
            values[r[0]] = _number(r[2 + column])
    return {'operating_profit': values.get('6900'), 'eps': values.get('9750')}


def _parse_flat_statement(rows, year, season):
    """Pre-2019 viewer: one cell stream; a header of N date ranges precedes the statement and each label is followed by N values."""
    import re
    cells = [c for r in rows for c in r]
    try:
        start = cells.index('綜合損益表')
    except ValueError:
        return None
    head = max(i for i in range(start) if cells[i] == '會計項目')
    ranges = [re.search(r'(\d{4})年(\d{2})月(\d{2})日至(\d{4})年(\d{2})月(\d{2})日', c) for c in cells[head + 1:start]]
    if not ranges or not all(ranges):
        return None
    column = next((i for i, m in enumerate(ranges) if (int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5])) == (int(year), 1, 1, int(year), 3 * int(season))), None)
    if column is None:
        return None
    values = {}
    for label, key in (('營業利益（損失）', 'operating_profit'), ('基本每股盈餘合計', 'eps')):
        at = next((i for i in range(start, len(cells)) if cells[i] == label), None)
        values[key] = _number(cells[at + 1 + column]) if at is not None and at + 1 + column < len(cells) else None
    return values


def routeb():
    """Design D5: already-present licensed sources only. Package presence, variable NAMES (values never kept) and the code gate."""
    import re
    packages = {m: importlib.util.find_spec(m) is not None for m in ('tejapi', 'finlab', 'FinMind', 'twstock', 'yfinance', 'cmoney')}
    names = set(os.environ)
    dotfile = ROOT / '.env'
    if dotfile.is_file():
        names |= {m[1] for m in (re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', line) for line in dotfile.read_text(encoding='utf-8', errors='replace').splitlines()) if m}
    relevant = sorted(n for n in names if re.search('TEJ|FINLAB|FINMIND|CMONEY|XQ|QUANTDATA', n, re.I))
    from core.runtime import operation_policy as policy
    from core.runtime import public_data
    try:
        public_data.restricted_sdk('finlab')
        sdk = 'ALLOWED'
    except Exception as exc:  # ponytail: the gate's own denial reason is the evidence
        sdk = f'{type(exc).__name__}: {str(exc)[:160]}'
    return _write(OUT / 'routeb_inventory.json', {
        'packages_installed': packages, 'credential_variable_names': relevant, 'mode': policy.current_mode().value,
        'finlab_sdk_gate_in_research': sdk,
        'authorization_matrix': 'docs/research/authorization_matrix.md: FinLab login, paid data and pip install are Level 2 (Owner only)',
        'conditions': {
            'TEJ_PIT': {'licensed_here': False, 'reason': 'tejapi not installed and no TEJ variable; a subscription is a purchase (Level 2)'},
            'FinLab': {'licensed_here': 'token variable present', 'usable_in_research': False,
                       'reason': 'SDK is denied as UNMETERED_RESTRICTED_SDK in Research; login is Level 2; the repository FinLab evidence '
                                 '(2026-10-30 source evaluation) has no first-publication version or correction history'},
            'FinMind': {'licensed_here': 'package installed, no token variable', 'usable_in_research': False,
                        'reason': 'not on the public allowlist (its HTTP is not metered by the research transport); statement rows carry the '
                                  'report period, not a publication timestamp or version, so first-published values cannot be shown'},
            'twstock_yfinance': {'licensed_here': 'packages installed', 'usable_in_research': False,
                                 'reason': 'twstock has prices only; yfinance has no point-in-time Taiwan quarterly statements for delisted companies'}},
        'result': 'NO_ALREADY_LICENSED_SOURCE_USABLE'})


V34 = ROOT / 'data/processed/fundamental_pit_v3_4_hist'


def recovered_filings():
    """Parsed statements joined to their filing-list row; one row per fetched company-quarter with its PIT status."""
    from scripts.research_fundamental_pit_recovery import NONE_MARK, listing_rows
    lists, values = {}, {}
    for a in attempts():
        if a['result'] != 'VERIFIED':
            continue
        raw = (RAW / a['file']).read_bytes()
        if a['source'].startswith('S2'):
            for r in listing_rows(raw):
                if r['kind'] == '財務報告書':
                    lists[(a['ticker'], r['roc_year'] + 1911, r['season'])] = {**r, 'retrieved_at': a['at'], 'listing_sha256': a['sha256']}
        else:
            values[(a['ticker'], int(a['year']), int(a['season']))] = {**(parse_statement(raw, a['year'], a['season']) or {}), 'statement_sha256': a['sha256'],
                                                                       'statement_file': a['file']}
    rows = []
    for (ticker, year, quarter) in sorted({(t, y, q) for t, y, q, _ in [(j[1], j[2] + 1911 if j[0] == 't57' else j[2], j[3], j[0]) for j in plan()] if _ == 't164'}):
        listing, value = lists.get((ticker, year, quarter)), values.get((ticker, year, quarter), {})
        complete = listing is not None and value.get('operating_profit') is not None and value.get('eps') is not None
        status = ('MISSING' if not complete else 'VERIFIED' if listing['correction'] == NONE_MARK
                  else 'EXCLUDED' if listing['correction'].startswith('CORRECTION_RECORD') else 'UNVERIFIED')
        rows.append({'ticker': ticker, 'fiscal_year': year, 'quarter': quarter, 'operating_profit': value.get('operating_profit'), 'eps': value.get('eps'),
                     'filename': listing['filename'] if listing else '', 'publication_timestamp': listing['upload_timestamp'] if listing else '',
                     'correction': listing['correction'] if listing else '', 'retrieved_at': listing['retrieved_at'] if listing else '',
                     'statement_sha256': value.get('statement_sha256', ''), 'listing_sha256': listing['listing_sha256'] if listing else '',
                     'basis': 'CONSOLIDATED (t164sb01 REPORT_ID=C)', 'verification_status': status})
    return pd.DataFrame(rows)


def hist_yoy(filings):
    """v3.3-hist rules over v3.1 same-basis records plus the recovered filings. Returns (records, audit frame, yoy rows with status)."""
    from datetime import date
    from core.research.fundamental_pit_v3 import add_derived_metrics, normalize_financial_records
    from core.research.fundamentals.pit import available_date_for_publication
    from scripts import research_h4_hist_run as hr
    from scripts.research_fundamental_pit_recovery import audit, verified_records
    old = pd.read_parquet(hr.SAME_BASIS / 'fundamental_records.parquet')
    old['ticker'] = old['ticker'].astype(str)
    usable = filings.loc[filings['verification_status'] != 'MISSING'].copy()
    new = pd.DataFrame()
    if not usable.empty:
        usable['period_end'] = [str((pd.Timestamp(f'{y}-{3 * q:02d}-01') + pd.offsets.MonthEnd(0)).date()) for y, q in zip(usable['fiscal_year'], usable['quarter'])]
        usable['publication_date'] = usable['publication_timestamp'].str[:10]
        usable['available_date'] = usable['publication_date']  # placeholder, re-derived below with the same rule as every other record
        usable['source_record_id'] = [f'mops:t57sb01:{t}:{y - 1911}:Q{q}:{f}' for t, y, q, f in zip(usable['ticker'], usable['fiscal_year'], usable['quarter'], usable['filename'])]
        normalized = normalize_financial_records(usable[['ticker', 'fiscal_year', 'quarter', 'period_end', 'operating_profit', 'eps']],
                                                 usable[['ticker', 'fiscal_year', 'quarter', 'publication_date', 'publication_timestamp', 'available_date',
                                                         'source_record_id', 'retrieved_at']])
        new = add_derived_metrics(normalized)
        base = normalized.set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])['REPORTING_BASIS']
        prior = {'operating_income_yoy': 'operating_profit', 'eps_yoy': 'eps'}
        same = pd.Series([m not in prior or base.get((t, y, q, prior[m])) == base.get((t, y - 1, q, prior[m]))
                for t, y, q, m in zip(new['ticker'], new['fiscal_year'], new['quarter'], new['metric'])], index=new.index)
        new = new.loc[same & new['metric'].isin(['operating_profit', 'eps', 'operating_income_yoy', 'eps_yoy'])].assign(data_version='fundamental-pit-v3.4-hist')
    records = pd.concat([old, new], ignore_index=True)
    hist_days = sorted(hr.load(hr.PREPARED / 'bars.json')['bars'])
    later = pd.read_parquet(hr.SAME_BASIS / 'fundamental_matrix.parquet', columns=['asof_date'])['asof_date'].unique()
    calendar = [date.fromisoformat(d) for d in hist_days] + sorted(d.date() for d in pd.to_datetime(later) if pd.Timestamp(hr.END) < d <= pd.Timestamp('2025-12-31'))
    mapping = {}
    for pub, stamp in records[['publication_date', 'publication_timestamp']].drop_duplicates().itertuples(index=False):
        day = date.fromisoformat(str(pub)[:10])
        if day < calendar[-1]:
            mapping[(pub, stamp)] = available_date_for_publication(day, pd.Timestamp(stamp).to_pydatetime() if str(stamp) else None, calendar)[0].isoformat()
    records['available_date'] = [mapping.get(k) for k in zip(records['publication_date'], records['publication_timestamp'])]
    records = records.loc[records['available_date'].notna() & (records['publication_date'] <= hr.END)].copy()
    frame = audit(records.loc[~records['ticker'].isin(set(filings['ticker']))], since=2014)
    mine = filings.loc[filings['verification_status'] != 'MISSING', ['ticker', 'fiscal_year', 'quarter', 'filename', 'publication_timestamp', 'correction', 'verification_status']]
    frame = pd.concat([frame, mine.rename(columns={'publication_timestamp': 'listing_upload_timestamp'}).assign(
        reason='h4-final-resolution-v1 recovered filing; listing row read with the v3.2 parser')], ignore_index=True)
    yoy = verified_records(records, frame, start=2015)
    return records, frame, yoy


def signal_members():
    from scripts import research_h4_hist_run as hr
    universe = pd.DataFrame(hr.load(hr.PREPARED / 'universe.json'))
    schedule = hr.load(hr.SIGNALS / 'signals_manifest.json')['signal_days']['60']
    m = universe.loc[universe['member'] & universe['asof_date'].isin(schedule), ['asof_date', 'asset_id']].rename(columns={'asset_id': 'ticker'})
    return m.assign(ticker=m['ticker'].astype(str)), schedule


def pit_check(yoy):
    """Design D4 on every signal-day member. Corrected filings have no recoverable first version and no correction date
    (details are outside the fetch scope), so any member-day whose G2/G3 would differ with them is correction-dependent."""
    from core.research.fundamental_pit_v3 import build_matrix
    members, schedule = signal_members()
    growth = yoy.loc[yoy['metric'].isin(['operating_income_yoy', 'eps_yoy'])]
    kept = growth.loc[growth['verification_status'].eq('VERIFIED')]
    with_corrected = growth.loc[growth['verification_status'].isin(['VERIFIED', 'EXCLUDED'])]
    tickers, days = sorted(members['ticker'].unique()), [pd.Timestamp(d) for d in schedule]
    used, _ = build_matrix(kept, tickers, days)
    alt, _ = build_matrix(with_corrected, tickers, days)
    key = ['asof_date', 'ticker']
    for frame in (used, alt):
        frame['asof_date'] = pd.to_datetime(frame['asof_date']).dt.strftime('%Y-%m-%d')
    joined = members.merge(used, on=key, how='left').merge(alt, on=key, how='left', suffixes=('', '_with_corrected'))
    differs = pd.Series(False, index=joined.index)
    for metric in ('operating_income_yoy', 'eps_yoy'):
        a, b = joined[metric], joined[metric + '_with_corrected']
        differs |= ~((a == b) | (a.isna() & b.isna()))
    joined['correction_dependent'] = differs
    premature = kept.loc[(pd.to_datetime(kept['available_date']) < pd.to_datetime(kept['publication_date'].astype(str).str[:10]))
                         | (pd.to_datetime(kept['period_end']) >= pd.to_datetime(kept['publication_date'].astype(str).str[:10]))]
    per_day = joined.groupby('asof_date').agg(members=('ticker', 'size'), correction_dependent=('correction_dependent', 'sum'))
    checks = {'no_early_use': {'violations': int(len(premature)), 'pass': len(premature) == 0},
              'no_future_survival_status': {'violations': 0, 'pass': True,
                                            'evidence': 'membership is built from same-day quotes (research_mode_inputs.build_research_universe); delistings enter only as events on their own date'},
              'corrections_as_of_signal_date': {'violations': int(differs.sum()), 'pass': bool(differs.sum() == 0),
                                                'evidence': 'correction dates and first-published values of corrected filings are not recoverable within the approved scope'},
              'version_recoverable': {'violations': int(differs.sum()), 'pass': bool(differs.sum() == 0)}}
    return {'status': 'PASS' if all(c['pass'] for c in checks.values()) else 'FAIL', 'checks': checks,
            'member_days': int(len(joined)), 'correction_dependent_member_days': int(differs.sum()),
            'correction_dependent_share': float(differs.mean()), 'correction_dependent_tickers': int(joined.loc[differs, 'ticker'].nunique()),
            'max_daily_share': float((per_day['correction_dependent'] / per_day['members']).max())}, joined, per_day


def admission(filings):
    """The four unchanged limits (research_h4_unblock.daily/passes): actual = companies with every needed quarter recovered."""
    from scripts import research_h4_unblock as ru
    cover = filings.assign(ok=filings['verification_status'].ne('MISSING')).groupby('ticker')['ok'].agg(['sum', 'size'])
    complete = sorted(cover.index[cover['sum'] == cover['size']])
    m = ru.members()
    result = {}
    for name, recovered in (('before_this_round', []), ('actual_complete_companies', complete), ('scenario_all_15', sorted(filings['ticker'].unique()))):
        d = ru.daily(m, recovered)
        checks = {k: bool(v) for k, v in ru.passes(d).items()}
        result[name] = {'recovered': recovered, 'survivor_gap_mean': float(d['survivor_gap_share'].mean()), 'survivor_gap_max': float(d['survivor_gap_share'].max()),
                        'no_source_mean': float(d['no_source_share'].mean()), 'no_source_max': float(d['no_source_share'].max()),
                        'checks': checks, 'status': 'PASS' if all(checks.values()) else 'FAIL'}
    return {'limits': ru.LIMITS, 'coverage': {t: {'recovered_quarters': int(r['sum']), 'needed_quarters': int(r['size'])} for t, r in cover.iterrows()},
            **result}


def save_pit():
    filings = recovered_filings()
    _, frame, yoy = hist_yoy(filings)
    result, joined, per_day = pit_check(yoy)
    joined.to_csv(OUT / 'pit_member_days.csv', index=False, encoding='utf-8-sig')
    per_day.to_csv(OUT / 'pit_daily.csv', encoding='utf-8-sig')
    result['audit_status_counts'] = frame['verification_status'].value_counts().to_dict()
    return _write(OUT / 'pit.json', result)


DOCS = ROOT / 'docs/research/2026-11-05-h4-final-resolution-v1'
NAMES = {'2456': '奇力新', '5264': '鎧勝-KY', '2448': '晶電', '6452': '康友-KY', '3454': '晶睿', '3698': '隆達', '1507': '永大', '5305': '敦南',
         '1262': '綠悅-KY', '1704': '榮化', '3519': '綠能', '2455': '全新', '2499': '東貝', '6251': '定穎', '6288': '聯嘉'}


def report():
    """Generated deliverables. Markdown narrative is written by hand; index.html and SHA256SUMS come last (command index)."""
    import shutil
    from scripts import research_h4_hist_report as rr
    rr.DOCS = DOCS
    DOCS.mkdir(parents=True, exist_ok=True)
    log = pd.DataFrame(attempts())
    log['form'] = log['form'].map(lambda f: json.dumps(f, ensure_ascii=False))
    log.drop(columns=['url']).to_csv(DOCS / 'recovery_attempts.csv', index=False, encoding='utf-8-sig')
    filings = pd.read_csv(OUT / 'recovered_filings.csv', dtype={'ticker': str})
    adm = json.loads((OUT / 'admission.json').read_text(encoding='utf-8'))
    rows = []
    for t in NAMES:
        mine = log.loc[log['ticker'] == t]
        cov = adm['coverage'][t]
        for source, label in (('S1_XBRL_VIEWER', 'MOPS t164sb01 stored XBRL statement viewer'), ('S2_FILING_LIST', 'doc.twse t57sb01 filing list')):
            s = mine.loc[mine['source'] == source]
            ok, bad = int((s['result'] == 'VERIFIED').sum()), int((s['result'] == 'FAILED').sum())
            status = ('USABLE_VALUES_AND_TIMESTAMPS' if ok and not bad else 'CONNECTION_RESET_PARTIAL' if bad else 'NOT_FETCHED_EXPANSION_STOPPED')
            rows.append({'ticker': t, 'name': NAMES[t], 'source': source, 'description': label, 'requests_verified': ok, 'requests_failed': bad,
                         'company_quarters_recovered': cov['recovered_quarters'] if source == 'S1_XBRL_VIEWER' else '',
                         'company_quarters_needed': cov['needed_quarters'], 'status': status})
        rows.append({'ticker': t, 'name': NAMES[t], 'source': 'S3_ACQUIRER_ARCHIVE', 'description': 'merged or acquiring company IR archive',
                     'requests_verified': 0, 'requests_failed': 0, 'status': 'UNUSABLE_FOR_PIT',
                     'reason': 'annual or PDF reports without a provable upload time; no PDF parser (install is Level 2); S1+S2 already give values and times'})
        rows.append({'ticker': t, 'name': NAMES[t], 'source': 'S4_WEB_ARCHIVE', 'description': 'public web archive snapshots',
                     'requests_verified': 0, 'requests_failed': 0, 'status': 'UNUSABLE_FOR_PIT',
                     'reason': 'MOPS statements are POST or server pages; a capture time is only an upper bound and cannot identify the first version'})
    routeb = json.loads((OUT / 'routeb_inventory.json').read_text(encoding='utf-8'))
    for name, c in routeb['conditions'].items():
        rows.append({'ticker': 'ALL', 'name': '', 'source': f'B_{name}', 'description': 'already licensed database check (names only, no values read)',
                     'requests_verified': 0, 'requests_failed': 0, 'status': 'UNUSABLE_NOT_LICENSED_OR_NOT_PIT', 'reason': c['reason']})
    pd.DataFrame(rows).to_csv(DOCS / 'source_availability.csv', index=False, encoding='utf-8-sig')
    filings.to_csv(DOCS / 'recovered_filings.csv', index=False, encoding='utf-8-sig')
    pd.read_csv(OUT / 'pit_daily.csv').to_csv(DOCS / 'pit_daily.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'scenario': k, **{m: v[m] for m in ('survivor_gap_mean', 'survivor_gap_max', 'no_source_mean', 'no_source_max', 'status')},
                   'recovered_companies': len(v['recovered'])} for k, v in adm.items() if isinstance(v, dict) and 'status' in v]) \
        .to_csv(DOCS / 'data_admission.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'hypothesis_id': 'H4:HIST-2016-2022-V1', 'status': 'REGISTERED_NOT_RUN', 'period': '2016-01-04..2022-12-30',
                   'result': 'NOT_RUN', 'reason': 'admission FAIL (no company fully recovered) and PIT FAIL (1,129 correction-dependent member-days)'},
                  {'hypothesis_id': 'H4:HIST-2016-2022-V2', 'status': 'NOT_REGISTERED', 'period': '2016-01-04..2022-12-30', 'result': 'NOT_RUN',
                   'reason': 'registration requires admission and PIT PASS on v3.4-hist; v3.4-hist not built'}]) \
        .to_csv(DOCS / 'h4_historical_results.csv', index=False, encoding='utf-8-sig')
    sealed = json.loads((RESEARCH / 'report_h4_v2/h4_result.json').read_text(encoding='utf-8'))['primary']
    v = sealed['values']
    archived = {'r1': ('net and excess 2x Sharpe > 0', f"net {v['net_sharpe_2x']:.4f}; excess {v['excess_sharpe_2x']:.4f}"),
                'r2': ('bootstrap lower bound > 0', f"{v['bootstrap_lower']:.4f}"),
                'r3': ('DSR > 0.95 at 743 trials', f"{v['deflated_sharpe']:.4f}"),
                'r4': ('top-5 stock and best-5% day profit shares < 50%', f"{v['top5_assets_profit_share']:.1%}; {v['best5pct_days_return_share']:.1%}"),
                'r5': ('bull and bear excess positive, >= 60 sessions each', f"bull {v['regime']['bull']['annualized_excess']:.4f} ({v['regime']['bull']['sessions']}); bear {v['regime']['bear']['annualized_excess']:.4f} ({v['regime']['bear']['sessions']})")}
    pd.DataFrame([{'gate': g, 'criterion': c, 'period_2016_2022': 'NOT_RUN', 'sealed_2023_2025_value': val,
                   'sealed_2023_2025_result': 'PASS' if sealed['gates'][g] else 'FAIL'} for g, (c, val) in archived.items()]) \
        .to_csv(DOCS / 'h4_r1_r5_results.csv', index=False, encoding='utf-8-sig')
    for src, dst in ((RESEARCH / 'signal_shadow_v1/H4_GROWTH-MOMENTUM-V2', 'research_only_runs/dev_replay'),
                     (RESEARCH / 'signal_shadow_v1/synthetic/H4_GROWTH-MOMENTUM-V2', 'research_only_runs/synthetic')):
        shutil.copytree(src, DOCS / dst, dirs_exist_ok=True)
    cover = pd.DataFrame([{'ticker': f'{t} {NAMES[t]}', **adm['coverage'][t]} for t in NAMES])

    def bars(ax):
        ax.barh(cover['ticker'], cover['needed_quarters'], color='#d9d9d9', label='needed')
        ax.barh(cover['ticker'], cover['recovered_quarters'], color='#2f6db5', label='recovered (VERIFIED or EXCLUDED)')
        ax.invert_yaxis()
        ax.set_xlabel('company-quarters')
        ax.legend()
    rr.figure('01_recovery_coverage', '15 家公司季度還原進度（擴大於 PIT 判定 FAIL 後停止）', 'Source: MOPS t164sb01 + doc.twse t57sb01｜' + rr.CAPTION, cover, bars)
    scen = pd.read_csv(DOCS / 'data_admission.csv')

    def adm_draw(axes):
        for ax, (col, lim, title) in zip(axes, (('survivor_gap_mean', .02, 'survivor gap mean'), ('survivor_gap_max', .05, 'survivor gap max day'),
                                                ('no_source_mean', .10, 'no source mean'), ('no_source_max', .15, 'no source max day'))):
            ax.bar(scen['scenario'], scen[col] * 100, color=['#999', '#2f6db5', '#7fb069'])
            ax.axhline(lim * 100, color='#c0392b', ls='--', label=f'limit {lim:.0%}')
            ax.set_title(title)
            ax.tick_params(axis='x', rotation=20)
            ax.legend()
    rr.figure('02_admission_scenarios', '四項資料准入：實際與 15 家全補情境（%）', '門檻未修改｜' + rr.CAPTION, scen, adm_draw, ncols=4)
    daily = pd.read_csv(DOCS / 'pit_daily.csv')
    daily['share'] = daily['correction_dependent'] / daily['members']

    def pit_draw(ax):
        ax.bar(daily['asof_date'], daily['share'] * 100, color='#c0392b')
        ax.set_ylabel('% of signal-day members')
        ax.tick_params(axis='x', rotation=60)
    rr.figure('03_pit_correction_dependence', 'PIT：G2／G3 取決於無法還原版本之更正申報的成員比例', 'PIT FAIL 原因｜' + rr.CAPTION, daily, pit_draw)
    return {'files': sorted(p.name for p in DOCS.iterdir())}


def finish_index():
    """Offline index, source manifest and SHA256SUMS over every delivered file."""
    from scripts import research_h4_hist_report as rr
    rr.DOCS = DOCS
    rr.index_html('H4 最終結案：替代資料補件、PIT 驗證與研究型系統銜接')
    inputs = [ROOT / p for p in ('scripts/research_h4_final.py', 'core/research/strategy_signal.py', 'core/research/h4_signal.py',
                                 'scripts/research_signal_run.py', 'scripts/prepare_h4_signals.py', 'core/runtime/public_data.py',
                                 'test/test_research_signal.py', 'openspec/changes/2026-11-05-h4-final-resolution-v1/design.md')]
    inputs += sorted(RAW.glob('*.html'))
    rows = [{'path': str(p.relative_to(ROOT)).replace(os.sep, '/'), 'sha256': workflow.sha256(p), 'bytes': p.stat().st_size} for p in inputs]
    pd.DataFrame(rows).to_csv(DOCS / 'source_manifest.csv', index=False, encoding='utf-8-sig')
    files = sorted(p for p in DOCS.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (DOCS / 'SHA256SUMS').write_text(''.join(f'{workflow.sha256(p)}  {p.relative_to(DOCS).as_posix()}\n' for p in files), encoding='utf-8', newline='')
    return len(files)


def plan():
    """Design D2 expansion: every needed quarter of the 15 companies plus one filing list per company-year."""
    inventory = pd.read_csv(ROOT / 'docs/research/2026-11-05-h4-validation-unblock-v1/missing_fundamental_inventory.csv', dtype={'asset_id': str})
    jobs = []
    for row in inventory.dropna(subset=['minimal_recovery_rank']).itertuples(index=False):
        first, last = row.quarters_needed.split('..')
        quarters = [(y, q) for y in range(int(first[:4]), int(last[:4]) + 1) for q in range(1, 5) if first <= f'{y}Q{q}' <= last]
        jobs += [('t57', row.asset_id, y - 1911, '') for y in sorted({y for y, _ in quarters})]
        jobs += [('t164', row.asset_id, y, q) for y, q in quarters]
    return jobs


def expand():
    """Chunked: 120 network attempts then a 15 minute pause; stop on a block or 5 consecutive failures."""
    import time
    done = {(a['source'][:2], a['ticker'], str(a['year']), str(a['season'])) for a in attempts() if a['result'] == 'VERIFIED'}
    sent, failures = 0, 0
    for kind, ticker, year, season in plan():
        if (('S1' if kind == 't164' else 'S2'), ticker, str(year), str(season)) in done:
            continue
        entry = fetch(kind, ticker, year, season, stage='EXPAND')
        sent += not entry.get('cache_hit')
        failures = failures + 1 if entry['result'] == 'FAILED' else 0
        print(kind, ticker, year, season, entry['result'], entry.get('error', '')[:120], flush=True)
        if any(word in entry.get('error', '') for word in ('SOURCE_COOLDOWN', 'SOURCE_BLOCKED', 'BUDGET', 'HTTP_STOP')) or failures >= 5:
            return {'stopped': entry.get('error'), 'sent': sent}
        if sent and sent % 120 == 0:
            time.sleep(900)
    return {'stopped': None, 'sent': sent}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('baseline', 'design-sha', 'fetch', 'expand', 'report', 'index'))
    parser.add_argument('args', nargs='*')
    args = parser.parse_args()
    if args.command in ('report', 'index'):
        result = report() if args.command == 'report' else finish_index()
    elif args.command == 'expand':
        result = expand()
    elif args.command == 'fetch':
        result = fetch(*args.args)
    else:
        result = baseline(*(args.args or ['before'])) if args.command == 'baseline' else design_sha()
    print(json.dumps(result, ensure_ascii=False, default=str)[:1500])
