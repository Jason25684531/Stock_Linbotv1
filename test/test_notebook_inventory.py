import csv
import json

from scripts import notebook_inventory as ni
from scripts.redact_notebook_key import ENV_LINE, redact

KEY = 'AbCdEfGhIjKlMnOpQrStUvWxYz0123'  # synthetic 30-char literal


def _nb(path, cells):
    nb = {'cells': [{'cell_type': t, 'metadata': {}, 'source': src, **({'outputs': [], 'execution_count': None} if t == 'code' else {})}
                    for t, src in cells],
          'metadata': {'kernelspec': {'name': 'python3'}}, 'nbformat': 4, 'nbformat_minor': 5}
    path.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return path


def _setup(tmp_path):
    d = tmp_path / 'nb'
    d.mkdir()
    _nb(d / 'TQ_MACD交易策略.ipynb', [('code', ["import os\n", "os.environ['TEJAPI_KEY'] = 'your key'\n"])])
    _nb(d / 'TQ_ETF資產配置模型.ipynb', [('code', ["pool = get_universe(start, end, stktp_e=['ETF'])\n"])])
    _nb(d / 'TQ_期貨策略範例.ipynb', [('markdown', ['# futures']),
                                 ('code', ["import os\n", f"os.environ['TEJAPI_KEY'] = '{KEY}'\n", "x = 1\n"]),
                                 ('code', ['print(x)\n'])])
    _nb(d / 'TQ_unknown.ipynb', [('code', ["tejapi.ApiConfig.api_key = os.getenv('TEJAPI_KEY')\n"])])
    return d


def test_inventory_rows_conclusions_and_no_leak(tmp_path):
    d = _setup(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    out = tmp_path / 'out'
    rows = ni.generate(d, out, tmp_path / 'q')
    assert len(rows) == len(list(d.glob('*.ipynb')))
    assert all(r['conclusion'] in ni.CONCLUSIONS for r in rows)
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before  # notebooks untouched
    for f in out.iterdir():
        assert KEY not in f.read_text(encoding='utf-8')
    with open(out / 'notebook_inventory.csv', encoding='utf-8') as fh:
        by = {r['name']: r for r in csv.DictReader(fh)}
    assert by['TQ_ETF資產配置模型.ipynb']['conclusion'] == 'ETF_SEPARATE'
    assert by['TQ_期貨策略範例.ipynb']['conclusion'] == 'FUTURES_SEPARATE'
    assert by['TQ_期貨策略範例.ipynb']['credential_finding'].startswith('LITERAL_REDACTED: cell 1')
    assert by['TQ_MACD交易策略.ipynb']['credential_finding'] == 'PLACEHOLDER'
    assert by['TQ_unknown.ipynb']['credential_finding'] == 'ENV_READ'
    assert by['TQ_MACD交易策略.ipynb']['in_user_list'] == 'YES'
    md = (out / 'notebook_inventory.md').read_text(encoding='utf-8')
    assert 'MISSING: TQ_iValue' in md
    assert 'TQ_期貨策略範例.ipynb' in (out / 'credential_actions.md').read_text(encoding='utf-8')


def test_safe_write_refuses_literal(tmp_path):
    try:
        ni.safe_write(tmp_path / 'x.md', f'leak {KEY}', {KEY})
    except AssertionError:
        assert not (tmp_path / 'x.md').exists()
    else:
        raise AssertionError('literal was written')


def test_redact_quarantines_and_rewrites_only_key_line(tmp_path):
    d = _setup(tmp_path)
    p = d / 'TQ_期貨策略範例.ipynb'
    raw = p.read_bytes()
    orig = json.loads(raw)
    q = tmp_path / 'q'
    res = redact(p, q)
    assert KEY not in str(res)
    assert (q / (p.name + '.orig')).read_bytes() == raw
    new = json.loads(p.read_bytes())
    assert KEY not in p.read_text(encoding='utf-8')
    assert new['cells'][1]['source'] == ["import os\n", ENV_LINE + "\n", "x = 1\n"]
    for i in (0, 2):
        assert json.dumps(new['cells'][i], sort_keys=True) == json.dumps(orig['cells'][i], sort_keys=True)
    assert res['format_roundtrip'] and res['file_lines_changed'] == 2  # one line out, one line in

    # inventory after redaction: credential still listed for the Owner, literal still absent
    out = tmp_path / 'out'
    rows = ni.generate(d, out, q)
    row = next(r for r in rows if r['name'] == p.name)
    assert row['sha256_original'] != row['sha256']
    ca = (out / 'credential_actions.md').read_text(encoding='utf-8')
    assert 'REDACTED_IN_PLACE' in ca and KEY not in ca


def test_redact_refuses_mismatched_quarantine(tmp_path):
    d = _setup(tmp_path)
    p = d / 'TQ_期貨策略範例.ipynb'
    q = tmp_path / 'q'
    q.mkdir()
    (q / (p.name + '.orig')).write_bytes(b'other')
    try:
        redact(p, q)
    except RuntimeError:
        assert KEY in p.read_text(encoding='utf-8')  # untouched
    else:
        raise AssertionError('should refuse')
