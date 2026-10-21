"""Inventory local strategy identities without executing Notebook code.

Run with the project interpreter. Outputs are derived evidence, never registrations.
"""
import argparse
import ast
from collections import Counter
import csv
import copy
from dataclasses import asdict
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.strategy_manager import StrategyManager

SENSITIVE = re.compile(r"api.?key|password|secret|token|os\.environ|https?://", re.I)
EXCLUDED = {".git", ".codex", ".agents", "myenv", ".venv", "venv", "node_modules",
            "__pycache__", ".pytest_cache", "openspec"}
CALLBACKS = {"initialize", "handle_data", "before_trading_start"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_expression(node):
    value = ast.unparse(node)
    if SENSITIVE.search(value):
        return None
    # Credentials must never escape through an unrelated literal assignment.
    if any(isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.fullmatch(r"[A-Za-z0-9_\-]{32,}", n.value) for n in ast.walk(node)):
        return None
    return value


def normalized_cell(source):
    """Remove IPython command lines in memory while preserving source coordinates."""
    return "\n".join("" if line.lstrip().startswith(("%", "!")) else line
                     for line in source.splitlines())


def semantic_fingerprint(definitions, bindings, callbacks, call):
    """Hash reachable trading callbacks, not unrelated plotting/redefined helpers."""
    pending = list(callbacks.values())
    reachable, referenced = {}, set()
    while pending:
        name = pending.pop()
        if name in reachable or name not in definitions:
            continue
        node = copy.deepcopy(definitions[name])
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        refs = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        referenced.update(refs)
        pending.extend(refs - reachable.keys())
        node.name = next((key for key, value in callbacks.items() if value == name), name)
        reachable[name] = ast.dump(node, include_attributes=False)
    roots = {key: reachable.get(value, value) for key, value in callbacks.items()}
    helpers = {name: value for name, value in reachable.items() if name not in callbacks.values()}
    constants = {name: ast.dump(value, include_attributes=False) for name, value in bindings.items()
                 if name in referenced and isinstance(value, (ast.Constant, ast.List, ast.Tuple, ast.Dict))
                 and safe_expression(value)}
    return hashlib.sha256(json.dumps([roots, helpers, constants, call], sort_keys=True).encode()).hexdigest()


def notebook_inventory(path):
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    definitions, bindings, runs, rules, imports, errors = {}, {}, [], [], set(), []
    for cell_no, cell in enumerate(document["cells"]):
        if cell.get("cell_type") != "code":
            continue
        try:
            tree = ast.parse(normalized_cell("".join(cell.get("source", []))))
        except SyntaxError as exc:
            errors.append({"cell": cell_no, "line": exc.lineno, "reason": exc.msg})
            continue
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                definitions[statement.name] = statement
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = statement.value
            for node in ast.walk(statement):
                if isinstance(node, ast.Import):
                    imports.update(a.name.split(".")[0] for a in node.names)
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
                kind = None
                if isinstance(node, ast.Compare):
                    kind = "condition"
                if isinstance(node, ast.Assign) and any(
                    re.search(r"context\.|top|window|lookback|vol|weight|hold|fee|cost|stock|period|capital|fields|columns|assets|pool|universe|rebalance|signals|screen", ast.unparse(t), re.I)
                    for t in node.targets):
                    kind = "parameter_or_state"
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    kind = "field_access"
                if isinstance(node, ast.Attribute) and node.attr == "latest":
                    kind = "field_access"
                if isinstance(node, ast.Call):
                    call = ast.unparse(node.func).split(".")[-1]
                    if call in {"set_commission", "set_slippage", "schedule_function"}:
                        kind = "cost_or_schedule"
                    elif call.startswith("order"):
                        kind = "order"
                    if call == "run_algorithm" or (call == "run" and ast.unparse(node.func) == "algo.run"):
                        choices = {kw.arg: [ast.unparse(kw.value)] for kw in node.keywords
                                   if kw.arg in CALLBACKS and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)}
                        # Resolve callback lists in actual method loops, never execute the loop.
                        ancestor = parents.get(node)
                        while ancestor is not None:
                            if isinstance(ancestor, ast.For):
                                iterable = ancestor.iter
                                if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name) and iterable.func.id == "enumerate":
                                    iterable = iterable.args[0]
                                items = bindings.get(iterable.id) if isinstance(iterable, ast.Name) else iterable
                                target = ancestor.target.elts[-1] if isinstance(ancestor.target, ast.Tuple) else ancestor.target
                                if isinstance(items, (ast.List, ast.Tuple)) and isinstance(target, ast.Name):
                                    for key, values in choices.items():
                                        if values == [target.id]:
                                            choices[key] = [ast.unparse(item) for item in items.elts]
                            ancestor = parents.get(ancestor)
                        expansions = itertools.product(*choices.values()) if choices else [()]
                        for expansion in expansions:
                            callbacks = dict(zip(choices, expansion))
                            fingerprint = semantic_fingerprint(definitions, bindings, callbacks, call)
                            benchmark = any(re.search(r"_(tse|otc|ir0001)$", value) for value in callbacks.values())
                            runs.append({"cell": cell_no, "line": node.lineno, "callbacks": callbacks,
                                "call": ast.unparse(node.func), "fingerprint": fingerprint,
                                "arguments": {kw.arg: safe_expression(bindings.get(kw.value.id, kw.value) if isinstance(kw.value, ast.Name) else kw.value)
                                    for kw in node.keywords if kw.arg in {"start", "end", "start_session", "end_session", "capital_base", "bundle"}},
                                "role": "BENCHMARK" if benchmark else "STRATEGY"})
                if kind and (expression := safe_expression(node)):
                    owner = parents.get(node)
                    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.ClassDef)):
                        owner = parents.get(owner)
                    rules.append({"cell": cell_no, "line": node.lineno, "owner": getattr(owner, "name", "global"),
                                  "kind": kind, "expression": expression})
    return {"source": path.relative_to(ROOT).as_posix(), "sha256": digest(path),
            "runs": runs, "rules": rules, "dependencies": sorted(imports), "parse_errors": errors,
            "defined_callbacks": sorted(name for name in definitions if name.startswith("handle_data"))}


