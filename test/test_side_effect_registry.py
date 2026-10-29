"""Production gates plus actual Research senders across core/jobs/scripts/services. No known-unwired exemption for public downloads."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDE_EFFECT = re.compile(r'INSERT INTO|REPLACE INTO|DELETE FROM|UPDATE \w+ SET|\.to_sql\(|requests\.(get|post)|urlopen\(|\bsession\.(get|post)\b|line_bot_api\.|\.broadcast\(|push_message|httpx\.|aiohttp|urllib\.request|public_data\.(request|restricted_http|restricted_sdk|internal_service)')
WIRED = re.compile(r'core\.runtime\.operation_policy import|from core\.runtime import .*public_data')
# Known gaps: external fetchers and read-mostly integrations that still rely on the guard, RequestGate and request ledgers only.
# Wiring one means removing it from this set; the test also fails when a listed file becomes wired but is still listed.
KNOWN_UNWIRED = {}


def side_effect_files():
    for folder in ('core', 'jobs'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            text = path.read_text(encoding='utf-8', errors='ignore')
            if SIDE_EFFECT.search(text):
                relative = path.relative_to(ROOT).as_posix()
                # Local SQLite allowance/audit state is not a Production MySQL writer. Raw HTTP is checked separately below.
                yield relative, bool(WIRED.search(text)) or relative == 'core/runtime/research_budget.py'


def test_every_side_effect_file_is_wired_or_a_known_gap():
    found = dict(side_effect_files())
    unregistered = sorted(p for p, wired in found.items() if not wired and p not in KNOWN_UNWIRED)
    assert not unregistered, f'side-effect code without a policy gate (wire it to operation_policy or list it as a known gap): {unregistered}'
    stale = sorted(p for p in KNOWN_UNWIRED if found.get(p) is not False)
    assert not stale, f'listed as unwired but wired or gone: {stale}'


def test_the_wired_core_paths_are_really_gated():
    found = dict(side_effect_files())
    for path in ('core/db_helper.py', 'core/research/sources/twse.py', 'jobs/push_to_line.py', 'jobs/run_daily.py', 'core/update_monthly_revenue.py', 'core/richmenu.py'):
        assert found[path] is True, path


def test_no_new_raw_network_sender_can_escape_the_shared_boundary():
    violations = []
    for folder in ('core', 'jobs', 'scripts', 'services'):
        for path in (ROOT / folder).rglob('*.py'):
            source = path.read_text(encoding='utf-8-sig')
            tree = ast.parse(source)
            relative = path.relative_to(ROOT).as_posix()
            for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                body = ast.get_source_segment(source, function) or ''
                for node in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                    call = ast.unparse(node.func)
                    raw = call in ('requests.get', 'requests.post', 'urlopen', 'urllib.request.urlopen', 'self.session.get', 'self.session.post', 'client.get', 'client.post', 'client.request')
                    sdk = call.endswith('.generate_content') or ('yf.Ticker(' in call and call.endswith('.history')) or (call == 'data.get' and 'from finlab import data' in source) or call in ('yf.download', 'yfinance.download', 'self._load().download')
                    if not raw and not sdk:
                        continue
                    allowed = (relative == 'core/runtime/public_data.py' and function.name == '_send')
                    allowed |= (relative == 'core/mcp_client.py' and function.name == '_post_json' and 'public_data.internal_service(self.base_url, endpoint)' in body)
                    allowed |= (relative == 'core/richmenu.py' and function.name == 'upload_rich_menu_image'
                                and 'gate(Operation.PUBLISH_EXTERNAL,' in body and body.index('gate(Operation.PUBLISH_EXTERNAL,') < body.index('requests.post('))
                    allowed |= (sdk and 'public_data.restricted_sdk(' in body and node.lineno > min((n.lineno for n in ast.walk(function) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'public_data.restricted_sdk'), default=node.lineno))
                    allowed |= (relative == 'jobs/backfill_pipeline.py' and call == 'yf.download'
                                and 'public_data.restricted_sdk(\'yahoo\')' in source)  # the actual lazy proxy download is checked above
                    # Fixed local metadata diagnostic, not an external market-data route.
                    allowed |= (relative == 'scripts/validate_strategy_inventory.py' and raw and node.args
                                and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'http://localhost:1688/api/strategies')
                    if not allowed:
                        violations.append(f'{relative}:{node.lineno}:{call}')
    assert not violations, 'uncontrolled actual senders: ' + ', '.join(sorted(set(violations)))
