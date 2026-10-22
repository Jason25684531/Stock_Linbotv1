# Owner Decisions Required — 2026-10-22 Daily Selection Recovery

Decided this round (OD-1..OD-7) are recorded in `openspec/changes/2026-10-22-recover-daily-selection-pipeline/design.md` §9.
Open items below need an explicit owner decision; nothing here was changed without one.

| ID | Item | Evidence | Options | Current state |
|---|---|---|---|---|
| OD-3a | Training-time `chip_score` differs from the formula with real dealer/margin data | `model_contract_report.json` `chip_score_training_trace`: dealer/margin NULL in every training row; `dealer_ratio_z` importance 0, `chip_score_z` > 0 in all 7 models | (a) retrain on certified chips with `training_data_end` recorded; (b) keep models blocked | MODEL_BLOCKED, not retrained |
| OD-3b | Models lack `training_data_end` | all 7 pickles: keys without `training_data_end` | re-export metadata from the training run, or retrain with it recorded | MODEL_BLOCKED |
| OD-5a | Recovered candidates without valid AI scores | every native model is MODEL_BLOCKED, so recovered Daily has no publishable ranking | publish by rule order, wait for models, or keep shadow | shadow only |
| OD-7a | Switch production to `DAILY_FEATURE_MODE=recovered` | parity and tests in this directory | separate acceptance run, then approval | legacy; recovered refuses non-dry-run |
| OD-8 | TPEx rows in live recovered Daily | research excludes TPEx (no official TPEx cache); live MySQL still carries TPEx stocks (weekend/duplicate history, native7 `tpex_audit.json`) | restrict live recovered to TWSE, or fetch and certify TPEx official data | uncertified, reported |
| OD-9 | MySQL institutional flows stored as 0 on whole dates | `data_quality_report.json` MYSQL_FOREIGN_BUY/TRUST_BUY_RESEARCH_WINDOW: 19 dates with < 99% agreement, MySQL 0 vs official non-zero; Oct trust 53 mismatches | repair the crawler/DB from official T86 (DB write needs approval) | research uses official T86; MySQL untouched |
| OD-10 | V36 relaxed mode drops the consecutive-buy condition | `core/strategies/institutional_flow_confirmation.py` relaxed branch keeps stage-1 rows with chip_score >= 27.5; with real chips the neutral score is ~50, so the relaxed branch admits nearly every stage-1 row when stage 2 is empty | keep, tighten, or remove (strategy rule change) | unchanged; test documents it |
| OD-11 | Ex-dividend bias in raw-price `high_60` (OD-2) | system has raw prices only; after ex-rights `high_60` stays above an adjusted high for up to 60 sessions | keep raw (stricter), or add adjusted prices | raw, reported |
| OD-12 | Monthly revenue and post-FY2025 filings have no publication timestamp | `data_quality_report.json` FUNDAMENTALS / MONTHLY_REVENUE | add MOPS publication capture for revenue and recent quarters | DATA_BLOCKED, deadline estimate flagged |
| OD-13 | Model-only inputs also exclude rule candidates | when the model contract loads (2026-10-13..16) stocks missing a model input (mostly no margin data) are excluded, e.g. 448 of 458 exclusions for mean_reversion on 2026-10-15; when the contract is refused no model exclusion applies, so candidate counts depend on model availability | keep (research engine does the same; parity relies on it), or exclude by rule inputs only and leave those stocks unscored | kept, identical in research and Daily |
