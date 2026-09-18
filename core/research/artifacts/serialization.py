"""Pure artifact writers for research-stage outputs."""

import csv
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd


SOURCE_COVERAGE_FIELDS = ("trade_date", "classification", "bound", "code", "severity", "detail")
VALIDATION_FIELDS = ("stage", "code", "severity", "trade_date", "stock_id", "detail")
FACTOR_FIELDS = (
    "trade_date", "stock_id", "factor_name", "factor_version", "value", "price_basis", "run_id",
    # Canonical aliases mirroring the legacy columns above; kept alongside them for compatibility.
    "asof_date", "asset_id", "factor_id", "raw_value",
)


def write_source_coverage(output_dir: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    """Write source classifications without importing or orchestrating research stages."""

    output = Path(output_dir) / "source_coverage.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output


def write_validation_report(output_dir: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    return _write_rows(Path(output_dir) / "validation_report.csv", VALIDATION_FIELDS, rows)


def write_universe_counts(output_dir: Path, counts: pd.DataFrame) -> Path:
    return _write_frame(Path(output_dir) / "universe_counts.csv", counts)


def write_reconciliation_summary(output_dir: Path, summary: pd.DataFrame) -> Path:
    return _write_frame(Path(output_dir) / "reconciliation_summary.csv", summary)


def write_universe_membership(output_dir: Path, membership: pd.DataFrame) -> Path:
    return _write_frame(Path(output_dir) / "universe_membership.csv", membership)


def write_preprocessing_summary(output_dir: Path, dataset: pd.DataFrame) -> Path:
    columns = ["asof_date", "factor_id", "member"]
    return _write_frame(Path(output_dir) / "preprocessing_summary.csv", dataset.loc[:, columns].groupby(columns[:2], dropna=False).agg(member_count=("member", "sum")).reset_index())


def write_leakage_validation(output_dir: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    return _write_rows(Path(output_dir) / "leakage_validation.csv", VALIDATION_FIELDS, rows)


def write_label_coverage(output_dir: Path, dataset: pd.DataFrame) -> Path:
    labels = [column for column in dataset if column.startswith("forward_return_") and not column.endswith("_missing_reason")]
    return _write_frame(Path(output_dir) / "label_coverage.csv", pd.DataFrame({"label": labels, "non_null_count": [int(dataset[label].notna().sum()) for label in labels]}))


def write_research_dataset(output_dir: Path, dataset: pd.DataFrame) -> list[Path]:
    paths = []
    work = dataset.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    for (factor_id, year), partition in work.groupby(["factor_id", work["asof_date"].dt.year], sort=True):
        paths.append(_write_frame(Path(output_dir) / "research_dataset" / str(factor_id) / f"{year}.csv", partition))
    return paths


def write_factor_values(
    output_dir: Path, values: pd.DataFrame, *, factor_name: str, factor_version: str,
    price_basis: str, run_id: str, qa: bool = False,
) -> list[Path]:
    """Serialize one factor into separate calendar-year long-form CSVs."""

    root = Path(output_dir) / ("qa/values_raw" if qa else "values") / factor_name
    long = values.rename_axis(index="trade_date", columns="stock_id").stack(dropna=False).rename("value").reset_index()
    long["trade_date"] = pd.to_datetime(long["trade_date"])
    long["factor_name"] = factor_name
    long["factor_version"] = factor_version
    long["price_basis"] = price_basis
    long["run_id"] = run_id
    long["asof_date"] = long["trade_date"]
    long["asset_id"] = long["stock_id"]
    long["factor_id"] = long["factor_name"]
    long["raw_value"] = long["value"]
    paths = []
    for year, partition in long.groupby(long["trade_date"].dt.year, sort=True):
        paths.append(_write_frame(root / f"{year}.csv", partition.loc[:, FACTOR_FIELDS]))
    return paths


def write_manifest(output_dir: Path, manifest: Mapping[str, object]) -> Path:
    output = Path(output_dir) / "run_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output


def _write_rows(path: Path, fieldnames: tuple[str, ...], rows: Iterable[Mapping[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_frame(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path
