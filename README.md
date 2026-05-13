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
  api/          FastAPI service
  web/          React/Vite application
packages/
  contracts/    Shared OpenAPI/types generated from backend contracts
infra/
  compose/      Local Docker Compose files
docs/
  exec-plans/   Durable execution plans and architecture decisions
scripts/
  check.sh      Repository quality gate
```

## Local Development Contract

The first product-code slice should provide these commands:

```bash
docker compose -f infra/compose/dev.yml up --build
scripts/check.sh
```

Until product code is added, `scripts/check.sh` validates the repository documentation, links, conflict markers, and architecture references. Future implementation slices must extend it with backend tests, frontend tests, type checks, and builds instead of replacing it.

## Task Source

Work is tracked in the CargoFlow Feishu Base task table. Do not rely on chat history as the task source when selecting or updating execution work.
