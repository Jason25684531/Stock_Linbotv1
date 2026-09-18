# Container Dependency Inventory — 2026-09-18

`requirements.txt` remains the broad development/research environment. The
two Compose services use direct, service-scoped manifests so a container does
not install research-only packages merely because the repository has them.

| Service | Manifest | Direct import families covered |
|---|---|---|
| MCP | `requirements.mcp.txt` | Flask, pandas/numpy, requests/httpx, SQLAlchemy/PyMySQL, BeautifulSoup/lxml |
| Application | `requirements.runtime.txt` | Flask/Flask-Login, LINE SDK, pandas/numpy, SQLAlchemy/PyMySQL, MCP HTTP, Plotly, model/news helpers |

The manifests are intentionally explicit and pinned.

## Verification on 2026-09-18

- `docker compose config --quiet`: PASS with temporary non-secret validation
  environment values.
- `docker compose up --build -d db twse_mcp_server stock_bot`: PASS.
- `docker compose ps`: `db`, `twse_mcp_server`, and `stock_bot` all reported
  `healthy`.
- MCP container `/health`: HTTP 200.
- Application host endpoint `http://localhost:1688/health`: HTTP 200.
- The verification used temporary environment values and did not modify the
  tracked `.env` file. Existing named volumes were retained.
