# Generated artifacts

`artifacts/` contains local generated material, not source code.

- `backtests/`, `d5_handoff/`, and `factors/` are retained until their owning
  run/change records an archive disposition.
- `cache/<source>/<identity>/` is the planned location for resumable raw
  source cache; cache entries require source identity and retrieval metadata.
- `reports/<run-or-change-id>/` is for review reports with a manifest. See
  `artifacts/reports/README.md`.

Do not delete a non-cache artifact without the evidence rules in
`docs/refactor/deletion_candidates.md`.
