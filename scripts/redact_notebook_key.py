"""Replace a literal TEJ key in a notebook with an environment read (user-authorized step).

The original bytes are first copied to <quarantine_dir>/<filename>.orig. The literal is never
printed or returned.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.notebook_inventory import QUARANTINE_DIR, scan_credentials  # noqa: E402

ENV_LINE = "os.environ['TEJAPI_KEY'] = os.environ.get('TEJAPI_KEY', '')"
IMPORT_OS = re.compile(r'^\s*import\s+os\b', re.M)


def _layout(text: str) -> tuple[str, int, str]:
    """(newline, indent, trailer) of the original file; Jupyter uses indent=1, Colab/VS Code 2."""
    newline = '\r\n' if '\r\n' in text else '\n'
    second = text.split(newline, 2)[1] if newline in text else ''
    indent = (len(second) - len(second.lstrip(' '))) or 1
    trailer = newline if text.endswith(newline) else ''
    return newline, indent, trailer


def _dump(nb: dict, layout: tuple[str, int, str]) -> str:
    # JSON strings escape newlines, so swapping the line terminator only touches layout.
    newline, indent, trailer = layout
    return json.dumps(nb, ensure_ascii=False, indent=indent).replace('\n', newline) + trailer


def redact(path, quarantine_dir=QUARANTINE_DIR) -> dict:
    path, qdir = Path(path), Path(quarantine_dir)
    raw = path.read_bytes()
    qdir.mkdir(parents=True, exist_ok=True)
    qcopy = qdir / (path.name + '.orig')
    if qcopy.exists():
        if qcopy.read_bytes() != raw:
            raise RuntimeError(f'quarantine copy {qcopy.name} exists with different bytes; refusing')
    else:
        qcopy.write_bytes(raw)
        assert qcopy.read_bytes() == raw

    text = raw.decode('utf-8')
    layout = _layout(text)
    newline = layout[0]
    orig = json.loads(text)
    if _dump(orig, layout) != text:
        raise RuntimeError(f'{path.name} does not round-trip through json; refusing to rewrite the whole file')
    nb = json.loads(text)
    hits = [f for f in scan_credentials(orig) if f['literal']]
    if not hits:
        raise RuntimeError(f'no literal credential found in {path.name}')
    literals = {f['literal'] for f in hits}
    cells = sorted({f['cell'] for f in hits})

    changed = 0
    for idx in cells:
        src = nb['cells'][idx]['source']
        lines = src if isinstance(src, list) else src.splitlines(keepends=True)
        has_os = any(IMPORT_OS.search(''.join(c['source'])) for c in nb['cells'][:idx] if c.get('cell_type') == 'code')
        new = []
        for line in lines:
            if any(lit in line for lit in literals):
                if not has_os and not IMPORT_OS.search(''.join(new)):
                    new.append('import os\n')
                indent = line[:len(line) - len(line.lstrip())]
                new.append(indent + ENV_LINE + ('\n' if line.endswith('\n') else ''))
                changed += 1
            else:
                new.append(line)
        nb['cells'][idx]['source'] = new if isinstance(src, list) else ''.join(new)

    out = _dump(nb, layout)
    assert not any(lit in out for lit in literals)
    path.write_bytes(out.encode('utf-8'))

    # Verify the written file.
    written = path.read_bytes().decode('utf-8')
    back = json.loads(written)
    assert not any(lit in written for lit in literals), 'literal still present'
    assert len(back['cells']) == len(orig['cells'])
    for i, (a, b) in enumerate(zip(orig['cells'], back['cells'])):
        if i in cells:
            assert {k: v for k, v in a.items() if k != 'source'} == {k: v for k, v in b.items() if k != 'source'}
            assert ENV_LINE in ''.join(b['source'])
        else:
            assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True), i
    assert {k: v for k, v in orig.items() if k != 'cells'} == {k: v for k, v in back.items() if k != 'cells'}
    old_lines, new_lines = text.split(newline), written.split(newline)
    return {
        'file': path.name, 'cells': cells, 'lines_replaced': changed, 'quarantine': str(qcopy),
        'format_roundtrip': True,
        'file_lines_changed': sum(1 for d in difflib.unified_diff(old_lines, new_lines, n=0, lineterm='')
                                  if d[:1] in '+-' and d[:3] not in ('+++', '---')),
        'literal_absent': True, 'other_cells_identical': True,
    }


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    for arg in sys.argv[1:]:
        print(redact(arg))
