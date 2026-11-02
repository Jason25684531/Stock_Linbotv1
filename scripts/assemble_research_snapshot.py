"""Assemble a read-only, provenance-labelled snapshot without inventing candidates."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.evidence_ledger import read
from scripts.research_status_snapshot import write_snapshot

OUTPUT_ROOT = ROOT / 'outputs/research_workflow'
MATRIX = OUTPUT_ROOT / 'autonomous_20261030_v3/strategy_qualification_matrix.csv'
EVIDENCE = ROOT / 'outputs/research_repair/autonomous_evidence_inventory_20261030'


def assemble(output, research_track=None):
    output = Path(output).resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()) or output == OUTPUT_ROOT.resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_WORKFLOW')
    matrix_raw = MATRIX.read_bytes()
    with MATRIX.open(encoding='utf-8-sig', newline='') as stream:
        matrix = list(csv.DictReader(stream))
    summary_raw = (EVIDENCE / 'evidence_summary.json').read_bytes()
    summary = json.loads(summary_raw)
    ledger = EVIDENCE / 'evidence.jsonl'
    audit = json.loads((EVIDENCE / 'evidence_audit.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
    if audit['chain'] != 'PASS' or digest != audit['sha256'] or not read(ledger):
        raise ValueError('EVIDENCE_LEDGER_UNVERIFIED')
    if summary.get('coverage') is None and not summary.get('reason'):
        raise ValueError('MISSING_COVERAGE_REASON')
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'strategy_qualification_matrix.csv').open('xb') as stream:
        stream.write(matrix_raw)
    with (output / 'evidence_summary.json').open('xb') as stream:
        stream.write(summary_raw)
    statuses = write_snapshot(MATRIX, output)
    expected = {row['strategy']: (row['RESEARCH_STATUS'], row['RESEARCH_REASON']) for row in matrix}
    if len(expected) != 22 or any(expected.get(row['strategy']) != (row['status'], row['reason']) for row in statuses):
        raise ValueError('MATRIX_AND_RESEARCH_STATUS_DISAGREE')
    candidates = {'status': 'BLOCKED', 'reason': 'No candidate run with verified PIT source evidence is available.',
                  'research_only': True, 'candidates': []}
    with (output / 'candidates.json').open('x', encoding='utf-8') as stream:
        json.dump(candidates, stream, indent=2)
    files = ['strategy_qualification_matrix.csv', 'research_status.json', 'qualified_strategies.json',
             'evidence_summary.json', 'candidates.json']
    if research_track is not None:
        track = json.loads(Path(research_track).read_text(encoding='utf-8'))
        if (track.get('label') != 'DIAGNOSTIC_ONLY' or track.get('data_label') != 'RESEARCH_UNCERTIFIED'
                or track.get('research_only') is not True or track.get('terms_status') != 'RESEARCH_ASSUMPTIONS'
                or not isinstance(track.get('strategies'), list)):
            raise ValueError('INVALID_RESEARCH_TRACK')
        with (output / 'research_track.json').open('x', encoding='utf-8') as stream:
            json.dump(track, stream, ensure_ascii=False, indent=2, allow_nan=False)
        files.append('research_track.json')
    manifest = {'label': 'RESEARCH_ONLY', 'evidence_ledger_sha256': digest,
                'files': {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in files}}
    with (output / 'snapshot_manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(OUTPUT_ROOT / 'research_status_snapshot'))
    parser.add_argument('--research-track')
    args = parser.parse_args()
    result = assemble(args.output, research_track=args.research_track)
    print(json.dumps({'status': 'READY', 'label': result['label'], 'files': len(result['files'])}))
