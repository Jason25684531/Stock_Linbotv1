# Owner Proposals — 2026-10-23 (not executed)

## P-1 Credential rotation (MySQL `trader`)
Commit `b1c33c8` once stored `MYSQL_PASSWORD` in `docker-compose.yaml`; HEAD reads it from the environment. The value is not reproduced here.
1. Create a new password in the secret store; never in Git.
2. `ALTER USER 'trader'@'%' IDENTIFIED BY <new>` in a maintenance window (DBA).
3. Update `.env` / deployment secrets; restart web, scheduler and LINE workers; check `/health` and one scheduler dry-run.
4. Verify the old password is rejected; list other hosts or tools that used it.
5. Git history is not rewritten; if the owner wants it purged, plan a coordinated history rewrite separately (force-push and every clone affected).

## P-2 Retraining (requires approval)
- Data: certified T86 / MI_MARGN (download stages 1-3), official prices, TWSE stocks only, PIT fundamentals; no NULL -> 0 for missing flows.
- Features: the shared core (`build_feature_history`) plus `build_multi_factor_matrix` on the same population used in Daily.
- Record `training_data_end`, code commit, data manifest SHA-256 and the train/test boundary in the model file.
- Labels: drop rows without a full horizon instead of labelling them 0; purge `look_ahead_days` sessions at the train/test boundary.
- Pre-register the OOS start (next session after `training_data_end`), metrics and the 252-session minimum before training.
- Keep the current pickles unchanged beside the new ones until certified.

## P-3 Production DB repair (requires approval)
- Load official T86 / MI_MARGN into a staging table; diff against `daily_market_data` per stock and date; review the diff.
- Update only mismatching rows, in a transaction, with a backup table and a rollback script; NULL where the official source has no row.

## P-4 Switch Daily to `recovered`
Conditions: retrained models certified; data gates PASS for the live window; parity PASS; at least 20 shadow sessions with no errors; owner sign-off.
