# 2026-10-14 Phase 2

Two small calculation functions live in the existing app/dashboard_payloads.py. Both Dashboard snapshots and LINE messages use them. Per-record int conversion, safe_float, NaN/missing handling, volume units and order are retained. Data retrieval, dates, errors, TTL/day keying, API schemas and message formatting remain with the original owners; web_server.py is unchanged from the initial dirty-tree checksum.

Validation: 102 passed, 1 existing monthly-resample warning in 42.55s. Includes 12 exact precleanup golden caller outputs and the expiration/day-boundary test, plus Rich Menu, Dashboard health/prewarm, Web platform, LINE interactions and dashboard/command characterization. Golden cache hit checks confirm the same keys and two independent fetches, with no extra fetch on hits. Source diff: 19 insertions, 20 deletions (net -1 line); no new package, class, dependency or cache layer.

Phase 1 commit: fcdf545. Phase 2 commit and reverse patch checks are recorded during final acceptance.
