# Dashboard payload boundary evidence (2026-10-07)

## Current cohesive domains

`app/dashboard_payloads.py` contains six recognizable domains: market/chip
health snapshots, macro payloads, war-room views, JSON serialization, MCP
overlay, and local fallback. The public builders are re-exported by
`app/__init__.py` and called by `app/web_server.py` and LINE flows.

## Characterization coverage

The existing contract tests exercise:

- MCP success, unavailable/timeout, cached, and local-fallback health payloads;
- requested-date fallback and `partial`/`degraded` status selection;
- degraded MCP fields and warning text;
- macro hotspot overlay and deterministic JSON NaN sanitization;
- complete war-room panes, provenance, serialization, and HTTP response shape.

The deterministic tests patch the application composition seam on `app`, so
the current `_ApplicationServices` proxy must remain in place while these
functions are housed in one module.

## Decision

**DEFER extraction.** The domains are conceptually cohesive, but the current
payload builder still shares local helpers and external news/MCP fallback
behavior. Moving functions into multiple modules now would require a larger
compatibility facade and would make the external-service timeout case harder to
characterize. No payload or HTTP contract changed in this phase. Revisit after
an isolated fixture-backed normalized-payload baseline exists for all external
paths.

## Commands and results

```text
python -m compileall -q app                 PASS
python -m pytest -q --timeout=10 test/test_dashboard_health_check_api.py
```

The deterministic health cases pass. One macro fallback case can exceed the
10-second guard when the external news/MCP service is absent; this is recorded
as an environmental limitation, not a passing full-suite result.
