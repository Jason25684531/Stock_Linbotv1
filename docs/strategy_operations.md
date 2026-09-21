# Strategy Operations Diagnostics

These commands inspect the system. They do not alter strategies, research, Fresh OOS, production flags, scheduler behavior, or broker execution. Only `report` writes a derived snapshot under `outputs/strategy_diagnostics/`.

Daily:

```powershell
python jobs/strategy_ops.py status
```

Data check:

```powershell
python jobs/strategy_ops.py data
python jobs/strategy_ops.py data-quality
python jobs/strategy_ops.py pipelines
```

Factor check:

```powershell
python jobs/strategy_ops.py factors
python jobs/strategy_ops.py factor --id G2_OPERATING_INCOME_YOY
```

Strategy check:

```powershell
python jobs/strategy_ops.py strategies
python jobs/strategy_ops.py strategy --id fundamental_g2g3_top5_reb60_score_weighted_v1
```

Runtime and OOS:

```powershell
python jobs/strategy_ops.py oos
python jobs/strategy_ops.py health
python jobs/strategy_ops.py validate
```

Full overview and report:

```powershell
python jobs/strategy_ops.py all
python jobs/strategy_ops.py all --verbose
python jobs/strategy_ops.py report
```

Every command supports deterministic machine output, for example:

```powershell
python jobs/strategy_ops.py status --json
```

`N/A`, `NOT_MATERIALIZED`, `MISSING_ARTIFACT`, `SOURCE_UNAVAILABLE`, `UNSUPPORTED_SCHEMA`, and `CONFLICT` describe evidence availability; none cause the CLI to re-run research.
