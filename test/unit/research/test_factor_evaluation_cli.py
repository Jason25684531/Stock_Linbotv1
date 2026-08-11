from pathlib import Path

import pandas as pd

from jobs import run_factor_evaluation as runner


def test_cli_refuses_existing_output_directory(tmp_path, monkeypatch, capsys):
    output = tmp_path / "outputs" / "factor_research" / "taken"
    output.mkdir(parents=True)

    assert runner.main(["--run-id", "d3", "--eval-run-id", "taken", "--output-root", str(tmp_path / "outputs" / "factor_research")]) == 1
    assert "already exists" in capsys.readouterr().err
