import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    ('app/platform_routes.py', 'scripts.combination_lab_run'),
    ('app/platform_routes.py', 'scripts.historical_foundation'),
    ('app/platform_routes.py', 'scripts.research_workflow'),
    ('core/research/combination_lab.py', 'scripts.research_workflow'),
    ('core/research/condition_pool.py', 'scripts.audit_strategy_history'),
    ('core/research/condition_pool.py', 'scripts.official_data_recovery'),
    ('core/research/condition_pool.py', 'scripts.prepare_h1_signals'),
    ('core/research/condition_pool.py', 'scripts.prepare_h4_signals'),
    ('core/research/h4_signal.py', 'scripts.prepare_h1_signals'),
    ('core/research/h4_signal.py', 'scripts.prepare_h4_signals'),
    ('core/research/h4_signal.py', 'scripts.prepare_research_mode'),
    ('core/research/oos_gate.py', 'scripts.research_workflow'),
    ('core/research/reference/catalog.py', 'scripts.legacy_baseline_run'),
    ('core/research/reference/runner.py', 'scripts.legacy_baseline_run'),
    ('core/research/reference/runner.py', 'scripts.reference_engine'),
    ('core/research/reference/runner.py', 'scripts.research_workflow'),
    ('core/research/repair_candidates.py', 'scripts.research_workflow'),
    ('core/research/repair_runner.py', 'scripts.prepare_d5_signals'),
    ('core/research/repair_runner.py', 'scripts.prepare_day3_signals'),
    ('core/research/repair_runner.py', 'scripts.prepare_h1_signals'),
    ('core/research/repair_runner.py', 'scripts.prepare_h4_signals'),
    ('core/research/repair_runner.py', 'scripts.prepare_research_inputs'),
    ('core/research/repair_runner.py', 'scripts.prepare_research_mode'),
    ('core/research/repair_runner.py', 'scripts.research_h4_hist_run'),
    ('core/research/repair_runner.py', 'scripts.research_workflow'),
    ('core/research/research_screen.py', 'scripts.research_workflow'),
    ('core/research/research_screen.py', 'scripts.track_b_stats'),
    ('core/research/trading_calendar.py', 'scripts.research_workflow'),
    ('core/runtime/fundamental_shadow.py', 'jobs.run_fundamental_strategy_validation'),
    ('core/runtime/ingestion_authority.py', 'scripts.task_controller'),
    ('core/runtime/operation_policy.py', 'scripts.task_controller'),
    ('core/runtime/public_data.py', 'scripts.task_controller'),
    ('core/strategy/composite.py', 'scripts.research_workflow'),
    ('services/market_data_update.py', 'jobs.update_database'),
    ('services/market_data_update.py', 'scripts.research_workflow'),
    ('services/market_data_update.py', 'scripts.task_controller'),
}
DYNAMIC_IMPORTS = {
    ('core/diagnostics/service.py', 'importlib.import_module(module_name)'),
    ('core/research/condition_pool.py', "importlib.import_module('scripts.' + 'offic' + 'ial_data_recovery')"),
    ('core/research/oos_gate.py', "import_module('core.research.repair_runner')"),
    ('core/strategy/adapters/legacy.py', 'importlib.import_module(module_name)'),
    ('core/strategy_manager.py', 'importlib.import_module(module_name)'),
}


def test_layer_import_ratchet():
    edges, dynamic = set(), set()
    for folder in ('core', 'services', 'app'):
        for path in (ROOT / folder).rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
                if isinstance(node, ast.Import):
                    edges.update((rel, alias.name) for alias in node.names if alias.name.split('.')[0] in ('scripts', 'jobs'))
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    if node.module in ('scripts', 'jobs'):
                        edges.update((rel, f'{node.module}.{alias.name}') for alias in node.names)
                    elif node.module.split('.')[0] in ('scripts', 'jobs'):
                        edges.add((rel, node.module))
                if folder == 'core' and isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == 'import_module'
                    or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'
                ):
                    call = ast.unparse(node)
                    dynamic.add((rel, call))
                    if (rel, call) == ('core/research/condition_pool.py', "importlib.import_module('scripts.' + 'offic' + 'ial_data_recovery')"):
                        edges.add((rel, 'scripts.official_data_recovery'))
    assert edges == ALLOWED, f'新增倒置依賴：{sorted(edges - ALLOWED)}；請移除過期項目：{sorted(ALLOWED - edges)}'
    assert dynamic == DYNAMIC_IMPORTS, f'新增動態匯入：{sorted(dynamic - DYNAMIC_IMPORTS)}；請移除過期項目：{sorted(DYNAMIC_IMPORTS - dynamic)}'
