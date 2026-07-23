# Remaining technical debt

- The extracted runner retains the characterized algorithm while future work can replace its internal transaction loop with smaller collaborators.
- Deprecated strategy IDs and root CLI wrappers are retained for compatibility; remove only in a major-version migration after downstream consumers migrate.

## Rollback

This worktree intentionally has no per-phase commits. Before merging, commit each
phase separately in task order. Roll back in reverse order with `git revert <phase
commit>`. Never rewrite historical strategy keys or generated baseline fixtures during rollback.