def inventory(output):
    output.mkdir(parents=True, exist_ok=True)
    records, notebook_sources, proxies, scanned, json_references, concrete_classes = [], [], [], [], [], []
    for sid, factory in StrategyManager.CANONICAL_REGISTRY.items():
        meta = asdict(StrategyManager.STRATEGY_METADATA[sid])
        source = Path(*factory.rsplit(".", 1)[0].split(".")).with_suffix(".py")
        records.append({"id": sid, "kind": "REGISTERED", "display_name": meta["display_name_zh"],
            "sources": [{"path": source.as_posix(), "sha256": digest(ROOT / source)}],
            "aliases": list(meta["legacy_ids"]), "original_notebook": meta["source_notebook"],
            "asset_class": "EQUITY", "implementation": factory, "counts_as_strategy": True,
            "source_rules": source.as_posix(), "parameters": "BaseStrategy/runner/settings overrides; see source",
            "daily": "REGISTERED_NOT_YET_VALIDATED", "production": "NOT_VERIFIED",
            "tests": ["test/test_tquant_strategies.py" if meta["source"] == "tquant" else "test/characterization/test_strategy_registry_regression.py"]})
    for key, target in StrategyManager.LEGACY_STRATEGY_REGISTRY.items():
        proxies.append({"alias": key, "target": target, "reason": "import-only proxy; no independent class"})
    grid_records = {}
    for directory, dirs, filenames in os.walk(ROOT):
        relative_directory = Path(directory).relative_to(ROOT).as_posix()
        data_tree = relative_directory.startswith(("outputs/", "data/", "artifacts/"))
        # Market-response caches contain observations, not strategy definitions.
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED
                         and not (data_tree and d.endswith("_cache")))
        for filename in sorted(filenames):
            path = Path(directory) / filename
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix in {".py", ".json", ".ipynb"}:
                scanned.append(rel)
            if path.suffix == ".ipynb":
                notebook_sources.append(notebook_inventory(path))
            elif path.suffix == ".py" and rel.startswith("core/strategies/"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in tree.body:
                    if isinstance(node, ast.ClassDef) and node.name not in {"BaseStrategy", "TQuantStrategy"}:
                        concrete_classes.append({"path": rel, "class": node.name})
            elif filename in {"strategy_grid.csv", "strategy_search_manifest.csv", "parameter_grid.csv"} and rel.startswith("outputs/"):
                namespace = path.relative_to(ROOT).parts[1]
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    for line, row in enumerate(csv.DictReader(handle), 2):
                        sid = row.get("strategy_id", row.get("config_id"))
                        if not sid:
                            continue
                        # A replay in another output directory is the same research family.
                        if "combination_method" in row and "config_id" in row:
                            namespace = "portfolio_research"
                        elif "factor_set_id" in row:
                            namespace = "fundamental_strategy_validation"
                        elif "composite_id" in row:
                            namespace = "strategy_research"
                        key = namespace + ":" + sid
                        parameters = {k: v for k, v in row.items() if k not in {"strategy_id", "config_id", "config_hash"}}
                        signature = row.get("config_hash") or hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
                        if key in grid_records and signature != grid_records[key]["configuration_fingerprint"]:
                            key += "@" + signature
                        record = grid_records.setdefault(key, {"id": key, "strategy_id": sid,
                            "display_name": sid, "kind": "RESEARCH_SETTING", "namespace": namespace,
                            "asset_class": "EQUITY_PORTFOLIO", "sources": [], "config_hashes": [],
                            "parameters": parameters, "configuration_fingerprint": signature,
                            "counts_as_strategy": True, "daily": "RESEARCH_ONLY", "production": "RESEARCH_ONLY"})
                        record["sources"].append({"path": rel, "line": line, "sha256": digest(path)})
                        if row.get("config_hash") and row["config_hash"] not in record["config_hashes"]:
                            record["config_hashes"].append(row["config_hash"])
            elif path.suffix == ".json" and not rel.startswith(("data/raw/", "test/", "outputs/strategy_validation/")) and path.stat().st_size < 2_000_000:
                # Read all small configuration/artifact JSON, export identity references only.
                try:
                    raw = path.read_bytes()
                    if b'"strategy_id"' not in raw and b'"config_id"' not in raw:
                        continue
                    payload = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, UnicodeError):
                    continue
                def visit(obj):
                    if isinstance(obj, dict):
                        for field in ("strategy_id", "config_id"):
                            if isinstance(obj.get(field), str):
                                ref = {"path": rel, "id": obj[field]}
                                if isinstance(obj.get("strategy_fingerprint"), str):
                                    ref["strategy_fingerprint"] = obj["strategy_fingerprint"]
                                json_references.append(ref)
                        for k, v in obj.items():
                            if not SENSITIVE.search(k):
                                visit(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            visit(v)
                visit(payload)
    records.extend(grid_records.values())
    fingerprints = {}
    for source in notebook_sources:
        stem = Path(source["source"]).stem.strip()
        asset = "FUTURE" if re.search(r"Future|期貨", stem) else "ETF_PORTFOLIO" if re.search(r"ETF|週期投資", stem) else "EQUITY"
        if not source["runs"]:
            records.append({"id": "notebook:" + stem, "kind": "ANALYSIS_ONLY", "display_name": stem,
                "asset_class": asset, "sources": [{"path": source["source"], "sha256": source["sha256"]}],
                "counts_as_strategy": False, "source_rules": source["source"], "daily": "RESEARCH_ONLY", "production": "RESEARCH_ONLY"})
        for run in source["runs"]:
            key = "notebook:" + stem + f":cell{run['cell']}:" + (run["callbacks"].get("handle_data") or run["callbacks"].get("initialize") or run["call"])
            if run["fingerprint"] in fingerprints:
                existing = fingerprints[run["fingerprint"]]
                existing["sources"].append({"path": source["source"], "sha256": source["sha256"], "cell": run["cell"]})
                source.setdefault("equivalent_runs", []).append({"cell": run["cell"], "equivalent_to": existing["id"],
                    "evidence": "identical reachable trading AST, literal inputs and execution entrypoint; function-name/documentation differences ignored"})
                continue
            record = {"id": key, "display_name": stem, "kind": run["role"], "asset_class": asset,
                "sources": [{"path": source["source"], "sha256": source["sha256"], "cell": run["cell"]}],
                "source_rules": source["source"], "callbacks": run["callbacks"], "fingerprint": run["fingerprint"],
                "counts_as_strategy": run["role"] == "STRATEGY", "dependencies": source["dependencies"],
                "parameters": "source_rules parameter_or_state; inherited defaults NOT_SPECIFIED_IN_SOURCE",
                "daily": "RESEARCH_ONLY", "production": "RESEARCH_ONLY"}
            records.append(record)
            fingerprints[run["fingerprint"]] = record
    runtime = StrategyManager.FUNDAMENTAL_RUNTIME_STRATEGY_ID
    records.append({"id": runtime, "display_name": "Fundamental Frozen", "kind": "FROZEN_RUNTIME",
        "asset_class": "EQUITY_PORTFOLIO", "sources": [{"path": "core/runtime/fundamental_shadow.py", "sha256": digest(ROOT / "core/runtime/fundamental_shadow.py")}],
        "parameters": {"top_n": 5, "rebalance_days": 60, "portfolio_weighting": "score_weighted"},
        "counts_as_strategy": True, "daily": "INDEPENDENT_GATES", "production": "INDEPENDENT_GATES",
        "research_candidate": "G2_PLUS_G3__EQUAL__TOP5__REB60__SCORE_WEIGHTED",
        "relationship": "deployment variant with frozen fingerprint and independent PIT/OOS gates; not a legacy alias"})
    for function in ("calculate_v30_signal", "calculate_pivot_strategy"):
        records.append({"id": "signal:" + function, "display_name": function, "kind": "SIGNAL_ONLY",
            "asset_class": "EQUITY", "sources": [{"path": "core/strategy.py", "sha256": digest(ROOT / "core/strategy.py")}],
            "counts_as_strategy": False, "daily": "NO_REGISTERED_EXECUTOR", "production": "RESEARCH_ONLY",
            "reason": "signal/risk hints without standalone portfolio and execution contract"})
    for record in records:
        record["selection_conditions"] = record.get("parameters", "See source_rules and effective callback coordinates")
        record["holding_count"] = record.get("parameters", {}).get("top_n", "NOT_SPECIFIED; source/engine policy required") if isinstance(record.get("parameters"), dict) else "See source_rules/runner MAX_HOLDINGS"
        record["rebalance_frequency"] = record.get("parameters", {}).get("rebalance_days", "See cost_or_schedule and callbacks") if isinstance(record.get("parameters"), dict) else "See cost_or_schedule and callbacks"
        record["required_data"] = "PIT OHLCV/universe; source-declared features and publication versions where used"
        if record.get("source_rules") in {n["source"] for n in notebook_sources}:
            original = next(n for n in notebook_sources if n["source"] == record["source_rules"])
            record["required_data"] = {"field_accesses": sorted({r["expression"] for r in original["rules"] if r["kind"] == "field_access"}),
                "availability": "original schema/publication versions required; dynamic dataframe fields may require original dependencies"}
        record["validation_status"] = "NOT_TESTED"
        record.setdefault("tests", ["test/test_strategy_inventory_audit.py"])
        record["blockers"] = ["VALIDATION_PENDING"]
    known = {r.get("strategy_id", r["id"]) for r in records}
    known.update(a for r in records for a in r.get("aliases", []))
    unresolved_json = [r for r in json_references if r["id"] not in known]
    registered_classes = {factory.rsplit(".", 1)[-1] for factory in StrategyManager.CANONICAL_REGISTRY.values()}
    unregistered_classes = [c for c in concrete_classes if c["class"] not in registered_classes]
    # Source-level records retain unused methods, benchmarks, parse errors, and analysis.
    result = {"schema_version": 1, "records": records, "notebooks": notebook_sources,
        "proxies": proxies, "json_identity_references": json_references, "unresolved_json_identities": unresolved_json,
        "concrete_strategy_classes": concrete_classes, "unregistered_concrete_classes": unregistered_classes,
        "scan": {"file_count": len(scanned), "paths_sha256": hashlib.sha256("\n".join(scanned).encode()).hexdigest(),
                 "exclusions": sorted(EXCLUDED) + ["*_cache under data/artifacts/outputs: raw market responses"]},
        "counts": {"strategies_and_execution_variants": sum(r["counts_as_strategy"] for r in records),
            "records": len(records), "by_kind": dict(Counter(r["kind"] for r in records)),
            "notebook_sources": len(notebook_sources), "ported_sources": sum(bool(r.get("original_notebook")) for r in records),
            "aliases": sum(len(r.get("aliases", [])) for r in records),
            "parse_errors": sum(len(s["parse_errors"]) for s in notebook_sources)}}
    assert len({r["id"] for r in records}) == len(records), "duplicate inventory identities"
    (output / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def write_documents(result, output):
    """Render every identity, including excluded benchmarks and signal-only sources."""
    counts = result["counts"]
    lines = ["# 2026-10-21 全策略盤點", "",
        f"Phase 0：{counts['strategies_and_execution_variants']} 個策略／執行變體，{counts['records']} 筆完整來源記錄。",
        "這是可獨立驗證的身分數，不宣稱是同樣數量的獨立投資理念。原始 Notebook 與採不同持股／成交規則的 port 為不同 execution variants，保留來源連結；純 alias 不另計。", "",
        "| 類別 | 數量 |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in counts["by_kind"].items())
    lines += ["", f"Notebook：{counts['notebook_sources']}；來源 port：{counts['ported_sources']}；metadata aliases：{counts['aliases']}；解析錯誤：{counts['parse_errors']}。",
        "5 筆已執行 benchmark、1 筆純分析與 2 筆 signal-only 留在清冊但不增加策略數。未執行的 callback（例如週期投資 handle_data_6 買入持有對照）保留在 Notebook 定義及原始規則索引，不當成本輪成功執行。",
        "同名策略的歷史 frozen fingerprint 保存在 JSON identity references；重跑／repro 不新增策略。Frozen runtime 是研究候選的部署變體，維持獨立 gate，不是 V alias。", "",
        "## 去重與完整性", "",
        "依 reachable trading AST、literal inputs、callback 與 execution entrypoint 比較；不同 dataset／輸出不是假冒新的演算法。相同名稱不足以合併，不同交易契約也不合併。"]
    for source in result["notebooks"]:
        for match in source.get("equivalent_runs", []):
            lines.append(f"- `{source['source']}` cell {match['cell']} → `{match['equivalent_to']}`：{match['evidence']}。")
    lines += ["", f"未解 JSON 身分：{len(result['unresolved_json_identities'])}；未註冊 concrete strategy class：{len(result['unregistered_concrete_classes'])}。",
        "所有 concrete class、7 個 V 匯入代理、JSON 身分引用與掃描排除規則均在機器可讀清冊。V30 的 signal 函式並非額外 canonical，LINE 的切換 alias 仍沿用既有路徑。",
        "排除環境、第三方套件、測試 fixtures／cache、OpenSpec 歷史；data/artifacts/outputs 下的 *_cache 為行情／財報 API 回應，不作策略定義，父目錄 manifest 仍掃描。Strategy_example 全部子目錄皆納入。", "",
        "## 全量身分矩陣", "",
        "原始訊號、選股條件、持股、頻率與成本的精確 AST／cell 座標見 [Notebook 邏輯索引](strategy_notebook_logic.md)；研究設定的 TopN／rebalance／weighting 在逐筆 JSON parameters。未定義值保留原因，不猜測。",
        "Canonical 的 portfolio／exit 規則由 BaseStrategy、runner 與既有 overrides 決定；目前 daily/production 是接線資訊，不等於驗證 PASS。", "",
        "| ID／顯示名稱 | Source／原始對應 | Asset | 持股／頻率 | 實作與資格 |", "|---|---|---|---|---|"]
    for record in result["records"]:
        sources = "; ".join(s["path"] + (f"#cell{s['cell']}" if "cell" in s else "") for s in record["sources"][:2])
        original = record.get("original_notebook")
        if original:
            sources += "; original=" + original
        lines.append(f"| `{record['id']}` {record['display_name']} | {sources} | {record['asset_class']} | {record.get('holding_count')}／{record.get('rebalance_frequency')} | {record['kind']}；Daily={record['daily']}；Production={record['production']}；本輪 NOT_TESTED |")
    lines += ["", "## 執行證據與復現", "", f"機器可讀清冊：`{output.as_posix()}/inventory.json`。",
        "每筆包含 sources/hash、parameters、callbacks、selection_conditions、holding_count、rebalance_frequency、required_data、tests、blockers、validation_status。Notebook source_rules 保存條件、參數／狀態、排程／成本、order 表達式及 owner／cell／line。未知繼承參數明示 NOT_SPECIFIED，不以 0 補值。",
        "資料與依賴資格由後續 validation.json／strategy_validation_report.md 更新；舊 Notebook 輸出與舊 scoreboard 不作本輪 PASS。",
        "復現：`myenv/Scripts/python.exe scripts/audit_strategy_inventory.py --output outputs/strategy_validation/2026-10-21`。",
        "新策略預設研究；本次盤點不註冊、不改 active/persistence、不改歷史資料。docs/openspec/outputs 依現有 ignore 政策保留本機。"]
    (ROOT / "docs/strategy_full_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["# 2026-10-21 Notebook 原始邏輯索引", "", "只解析來源，不執行 cell、不複製輸出／憑證。座標為 zero-based cell 與該 cell 的 one-based line。", ""]
    for source in result["notebooks"]:
        lines += [f"## {Path(source['source']).name}", "", f"Source: `{source['source']}`；SHA-256: `{source['sha256']}`。",
            "Dependencies: " + ", ".join(source["dependencies"]),
            "Execution callbacks: " + json.dumps(source["runs"], ensure_ascii=False),
            "Defined handle_data callbacks: " + ", ".join(source["defined_callbacks"]), ""]
        for rule in source["rules"]:
            lines += [f"cell {rule['cell']} L{rule['line']} `{rule['owner']}` / {rule['kind']}",
                      "```python", rule["expression"], "```", ""]
    (ROOT / "docs/strategy_notebook_logic.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.output)
    write_documents(result, args.output)
    print(json.dumps(result["counts"], ensure_ascii=False))
