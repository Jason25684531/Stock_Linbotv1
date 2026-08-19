from importlib import import_module


def test_canonical_mcp_public_contract():
    module = import_module('services.mcp.server')

    assert callable(module.main)
    assert module.app is not None


def test_compatibility_launcher_forwards_to_canonical_module():
    canonical = import_module('services.mcp.server')
    launcher = import_module('scripts.twse_mcp_server')

    assert launcher is canonical
