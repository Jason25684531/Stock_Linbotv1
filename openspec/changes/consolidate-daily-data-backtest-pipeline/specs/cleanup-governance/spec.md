## ADDED Requirements

### Requirement: Cleanup inventory shall classify files and flows before removal

The system governance process SHALL classify duplicate or ambiguous files and flows as active, legacy, or removable candidates before any deletion.

#### Scenario: Cleanup review begins
- **WHEN** maintainers review duplicate scripts, launchers, or pipeline flows
- **THEN** each item SHALL be recorded in a cleanup inventory with a classification

### Requirement: Files shall not be deleted without evidence

No file or flow SHALL be deleted until evidence shows it is no longer required.

#### Scenario: A file is considered removable
- **WHEN** maintainers propose removing a file or flow
- **THEN** they SHALL verify there are no imports, no docs references, no compose references, no scheduler references, and no test dependencies that still require it

### Requirement: Legacy flows shall be deprecated before removal when needed

Compatibility paths that still serve real workflows SHALL be deprecated before removal rather than deleted abruptly.

#### Scenario: A legacy launcher is still user-visible
- **WHEN** a compatibility launcher or wrapper is still documented or used operationally
- **THEN** it SHALL first be marked deprecated
- **AND** docs SHALL redirect operators to the official path before removal

### Requirement: Docs and tests shall point to official paths before deletion

Removal SHALL happen only after operator guidance and guardrail tests already align with the official path.

#### Scenario: Official path becomes the only supported path
- **WHEN** maintainers prepare to remove a legacy flow
- **THEN** docs SHALL already point to the official path
- **AND** tests SHALL protect the official path before deletion proceeds

### Requirement: Removal requires multi-surface verification

Deletion approval SHALL require verifying the candidate has no remaining dependency across code, docs, compose, scheduler, tests, or OpenSpec.

#### Scenario: Final deletion review
- **WHEN** a removable candidate reaches final review
- **THEN** the review SHALL confirm no imports, no CLI references, no README or docs references, no docker-compose references, no scheduler dependency, no test dependency, and no OpenSpec reference remain
