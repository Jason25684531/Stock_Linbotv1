"""Recovery of the canonical research universe from the existing D3 lineage.

This module is intentionally offline.  It only turns the output of the existing
``build_membership_v2`` pipeline into the persisted, date-dependent parquet
contract and records parity/evidence.  It never downloads a replacement stock
list and never calls the fundamental expansion endpoint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from core.research.fundamental_pit import clean_ticker


DEFAULT_SOURCE_RUN = Path("artifacts/factors/d3_full_20230103_20260728")
CANONICAL_UNIVERSE_PATH = Path("data/processed/research_universe.parquet")
MEMBERSHIP_COLUMNS = (
    "universe_rule_id", "trade_date", "stock_id", "listing_date",
    "listing_age_trading_days", "available_history_count",
    "listing_history_sufficient", "factor_history_sufficient",
    "liquidity_sufficient", "is_tradable_t", "is_tradable_t1", "member",
    "exclusion_reason",
)


@dataclass(frozen=True)
class UniverseLineage:
    builder: str | None
    source: str | None
    rules: Mapping[str, object]
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryResult:
    builder: str | None
    source: str | None
    research_universe_status: str
    universe_parity: str
    output_path: Path | None
    row_count: int
    eligible_ticker_count: int
    eligible_date_count: int
    target_ticker_count: int
    target_ticker_sha256: str
    reasons: tuple[str, ...] = ()


def _sha256_tickers(tickers: Iterable[object]) -> str:
    values = tuple(sorted({clean_ticker(value) for value in tickers if clean_ticker(value)}))
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def discover_universe_lineage(
    repo_root: str | Path = ".", source_run: str | Path = DEFAULT_SOURCE_RUN
) -> UniverseLineage:
    """Inspect the existing builder and its immutable D3 evidence.

    A lineage is established only when the implementation, source run manifest,
    membership output, raw quote cache, and listing-date cache are all present.
    The checks are read-only and do not fall back to a live source.
    """

    root = Path(repo_root)
    run = root / Path(source_run)
    builder_file = root / "core/research/selection/universe.py"
    pipeline_file = root / "core/research/pipeline/orchestrator.py"
    cli_file = root / "jobs/run_factor_research.py"
    manifest_file = run / "run_manifest.json"
    membership_file = run / "universe_membership.csv"
    profile_file = run / "_raw/twse_rwd/t187ap03_L.json"
    raw_dir = run / "_raw/twse_rwd"
    reasons: list[str] = []
    required_code = (builder_file, pipeline_file, cli_file)
    if not all(path.exists() for path in required_code):
        reasons.append("canonical builder/pipeline/CLI source is incomplete")
    if not manifest_file.exists():
        reasons.append("D3 run manifest is missing")
    if not membership_file.exists():
        reasons.append("D3 universe membership artifact is missing")
    if not profile_file.exists():
        reasons.append("official listing-date cache is missing")
    if not raw_dir.exists() or not any(raw_dir.glob("MI_INDEX_*.json")):
        reasons.append("official MI_INDEX raw cache is missing")
    rules: Mapping[str, object] = {}
    if manifest_file.exists():
        try:
            payload = json.loads(manifest_file.read_text(encoding="utf-8"))
            rules = payload.get("universe_parameters", payload.get("universe_rule", {}))
            if payload.get("status") != "success":
                reasons.append("D3 source run is not a successful completed run")
            rule_id = payload.get("universe_rule_id")
            if not rule_id and isinstance(rules, Mapping):
                rule_id = rules.get("rule_id")
            if rule_id != "twse_research_v2":
                reasons.append("D3 source run does not identify twse_research_v2")
        except (OSError, ValueError, AttributeError):
            reasons.append("D3 run manifest is unreadable")
    if reasons:
        return UniverseLineage(None, None, rules, "NOT_ESTABLISHED", tuple(reasons))
    return UniverseLineage(
        "core.research.universe.build_membership_v2",
        "TWSE MI_INDEX raw cache + twse_openapi/t187ap03_L listing-date cache",
        rules,
        "ESTABLISHED",
    )


def _normalise_membership(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(MEMBERSHIP_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"membership missing columns: {missing}")
    work = frame.loc[:, MEMBERSHIP_COLUMNS].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work["listing_date"] = pd.to_datetime(work["listing_date"], errors="coerce")
    work["stock_id"] = work["stock_id"].map(clean_ticker)
    work["member"] = work["member"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    work["is_eligible"] = work["member"]
    work["ticker"] = work["stock_id"]
    work["eligibility_date"] = work["trade_date"]
    work["universe_id"] = "UNIV_RESEARCH_V1"
    return work.sort_values(["trade_date", "ticker"], kind="stable").reset_index(drop=True)


def membership_parity(actual: pd.DataFrame, reference: pd.DataFrame | str | Path) -> tuple[str, str]:
    """Compare a rebuilt membership table with the prior D3 builder output."""

    try:
        expected = pd.read_csv(reference) if isinstance(reference, (str, Path)) else reference.copy()
        left = _normalise_membership(actual)
        right = _normalise_membership(expected)
    except (OSError, ValueError, TypeError) as exc:
        return "NOT_ESTABLISHED", str(exc)
    if left.shape != right.shape:
        return "FAIL", f"shape differs: rebuilt={left.shape}, reference={right.shape}"
    left = left.astype({"ticker": str, "stock_id": str}, copy=False)
    right = right.astype({"ticker": str, "stock_id": str}, copy=False)
    for column in ("universe_rule_id", "trade_date", "stock_id", "listing_date", "listing_age_trading_days", "available_history_count", "listing_history_sufficient", "factor_history_sufficient", "liquidity_sufficient", "is_tradable_t", "is_tradable_t1", "member", "exclusion_reason"):
        if column not in left or column not in right:
            return "FAIL", f"missing parity column: {column}"
        lvalues = left[column].astype("string").fillna("<NA>").tolist()
        rvalues = right[column].astype("string").fillna("<NA>").tolist()
        if lvalues != rvalues:
            return "FAIL", f"membership values differ in {column}"
    return "PASS", "rebuilt membership is identical to the existing D3 builder artifact"


def _atomic_parquet(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(output_path)


def recover_research_universe(
    membership: pd.DataFrame | str | Path,
    output_path: str | Path = CANONICAL_UNIVERSE_PATH,
    *,
    reference: pd.DataFrame | str | Path | None = None,
    lineage: UniverseLineage | None = None,
) -> RecoveryResult:
    """Persist the existing date-dependent membership as the canonical parquet.

    The caller supplies rows emitted by ``build_membership_v2``.  No row is
    invented: ``is_eligible`` is an alias of the builder's ``member`` verdict,
    and every original date/security row is retained for PIT eligibility.
    """

    if isinstance(membership, (str, Path)):
        frame = pd.read_csv(membership)
    else:
        frame = membership.copy()
    normalised = _normalise_membership(frame)
    parity = "NOT_ESTABLISHED"
    parity_reason = "reference membership not supplied"
    if reference is not None:
        parity, parity_reason = membership_parity(normalised, reference)
    eligible = normalised.loc[normalised["is_eligible"]]
    tickers = tuple(sorted(set(eligible["ticker"])))
    # Never promote a candidate whose parity cannot be established.  The
    # caller can still report the row counts/reason and retry after fixing the
    # upstream cache.
    if reference is not None and parity != "PASS":
        return RecoveryResult(
            lineage.builder if lineage else "core.research.universe.build_membership_v2",
            lineage.source if lineage else "TWSE MI_INDEX raw cache + twse_openapi/t187ap03_L listing-date cache",
            "NOT_ESTABLISHED",
            parity,
            None,
            len(normalised),
            len(tickers),
            int(eligible["trade_date"].nunique()),
            len(tickers),
            _sha256_tickers(tickers),
            (parity_reason,),
        )
    output = Path(output_path)
    _atomic_parquet(normalised, output)
    return RecoveryResult(
        lineage.builder if lineage else "core.research.universe.build_membership_v2",
        lineage.source if lineage else "TWSE MI_INDEX raw cache + twse_openapi/t187ap03_L listing-date cache",
        "REBUILT",
        parity,
        output,
        len(normalised),
        len(tickers),
        int(eligible["trade_date"].nunique()),
        len(tickers),
        _sha256_tickers(tickers),
        tuple(() if parity == "PASS" else (parity_reason,)),
    )


def _inventory(cache_root: str | Path) -> pd.DataFrame:
    root = Path(cache_root)
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        request_key = payload.get("request_key") if isinstance(payload, Mapping) else None
        ticker = payload.get("ticker") if isinstance(payload, Mapping) else None
        rows.append({"path": str(path.relative_to(root)).replace("\\", "/"), "request_key": request_key or "", "ticker": clean_ticker(ticker), "sha256": _file_sha256(path)})
    return pd.DataFrame(rows, columns=["path", "request_key", "ticker", "sha256"])


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def create_pre_run_snapshot(
    target_tickers: Iterable[object],
    cache_root: str | Path,
    snapshot_root: str | Path,
    *,
    existing_publication_tickers: Iterable[object] | None = None,
    target_source: str | Path = CANONICAL_UNIVERSE_PATH,
) -> dict[str, object]:
    """Write deterministic pre-network evidence without making a request."""

    root = Path(snapshot_root)
    root.mkdir(parents=True, exist_ok=True)
    target = tuple(sorted({clean_ticker(value) for value in target_tickers if clean_ticker(value)}))
    target_frame = pd.DataFrame({"ticker": target})
    target_path = root / "target_tickers.csv"
    target_frame.to_csv(target_path, index=False)
    inventory = _inventory(cache_root)
    inventory_path = root / "existing_cache_inventory.csv"
    inventory.to_csv(inventory_path, index=False)
    publication = tuple(sorted({clean_ticker(value) for value in (existing_publication_tickers or ()) if clean_ticker(value)}))
    publication_path = root / "existing_publication_tickers.csv"
    pd.DataFrame({"ticker": publication}).to_csv(publication_path, index=False)
    payload = {
        "snapshot_status": "PASS",
        "created_before_network": True,
        "network_fetch_count": 0,
        "target_source": str(target_source),
        "target_ticker_count": len(target),
        "target_ticker_sha256": _sha256_tickers(target),
        "existing_cache_file_count": len(inventory),
        "existing_cache_sha256": _file_sha256(inventory_path),
        "existing_publication_ticker_count": len(publication),
        "existing_publication_sha256": _file_sha256(publication_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = root / "pre_expansion_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["target_tickers_path"] = str(target_path)
    payload["cache_inventory_path"] = str(inventory_path)
    payload["publication_tickers_path"] = str(publication_path)
    payload["manifest_path"] = str(manifest_path)
    return payload


def write_recovery_report(
    result: RecoveryResult | None,
    lineage: UniverseLineage,
    output_path: str | Path,
    *,
    target_status: str = "BLOCKED",
    legacy_baseline_status: str = "NOT_ESTABLISHED",
    pre_run_snapshot: str = "NOT_RUN",
    full_expansion_unlocked: str = "NO",
    unblock_status: str | None = None,
) -> Path:
    """Write an evidence-first recovery report suitable for independent review."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Research Universe Recovery Report",
        "",
        "- CHANGE_ID: `expand-fundamental-pit-coverage-v1`",
        f"- CANONICAL_UNIVERSE_BUILDER: `{lineage.builder or 'NOT_ESTABLISHED'}`",
        f"- CANONICAL_UNIVERSE_SOURCE: `{lineage.source or 'NOT_ESTABLISHED'}`",
        f"- BUILDER_LINEAGE_STATUS: `{lineage.status}`",
        f"- RESEARCH_UNIVERSE_STATUS: `{result.research_universe_status if result else 'NOT_ESTABLISHED'}`",
        f"- UNIVERSE_PARITY: `{result.universe_parity if result else 'NOT_ESTABLISHED'}`",
        f"- TARGET_UNIVERSE_STATUS: `{target_status}`",
        f"- TARGET_TICKER_COUNT: `{result.target_ticker_count if result else 'NOT_ESTABLISHED'}`",
        f"- TARGET_TICKER_SHA256: `{result.target_ticker_sha256 if result else 'NOT_ESTABLISHED'}`",
        f"- LEGACY_BASELINE_STATUS: `{legacy_baseline_status}`",
        "- EXISTING_PUBLICATION_TICKER_COUNT: `0` (baseline inventory not established)",
        f"- PRE_RUN_SNAPSHOT: `{pre_run_snapshot}`",
        f"- FULL_EXPANSION_UNLOCKED: `{full_expansion_unlocked}`",
        f"- READY_FOR_FULL_EXPANSION: `{'YES' if full_expansion_unlocked == 'YES' else 'NO'}`",
        "- NETWORK_FETCH_REQUESTS: `0`",
        "- READY_FOR_REVIEW: `NO`",
        "- READY_FOR_RESEARCH_CYCLE_V3: `NO`",
        "- READY_FOR_FACTOR_GATE: `NO`",
        "- READY_FOR_BACKTEST: `NO`",
        f"- UNBLOCK_STATUS: `{unblock_status or ('COMPLETE' if full_expansion_unlocked == 'YES' else 'BLOCKED')}`",
        "",
        "## Universe semantics",
        "",
        "Eligibility is date-dependent and retains the existing `twse_research_v2` builder output. It uses normalized four-digit TWSE ordinary-share identifiers, official listing dates for the 252-trading-day gate, a complete trailing 60-day official traded-value window, unadjusted close >= 10, valid official OHLC/volume rows, and same-day positive volume. Adjusted OHLC remains an available pipeline field for factors but is not used by the membership gate. Six-digit/nonstandard identifiers such as `912000` are excluded by the canonical quote normalizer; if supplied as an acceptance ticker, the fundamental runner retains and explicitly classifies it instead of coercing it. The current-listed-only survivorship limitation remains `KNOWN_LIMITATION`.",
        "",
        "## Evidence",
        "",
        "- Builder: `core/research/universe.py::build_membership_v2`.",
        "- Orchestration: `core/research/pipeline.py` and `jobs/run_factor_research.py`.",
        "- Source cache: D3 `MI_INDEX_*.json` and `t187ap03_L.json`; no live request was made by recovery.",
    ]
    if result:
        lines.extend([
            "",
            "## Rebuilt output",
            "",
            f"- output: `{result.output_path}`",
            f"- rows: `{result.row_count}`",
            f"- eligible tickers (date-union): `{result.target_ticker_count}`",
        f"- target ticker SHA256: `{result.target_ticker_sha256}`",
            "- target count difference: historical review fingerprint is not present; no count comparison was inferred.",
        ])
    if lineage.reasons:
        lines.extend(["", "## Unresolved evidence", "", *[f"- {reason}" for reason in lineage.reasons]])
    if result and result.reasons:
        lines.extend(["", "## Parity notes", "", *[f"- {reason}" for reason in result.reasons]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def expansion_unlock_status(
    lineage: UniverseLineage,
    result: RecoveryResult | None,
    pre_run_snapshot: Mapping[str, object] | None,
) -> str:
    """Return YES only when canonical lineage, parity, target and snapshot exist."""

    if lineage.status != "ESTABLISHED" or result is None or result.research_universe_status not in {"REBUILT", "RECOVERED"} or result.universe_parity != "PASS":
        return "NO"
    if not pre_run_snapshot or pre_run_snapshot.get("snapshot_status") != "PASS":
        return "NO"
    return "YES"


__all__ = [
    "CANONICAL_UNIVERSE_PATH", "DEFAULT_SOURCE_RUN", "MEMBERSHIP_COLUMNS", "RecoveryResult", "UniverseLineage",
    "create_pre_run_snapshot", "discover_universe_lineage", "expansion_unlock_status", "membership_parity",
    "recover_research_universe", "write_recovery_report",
]
