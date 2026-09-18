from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_artifact_roots_have_retention_guidance():
    assert (ROOT / "artifacts" / "README.md").is_file()
    assert (ROOT / "artifacts" / "reports" / "README.md").is_file()
    assert (ROOT / "docs" / "refactor" / "artifact_retention_inventory_2026-09-18.md").is_file()


def test_source_tree_has_no_generated_data_files():
    generated_suffixes = {".parquet", ".pkl", ".feather"}
    generated = [path for path in (ROOT / "core").rglob("*") if path.is_file() and path.suffix in generated_suffixes]
    assert generated == []
