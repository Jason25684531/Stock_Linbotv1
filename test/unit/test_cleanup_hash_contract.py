"""Exercise actual small hash bodies without running research entrypoints."""

import ast
import hashlib
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
HELPERS = [
    ("jobs/run_factor_validation.py", "_sha256"),
    ("core/research/fundamental_factor_validation.py", "_sha256"),
    ("core/runtime/fresh_oos_promotion.py", "_sha256"),
    ("core/runtime/fundamental_advancement.py", "_sha256"),
    ("core/research/universe_recovery.py", "_file_sha256"),
    ("core/research/fundamentals/pit.py", "_file_sha256"),
]


@pytest.mark.parametrize("path,name", HELPERS)
def test_hash_bytes_errors_and_resource_closure(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"hashlib": hashlib, "Path": Path}
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    fn = namespace[name]

    class Probe:
        def __init__(self, data=None):
            self.handle = io.BytesIO(data) if data is not None else None

        def open(self, mode):
            assert mode == "rb"
            if self.handle is None:
                raise PermissionError("unreadable")
            return self.handle

    for data in (b"", bytes(range(256)), b"binary\x00" * 400000):
        probe = Probe(data)
        assert fn(probe) == hashlib.sha256(data).hexdigest()
        assert probe.handle.closed
    if name == "_file_sha256":
        assert fn(Probe()) is None
        assert fn(ROOT / "nonexistent-cleanup-hash-file") is None
    else:
        with pytest.raises(PermissionError):
            fn(Probe())
        with pytest.raises(FileNotFoundError):
            fn(ROOT / "nonexistent-cleanup-hash-file")
