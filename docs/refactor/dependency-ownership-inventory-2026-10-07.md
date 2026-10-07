# Dependency ownership inventory (2026-10-07)

## Service manifests

| Manifest | Owner | Direct consumer | Decision |
|---|---|---|---|
| `requirements.runtime.txt` | `stock_bot` Compose service | `app/`, `core/` runtime imports, Web, LINE, report/news helpers | KEEP; independently installable |
| `requirements.mcp.txt` | `twse_mcp_server` Compose service | `services/mcp/`, MCP crawlers and HTTP transport | KEEP; independently installable |
| `requirements.txt` | local development/research/test environment | research jobs, notebooks/tooling, test plugins, and runtime superset | KEEP for this change; treat as a broad frozen environment, not a service manifest |

Compose uses Python 3.11 images and installs only the service manifests. No
runtime manifest was broadened or made dependent on the developer environment.

## Ownership map

| Package family | Evidence checked | Classification |
|---|---|---|
| Flask, Flask-Login, LINE SDK | `app/`, `app.py`, Web/LINE tests | KEEP/runtime |
| pandas, numpy, SQLAlchemy, PyMySQL | `core/`, `jobs/`, persistence and strategy tests | KEEP/runtime/research |
| httpx, requests, BeautifulSoup, lxml, feedparser | MCP client/server, crawlers, news agent, service manifests | KEEP/runtime/MCP |
| google-genai, joblib, plotly, pydantic | report/news/model/visualization imports and tests | KEEP/runtime optional paths |
| pytest, coverage, black, flake8, mypy, nbqa | test and developer commands/configuration | KEEP/development |
| backtrader, alphalens, empyrical, finlab, finmind | research jobs, notebooks, or historical operator workflows | UNKNOWN; retain |
| torch, stable-baselines3, gym/gymnasium, selenium, seaborn, twstock | no sufficient single static-import proof across all supported surfaces | UNKNOWN; retain |

The broad freeze contains 196 entries, including transitive packages. A full
package-by-package direct/transitive split was not safe to infer from static
imports alone. Dynamic import strings, notebooks, CLI tools, and operator
workflows remain part of the evidence gate.

## Removal gate

No package is `SAFE_TO_REMOVE` in this change. Before any removal, update
[`docs/refactor/deletion_candidates.md`](deletion_candidates.md) with static and
dynamic references, scripts, tests, docs/deployment consumers, replacement,
rollback, and post-install/collection/smoke results. `UNKNOWN` defaults to
KEEP. A future direct development/research manifest can be introduced only
after that inventory is complete; service manifest ownership must not change.

## Checks

```text
docker-compose.yaml inspection: Python 3.11, requirements.runtime.txt and requirements.mcp.txt PASS
requirements.runtime.txt / requirements.mcp.txt clean-checkout ownership: PASS by Compose configuration
No dependency removal performed: PASS
```
