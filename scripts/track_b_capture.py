"""Track B reruns: run the existing G2/G3, D5 and Day3 jobs unchanged and keep every configuration's daily returns and orders.

Only the engine call of each job is wrapped. Besides each family's native tracks, every configuration is also run under the project
cost constants (fee, tax, slippage) and under twice those costs, so all families can be compared under the same costs.
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import write
from scripts.track_b_validation import FAMILY_SIZE, OUT

CANONICAL = {'DAY3': ROOT / 'outputs/strategy_research/strategy_search_20260914_v2',
             'G2G3': ROOT / 'outputs/fundamental_strategy_validation/add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4',
             'D5': ROOT / 'outputs/portfolio_research/d5_portfolio_research_20260817_v2'}
ORDER_COLUMNS = ['asset', 'date', 'side', 'shares', 'price', 'raw_price', 'notional']


class Collector:
    """Daily returns of every track and the native-base orders of every configuration, in a common schema."""

    def __init__(self):
        self.returns, self.orders, self.ids = [], [], []

    def add(self, config_id, track, returns):
        self.returns.append(pd.DataFrame({'config_id': str(config_id), 'track': track, 'date': pd.to_datetime(returns.index), 'ret': returns.to_numpy(dtype=float)}))
        if str(config_id) not in self.ids:
            self.ids.append(str(config_id))

    def add_orders(self, config_id, orders):
        self.orders.append(orders.assign(config_id=str(config_id)))


def orders_from_vbt(frame, slippage):
    side = frame['Side'].astype(str).str.lower()
    raw = frame['Price'] / (1 + side.map({'buy': slippage, 'sell': -slippage}))
    return pd.DataFrame({'asset': frame['Column'].astype(str).to_numpy(), 'date': pd.to_datetime(frame['Timestamp']).to_numpy(), 'side': side.to_numpy(),
                         'shares': frame['Size'].to_numpy(), 'price': frame['Price'].to_numpy(), 'raw_price': raw.to_numpy(),
                         'notional': (frame['Size'] * frame['Price']).to_numpy()})


def orders_from_trades(trades, slippage):
    frame = pd.DataFrame(trades)
    if frame.empty:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    raw = frame['price'] / (1 + frame['side'].map({'buy': slippage, 'sell': -slippage}))
    return pd.DataFrame({'asset': frame['asset_id'].astype(str), 'date': pd.to_datetime(frame['execution_date']), 'side': frame['side'], 'shares': frame['shares'],
                         'price': frame['price'], 'raw_price': raw, 'notional': frame['notional']})


def project_costs(multiple):
    from config.settings import Config

    return {'fee_rate': multiple * Config.FEE_RATE, 'tax_rate': multiple * Config.TAX_RATE, 'slippage': multiple * Config.SLIPPAGE_RATE}


def install_capture(family, collector):
    """Wrap the engine call of one job; returns the function that undoes the wrapping."""
    if family == 'DAY3':
        import jobs.run_strategy_search as job

        ss, state = job.ss, {}
        original_cfg, original_tracks = job._run_one_config, ss.run_backtest_tracks

        def cfg(strategy_id, *a, **k):
            state['id'] = strategy_id
            return original_cfg(strategy_id, *a, **k)

        def tracks(close, target, **k):
            result = original_tracks(close, target, **k)
            for name, r in result.items():
                collector.add(state['id'], name, r['returns'])
            collector.add_orders(state['id'], orders_from_vbt(result['net_base']['orders'], 0.0))
            for name, multiple in (('PROJECT_BASE', 1), ('PROJECT_STRESS2X', 2)):
                run = ss.run_vectorbt(close, target, initial_capital=1_000_000.0, sparse_rebalance=True, **project_costs(multiple))
                collector.add(state['id'], name, run['returns'])
            return result

        job._run_one_config, ss.run_backtest_tracks = cfg, tracks
        return lambda: (setattr(job, '_run_one_config', original_cfg), setattr(ss, 'run_backtest_tracks', original_tracks))
    if family == 'D5':
        import jobs.run_portfolio_research as job

        original, ids = job.run_vectorbt, iter(job.parameter_grid().config_id)

        def wrapper(close, target, **k):
            result, config_id = original(close, target, **k), next(ids)
            collector.add(config_id, 'NATIVE_BASE', result['returns'])
            collector.add_orders(config_id, orders_from_vbt(result['orders'], k.get('slippage', 0.0)))
            for name, multiple in (('PROJECT_BASE', 1), ('PROJECT_STRESS2X', 2)):
                run = original(close, target, initial_capital=k['initial_capital'], sparse_rebalance=True, **project_costs(multiple))
                collector.add(config_id, name, run['returns'])
            return result

        job.run_vectorbt = wrapper
        return lambda: setattr(job, 'run_vectorbt', original)
    import jobs.run_fundamental_strategy_validation as job

    f = job.f
    original = f.run_cost_tracks

    def wrapper(targets, prices, policy=f.DEFAULT_POLICY):
        result, config_id = original(targets, prices, policy), str(targets['strategy_id'].iloc[0])
        for name, r in result.items():
            collector.add(config_id, name, r['returns'])
        collector.add_orders(config_id, orders_from_trades(result['BASE_COST']['trades'], policy.base_slippage))
        collector.add(config_id, 'PROJECT_BASE', result['BASE_COST']['returns'])  # same costs as the project constants
        stress = f.simulate_target_weights(targets, prices, initial_capital=policy.initial_capital, cost=project_costs(2))
        collector.add(config_id, 'PROJECT_STRESS2X', stress['returns'])
        return result

    f.run_cost_tracks = wrapper
    return lambda: setattr(f, 'run_cost_tracks', original)


def compare_outputs(canonical, rerun_dir):
    """Same CSV bytes as the archived canonical run? (the jobs write canonically sorted CSV files)"""

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    names = sorted(p.name for p in Path(canonical).glob('*.csv'))
    same = [n for n in names if (Path(rerun_dir) / n).exists() and sha(Path(canonical) / n) == sha(Path(rerun_dir) / n)]
    return {'canonical': Path(canonical).relative_to(ROOT).as_posix(), 'csv_files': len(names), 'identical': len(same),
            'different': [n for n in names if n not in same], 'status': 'IDENTICAL' if len(same) == len(names) else 'DIFFERENT'}


def rerun(family, fundamental_matrix=None, out=None):
    # ponytail: fundamental_matrix/out only for G2G3 diagnostics on another dataset version; defaults keep the archived layout
    run_dir = Path(out) / 'run' if out else OUT / 'track_b_runs' / family.lower()
    sink = Path(out) if out else OUT
    if run_dir.exists():
        raise FileExistsError(run_dir)
    collector, started = Collector(), time.perf_counter()
    restore = install_capture(family, collector)
    try:
        if family == 'DAY3':
            import jobs.run_strategy_search as job
            job.run(day2_dir=job.DEFAULT_DAY2_DIR, d3_root=job.DEFAULT_D3_ROOT, master_path=job.DEFAULT_MASTER_PATH, output_dir=run_dir)
        elif family == 'G2G3':
            import jobs.run_fundamental_strategy_validation as job
            job.run_pipeline(output_dir=run_dir, **({'fundamental_matrix': Path(fundamental_matrix)} if fundamental_matrix else {}))
        else:
            import jobs.run_portfolio_research as job
            job.run(handoff_path=Path('artifacts/d5_handoff/d4_acceptance_20260811_v8_with_scoreboard'),
                    dataset_path=Path('artifacts/factors/d3_full_20230103_20260728/research_dataset'), output_dir=run_dir)
    finally:
        restore()
    returns, orders = pd.concat(collector.returns, ignore_index=True), pd.concat(collector.orders, ignore_index=True)
    returns.to_parquet(sink / f'returns_{family.lower()}.parquet', index=False)
    orders.to_parquet(sink / f'orders_{family.lower()}.parquet', index=False)
    summary = {'family': family, 'configurations_captured': len(collector.ids), 'expected': FAMILY_SIZE[family], 'seconds': round(time.perf_counter() - started, 1),
               'returns_rows': len(returns), 'orders_rows': len(orders), 'first_date': str(returns.date.min().date()), 'last_date': str(returns.date.max().date()),
               'reproduction': compare_outputs(CANONICAL[family], run_dir)}
    write(sink / f'rerun_{family.lower()}.json', summary)
    print(summary)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('family', choices=tuple(FAMILY_SIZE))
    rerun(parser.parse_args().family)
