"""How much of the archived P&L came from orders that could not have filled (locked limit at the open, no bar, no trade)?

Re-analysis of the archived orders only: no backtest rerun, no new data. The archived evaluation flagged these orders (execution_audit
PASS_FLAG_ONLY) but left them in the returns. Here each unfillable BUY lot is priced from its fill to the next sell of the same asset in the
same configuration (or the last adjusted close if never sold); that is the P&L the backtest credited for a trade that could not happen.
ponytail: one lot per buy, next-sell exit; FIFO across overlapping buys only matters when a name is bought twice before a sell.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.track_b_data import adjustment_factor  # noqa: E402
from scripts.track_b_evaluate import asset_pnl, audit_orders, load_shared, official_reference  # noqa: E402
from scripts.track_b_validation import OUT as DT  # noqa: E402

OUT = ROOT / 'outputs/research_workflow/2026-10-25'
FAMILIES = {'day3': 'A191_132', 'd5': None}  # None: every configuration of the family


def lot_pnl(orders, flags, last_price):
    """P&L credited to each unfillable buy: notional x (exit price / fill price - 1)."""
    rows = []
    sells = orders[orders.side == 'sell'].sort_values('date')
    for o in orders[(orders.side == 'buy') & flags.unfillable.to_numpy()].itertuples():
        later = sells[(sells.asset == o.asset) & (sells.date > o.date)]
        exit_price = later.price.iloc[0] if len(later) else last_price.get(o.asset, np.nan)
        rows.append({'config_id': o.config_id, 'asset': o.asset, 'date': o.date, 'notional': o.notional,
                     'exit': 'SOLD' if len(later) else 'HELD_TO_END', 'pnl': o.notional * (exit_price / o.price - 1)})
    return pd.DataFrame(rows, columns=['config_id', 'asset', 'date', 'notional', 'exit', 'pnl'])


def impact(family, name, shared):
    bars, events, *_ = shared
    orders = pd.read_parquet(DT / f'orders_{family}.parquet')
    if name:
        orders = orders[orders.config_id.str.startswith(f'{name}__')]
    reference = official_reference(bars, events)
    close = bars.pivot(index='trade_date', columns='stock_id', values='close').sort_index().ffill()
    out = []
    for config_id, g in orders.groupby('config_id'):
        g = g.reset_index(drop=True)
        flags = audit_orders(g, reference, events)
        end = g.date.max()
        last = close.loc[:end].iloc[-1]
        last = (last * pd.Series(adjustment_factor(events[events.range.eq('D3')], last.index.astype(str), [end] * len(last)), index=last.index)).dropna()
        lots = lot_pnl(g, flags, last.to_dict())
        total = float(asset_pnl(g, last.to_dict()).sum())
        credited = float(lots.pnl.sum()) if len(lots) else 0.0
        out.append({'family': family, 'config_id': config_id, 'orders': len(g), 'unfillable_buys': len(lots), 'unfillable_sells': int((flags.unfillable & (g.side == 'sell')).sum()),
                    'credited_pnl': credited, 'total_pnl': total, 'credited_share_of_pnl': credited / total if total else None,
                    'pnl_without_unfillable_buys': total - credited})
    return pd.DataFrame(out)


DIFF_OUT = ROOT / 'outputs/research_workflow/2026-10-26'
NEW_STATUS = (('no_bar', 'REJECTED_NO_BAR'), ('no_trade', 'REJECTED_NO_TRADE'), ('lock_buy', 'REJECTED_LOCKED_LIMIT_UP'),
              ('lock_sell', 'REJECTED_LOCKED_LIMIT_DOWN'), ('over_5pct', 'PARTIAL_PARTICIPATION_CAP'))


def diff_status(flags):
    """Audit flags -> per-order old/new status. Precedence no_bar > no_trade > locked buy > locked sell > over the 5 % cap; else FILLED."""
    new = np.select([flags[c] for c, _ in NEW_STATUS], [s for _, s in NEW_STATUS], 'FILLED')
    return flags[['config_id', 'date', 'asset', 'side', 'notional']].assign(old_status='FILLED', new_status=new, label='DIAGNOSTIC_ONLY')


def fill_rule_diff(family, name, shared):
    """Per archived order: the old engine's status (always FILLED) against the shared fill rule's verdict on the same official bar.
    A replay of archived orders, not a rerun: a rejected buy would have changed every later order, which only a corrected rerun shows."""
    bars, events, *_ = shared
    orders = pd.read_parquet(DT / f'orders_{family}.parquet')
    if name:
        orders = orders[orders.config_id.str.startswith(f'{name}__')]
    return diff_status(audit_orders(orders.reset_index(drop=True), official_reference(bars, events), events)).assign(family=family)


def main(diff=False):
    import hashlib
    shared = load_shared()
    if diff:
        DIFF_OUT.mkdir(parents=True, exist_ok=True)
        for family, name in FAMILIES.items():
            frame = fill_rule_diff(family, name, shared).sort_values(['config_id', 'date', 'asset', 'side']).reset_index(drop=True)
            path = DIFF_OUT / f'fill_rule_diff_{family}.csv'
            frame.to_csv(path, index=False, encoding='utf-8-sig', lineterminator='\n')
            print(family, hashlib.sha256(path.read_bytes()).hexdigest(), frame.new_status.value_counts().to_dict())
        return
    frame = pd.concat([impact(f, n, shared) for f, n in FAMILIES.items()], ignore_index=True).assign(label='DIAGNOSTIC_ONLY')
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT / 'fill_feasibility_impact.csv', index=False, encoding='utf-8-sig')
    print(frame.groupby('family')[['unfillable_buys', 'credited_pnl', 'total_pnl', 'credited_share_of_pnl']].describe().T.round(3).to_string())


if __name__ == '__main__':
    main(diff='--diff' in sys.argv)
