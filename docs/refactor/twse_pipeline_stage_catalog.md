# TWSE Pipeline Stage Catalog

Status: contract baseline, 2026-09-18

The catalog is intentionally conservative. A label without approved business
evidence stays `UNMAPPED`; it must not be treated as an alias for D2, D3, or D4.

| Stage | Status | Current owner/schema | As-of rule |
|---|---|---|---|
| `TWSE_OFFICIAL` | MAPPED | `core.research.sources` / `declared/v1` | source retrieval and trading date |
| `ADJUSTMENT` | MAPPED | `core.research.normalize` / `declared/v1` | point-in-time adjustment-as-of |
| `L2` | UNMAPPED | no approved owner / `unmapped/v1` | blocked until definition is approved |
| `L4` | UNMAPPED | no approved owner / `unmapped/v1` | blocked until definition is approved |
| `S3` | UNMAPPED | no approved owner / `unmapped/v1` | blocked until definition is approved |
| `TOP5` | MAPPED | approved selection boundary / `declared/v1` | consumes valid selection input |
| `TARGET` | MAPPED | approved target-weight boundary / `declared/v1` | consumes deterministic Top5 output |

Every hand-off carries run ID, schema version, as-of value, provenance hash,
output ID, diagnostics, and status. A fatal diagnostic blocks downstream
selection; runtime and reporting consume the target contract rather than
recomputing it.

The typed contract implementation is
[`core/research/stage_contracts.py`](../../core/research/stage_contracts.py),
with focused tests in
[`test/unit/research/test_stage_contracts.py`](../../test/unit/research/test_stage_contracts.py).
