"""Assemble offline research input checkpoints from verified annual raw caches."""
import argparse
from bisect import bisect_left
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V2, RESEARCH_ASSUMPTIONS_V6, assumptions_sha, reference_change_policy
from scripts.prepare_research_mode import digest
from scripts.research_mode_inputs import build_research_universe, load_development_events


def write_once(path, value):
    path = Path(path)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if path.exists():
        if digest(path) != hashlib.sha256(data).hexdigest():
            raise ValueError(f'CHECKPOINT_CHANGED:{path.name}')
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.research-input-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def align_events(converted, sessions):
    """Keep non-session events unresolved; do not invent their effective terms or date."""
    sessions = sorted(sessions)
    valid, unresolved, outside = [], [], 0
    for event, known_terms in [(item, True) for item in converted['actions']] + [(item, False) for item in converted['unresolved_events']]:
        day = event['ex_date']
        if day < sessions[0] or day > sessions[-1]:
            outside += 1
            continue
        index = bisect_left(sessions, day)
        if sessions[index] != day:
            unresolved.append({'ex_date': sessions[index], 'known_date': sessions[index], 'source_event_date': day,
                               'stock_id': event['stock_id'], 'source_sha256': event['source_sha256'],
                               'missing_terms': ['actual_effective_event_date'] + ([] if known_terms else event['missing_terms']),
                               **({'value_per_share': event['value_per_share']} if 'value_per_share' in event else {})})
        elif known_terms:
            valid.append(event)
        else:
            unresolved.append(event)
    return {'actions': valid, 'unresolved_events': unresolved, 'outside_cache_window': outside}


def prepare(cache, output, dividend, references, delistings, assumptions=RESEARCH_ASSUMPTIONS_V6):
    cache, output = Path(cache), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    market = {'price_basis': 'RAW', 'source': 'twse_rwd', 'bars': {}, 'source_sha256_by_date': {},
              'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(assumptions)}
    for year in range(2022, 2026):
        path, metadata = cache / f'bars_{year}.json', cache / f'bars_{year}.manifest.json'
        manifest = json.loads(metadata.read_text(encoding='utf-8'))
        if digest(path) != manifest['output_sha256'] or manifest['assumptions_sha256'] != assumptions_sha(RESEARCH_ASSUMPTIONS_V2):
            raise ValueError(f'CHECKPOINT_UNVERIFIED:{year}')
        chunk = json.loads(path.read_text(encoding='utf-8'))
        if set(market['bars']) & set(chunk['bars']):
            raise ValueError('DUPLICATE_CLOSING_SESSION')
        market['bars'].update(chunk['bars'])
        market['source_sha256_by_date'].update(chunk['source_sha256_by_date'])
    if max(market['bars']) != '2025-12-31':
        raise ValueError('INCOMPLETE_DEVELOPMENT_WINDOW')
    market_sha = write_once(output / 'bars.json', market)
    print('bars: VERIFIED', flush=True)
    universe = build_research_universe(market)
    universe = universe.loc[universe['asof_date'] >= '2023-01-03']
    universe_sha = write_once(output / 'universe.json', universe.to_dict('records'))
    print('universe: VERIFIED', flush=True)
    converted = load_development_events(dividend, references, '2025-12-31', reference_policy=reference_change_policy(assumptions))
    raw_delistings = Path(delistings).read_bytes()
    entries = json.loads(raw_delistings.decode('utf-8'))
    if not isinstance(entries, list):
        raise ValueError('INVALID_DELISTING_CACHE')
    for row in entries:
        try:
            year, month, day = map(int, row['DelistingDate'].split('/'))
            event_day = date(year + 1911, month, day)
        except (KeyError, TypeError, ValueError):
            raise ValueError('INVALID_DELISTING_DATE') from None
        if event_day > date(2025, 12, 31):
            continue
        asset = str(row.get('Code', ''))
        if len(asset) != 4 or not asset.isdigit() or asset.startswith('0'):
            continue
        converted['unresolved_events'].append({'ex_date': event_day.isoformat(), 'stock_id': asset,
                                                'missing_terms': ['delisting_settlement_terms'],
                                                'source_sha256': hashlib.sha256(raw_delistings).hexdigest()})
    days = set(market['bars'])
    converted = align_events(converted, days)
    assets = sorted({asset for daily in market['bars'].values() for asset in daily})
    converted['coverage'] = {asset: sorted(days) for asset in assets}
    converted.update(label='DIAGNOSTIC_ONLY', data_label='RESEARCH_UNCERTIFIED', research_only=True,
                     terms_status='RESEARCH_ASSUMPTIONS', assumptions_sha256=market['assumptions_sha256'])
    events_sha = write_once(output / 'actions.json', converted)
    manifest = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
                'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': market['assumptions_sha256'],
                'producer_sha256': digest(ROOT / 'scripts/research_mode_inputs.py'),
                'orchestrator_sha256': digest(Path(__file__)), 'development_end': '2025-12-31',
                'files': {'bars.json': market_sha, 'universe.json': universe_sha, 'actions.json': events_sha},
                'source_sha256': {Path(path).name: digest(path) for path in (dividend, *references.values(), delistings)},
                'assumptions': ['observed listing age is a conservative proxy', 'action coverage is local-cache uncertified'],
                'exclusion_counts': {'unresolved_events': len(converted['unresolved_events'])}}
    write_once(output / 'input_manifest.json', manifest)
    print('events: VERIFIED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'outputs/research_mode/input_cache_v4')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/prepared_v6')
    args = parser.parse_args()
    t1b = ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official'
    references = {}
    for pattern, kind in (('TWTAUU_*+0000.bin', 'CAPITAL_REDUCTION'), ('TWTB8U_*+0000.bin', 'PAR_VALUE_CHANGE')):
        files = list(t1b.glob(pattern))
        if len(files) != 1:
            raise ValueError(f'UNVERIFIED_REFERENCE_CACHE:{kind}')
        references[kind] = files[0]
    prepare(args.cache, args.output, ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd/TWT49U_20230103_20260728.json', references,
            ROOT / 'artifacts/official_dual_track_cache/suspendListing.json')
