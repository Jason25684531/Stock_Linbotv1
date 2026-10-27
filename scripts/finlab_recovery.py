"""FinLab recovery (2026-10-24-recover-data-gaps-with-finlab), free plan: reconcile the 2023 part of the 385 events TWT49U cannot split.

An event is VERIFIED only when FinLab's announced cash, stock ratio and rights terms reproduce both official TWT49U reference prices
(TWSE formulas, 0.01 tolerance for the two-decimal reference). Disagreeing announcements are UNKNOWN, never resolved by picking one.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/finlab_recovery/2026-10-24'
TOLERANCE = 0.01
FREE_END = '2023-12-31'


def _par(text) -> float | None:
    m = re.search(r'(\d+(?:\.\d+)?)', str(text))
    return float(m.group(1)) if m else None  # 無面額 or blank: the stock ratio cannot be derived


def known_at(announcements: list[dict], ex_date: str) -> list[dict]:
    """Point in time: only announcements published before the ex-date count, and only the latest of them (earlier ones were superseded).
    Several versions at that same latest time stay ambiguous. Chosen by time only, never by which version reproduces the reference."""
    before = [a for a in announcements if str(a['announced']) < str(ex_date)]
    latest = max((str(a['announced']) for a in before), default=None)
    return [a for a in before if str(a['announced']) == latest]


def reconcile_event(pre_close: float, ex_ref: float, dividend_ref: float, announcements: list[dict]) -> dict:
    """ex reference = (P - cash + price * rights) / (1 + stock + rights); dividend reference = (P - cash) / (1 + stock)."""
    if not announcements:
        return {'status': 'MISSING', 'reason': 'no FinLab announcement for this stock and ex-date'}
    terms = {(a['cash'], a['stock_ntd'], a['rights_ratio_pct'], a['rights_price'], a['par']) for a in announcements}
    if len(terms) > 1:
        return {'status': 'UNKNOWN', 'reason': f'{len(terms)} disagreeing FinLab announcements; none is chosen'}
    cash, stock_ntd, rights_pct, rights_price, par_text = terms.pop()
    par = _par(par_text)
    if stock_ntd and not par:
        return {'status': 'UNKNOWN', 'reason': f'par value {par_text!r}: stock ratio not derivable'}
    stock = stock_ntd / par if stock_ntd else 0.0
    rights = rights_pct / 100
    want_div = (pre_close - cash) / (1 + stock)
    want_ex = (pre_close - cash + rights_price * rights) / (1 + stock + rights)
    ok = abs(want_div - dividend_ref) <= TOLERANCE and abs(want_ex - ex_ref) <= TOLERANCE
    return {'status': 'VERIFIED' if ok else 'CONFLICT', 'cash': cash, 'stock_ratio': round(stock, 6), 'rights_ratio': rights, 'rights_price': rights_price,
            'computed_dividend_ref': round(want_div, 4), 'computed_ex_ref': round(want_ex, 4),
            'reason': '' if ok else f'computed {want_div:.2f}/{want_ex:.2f} vs official {dividend_ref:.2f}/{ex_ref:.2f}'}


def dividends():
    import pandas as pd
    from core.research.adjustment.normalization import parse_number
    from scripts.p0_p4_research import TWT49U, split_event
    ann = pd.read_parquet(OUT / 'raw/dividend_announcement.parquet')
    ann['stock_id'] = ann.stock_id.astype(str)
    num = lambda c: pd.to_numeric(ann[c], errors='coerce').fillna(0.0)
    ann = ann.assign(cash=num('盈餘分配之股東現金股利(元/股)') + num('法定盈餘公積、資本公積發放之現金(元/股)'),
                     stock_ntd=num('盈餘轉增資配股(元/股)') + num('法定盈餘公積、資本公積轉增資配股(元/股)'),
                     rights_ratio_pct=num('現金增資認股比率(%)'), rights_price=num('現金增資認購價(元/股)'), par=ann['普通股每股面額'].astype(str))
    rows = []
    for r in json.loads(TWT49U.read_text(encoding='utf-8'))['data']:
        if not (r[1].isdigit() and len(r[1]) == 4):
            continue
        pre, ex_ref, div_ref = parse_number(r[3]), parse_number(r[4]), parse_number(r[10])
        split = split_event(r[6], pre, ex_ref, div_ref)
        if split['status'] == 'SPLIT_PROVEN':
            continue
        stock, day = r[11].split(',')
        date = pd.Timestamp(day)
        base = {'stock_id': stock, 'ex_date': str(date.date()), 'twt49u_kind': r[6], 'mixed_not_identifiable': split['cash'] is None,
                'rights_offering': split['rights_offering'], 'pre_close': pre, 'ex_ref': ex_ref, 'dividend_ref': div_ref}
        if str(date.date()) > FREE_END:
            rows.append({**base, 'status': 'UNKNOWN', 'reason': 'outside the free FinLab range (VIP needed); not reconciled'})
            continue
        hit = ann[(ann.stock_id == stock) & ((pd.to_datetime(ann['除權交易日'], errors='coerce') == date) | (pd.to_datetime(ann['除息交易日'], errors='coerce') == date))]
        hit = hit.assign(announced=pd.to_datetime(hit['公告日期'], errors='coerce').dt.strftime('%Y-%m-%d') + ' ' + hit['公告時間'].astype(str))
        records = hit[['cash', 'stock_ntd', 'rights_ratio_pct', 'rights_price', 'par', 'announced']].to_dict('records')
        usable = known_at(records, str(date.date()))
        result = reconcile_event(pre, ex_ref, div_ref, [{k: v for k, v in a.items() if k != 'announced'} for a in usable])
        if records and not usable:
            result = {'status': 'UNKNOWN', 'reason': 'every FinLab announcement is dated on or after the ex-date (not known in time)'}
        pay = pd.to_datetime(hit['現金股利發放日'], errors='coerce').dropna()
        published = pd.to_datetime(hit['公告日期'], errors='coerce').dropna()
        rows.append({**base, **result, 'cash_payment_date': str(pay.min().date()) if len(pay) else 'UNKNOWN',
                     'announced_on': str(published.min().date()) if len(published) else 'UNKNOWN',
                     'announced_before_ex_date': bool(len(published)) and bool(published.min() < date)})
    frame = pd.DataFrame(rows)
    assert not frame.duplicated(['stock_id', 'ex_date']).any()  # one row per event: the 24 mixed-with-rights events are counted once
    path = OUT / 'DIVIDEND_EVENT_RECONCILIATION_r2.csv'  # r1 left superseded announcements as disagreements
    assert not path.exists()
    frame.to_csv(path, index=False, encoding='utf-8-sig')
    inside = frame[frame.ex_date <= FREE_END]
    print('events', len(frame), 'mixed&rights overlap', int((frame.mixed_not_identifiable & frame.rights_offering).sum()), '2023 events', len(inside))
    print(inside.status.value_counts().to_dict(), '| payment date known', int((inside.get('cash_payment_date', pd.Series(dtype=str)) != 'UNKNOWN').sum()))



def unblock():
    """Per Day3/D5 configuration: unblocked only if every held event TWT49U cannot split is VERIFIED and is not a rights offering
    (whether a holder subscribes cannot be assumed). Same Holdings logic and archived orders as the P0 exposure check."""
    import pandas as pd
    from scripts.p0_p4_research import DT, DAY3_D5
    from scripts.track_b_evaluate import Holdings
    events = pd.read_csv(OUT / 'DIVIDEND_EVENT_RECONCILIATION_r2.csv', encoding='utf-8-sig', dtype={'stock_id': str})
    events['ex_date'] = pd.to_datetime(events.ex_date)
    b = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    b = b[b.family.isin(DAY3_D5)]
    orders = pd.concat([pd.read_parquet(DT / f'orders_{f}.parquet') for f in ('day3', 'd5')])
    orders['asset'] = orders.asset.astype(str)
    rows = []
    for cfg in b.itertuples():
        held = Holdings(orders[orders.config_id == cfg.config_id])
        inside = events[(events.ex_date >= pd.Timestamp(cfg.start)) & (events.ex_date <= pd.Timestamp(cfg.end)) & events.stock_id.isin(held.by_asset)]
        hit = inside[[held.shares(r.stock_id, r.ex_date, strictly_before=True) > 0 for r in inside.itertuples()]]
        open_events = hit[(hit.status != 'VERIFIED') | hit.rights_offering]
        rows.append({'family_name': cfg.family_name, 'config_id': cfg.config_id, 'held_unsplit_events': len(hit), 'verified_by_finlab': int((hit.status == 'VERIFIED').sum()),
                     'still_open': len(open_events), 'open_after_free_range': int((open_events.ex_date > pd.Timestamp(FREE_END)).sum()),
                     'nonofficial_prices_held': cfg.held_on_nonofficial_prices,
                     'total_return': 'UNBLOCKED' if not len(open_events) else 'TOTAL_RETURN_BLOCKED'})
    frame = pd.DataFrame(rows)
    path = OUT / 'dividend_unblock_by_config.csv'
    assert not path.exists()
    frame.to_csv(path, index=False, encoding='utf-8-sig')
    print(frame.groupby('family_name').agg(configs=('config_id', 'size'), unblocked=('total_return', lambda s: int((s == 'UNBLOCKED').sum())),
                                           held=('held_unsplit_events', 'sum'), verified=('verified_by_finlab', 'sum'), open_after_2023=('open_after_free_range', 'sum')).to_string())



def corporate_actions():
    """T1b, free range: FinLab capital reductions and par-value changes vs the jumps detected after a halt (label_policy_excluded_events.csv)."""
    import pandas as pd
    from scripts.post_archive_readiness import BARS
    bars = pd.read_parquet(BARS)
    universe, first, last = set(bars.stock_id.astype(str)), bars.trade_date.min(), min(bars.trade_date.max(), pd.Timestamp(FREE_END))
    rows = []
    for key, kind in (('capital_reduction_tse__twse_cap_divide_ratio', 'CAPITAL_REDUCTION'), ('par_value_change_tse__twse_par_value_change_divide_ratio', 'PAR_VALUE_CHANGE')):
        s = pd.read_parquet(OUT / f'raw/{key}.parquet').stack()
        for (date, sid), ratio in s[s.notna()].items():
            if str(sid) in universe and first <= pd.Timestamp(date) <= last:
                rows.append({'stock_id': str(sid), 'resume_date': str(pd.Timestamp(date).date()), 'kind': kind, 'finlab_ratio': float(ratio)})
    finlab = pd.DataFrame(rows)
    jumps = pd.read_csv(ROOT / 'outputs/post_archive_readiness/2026-10-24/label_policy_excluded_events.csv', dtype={'stock_id': str})
    jumps = jumps[(jumps.kind == 'UNEXPLAINED_MOVE_BEYOND_DAILY_LIMIT') & (pd.to_datetime(jumps.session) <= last)].rename(columns={'session': 'resume_date'})
    merged = finlab.merge(jumps[['stock_id', 'resume_date']].assign(detected_jump=True), on=['stock_id', 'resume_date'], how='outer')
    merged['detected_jump'] = merged.detected_jump.fillna(False).astype(bool)
    merged['status'] = merged.apply(lambda r: 'MATCHED' if r.detected_jump and pd.notna(r.kind) else 'FINLAB_ONLY_UNDETECTED_BY_JUMP' if pd.notna(r.kind)
                                    else 'JUMP_WITHOUT_FINLAB_EVENT', axis=1)
    merged['ratio_verified'] = 'UNKNOWN: no cached official resumption reference (TWTAUU) to compare the ratio with'
    path = OUT / 'CORPORATE_ACTION_COVERAGE.csv'
    assert not path.exists()
    merged.sort_values(['resume_date', 'stock_id']).to_csv(path, index=False, encoding='utf-8-sig')
    print(merged.status.value_counts().to_dict(), '| window', first.date(), '..', last.date())
    print(merged[merged.status == 'FINLAB_ONLY_UNDETECTED_BY_JUMP'].finlab_ratio.describe().round(3).to_dict())



def fundamentals():
    """OD-P4 (2022-2023, free range): FinLab disclosure date and its own source label vs the project's MOPS publication date; FinLab EPS (latest value,
    no version history: financial_statement_versions is not in the catalogue) vs the project snapshot. Nothing becomes PIT-proven from FinLab alone."""
    import pandas as pd
    from core.research.native_frame import PIT_RECORDS
    from scripts.native7_rules_only import MYSQL
    from scripts.post_archive_readiness import BARS
    universe = set(pd.read_parquet(BARS, columns=['stock_id']).stock_id.astype(str))
    long = lambda name, col: pd.read_parquet(OUT / f'raw/{name}.parquet').stack().rename(col).reset_index().rename(columns={'symbol': 'stock_id'})
    fl = long('etl__financial_statements_disclosure_dates', 'finlab_disclosure_date').merge(long('etl__financial_statements_disclosure_sources', 'finlab_date_source'), on=['date', 'stock_id'], how='left')
    fl = fl.merge(long('financial_statement__每股盈餘', 'finlab_eps'), on=['date', 'stock_id'], how='left')
    fl['year'], fl['quarter'] = fl.date.str[:4].astype(int), fl.date.str[-1].astype(int)
    fl = fl[fl.stock_id.astype(str).isin(universe) & fl.year.between(2022, 2023)]
    pit = pd.read_parquet(PIT_RECORDS, columns=['ticker', 'fiscal_year', 'quarter', 'publication_date', 'data_version'])
    pit = pit[pit.data_version == 'fundamental-pit-v3'].groupby(['ticker', 'fiscal_year', 'quarter']).publication_date.min().reset_index()
    pit.columns = ['stock_id', 'year', 'quarter', 'mops_publication_date']
    snap = pd.read_parquet(MYSQL / 'snapshot_financial_statements.parquet')[['stock_id', 'year', 'quarter', 'eps']].rename(columns={'eps': 'project_eps'})
    for t in (pit, snap):
        t['stock_id'] = t.stock_id.astype(str)
    fl = fl.sort_values(['stock_id', 'year', 'quarter'])
    # FinLab EPS is per single quarter, the project snapshot is year-to-date: compare cumulative sums, and only when every earlier quarter is present
    complete = fl.groupby(['stock_id', 'year']).finlab_eps.transform(lambda e: e.notna().cumprod().astype(bool))
    fl['finlab_eps_ytd'] = fl.groupby(['stock_id', 'year']).finlab_eps.cumsum().where(complete & (fl.quarter == fl.groupby(['stock_id', 'year']).cumcount() + 1))
    out_ = fl.merge(pit, on=['stock_id', 'year', 'quarter'], how='left').merge(snap, on=['stock_id', 'year', 'quarter'], how='left')
    gap = (pd.to_datetime(out_.finlab_disclosure_date, errors='coerce') - pd.to_datetime(out_.mops_publication_date, errors='coerce')).dt.days
    out_['date_gap_days_finlab_minus_mops'] = gap
    out_['date_status'] = out_.apply(lambda r: 'NO_MOPS_RECORD' if pd.isna(r.mops_publication_date) else 'SAME_DAY' if r.date_gap_days_finlab_minus_mops == 0
                                     else 'FINLAB_LATER' if r.date_gap_days_finlab_minus_mops > 0 else 'FINLAB_EARLIER', axis=1)
    diff = (out_.finlab_eps_ytd - out_.project_eps).abs()
    out_['eps_status'] = diff.map(lambda d: 'NOT_COMPARABLE' if pd.isna(d) else 'SAME' if d <= 0.01 else 'DIFFERENT')
    out_['pit_status'] = 'NOT_PROVEN: FinLab keeps the latest value only and labels most dates fallback/unverified'
    path = OUT / 'FUNDAMENTAL_PIT_AUDIT_r2.csv'  # r1 compared single-quarter FinLab EPS with year-to-date project EPS
    assert not path.exists()
    out_.to_csv(path, index=False, encoding='utf-8-sig')
    print(len(out_), out_.finlab_date_source.str.split('|').str[0].value_counts().to_dict())
    print(out_.date_status.value_counts().to_dict(), out_.eps_status.value_counts().to_dict())
    print('FINLAB_EARLIER by source', out_[out_.date_status == 'FINLAB_EARLIER'].finlab_date_source.str.split('|').str[0].value_counts().to_dict())


def revenue():
    """Monthly revenue (2022-2023): FinLab value and its index date vs the project MySQL snapshot."""
    import pandas as pd
    from scripts.native7_rules_only import MYSQL
    from scripts.post_archive_readiness import BARS
    universe = set(pd.read_parquet(BARS, columns=['stock_id']).stock_id.astype(str))
    s = pd.read_parquet(OUT / 'raw/monthly_revenue__去年同月增減(%).parquet').stack().rename('finlab_yoy').reset_index().rename(columns={'symbol': 'stock_id'})
    s = s[s.stock_id.astype(str).isin(universe) & s.date.between('2022-01-01', '2023-12-31')]
    s['finlab_index_date'] = s.date.dt.strftime('%Y-%m-%d')
    data_month = s.date.dt.to_period('M') - 1  # FinLab dates a month's revenue by its release (~10th of the next month): data month = previous month
    s['year'], s['month'] = data_month.dt.year, data_month.dt.month
    rev = pd.read_parquet(MYSQL / 'snapshot_monthly_revenue.parquet')[['stock_id', 'year', 'month', 'revenue_yoy']].rename(columns={'revenue_yoy': 'project_yoy'})
    rev['stock_id'] = rev.stock_id.astype(str)
    if rev.year.max() < 1911 + 200:
        rev.loc[rev.year < 1911, 'year'] += 1911  # ROC years in the snapshot
    m = s.merge(rev, on=['stock_id', 'year', 'month'], how='left')
    d = (m.finlab_yoy - m.project_yoy).abs()
    m['status'] = d.map(lambda x: 'NOT_IN_PROJECT' if pd.isna(x) else 'SAME' if x <= 0.01 else 'DIFFERENT')
    m['pit_note'] = 'FinLab index date = release-month convention, not the observed announcement time'
    path = OUT / 'MONTHLY_REVENUE_AUDIT_r2.csv'  # r1 mapped the release month as the data month
    assert not path.exists()
    m.drop(columns='date').to_csv(path, index=False, encoding='utf-8-sig')
    print(len(m), m.status.value_counts().to_dict())



def t1b():
    """Official TWTAUU/TWTB8U list vs the jump detector and FinLab; label/feature windows touched (proposal input only, no spec change)."""
    import glob
    import pandas as pd
    from core.research.adjustment.normalization import parse_number
    from scripts.post_archive_readiness import BARS
    rows = []
    for name, kind in (('TWTAUU', 'CAPITAL_REDUCTION'), ('TWTB8U', 'PAR_VALUE_CHANGE')):
        path = sorted(glob.glob(str(OUT / f't1b_official/{name}_*+0000.bin')))[-1]  # the startDate response (the first strDate ones are kept, incomplete)
        for r in json.loads(Path(path).read_text(encoding='utf-8'))['data']:
            y, m, d = r[0].split('/')
            rows.append({'stock_id': r[1], 'resume_date': f'{int(y) + 1911}-{m}-{d}', 'kind': kind, 'last_close': parse_number(r[3]), 'resume_ref': parse_number(r[4]),
                         'reason': r[9] if name == 'TWTAUU' else 'par value change', 'source_file': Path(path).name})
    off = pd.DataFrame(rows)
    off['price_factor'] = off.resume_ref / off.last_close
    bars = pd.read_parquet(BARS)
    bars['stock_id'] = bars.stock_id.astype(str)
    close = bars.pivot(index='trade_date', columns='stock_id', values='close').sort_index()
    off = off[off.stock_id.isin(close.columns)].copy()
    def first_traded(sid, day):  # the stock's own first bar on or after the resumption date (it may not trade on the market's first session)
        traded = close[sid].loc[pd.Timestamp(day):].dropna()
        return str(traded.index[0].date()) if len(traded) else None
    off['first_session'] = [first_traded(s_, d) for s_, d in zip(off.stock_id, off.resume_date)]
    jumps = pd.read_csv(ROOT / 'outputs/post_archive_readiness/2026-10-24/label_policy_excluded_events.csv', dtype={'stock_id': str})
    jumps = jumps[jumps.kind == 'UNEXPLAINED_MOVE_BEYOND_DAILY_LIMIT']
    jset = set(zip(jumps.stock_id, jumps.session))
    off['detected_by_jump'] = [(s, d) in jset for s, d in zip(off.stock_id, off.first_session)]
    fl = pd.read_csv(OUT / 'CORPORATE_ACTION_COVERAGE.csv', encoding='utf-8-sig', dtype={'stock_id': str}).dropna(subset=['kind'])
    fset = set(zip(fl.stock_id, fl.resume_date))
    off['in_finlab_2022_2023'] = [(s, d) in fset if d <= FREE_END else None for s, d in zip(off.stock_id, off.resume_date)]
    off['beyond_daily_limit'] = (off.price_factor - 1).abs() > 0.105
    sess = list(close.index)
    pos = {d: k for k, d in enumerate(sess)}
    label_cells = feature_cells = 0
    for r in off.itertuples():
        if r.first_session is None:
            continue
        k = pos[pd.Timestamp(r.first_session)]
        col = close[r.stock_id].notna().to_numpy()
        # label: rows t with a full 20-session horizon (the longest, quality_growth) that contains the event session
        label_cells += sum(col[t + 1:t + 21].all() and len(col[t + 1:t + 21]) == 20 for t in range(max(0, k - 20), k) if col[t])
        feature_cells += int(col[k:k + 30].sum())  # rows whose 30-session feature window includes the event session
    path = OUT / 'T1B_OFFICIAL_RECONCILIATION_r2.csv'  # r1 aligned on the market calendar, missing stocks that did not trade on that session
    assert not path.exists()
    off.to_csv(path, index=False, encoding='utf-8-sig')
    detected_jumps_explained = sum((s, d) in set(zip(off.stock_id, off.first_session)) for s, d in jset)
    summary = {'official_events_in_universe': len(off), 'by_kind': off.kind.value_counts().to_dict(), 'beyond_daily_limit': int(off.beyond_daily_limit.sum()),
               'detected_by_jump': int(off.detected_by_jump.sum()), 'jumps_total': len(jset), 'jumps_explained_by_official_list': detected_jumps_explained,
               'finlab_2022_2023_match': int(off.in_finlab_2022_2023.fillna(False).astype(bool).sum()), 'official_2022_2023': int((off.resume_date <= FREE_END).sum()),
               'label_rows_full_20_session_horizon_crossing_event': int(label_cells), 'feature_rows_window_contains_event': feature_cells}
    (OUT / 'T1B_OFFICIAL_SUMMARY_r2.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(summary)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'dividends': dividends, 'unblock': unblock, 'corporate-actions': corporate_actions, 'fundamentals': fundamentals, 'revenue': revenue, 't1b': t1b}[sys.argv[1]]()
