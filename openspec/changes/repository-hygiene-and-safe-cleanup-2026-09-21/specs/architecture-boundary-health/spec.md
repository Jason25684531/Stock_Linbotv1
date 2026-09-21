## ADDED Requirements

### Requirement: Architecture health report is evidence based
The repository SHALL maintain a dated architecture-boundary health report based on current working-tree evidence.  The report SHALL identify canonical runtime ownership, dependency direction, module concentration, compatibility surfaces, verification evidence, confirmed strengths, and unresolved risks without treating historical documentation as current proof.

#### Scenario: Health assessment completes
- **WHEN** maintainers assess the repository architecture
- **THEN** the report SHALL cite the inspected runtime entrypoints, imports, Compose/scheduler wiring, tests, and compatibility references for every conclusion

### Requirement: Boundary risks remain separately scoped from cleanup
The repository SHALL record any cross-layer or package-namespace dependency risk and SHALL NOT alter that boundary as an incidental cleanup change.  A behavior-preserving refactor requires a separate approved change with characterization evidence for affected CLI, Web, LINE Bot, scheduler, and backtest contracts.

#### Scenario: A boundary risk is found during hygiene work
- **WHEN** the health report finds a coupling pattern that conflicts with an existing architecture requirement
- **THEN** the hygiene change SHALL record the evidence, risk, and smallest proposed follow-up scope without changing runtime code

#### Scenario: A boundary refactor is proposed
- **WHEN** maintainers choose to address a recorded boundary risk
- **THEN** the follow-up change SHALL establish and pass characterization baselines before modifying the affected boundary

### Requirement: Cleanup preserves public compatibility
The repository SHALL preserve `4_run_backtest.py`, `5_push_to_line.py`, `app.py`, `config.py`, legacy strategy aliases, the Web and LINE contracts, and `jobs/scheduler.py` unless a future deletion change independently passes the complete no-reference and compatibility gates.

#### Scenario: A compatibility facade appears redundant
- **WHEN** a facade delegates to a canonical module
- **THEN** the hygiene change SHALL classify it as `LEGACY_COMPATIBILITY` unless a separate approved deletion change presents complete current evidence and recovery guidance
