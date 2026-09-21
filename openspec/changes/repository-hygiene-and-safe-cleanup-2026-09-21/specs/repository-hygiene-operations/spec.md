## ADDED Requirements

### Requirement: Current evidence-led hygiene classification
The repository SHALL maintain a dated hygiene ledger that assigns each inspected candidate exactly one of `ACTIVE`, `LEGACY_COMPATIBILITY`, `DEVELOPMENT_TOOLING`, `GENERATED_ARTIFACT`, `CACHE`, `DUPLICATE`, `UNREFERENCED`, `DELETION_CANDIDATE`, or `UNKNOWN`.  A non-cache deletion candidate SHALL include current evidence covering Python imports, dynamic strings, CLI and batch entrypoints, scheduler wiring, Compose, CI, docs, tests, Web, LINE Bot, environment/agent configuration, and OpenSpec.

#### Scenario: Candidate retains an active or unresolved reference
- **WHEN** any required reference check finds a use or cannot establish absence
- **THEN** the ledger SHALL classify the candidate as preserved or `UNKNOWN` and SHALL NOT authorize deletion

#### Scenario: Candidate has complete no-reference evidence
- **WHEN** every required reference check completes with no active reference
- **THEN** the ledger SHALL record the path, evidence commands/results, reason, impact, validation, and recovery method before it is eligible for a separately reviewed deletion batch

### Requirement: Manifest-first artifact archival
The repository SHALL relocate a reproducible output only after a dated archive manifest identifies its owner, command, input hashes, output hashes, status, original location, canonical location, retention/disposition, and recovery method.  Unknown, data, model, unique evidence, and active-run material SHALL remain preserved.

#### Scenario: Same-purpose reproducible output is approved for archival
- **WHEN** an owner confirms that an inactive output is reproducible and redundant with another retained run
- **THEN** the implementation SHALL validate and retain its manifest before performing a reversible relocation

#### Scenario: Output provenance is incomplete
- **WHEN** an output lacks an owner, rebuild proof, active-run status, or required manifest data
- **THEN** the implementation SHALL classify it as `UNKNOWN` or preserved and SHALL NOT move or delete it

### Requirement: Cache disposal is narrow and active-run aware
The repository SHALL remove only confirmed idle paths classified as disposable cache: `__pycache__/`, `.pytest_cache/`, `.coverage`, `htmlcov/`, `.mypy_cache/`, and `.ruff_cache/`.  It SHALL record exact target paths and counts before disposal and SHALL not use a broad unresolved root as a deletion target.

#### Scenario: Confirmed idle cache is cleaned
- **WHEN** the active-run check confirms that a listed cache path is not in use
- **THEN** the implementation SHALL remove only that cache path and record the operation as a reversible cache-cleanup phase

#### Scenario: Cache may belong to an active run
- **WHEN** process or run-state evidence is ambiguous
- **THEN** the implementation SHALL defer cleanup and record the reason

### Requirement: Active OpenSpec artifacts are normally reviewable
The repository SHALL expose active OpenSpec proposal, design, spec, task, and evidence artifacts through normal `git status` without force-add while keeping only generated or archived OpenSpec state ignored.  Any conflict between installed CLI naming validation and project date-prefix policy SHALL be documented with an explicit resolution path.

#### Scenario: New active change is created
- **WHEN** a maintainer creates a new active OpenSpec change
- **THEN** its required artifacts SHALL appear in ordinary Git status and be reviewable without an ignore override

#### Scenario: CLI rejects the required date-prefix name
- **WHEN** the installed CLI rejects a change identifier that follows the project date-prefix policy
- **THEN** the implementation SHALL record the command result, chosen compatible identifier, creation date, and follow-up resolution rather than silently bypassing the policy
