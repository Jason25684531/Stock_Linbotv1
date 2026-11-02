"""Build resumable, offline raw-price checkpoints through the development window."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V2, assumptions_sha
from scripts.research_mode_inputs import build_raw_bars


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def prepare(cache, output):
    cache, output = Path(cache), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = {path.stem[-8:]: path for path in cache.glob('MI_INDEX_*.json')
             if '20211230' <= path.stem[-8:] <= '20251231'}
    # Bind the checkpoint to the bar-building function itself, not to unrelated edits elsewhere in its module.
    producer = hashlib.sha256(inspect.getsource(build_raw_bars).encode('utf-8')).hexdigest()
    orchestrator = digest(Path(__file__))
    previous = None
    for year in range(2022, 2026):
        selected = sorted(path for day, path in paths.items() if day.startswith(str(year)) or (year == 2022 and day.startswith('2021')))
        if previous is not None:
            selected.insert(0, previous)
        if not selected:
            raise ValueError(f'MISSING_RAW_PRICE_YEAR:{year}')
        data_path = output / f'bars_{year}.json'
        meta_path = output / f'bars_{year}.manifest.json'
        inputs = {path.name: digest(path) for path in selected}
        if data_path.exists() or meta_path.exists():
            if not data_path.is_file() or not meta_path.is_file():
                raise ValueError(f'INCOMPLETE_CHECKPOINT:{year}')
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if (meta['producer_sha256'] != producer or meta.get('orchestrator_sha256') != orchestrator
                    or meta['input_sha256'] != inputs or meta['output_sha256'] != digest(data_path)):
                raise ValueError(f'CHECKPOINT_CHANGED:{year}')
        else:
            market = build_raw_bars(selected)
            if inputs != {path.name: digest(path) for path in selected}:
                raise ValueError(f'INPUT_CHANGED_DURING_PREPARATION:{year}')
            if previous is not None:
                prior_day = previous.stem[-8:]
                previous_key = f'{prior_day[:4]}-{prior_day[4:6]}-{prior_day[6:]}'
                market['bars'].pop(previous_key, None)
                market['source_sha256_by_date'].pop(previous_key, None)
            market.update(label='DIAGNOSTIC_ONLY', research_only=True, data_label='RESEARCH_UNCERTIFIED',
                          terms_status='RESEARCH_ASSUMPTIONS', assumptions_sha256=assumptions_sha(RESEARCH_ASSUMPTIONS_V2))
            data_tmp = data_path.with_suffix('.json.tmp')
            with data_tmp.open('x', encoding='utf-8') as stream:
                json.dump(market, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            data_tmp.replace(data_path)
            meta = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
                    'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V2),
                    'producer_sha256': producer, 'orchestrator_sha256': orchestrator, 'input_sha256': inputs, 'output_sha256': digest(data_path),
                    'last_session': max(market['bars'])}
            with meta_path.open('x', encoding='utf-8') as stream:
                json.dump(meta, stream, ensure_ascii=False, indent=2)
        previous = paths[meta['last_session'].replace('-', '')]
        print(f'{year}: VERIFIED checkpoint, last session {meta["last_session"]}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/input_cache_v4')
    args = parser.parse_args()
    prepare(args.cache, args.output)
