"""Pure artifact writers for research-stage outputs."""

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path


SOURCE_COVERAGE_FIELDS = ("trade_date", "classification", "bound", "code", "severity", "detail")


def write_source_coverage(output_dir: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    """Write source classifications without importing or orchestrating research stages."""

    output = Path(output_dir) / "source_coverage.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output
