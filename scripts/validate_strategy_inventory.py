"""Run isolated diagnostics and retain every inventory verdict, including blockers."""
import argparse
from collections import Counter
import contextlib
from dataclasses import asdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_inventory import digest


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")


def axis(status, reason=None, *, applicable=True, evidence=None):
    return {"status": status, "reason": reason, "applicable": applicable, "evidence": evidence}


def compare_parity(reference, port):
    """Compare independently exported ordered rows, never compute expected via the port.

    Export rows as {identity: [...], values: [...]}. Put dates, IDs, ranks,
    direction, sequence and quantities in identity; scores/prices/weights in values.
    """
    import numpy as np
    context = ('snapshot_sha256', 'calendar_sha256', 'as_of', 'universe_sha256')
    if not all(reference.get(k) and reference[k] == port.get(k) for k in context):
        return {name: axis('BLOCKED', 'IDENTICAL_INPUT_PROVENANCE_REQUIRED') for name in ('signals', 'holdings', 'execution')}
    if reference.get('producer_sha256') == port.get('producer_sha256') or not reference.get('producer_sha256') or not port.get('producer_sha256'):
        return {name: axis('BLOCKED', 'INDEPENDENT_REFERENCE_REQUIRED') for name in ('signals', 'holdings', 'execution')}
    result = {}
    for name in ('signals', 'holdings', 'execution'):
        left, right = reference.get(name), port.get(name)
        if left is None or right is None:
            result[name] = axis('BLOCKED', 'REFERENCE_OR_PORT_EXPORT_MISSING')
            continue
        differences = []
        if len(left) != len(right):
            differences.append({'field': 'row_count', 'reference': len(left), 'port': len(right)})
        for i, (expected, actual) in enumerate(zip(left, right)):
            if expected['identity'] != actual['identity']:
                differences.append({'row': i, 'field': 'identity', 'reference': expected['identity'], 'port': actual['identity']})
            try:
                if not all(np.isfinite(np.asarray(r['values'], dtype=float)).all() for r in (expected, actual)):
                    raise AssertionError('undefined comparison input')
                np.testing.assert_allclose(actual['values'], expected['values'], rtol=1e-9, atol=1e-12, equal_nan=False)
            except AssertionError:
                differences.append({'row': i, 'field': 'values', 'reference': expected['values'], 'port': actual['values']})
        result[name] = axis('FAIL' if differences else 'PASS', 'ORDERED_INDEPENDENT_EXPORT_COMPARISON')
        result[name]['differences'] = differences
    return result


