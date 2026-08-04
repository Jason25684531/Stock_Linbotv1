"""Orchestrate the standalone, in-memory factor-research stages."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

import pandas as pd

from core.research import artifacts, factors, market_data, normalize, reconcile, universe, validation
from core.research.sources import twse


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    generated_at: str
    adjustment_as_of: object
    requested_start: object
    requested_end: object
    output_dir: Path
    quotes: pd.DataFrame | None = None
    actions: pd.DataFrame | None = None
    vendor_quotes: pd.DataFrame | None = None
    reconciliation_seed: int = 0
    allow_vendor_fallback: bool = False
    source_coverage: tuple[Mapping[str, object], ...] = ()
    no_fetch: bool = False


@dataclass(frozen=True)
class RunResult:
    status: str
    diagnostics: list[dict[str, object]]
    skipped_factors: tuple[str, ...]
    output_dir: Path


def run(config: RunConfig) -> RunResult:
    """Run validation, factors, universe, and pure artifact serialization in order."""

    if config.quotes is None:
        config = replace(config, quotes=_load_twse_quotes(config))
    if config.actions is None:
        config = replace(config, actions=_load_actions(config))
    output_dir = Path(config.output_dir)
    contract_diagnostics = [_diagnostic(item) for item in validation.validate(config.quotes)]
    if _has_fatal(contract_diagnostics):
        return _failed(config, contract_diagnostics)

    adjusted = normalize.apply_adjustments(config.quotes, config.actions, config.adjustment_as_of).quotes
    factor_diagnostics: list[dict[str, object]] = []
    skipped: list[str] = []
    value_count = null_count = 0
    for name, spec in factors.FACTOR_REGISTRY.items():
        if not set(spec.required_columns).issubset(adjusted):
            skipped.append(name)
            continue
        source_columns = sorted(set(spec.required_columns) | ({"volume"} if name == "amihud_20d" else set()) | {
            f"raw_{column[9:]}" for column in spec.required_columns if column.startswith("adjusted_")
        })
        frames = market_data.to_wide(adjusted, source_columns)
        raw_frames = _raw_frames(frames)
        primary = factors.compute_factor(spec, frames)
        qa = factors.compute_factor(spec, raw_frames)
        published, comparison = _requested_wide(primary.values, config), _requested_wide(qa.values, config)
        value_count += published.size + comparison.size
        null_count += int(published.isna().sum().sum() + comparison.isna().sum().sum())
        factor_diagnostics.extend(_diagnostic(item) for item in primary.diagnostics + qa.diagnostics)
        artifacts.write_factor_values(output_dir, published, factor_name=name,
                                      factor_version=spec.version, price_basis=spec.price_basis, run_id=config.run_id)
        artifacts.write_factor_values(output_dir, comparison, factor_name=name,
                                      factor_version=spec.version,
                                      price_basis="not_applicable" if spec.price_basis == "not_applicable" else "raw_unadjusted",
                                      run_id=config.run_id, qa=True)
        del frames, raw_frames, primary, qa

    mask, universe_warnings = universe.build_mask(adjusted)
    liquidity_basis = market_data.to_wide(adjusted, ["liquidity_basis"])["liquidity_basis"]
    counts = universe.universe_counts(mask, liquidity_basis)
    merged_diagnostics = contract_diagnostics + factor_diagnostics + [
        {"stage": "universe", "code": code, "severity": "WARN", "trade_date": None, "stock_id": None, "detail": ""}
        for code in universe_warnings
    ]
    artifacts.write_validation_report(output_dir, merged_diagnostics)
    artifacts.write_source_coverage(output_dir, config.source_coverage)
    artifacts.write_universe_counts(output_dir, _requested(counts, config))
    artifacts.write_reconciliation_summary(output_dir, _reconciliation(config))
    artifacts.write_manifest(output_dir, _manifest(config, "success", merged_diagnostics, counts, skipped, null_count / value_count if value_count else None))
    return RunResult("success", merged_diagnostics, tuple(skipped), output_dir)


def _failed(config: RunConfig, diagnostics: list[dict[str, object]]) -> RunResult:
    artifacts.write_validation_report(config.output_dir, diagnostics)
    artifacts.write_source_coverage(config.output_dir, config.source_coverage)
    artifacts.write_manifest(config.output_dir, _manifest(config, "failed", diagnostics, pd.DataFrame(), []))
    return RunResult("failed", diagnostics, (), Path(config.output_dir))


def _load_twse_quotes(config: RunConfig) -> pd.DataFrame:
    """Fetch or reuse MI_INDEX raw files, then normalize only TWSE source rows."""

    cache_dir = Path(config.output_dir) / "_raw" / "twse_rwd"
    quotes = []
    window = market_data.loaded_window(config.requested_start, config.requested_end, maximum_lookback=253)
    gate = twse.RequestGate(3.0)
    for day in pd.bdate_range(window.loaded_start, window.loaded_end):
        cache_file = cache_dir / f"MI_INDEX_{day:%Y%m%d}.json"
        if config.no_fetch and not cache_file.exists():
            continue
        response = twse.fetch_daily_quotes(day.date(), cache_dir, gate=gate)
        classification = twse.classify(response)
        if classification.kind is twse.ResponseKind.TRADING_DAY:
            quotes.append(normalize.normalize_twse_closing_table(twse.find_closing_table(response.payload), day, response.retrieved_at))
    return pd.concat(quotes, ignore_index=True) if quotes else pd.DataFrame()


def _load_actions(config: RunConfig) -> pd.DataFrame | None:
    cache_dir = Path(config.output_dir) / "_raw" / "twse_rwd"
    try:
        response = twse.fetch_corporate_actions(pd.Timestamp(config.requested_start).date(), pd.Timestamp(config.requested_end).date(), cache_dir)
        return normalize.normalize_corporate_actions(response.payload, response.retrieved_at)
    except Exception:
        return None


def _required_frame_columns() -> tuple[str, ...]:
    return tuple(sorted({column for spec in factors.FACTOR_REGISTRY.values() for column in spec.required_columns} | {"raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"}))


def _raw_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        (f"adjusted_{name[4:]}" if name.startswith("raw_") else name): value
        for name, value in frames.items()
    }


def _requested_wide(values: pd.DataFrame, config: RunConfig) -> pd.DataFrame:
    return values.loc[(values.index >= pd.Timestamp(config.requested_start)) & (values.index <= pd.Timestamp(config.requested_end))]


def _requested(values: pd.DataFrame, config: RunConfig) -> pd.DataFrame:
    if values.empty or "trade_date" not in values:
        return values
    dates = pd.to_datetime(values["trade_date"])
    return values.loc[(dates >= pd.Timestamp(config.requested_start)) & (dates <= pd.Timestamp(config.requested_end))]


def _reconciliation(config: RunConfig) -> pd.DataFrame:
    columns = ["stock_id", "trade_date", "raw_close", "close", "relative_difference"]
    if config.vendor_quotes is None:
        return pd.DataFrame(columns=columns)
    return reconcile.reconcile(config.quotes, config.vendor_quotes, config.reconciliation_seed).summary


def _diagnostic(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {"stage": value.get("stage", "factor"), "code": value.get("code", ""), "severity": value.get("severity", "WARN"), "trade_date": value.get("trade_date"), "stock_id": value.get("stock_id"), "detail": value.get("detail", "")}
    return {"stage": value.stage, "code": value.code, "severity": value.severity, "trade_date": value.trade_date, "stock_id": value.stock_id, "detail": value.detail}


def _has_fatal(diagnostics: list[dict[str, object]]) -> bool:
    return any(item["severity"] == "FATAL" for item in diagnostics)


def _manifest(config: RunConfig, status: str, diagnostics: list[dict[str, object]], counts: pd.DataFrame, skipped: list[str], nan_ratio: float | None = None) -> dict[str, object]:
    return {
        "run_id": config.run_id, "status": status, "generated_at": config.generated_at,
        "git_commit": None, "market_scope": "TWSE",
        "fallback_mode": "allow_vendor_fallback" if config.allow_vendor_fallback else "official_only",
        "reconciliation_seed": config.reconciliation_seed,
        "source_versions": {}, "source_parameters": {},
        "factor_versions": {name: spec.version for name, spec in factors.FACTOR_REGISTRY.items()},
        "requested_window": {"start": str(config.requested_start), "end": str(config.requested_end)},
        "loaded_window": {"start": str(config.quotes["trade_date"].min()) if "trade_date" in config.quotes else None, "end": str(config.quotes["trade_date"].max()) if "trade_date" in config.quotes else None},
        "maximum_lookback": 253, "adjustment_as_of": str(config.adjustment_as_of),
        "universe_rule": {"code_regex": "^[1-9]\\d{3}$", "market": "TWSE", "price_column": "raw_close", "min_raw_close": 10, "min_liquidity_twd": 20_000_000, "liquidity_window": 20, "liquidity_proxy_formula": "raw_close * volume"},
        "universe_count_median": None if counts.empty else float(counts["count"].median()),
        "price_basis": {"primary": "local_adjusted", "qa": "raw_unadjusted"},
        "warning_counts": {code: sum(item["code"] == code for item in diagnostics) for code in sorted({item["code"] for item in diagnostics if item["severity"] == "WARN"})},
        "nan_ratio": nan_ratio,
        "skipped_factors": skipped,
    }
