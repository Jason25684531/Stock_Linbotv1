"""An import failure is not an identity or safety rejection."""
import ast
import importlib
from pathlib import Path
import sys


def test_missing_dependency_is_safe_and_successful_retry_clears_it(manager, monkeypatch):
    real_import = importlib.import_module

    def broken(name, *args, **kwargs):
        if name == 'core.strategies.regression_momentum':
            raise ModuleNotFoundError('sensitive connection details must not reach the catalog')
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(importlib, 'import_module', broken)
        assert manager.get_strategy('regression_momentum') is None
        error = manager.get_strategy_load_error('regression_momentum')
        assert error == {'code': 'DEPENDENCY_BLOCKED', 'error_type': 'ModuleNotFoundError'}
        assert 'sensitive' not in str(error)
    assert manager.get_strategy('regression_momentum') is not None
    assert manager.get_strategy_load_error('regression_momentum') == {}


def test_catalog_does_not_offer_backtest_when_executor_load_fails(manager, monkeypatch):
    import app as app_module
    from app import web_server
    original = manager.get_strategy
    monkeypatch.setattr(manager, 'get_strategy', lambda sid: None if sid == 'bias_reversion' else original(sid))
    manager._strategy_load_errors['bias_reversion'] = {'code': 'EXECUTOR_LOAD_FAILED', 'error_type': 'TypeError'}
    monkeypatch.setattr(app_module, 'StrategyManager', lambda: manager)
    item = next(r for r in web_server._build_strategy_catalog() if r['strategy_id'] == 'bias_reversion')
    assert not item['available_for_daily'] and not item['available_for_backtest']
    assert item['daily_reason_code'] == 'EXECUTOR_LOAD_FAILED'
    assert 'legacy identity' not in item['daily_reason']


def test_daily_listing_mismatch_is_distinct_from_loading_failure(manager, monkeypatch):
    import app as app_module
    from app import web_server
    monkeypatch.setattr(manager, 'list_strategies', lambda: [])
    monkeypatch.setattr(app_module, 'StrategyManager', lambda: manager)
    item = next(r for r in web_server._build_strategy_catalog() if r['strategy_id'] == 'volume_surge')
    assert not item['available_for_daily']
    assert item['daily_reason_code'] == 'DAILY_IDENTITY_UNSUPPORTED'
    assert manager.get_strategy_load_error('not_registered') == {'code': 'UNKNOWN_STRATEGY'}


def test_direct_strategy_imports_belong_to_runtime_manifest():
    root = Path(__file__).parents[1]
    declared = {line.split('==')[0].lower() for line in (root / 'requirements.runtime.txt').read_text().splitlines()
                if line and not line.startswith('#')}
    imported = set()
    for source in (root / 'core/strategies').glob('*.py'):
        for node in ast.parse(source.read_text(encoding='utf-8-sig')).body:
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imported.add(node.module.split('.')[0])
    external = imported - sys.stdlib_module_names - {'core', 'config'}
    assert external <= declared