def probe(output):
    from core.strategy_manager import StrategyManager
    import urllib.request
    settings_bytes = StrategyManager.SETTINGS_FILE.read_bytes()
    with tempfile.TemporaryDirectory(prefix="strategy-probe-") as tmp:
        settings = Path(tmp) / "settings.json"
        settings.write_bytes(settings_bytes)
        with patch.object(StrategyManager, "_instance", None):
            manager = StrategyManager(settings_path=settings)
            with open(os.devnull, "w", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                result = {sid: {"loaded": manager.get_strategy(sid) is not None, "listed": sid in manager.list_strategies()}
                          for sid in manager.CANONICAL_REGISTRY}
    assert StrategyManager.SETTINGS_FILE.read_bytes() == settings_bytes
    result["__metadata__"] = {"command": f"python scripts/validate_strategy_inventory.py probe --output {output.as_posix()}",
        "scope": "local load/list capability only; settings unchanged"}
    write_json(output / "daily_load_probe.after.json", result)
    try:
        with urllib.request.urlopen("http://localhost:1688/api/strategies", timeout=8) as response:
            catalog = json.load(response)
        deployed = {"status": "READABLE", "command": "GET http://localhost:1688/api/strategies",
            "items": [{k: row.get(k) for k in ("strategy_id", "available_for_daily", "daily_reason", "available_for_backtest")}
                      for row in catalog.get("strategies", [])]}
    except Exception as exc:
        deployed = {"status": "UNAVAILABLE", "error_type": type(exc).__name__}
    write_json(output / "deployed_catalog_probe.after.json", deployed)


def stocks(output, start, end):
    """Use the existing engine on one read-only consistent MySQL snapshot.

    No strategy or fill rule is replaced. Diagnostic completion is never a PASS.
    """
    import pandas as pd
    from sqlalchemy import text
    from sqlalchemy.engine import Connection
    from core.backtest import runner
    from core.backtest.metrics import calculate_metrics
    from core.db_helper import get_db_engine
    from core.strategy_manager import StrategyManager

    plan = {"start": start, "end": end, "seed": "2026-10-21", "capital": 1_000_000,
        "scope": "current public engine characterization; NOT a valid source-parity/OOS certification",
        "predeclared_failures": ["same-day close signal and fill: core/backtest/runner.py:1035-1041",
            "latest financial/revenue joins lack publication-date PIT: runner.py:754-801"],
        "oos": "NOT_VERIFIABLE: diagnostic window is not untouched post-selection OOS",
        "minimum_oos_sessions": 252, "regime_minimum_sessions": 20,
        "benchmark": "asset-matched total-return benchmark; unavailable until independently versioned",
        "research": "existing frozen research windows/folds/cost tracks; historical replay, not new Fresh OOS"}
    assert not (output / "stocks.json").exists(), "Use a new output directory; do not overwrite execution evidence"
    write_json(output / "validation_plan.json", plan)
    result = {}
    events = {}
    base = runner.BacktestEngine

    class RecordingEngine(base):
        def buy(self, stock_id, price, date_str):
            existed = stock_id in self.positions
            super().buy(stock_id, price, date_str)
            if not existed and stock_id in self.positions:
                position = self.positions[stock_id]
                events[self.mode].append([date_str, stock_id, "BUY", position["shares"], position["cost"]])

        def sell(self, stock_id, price, date_str, reason):
            quantity = self.positions[stock_id]["shares"] if stock_id in self.positions else None
            super().sell(stock_id, price, date_str, reason)
            if quantity is not None and stock_id not in self.positions:
                events[self.mode].append([date_str, stock_id, "SELL", quantity, self.trades[-1]["sell_price"]])

    engine = get_db_engine()
    protected_settings = StrategyManager.SETTINGS_FILE.read_bytes()
    with tempfile.TemporaryDirectory(prefix="strategy-audit-") as tmp:
        settings = Path(tmp) / "settings.json"
        settings.write_bytes(protected_settings)
        with patch.object(StrategyManager, "SETTINGS_FILE", settings), patch.object(StrategyManager, "_instance", None):
            with engine.connect() as connection:
                connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                history_start = (pd.Timestamp(start) - pd.Timedelta(days=550)).strftime("%Y-%m-%d")
                snapshots = {}
                for table in ("daily_market_data", "monthly_revenue", "financial_statements"):
                    query = f"SELECT * FROM {table}"
                    if table == "daily_market_data":
                        query += " WHERE trade_date BETWEEN :start AND :end ORDER BY stock_id, trade_date"
                    else:
                        query += " ORDER BY stock_id, year, " + ("month" if table == "monthly_revenue" else "quarter")
                    frame = pd.read_sql(text(query), connection, params={"start": history_start, "end": end})
                    path = output / f"snapshot_{table}.parquet"
                    frame.to_parquet(path, index=False)
                    snapshots[table] = {"path": path.name, "rows": len(frame), "sha256": digest(path), "columns": list(frame.columns)}
                    del frame
                write_json(output / "snapshot_manifest.json", {"isolation": "consistent snapshot, READ ONLY",
                    "history_start": history_start, "end": end, "tables": snapshots,
                    "settings_sha256": hashlib.sha256(protected_settings).hexdigest(), "plan": plan})
                # pandas and existing helpers may enter the same Connection repeatedly.
                # Suppress inner close only; outer context rolls back/closes after patches exit.
                with patch.object(engine, "connect", return_value=connection), patch.object(Connection, "__exit__", return_value=False), patch.object(runner, "get_db_engine", return_value=engine):
                    for sid in StrategyManager.CANONICAL_REGISTRY:
                        print("DIAGNOSTIC", sid, flush=True)
                        events[sid] = []
                        try:
                            with (output / f"stock_{sid}.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                                random.seed(plan["seed"])
                                instance = RecordingEngine(mode=sid, start_date=start, end_date=end,
                                    initial_capital=plan["capital"], persist_results=False, persist_to_db=False)
                                instance.run(return_metrics=True)
                            metrics = calculate_metrics(instance.daily_assets, instance.trades)
                            # Turnover needs all actual fills, including still-open entries.
                            turnover = calculate_metrics(instance.daily_assets,
                                [{"notional": abs(row[3] * row[4])} for row in events[sid]]).get("turnover")
                            metrics.values["turnover"] = turnover
                            chosen = {key: asdict(metrics.get(key)) for key in
                                ("cagr", "sharpe", "sortino", "max_drawdown", "win_rate", "trade_count", "turnover")}
                            for metric in chosen.values():
                                if metric["value"] is not None and not math.isfinite(metric["value"]):
                                    metric.update(value=None, reason="existing metrics returned nonfinite value")
                            result[sid] = {"execution_status": "COMPLETED", "exit_status": 0,
                                "validation": "FAIL", "reason": "SAME_CLOSE_SIGNAL_AND_FILL",
                                "metrics": chosen, "dates": instance.daily_dates, "equity": instance.daily_assets,
                                "closed_trades": instance.trades, "events": events[sid], "plan": plan,
                                "snapshot_manifest": "snapshot_manifest.json"}
                        except Exception as exc:
                            result[sid] = {"execution_status": "FAILED", "exit_status": 1,
                                "validation": "FAIL", "reason": type(exc).__name__, "events": events[sid]}
                        write_json(output / "stocks.checkpoint.json", result)
    assert StrategyManager.SETTINGS_FILE.read_bytes() == protected_settings
    write_json(output / "stocks.json", result)
    print("DIAGNOSTICS_FINISHED", len(result), flush=True)


def report(output):
    inventory = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
    stocks_path = output / "stocks.json"
    stock_results = json.loads(stocks_path.read_text(encoding="utf-8")) if stocks_path.exists() else {}
    missing = {name: importlib.util.find_spec(name) is None for name in ("zipline", "tejapi", "TejToolAPI", "lightgbm")}
    research_diagnostics = {path.name.removesuffix("_diagnostics.json"): json.loads(path.read_text(encoding="utf-8"))
                            for path in output.glob("*_diagnostics.json")}
    quant_path = output / 'quant_assessments.json'
    quant = json.loads(quant_path.read_text(encoding='utf-8'))['records'] if quant_path.exists() else {}
    rows = []
    for record in inventory["records"]:
        row = {"id": record["id"], "source": record["sources"], "kind": record["kind"],
            "counts_as_strategy": record["counts_as_strategy"], "eligibility": "RESEARCH_ONLY",
            "parity": axis("NOT_TESTED", "NO_ORIGINAL_REFERENCE", applicable=False),
            "data": axis("NOT_TESTED", "DATA_VALIDATION_PENDING"),
            "backtest": axis("NOT_TESTED", "EXECUTION_PENDING"),
            "oos": axis("BLOCKED", "NOT_VERIFIABLE: no newly frozen untouched OOS evidence"),
            "daily": axis("BLOCKED", "NO_DAILY_EQUITY_EXECUTOR"),
            "metrics": {name: {"value": None, "reason": "required execution/input evidence unavailable"}
                        for name in ("cagr", "sharpe", "sortino", "max_drawdown", "win_rate", "trade_count", "turnover")}}
        if record["kind"] == "REGISTERED":
            sid = record["id"]
            if record.get("original_notebook"):
                row["parity"] = axis("BLOCKED", "DEPENDENCY_BLOCKED: reference Zipline/bundle unavailable; project holdings/exits differ",
                    evidence="core/strategies/_tquant.py:ENGINE_NOTICE; original Notebook cost_or_schedule/order rules")
                row["source_semantics"] = axis("FAIL", "PORT_SELECTION_ONLY; original portfolio/execution contract not preserved")
            row["data"] = axis("BLOCKED", "NOT_VERIFIABLE: publication/versioned fundamentals, historical universe and model training provenance not all established")
            if sid in stock_results:
                executed = stock_results[sid]
                row["backtest"] = axis(executed["validation"], executed["reason"], evidence="stocks.json#" + sid)
                row["metrics"].update(executed.get("metrics", {}))
            probe_path = output / "daily_load_probe.after.json"
            if not probe_path.exists():
                probe_path = output / "daily_load_probe.json"
            if probe_path.exists():
                probe = json.loads(probe_path.read_text(encoding="utf-8")).get(sid, {})
                if probe.get("loaded") and probe.get("listed"):
                    row["daily"] = axis("PASS", "existing capability only; not activation or Production qualification", evidence=probe_path.name + "#" + sid)
                    row["local_daily_capability"] = True
            deployed_file = output / "deployed_catalog_probe.after.json"
            if not deployed_file.exists():
                deployed_file = output / "deployed_catalog_probe.json"
            if deployed_file.exists():
                deployed = json.loads(deployed_file.read_text(encoding="utf-8"))
                item = next((i for i in deployed.get("items", []) if i.get("strategy_id") == sid), None)
                if item is not None:
                    row["deployed_daily_capability"] = item.get("available_for_daily")
                    if not item.get("available_for_daily"):
                        row["daily"] = axis("BLOCKED", "DEPLOYED_EXECUTOR_UNAVAILABLE: see container scipy import evidence; runtime manifest fix requires deployment",
                            evidence=deployed_file.name + "#" + sid)
        elif record["kind"] == "STRATEGY":
            row["parity"] = axis("BLOCKED", "PORT_MISSING or reference runtime/bundle absent")
            row["data"] = axis("BLOCKED", "DATA_BLOCKED: original licensed/versioned source snapshot not available locally")
            row["backtest"] = axis("BLOCKED", "ENGINE_UNSUPPORTED: futures contract/margin/roll model" if record["asset_class"] == "FUTURE" else "DEPENDENCY_BLOCKED: original Zipline/TEJ environment and bundle")
        elif record["kind"] == "RESEARCH_SETTING":
            namespace = record["namespace"]
            run_file = output / (namespace + "_attempt.json")
            attempt = json.loads(run_file.read_text(encoding="utf-8")) if run_file.exists() else None
            row["data"] = axis("BLOCKED", "PIT/benchmark/liquidity coverage requires source-manifest validation")
            if attempt:
                row["backtest"] = axis("BLOCKED" if attempt["exit_status"] else "NOT_TESTED",
                    attempt.get("reason", "NEW_REPLAY_RESULT_REQUIRES_ASSESSMENT"), evidence=run_file.name)
            diagnostic_file = output / (namespace + "_diagnostics.json")
            if diagnostic_file.exists():
                captures = research_diagnostics[namespace]
                captured = captures.get(record["strategy_id"])
                if captured:
                    base = captured.get("BASE_COST", captured.get("net_base", next(iter(captured.values()))))
                    row["metrics"].update(base["metrics"])
                    row["diagnostics"] = {"path": diagnostic_file.name, "id": record["strategy_id"],
                        "cost_tracks": list(captured), "order_count": base.get("order_count"),
                        "historical_segment_count": len(base.get("folds", []))}
                    row["backtest"] = axis("BLOCKED", "REPLAY_COMPLETED; exact asset costs/liquidity, PIT/selection history and benchmark certification incomplete",
                        evidence=diagnostic_file.name + "#" + record["strategy_id"])
        elif not record["counts_as_strategy"]:
            for field in ("parity", "data", "backtest", "oos", "daily"):
                row[field] = axis("NOT_TESTED", "NOT_APPLICABLE: benchmark/analysis/signal-only source", applicable=False)
        else:
            row["data"] = axis("BLOCKED", "FROZEN_RUNTIME: existing independent PIT/Fresh OOS gate must pass")
            row["backtest"] = axis("BLOCKED", "FROZEN_RUNTIME: no mutation/reranking of frozen candidate; independent validation required")
        row['parity_dimensions'] = {name: dict(row['parity']) for name in ('signals', 'holdings', 'execution')}
        assessment_key = (record['namespace'] + ':' + record['strategy_id']) if record['kind'] == 'RESEARCH_SETTING' else record['id']
        if assessment_key in quant:
            row['quant_assessment'] = {'path': quant_path.name, 'id': assessment_key, **quant[assessment_key]}
        if any(row[field]["status"] == "FAIL" for field in ("parity", "data", "backtest", "oos", "daily")) or row.get("source_semantics", {}).get("status") == "FAIL":
            row["verdict"] = "FAIL"
        elif any(row[field]["status"] == "BLOCKED" for field in ("parity", "data", "backtest", "oos", "daily")):
            row["verdict"] = "BLOCKED"
        else:
            row["verdict"] = "RESEARCH_ONLY" if not record["counts_as_strategy"] else "NOT_TESTED"
        rows.append(row)
    assert {r["id"] for r in rows} == {r["id"] for r in inventory["records"]}
    counted = [r for r in rows if r["counts_as_strategy"]]
    totals = {"discovered": len(counted), "records": len(rows), "ported": inventory["counts"]["ported_sources"],
        "parity": dict(Counter(r["parity"]["status"] for r in counted if r["parity"]["applicable"])),
        "backtest": dict(Counter(r["backtest"]["status"] for r in counted)),
        "blocked_unique": sum(any(r[f]["status"] == "BLOCKED" for f in ("parity", "data", "backtest", "oos", "daily")) for r in counted),
        "not_fully_validated": sum(r["verdict"] != "PASS" for r in counted),
        "daily_capable": sum(r["daily"]["status"] == "PASS" for r in counted), "production_eligible": 0}
    totals["local_daily_capable"] = sum(bool(r.get("local_daily_capability")) for r in counted)
    totals['replayed'] = len(stock_results) + sum(len(captures) for captures in research_diagnostics.values())
    totals['quant_assessed'] = len(quant)
    totals['not_tested_applicable'] = sum(any(r[f]['applicable'] and r[f]['status'] == 'NOT_TESTED' for f in ('parity', 'data', 'backtest', 'oos', 'daily')) for r in counted)
    totals['static_source_semantics_fail'] = sum(r.get('source_semantics', {}).get('status') == 'FAIL' for r in counted)
    write_json(output / "validation.json", {"totals": totals, "missing_reference_packages": missing, "records": rows})
    lines = ["# 2026-10-21 全策略驗證報告", "", "本輪保留每個策略、變體及失敗／阻擋項；能執行不等於回測驗證通過。", "",
        "```json", json.dumps(totals, ensure_ascii=False, indent=2), "```", "",
        "Parity 的 PASS／FAIL 僅計實際可重現比較；原始語義靜態差異另記 source_semantics。Daily PASS 僅代表既有載入／清單能力，不代表已啟用、資料合格或 PRODUCTION_ELIGIBLE。",
        "未定義績效為 null＋reason。股票診斷仍沿用現有訊號／成本／成交，因同收盤資訊與同收盤成交而 FAIL；沒有修改引擎將它偽裝成 PASS。",
        "IS/OOS、walk-forward、benchmark、流動性與行情分段尚缺完整證據的項目維持 BLOCKED／NOT_TESTED，不將舊研究結果改標本輪成功。", "",
        "| ID | Source | Parity | Data | Backtest | OOS | Daily | Verdict |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append("| `" + row["id"] + "` | " + row["source"][0]["path"] + " | " + " | ".join(row[f]["status"] for f in ("parity", "data", "backtest", "oos", "daily")) + " | " + row["verdict"] + " |")
    lines += ['', '## 本輪診斷績效（不是驗證通過）', '',
        '年化 252 個交易日、risk-free/MAR=0，沿用 core/backtest/metrics.py。研究勝率及交易數採扣費後 long-only flat-to-flat 完整現金週期；未平倉不計。股票採原引擎 closed trades。Turnover 為所有實際成交金額／期初資產。',
        'GROSS／BASE_COST／STRESS_COST 的逐筆成交、曲線、historical 70/30 與 252/63 expanding folds 見 *_diagnostics.json。這些期間已用於研究選擇，全部不是 Fresh OOS。價格指數及連續行情分段僅作診斷，缺少含息／資產相符基準、最低費及流動性驗證維持 BLOCKED。', '',
        '| ID | CAGR | Sharpe | Sortino | MDD | Win Rate | Closed Trades | Turnover |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in counted:
        values = [row['metrics'][name]['value'] for name in ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count', 'turnover')]
        lines.append('| `' + row['id'] + '` | ' + ' | '.join('null（見逐筆 reason）' if v is None else f'{v:.8g}' for v in values) + ' |')
    lines += ["", "## 逐筆原因與修復步驟", "",
        "完整原因、指令、snapshot、原始規則及績效見 `outputs/strategy_validation/2026-10-21/{inventory,validation,stocks,snapshot_manifest,validation_plan}.json`，研究 attempt JSON／log 與 Notebook cell 索引同在此證據鏈。",
        "1. 原始 Notebook：在獨立 Python 環境安裝與來源相容的 zipline-tej、tejapi、TejToolAPI（LTR 另需 LightGBM），以合法授權取得來源必要欄位／日期的 bundle、歷史 universe、公告版本；機密只由環境變數／.env 注入。保存輸入 manifest/hash 後才執行來源與 port 的相同快照比較。未聲稱帳戶沒有授權。",
        "2. PIT：補齊公告日／有效時間／財報版本／歷史成分與模型訓練截止，不能以當期年月代替公開時間或拿最新財報回填歷史。",
        "3. 股票成交：另立有基線的行為變更，處理 next-session fill；本輪為保留既有行為而只記 FAIL，不偷偷改成交時點。",
        "4. ETF／期貨：建立資產相容研究 execution adapter、effective tax、權重／資金、tick／乘數／保證金／每日結算／roll／shorting 與合約行情；缺項時不塞入股票選股引擎。",
        "5. OOS：依來源知識截止及已使用期間，凍結新資料日期、最低樣本／purge／embargo、folds、參數及合理含息 benchmark；無樣本不得補零或拿歷史挑選期間冒充 Fresh OOS。",
        "6. 研究：按 attempt JSON 中完整原命令重跑到新目錄；不得覆寫既有研究、Frozen Identity 或 OOS ledger。",
        "7. Parity 匯出契約：在原始獨立環境及 port 分別匯出 snapshot_sha256、calendar_sha256、as_of、universe_sha256、producer_sha256；signals／holdings／execution 為有序 rows，identity 存日期／代碼／排名／方向／順序／數量，values 存 score／權重／價格／成本。呼叫 compare_parity(reference, port)，先核對相同輸入及不同 producer，再分層比較；不執行含憑證的 Notebook，不用 port 產生 reference。本輪 reference 缺少，三層均 BLOCKED。",
        "", "未改 active/persistence、原始 Notebook、歷史資料、Fundamental gates；BROKER_ORDER_SUBMISSION 維持 DISABLED。"]
    (ROOT / "docs/strategy_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stocks", "report", "probe", "parity"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--end", default="2026-10-20")
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--port', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "stocks":
        stocks(args.output, args.start, args.end)
    elif args.mode == "probe":
        probe(args.output)
    elif args.mode == 'parity':
        if not args.reference or not args.port:
            parser.error('parity requires --reference and --port independent exports')
        compared = compare_parity(json.loads(args.reference.read_text(encoding='utf-8')), json.loads(args.port.read_text(encoding='utf-8')))
        write_json(args.output / 'parity_comparison.json', compared)
        sys.exit(0 if all(a['status'] == 'PASS' for a in compared.values()) else 1)
    else:
        report(args.output)
