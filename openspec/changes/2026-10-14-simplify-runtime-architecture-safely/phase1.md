# 2026-10-14 Phase 1

Phase 0 commit: a4b8bcbfda2653d7a267154c90749e43b8874b65.

Five allowed hash helpers now use hashlib.file_digest in binary context managers. Existing OSError-to-None behavior remains for universe_recovery; the four strict helpers continue to raise. The canonical PIT helper and its source checksum are retained (DEFER) because its manifest records self code_sha256. No frozen expected hash, evidence artifact or strategy fingerprint is rewritten.

The private _freeze_mapping section is removed after the Phase 0 no-reference evidence was committed. _freeze and immutable validation remain. No file or dependency is deleted. Verification and the independent Phase 1 commit are recorded after targeted checks pass.

Targeted validation: 202 passed, 2 existing pandas stack warnings in 39.35s. Frozen artifact and retained canonical PIT source SHA-256 checks: PASS. Source diff: 5 insertions, 29 deletions (net -24 lines). git diff --check PASS.

Phase 1 commit: fcdf545. Rollback: git revert fcdf545 (only this Phase); rerun the targeted 202-case suite. A revert patch check will also be exercised on an isolated copy before final acceptance.
