"""Research-only signal run (development replay or synthetic data): signals, target portfolio, simulated rebalance,
performance-record shell, data health and manifest under outputs/research_mode/signal_shadow_v1.

This is not a forward observation and publishes nothing: no recommendation table, no LINE, no broker."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import strategy_signal as ss
from core.strategy.runner import StrategyRunner

OUTPUT_ROOT = ss.RESEARCH_DIR / 'signal_shadow_v1'


def _previous(folder, day):
    """Latest earlier run of the same strategy in the same folder, if any."""
    earlier = sorted(p for p in folder.glob('*') if p.name < day and (p / 'target_portfolio.csv').is_file()) if folder.is_dir() else []
    if not earlier:
        return None, None
    return pd.read_csv(earlier[-1] / 'target_portfolio.csv', dtype={'ticker': str}), json.loads((earlier[-1] / 'data_health.json').read_text(encoding='utf-8'))


def run(strategy_id, context, output_root=OUTPUT_ROOT, registry=None, mode='DEVELOPMENT_REPLAY'):
    """Strategy-agnostic: any registered executor whose SelectionResult carries the standard signal frame."""
    if registry is None:
        from core.research.h4_signal import build_research_registry
        registry = build_research_registry()
    runner = StrategyRunner(registry)
    spec = runner.resolve_spec(strategy_id)
    day = str(context.asof_date)[:10]
    ss.check_window(day)
    result = runner.execute(strategy_id, context)
    signals, input_dates = result.metadata['signals'], dict(result.metadata.get('input_dates', {}))
    ss.check_window(*input_dates.values())
    if list(signals.columns[:len(ss.COMMON)]) != list(ss.COMMON):
        raise ValueError('NONSTANDARD_SIGNAL_FORMAT')
    folder = Path(output_root) / strategy_id.replace(':', '_')
    previous_target, previous_health = _previous(folder, day)
    target = ss.target_portfolio(signals, spec.portfolio_policy.get('max_weight'))
    outputs = {'signals.csv': signals, 'target_portfolio.csv': target, 'rebalance.csv': ss.rebalance(target, previous_target),
               'performance_record.csv': ss.performance_record(target, spec.parameters.get('rebalance_days')),
               'data_health.json': ss.data_health(signals, input_dates, day, previous_health)}
    manifest = {'strategy_version': spec.strategy_id, 'spec_version': spec.version, 'lifecycle': spec.lifecycle.value,
                'parameters': json.loads(json.dumps(dict(spec.parameters), default=dict)), 'signal_date': day, 'mode': mode,
                'data_version': signals['data_version'].iloc[0], 'source_hash': signals['source_hash'].iloc[0],
                'input_digests': dict(result.metadata.get('digests', {})), 'research_only': True, 'publish': 'DISABLED',
                'forward_observation': False, 'selected': len(target), 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'files': list(ss.FILES)}
    return ss.write_run(folder / day, outputs, manifest)


if __name__ == '__main__':
    from core.research.h4_signal import STRATEGY_ID, dev_context, synthetic_context
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strategy', default=STRATEGY_ID)
    parser.add_argument('--source', choices=('dev', 'synthetic'), default='dev')
    parser.add_argument('--asof', nargs='+', required=True, help='signal dates, run in order')
    parser.add_argument('--output-root', type=Path)
    args = parser.parse_args()
    contexts = {'dev': dev_context, 'synthetic': synthetic_context}
    root = args.output_root or (OUTPUT_ROOT if args.source == 'dev' else OUTPUT_ROOT / 'synthetic')
    for day in args.asof:
        out = run(args.strategy, contexts[args.source](day), root, mode='DEVELOPMENT_REPLAY' if args.source == 'dev' else 'SYNTHETIC')
        print(out)
