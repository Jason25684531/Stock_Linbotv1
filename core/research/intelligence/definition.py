"""Strategy Definition: card text bound to the source of the symbols it describes.

A source reference is `<repo-relative path>::<dotted symbol>` (class, function, method, property or module-level assignment).
The symbol is located by parsing the file (nothing is imported), so a card can cite engine and strategy code alike.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

from core.backtest.run_store import canonical_hash

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = ('hypothesis', 'factors', 'selection', 'entry', 'exit', 'rebalance', 'holdings', 'weighting', 'risk_controls',
            'cost_model', 'benchmark', 'data_requirements', 'pit_dependencies')


def _children(node):
    for child in getattr(node, 'body', []):
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child.name, child
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    yield target.id, child
        elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            yield child.target.id, child


def locate(ref: str, root=ROOT) -> dict:
    """The symbol's line range and the SHA256 of its exact source text; KeyError when it does not exist."""
    path, _, symbol = ref.partition('::')
    file = Path(root) / path
    if not symbol or not file.is_file():
        raise KeyError(f'SOURCE_SYMBOL_NOT_FOUND: {ref}')
    text = file.read_text(encoding='utf-8-sig')
    node = ast.parse(text)
    for part in symbol.split('.'):
        node = dict(_children(node)).get(part)
        if node is None:
            raise KeyError(f'SOURCE_SYMBOL_NOT_FOUND: {ref}')
    start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
    segment = '\n'.join(text.splitlines()[start - 1:node.end_lineno])
    return {'ref': ref, 'path': path, 'symbol': symbol, 'lines': [start, node.end_lineno], 'sha256': hashlib.sha256(segment.encode('utf-8')).hexdigest()}


def stamp(card: dict, root=ROOT) -> dict:
    """Record the current hash on every source that has none (authoring step). A recorded hash is never overwritten."""
    card = copy.deepcopy(card)
    for entry in card.get('fields', {}).values():
        for source in entry.get('sources', []):
            found = locate(source['ref'], root)
            source.setdefault('sha256', found['sha256'])
    return card


def build(card: dict, root=ROOT, numeric: dict | None = None) -> dict:
    """TRACED when every cited symbol still hashes to the recorded value, STALE_DEFINITION otherwise; no source means no text."""
    fields = {}
    for name in REQUIRED:
        entry = card.get('fields', {}).get(name) or {}
        if not entry.get('sources'):
            fields[name] = {'status': 'UNVERIFIED_DEFINITION', 'text': None, 'sources': []}
            continue
        sources = [locate(s['ref'], root) | {'recorded_sha256': s.get('sha256')} for s in entry['sources']]
        stale = any(s['recorded_sha256'] != s['sha256'] for s in sources)
        fields[name] = {'status': 'STALE_DEFINITION' if stale else 'TRACED', 'text': entry.get('text'), 'sources': sources}
    body = {'strategy_id': card['strategy_id'], 'fields': fields, 'numeric': numeric or {}}
    return body | {'definition_id': 'SD1-' + canonical_hash(body)[:16]}
