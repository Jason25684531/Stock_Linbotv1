"""Source discovery must preserve variants without executing Notebook code."""
import ast
import json

from scripts import audit_strategy_inventory as audit


def notebook(tmp_path, monkeypatch, cells):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    path = tmp_path / "example.ipynb"
    path.write_text(json.dumps({"cells": [{"cell_type": "code", "source": s} for s in cells]}), encoding="utf-8")
    return path


def test_magic_lines_preserve_locations_and_original_bytes(tmp_path, monkeypatch):
    path = notebook(tmp_path, monkeypatch, ["%matplotlib inline\n!pip install never-executed\ndef initialize(c):\n    pass\nrun_algorithm(initialize=initialize)\n"])
    before = path.read_bytes()
    result = audit.notebook_inventory(path)
    assert result["parse_errors"] == []
    assert result["runs"][0]["line"] == 5
    assert path.read_bytes() == before


def test_actual_callback_method_loops_are_expanded(tmp_path, monkeypatch):
    path = notebook(tmp_path, monkeypatch, ["""
def first(c, d):
    order_target_percent('2330', 1)
def second(c, d):
    order_target_percent('2330', .5)
methods = [first, second]
for i, method in enumerate(methods):
    run_algorithm(handle_data=method)
"""])
    runs = audit.notebook_inventory(path)["runs"]
    assert [r["callbacks"]["handle_data"] for r in runs] == ["first", "second"]
    assert len({r["fingerprint"] for r in runs}) == len(runs)


def test_redefined_callbacks_retain_execution_versions(tmp_path, monkeypatch):
    path = notebook(tmp_path, monkeypatch, ["def initialize(c):\n c.weight=1\nrun_algorithm(initialize=initialize)",
        "def initialize(c):\n c.weight=.5\nrun_algorithm(initialize=initialize)"])
    runs = audit.notebook_inventory(path)["runs"]
    assert runs[0]["fingerprint"] != runs[1]["fingerprint"]


def test_renamed_identical_initialize_is_not_another_strategy(tmp_path, monkeypatch):
    path = notebook(tmp_path, monkeypatch, ["def initialize(c):\n c.weight=1\nrun_algorithm(initialize=initialize)",
        "def initialize_2(c):\n c.weight=1\nrun_algorithm(initialize=initialize_2)"])
    runs = audit.notebook_inventory(path)["runs"]
    assert runs[0]["fingerprint"] == runs[1]["fingerprint"]


def test_benchmark_and_invalid_cells_are_explicit(tmp_path, monkeypatch):
    path = notebook(tmp_path, monkeypatch, ["run_algorithm(handle_data=handle_data_tse)", "def broken("])
    result = audit.notebook_inventory(path)
    assert result["runs"][0]["role"] == "BENCHMARK"
    assert result["parse_errors"][0]["cell"] == 1


def test_sensitive_assignments_are_never_exported():
    assert audit.safe_expression(ast.parse("context.api_key = credential").body[0]) is None
    assert audit.safe_expression(ast.parse("order_target_percent(stock, 0.5)").body[0]) is not None
