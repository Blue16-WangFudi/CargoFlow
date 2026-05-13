# CargoFlow

CargoFlow is the working repository for the CargoFlow smart logistics product. The first delivery phase focuses on shipment visibility, vehicle and cargo binding, alert handling, dispatch commands, driver status reporting, and owner-facing logistics Q&A.

## Current Architecture Decision

The product implementation will use a conservative web stack that keeps local development reproducible while leaving room for MQTT, map, and retrieval integrations required by the PRD.

| Layer | Decision | Notes |
| --- | --- | --- |
| Web frontend | React + TypeScript with Vite | Role-specific workspaces for cargo owners, warehouse admins, dispatchers, drivers, and system admins. Follow `DESIGN.md` before UI work. |
| Backend API | Python FastAPI | HTTP APIs, role guards, OpenAPI contract, background jobs, and integration adapters. |
| Database | PostgreSQL | System of record for cargo, vehicles, transport tasks, positions, alerts, dispatch commands, status reports, notifications, audit logs, and Q&A records. |
| Migrations | Alembic | Every persisted model change ships with a migration. |
| Async jobs | Redis + Celery | ETA refresh, delayed alert checks, notification fanout, and RAG indexing jobs. |
| MQTT ingestion | EMQX in non-production, managed MQTT in production | Vehicle GPS, heartbeat, box-open events, and dispatch command delivery use explicit topic contracts. |
| Maps | Provider adapter interface | Default development provider is deterministic offline geometry; production can plug in AMap, Baidu, or another approved provider. |
| RAG | pgvector-backed retrieval behind an interface | Phase 1 can ingest curated logistics knowledge and authorization-scoped business context. |
| Deployment | Docker Compose for local and staging baseline | Container boundaries mirror frontend, API, worker, PostgreSQL, Redis, and MQTT broker. |

Details and constraints are tracked in [docs/exec-plans/2026-05-13-tech-architecture-constraints.md](docs/exec-plans/2026-05-13-tech-architecture-constraints.md).

## Planned Repository Layout

```text
apps/
  api/          API service skeleton
  web/          Frontend console skeleton
packages/
  contracts/    Shared OpenAPI/types generated from backend contracts
infra/
  compose/      Local Docker Compose files
docs/
  exec-plans/   Durable execution plans and architecture decisions
scripts/
  start.sh      Starts the current local API and frontend
  check.sh      Repository quality gate
```

## Local Development Contract

Start the current minimal API and frontend without installing dependencies:

```bash
scripts/start.sh
```

Default URLs:

- API health: http://127.0.0.1:8000/health
- Demo shipment API: http://127.0.0.1:8000/api/shipments/demo
- Frontend console: http://127.0.0.1:5173

Ports can be changed with environment variables:

```bash
API_PORT=8010 FRONTEND_PORT=5180 scripts/start.sh
```

The Docker Compose baseline mirrors the planned service boundary and runs the
same skeleton services:

```bash
docker compose -f infra/compose/dev.yml up --build
```

Run the local quality gate:

```bash
scripts/check.sh
```

The first skeleton intentionally uses Python standard-library servers so a new
contributor can verify the flow before FastAPI, React/Vite, PostgreSQL, Redis,
and MQTT dependencies are introduced. Future product slices should replace the
temporary internals with the architecture choices above while preserving the
startup and check commands.

## API Surface

- `GET /health` returns service status and version metadata.
- `GET /api/shipments/demo` returns a demo shipment snapshot for frontend and
  integration smoke checks.

`scripts/check.sh` validates architecture references, Markdown links, script
syntax, required skeleton files, Python unit and HTTP smoke tests, frontend
asset wiring, conflict markers, and common accidental secret patterns.

## Task Source

Work is tracked in the CargoFlow Feishu Base task table. Do not rely on chat history as the task source when selecting or updating execution work.
