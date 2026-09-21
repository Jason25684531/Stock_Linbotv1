# OpenSpec Change Governance — 2026-09-21

## Reviewability

`.gitignore` now ignores generated OpenSpec material, exposes every non-archived directory under `openspec/changes/`, and continues to ignore `openspec/changes/archive/**`.  Active proposal, design, spec, task, and evidence files therefore appear through ordinary `git status`; no force-add is required.

## Naming conflict

The installed OpenSpec CLI rejected `2026-09-21-repository-hygiene-and-safe-cleanup` with `Change name must start with a letter`.  The change therefore uses the compatible ID `repository-hygiene-and-safe-cleanup-2026-09-21`, while its artifacts and task headings retain the ISO 8601 creation date.

Project policy still requires a date prefix.  Until the CLI supports a configurable prefix rule, maintainers must document this incompatibility and use a letter-leading ID with the creation date suffix; they must not bypass Git ignore rules or silently omit the date.  A tooling/policy alignment change is required before declaring the conflict resolved.

## Verification

Use `git check-ignore -v <active-artifact>` (no matching rule expected), `git check-ignore -v <archive-artifact>` (archive rule expected), `git status --short --untracked-files=all`, and `openspec status --change <id>`.
