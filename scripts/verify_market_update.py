"""Independent check of a market data update against the official replies (read-only).

It never trusts the update's own reconciliation: for every date it reads the verified official replies kept by the transport
(MI_INDEX, T86, MI_MARGN), restricts them to the same securities the cleaning step keeps, and compares every database row
(close, volume, three institutional flows, margin and short balances). It also checks that rows on or before the anchor date
equal the pre-update backup (per-date count and sums), that no key is duplicated, that no write intent is left PENDING and that
each backup file still matches its receipt.

CLI: python scripts/verify_market_update.py --dates 2026-10-28,2026-11-09 [--anchor 2026-10-27] [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
UPDATES = ROOT / 'outputs' / 'market_data_updates'
TWSE = 'https://www.twse.com.tw/rwd/zh'
KEPT = re.compile(r'(?:[1-9]\d{3}|00\d{2,4}[A-Z]?)')  # same filter as jobs/update_database.clean_market_frame


def _num(value):
    text = str(value).replace(',', '').strip()
    try:
        return float(text)
    except ValueError:
        return None  # '--' (no trade) and blanks


def _official(url, params):
    from core.runtime import public_data
    reply = public_data.cached(public_data.context(url, params=params))
    return None if reply is None else reply.json()


def _table(payload, title_word=None, field=None, qualified=False):
    from core.runtime import public_data
    for table in payload.get('tables') or [payload]:
        if table and (title_word is None or title_word in str(table.get('title', ''))) and (field is None or field in table.get('fields', [])):
            return pd.DataFrame(table['data'], columns=public_data.qualified_fields(table) if qualified else table['fields'])
    return None


def official_frame(day: str) -> tuple[pd.DataFrame, dict]:
    ymd = day.replace('-', '')
    info = {}
    quotes = _official(f'{TWSE}/afterTrading/MI_INDEX', {'date': ymd, 'type': 'ALL', 'response': 'json'})
    table = _table(quotes, field='證券代號')
    frame = pd.DataFrame({'stock_id': table['證券代號'].str.strip(), 'close': table['收盤價'].map(_num),
                          'volume': table['成交股數'].map(_num)})
    frame = frame[frame.stock_id.str.fullmatch(KEPT.pattern) & (frame.close > 0)]
    flows = _official(f'{TWSE}/fund/T86', {'date': ymd, 'selectType': 'ALL', 'response': 'json'})
    info['t86'] = flows is not None
    if flows is not None:
        t = pd.DataFrame(flows['data'], columns=flows['fields'])
        frame = frame.merge(pd.DataFrame({'stock_id': t['證券代號'].str.strip(),
                                          'foreign': t['外陸資買賣超股數(不含外資自營商)'].map(_num),
                                          'trust': t['投信買賣超股數'].map(_num), 'dealer': t['自營商買賣超股數'].map(_num)}),
                            on='stock_id', how='left')
    margin = _official(f'{TWSE}/marginTrading/MI_MARGN', {'date': ymd, 'selectType': 'ALL', 'response': 'json'})
    info['mi_margn'] = margin is not None
    if margin is not None:
        m = _table(margin, title_word='融資融券彙總', qualified=True)
        frame = frame.merge(pd.DataFrame({'stock_id': m['股票代號'].str.strip(), 'margin': m['融資今日餘額'].map(_num),
                                          'short': m['融券今日餘額'].map(_num)}), on='stock_id', how='left')
    return frame, info


def _same(a, b):
    both = a.notna() & b.notna()
    return int((both & ((a - b).abs() <= 1e-9 * b.abs().clip(lower=1))).sum()), int((both & ~((a - b).abs() <= 1e-9 * b.abs().clip(lower=1))).sum()), \
        int((a.isna() & b.notna()).sum())


def check_day(engine, day: str) -> dict:
    official, info = official_frame(day)
    db = pd.read_sql('SELECT stock_id, close_price, volume, foreign_buy, trust_buy, dealer_buy, margin_balance, short_balance '
                     'FROM daily_market_data WHERE DATE(trade_date) = %(d)s', engine, params={'d': day})
    db['stock_id'] = db.stock_id.astype(str).str.strip()
    joined = db.merge(official, on='stock_id', how='outer', indicator=True)
    out = {'date': day, 'db_rows': len(db), 'official_rows_same_filter': len(official),
           'only_in_db': int((joined._merge == 'left_only').sum()), 'only_official': int((joined._merge == 'right_only').sum()),
           'duplicate_keys': int(db.stock_id.duplicated().sum()), **info}
    both = joined[joined._merge == 'both']
    pairs = [('close_price', 'close'), ('volume', 'volume'), ('foreign_buy', 'foreign'), ('trust_buy', 'trust'),
             ('dealer_buy', 'dealer'), ('margin_balance', 'margin'), ('short_balance', 'short')]
    for mine, theirs in pairs:
        if theirs in both:
            equal, differ, missing = _same(pd.to_numeric(both[mine], errors='coerce'), both[theirs])
            out[mine] = {'equal': equal, 'differ': differ, 'null_in_db_but_official': missing}
    out['volume_sum_db'] = float(pd.to_numeric(both.volume_x, errors='coerce').sum()) if 'volume_x' in both else None
    out['volume_sum_official'] = float(both.volume_y.sum()) if 'volume_y' in both else None
    for sample in ('2330', '2317', '0050'):
        row = both[both.stock_id == sample]
        if len(row):
            out.setdefault('samples', {})[sample] = {k: (None if pd.isna(v) else float(v)) for k, v in row.iloc[0].items()
                                                     if k not in ('stock_id', '_merge')}
    out['pass'] = (out['only_in_db'] == 0 and out['only_official'] == 0 and out['duplicate_keys'] == 0
                   and all(v['differ'] == 0 and v['null_in_db_but_official'] == 0 for k, v in out.items() if isinstance(v, dict) and 'differ' in v))
    return out


def old_rows_unchanged(engine, anchor: str) -> dict:
    """Rows on or before the anchor equal the pre-update backup: per date, row count and the sums of close and volume."""
    backup = pd.read_parquet(UPDATES / 'backups' / 'daily_market_data' / 'export.parquet', columns=['trade_date', 'close_price', 'volume'])
    backup['d'] = pd.to_datetime(backup.trade_date).dt.strftime('%Y-%m-%d')
    backup = backup[backup.d <= anchor]
    now = pd.read_sql('SELECT DATE(trade_date) AS d, COUNT(*) AS n, SUM(close_price) AS c, SUM(volume) AS v FROM daily_market_data '
                      'WHERE trade_date < DATE_ADD(%(a)s, INTERVAL 1 DAY) GROUP BY DATE(trade_date)', engine, params={'a': anchor})
    now['d'] = pd.to_datetime(now.d).dt.strftime('%Y-%m-%d')
    before = backup.groupby('d').agg(n=('close_price', 'size'), c=('close_price', 'sum'), v=('volume', 'sum')).reset_index()
    joined = before.merge(now, on='d', how='outer', suffixes=('_before', '_now'))
    bad = joined[(joined.n_before != joined.n_now) | ((joined.c_before - joined.c_now).abs() > 1e-6 * joined.c_before.abs().clip(lower=1))
                 | ((joined.v_before - joined.v_now).abs() > 0.5)]
    return {'dates': len(joined), 'rows_before': int(before.n.sum()), 'rows_now': int(now.n.sum()), 'differing_dates': bad.d.tolist()[:20],
            'pass': bad.empty}


def receipts() -> dict:
    out = {}
    for receipt in sorted((UPDATES / 'backups').glob('*/RECEIPT.json')):
        r = json.loads(receipt.read_text(encoding='utf-8'))
        actual = hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()
        out[r['table']] = {'rows': r['rows'], 'sha256': r['sha256'], 'file_matches': actual == r['sha256']}
    return out


def pending_intents() -> list:
    return [p.name for p in sorted((UPDATES / 'intents').glob('*.json'))
            if json.loads(p.read_text(encoding='utf-8')).get('status') == 'PENDING']


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--dates', required=True)
    parser.add_argument('--anchor', default='2026-10-27')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    from core.db_helper import get_db_engine
    engine = get_db_engine()
    report = {'days': [check_day(engine, d) for d in args.dates.split(',')], 'old_rows': old_rows_unchanged(engine, args.anchor),
              'backups': receipts(), 'pending_intents': pending_intents()}
    report['pass'] = (all(d['pass'] for d in report['days']) and report['old_rows']['pass'] and not report['pending_intents']
                      and all(b['file_matches'] for b in report['backups'].values()))
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    print(text)
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
