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
| RAG | pgvector-backed retrieval behind an interface | Phase 1 ingests curated logistics knowledge, returns citations, and keeps authorization-scoped business context outside the shared index. See [docs/knowledge/qa-knowledge-scope-and-citations.md](docs/knowledge/qa-knowledge-scope-and-citations.md). |
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
- Demo latest location API: http://127.0.0.1:8000/api/shipments/CGF-DEMO-001/latest-location
- Demo ETA API: http://127.0.0.1:8000/api/shipments/CGF-DEMO-001/eta
- Demo trajectory replay API: http://127.0.0.1:8000/api/shipments/CGF-DEMO-001/trajectory
- Demo dispatcher vehicle distribution API: http://127.0.0.1:8000/api/dispatch/vehicle-distribution
- Demo Q&A ask API: http://127.0.0.1:8000/api/qa/ask
- Demo driver tasks API: http://127.0.0.1:8000/api/driver/tasks
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
- `GET /api/shipments/demo` returns a guarded demo shipment snapshot for
  frontend and integration smoke checks. Until a real identity provider is
  wired, local requests must send `X-CargoFlow-User-Id`, `X-CargoFlow-Role`,
  and `X-CargoFlow-Tenant-Id`; warehouse admins and dispatchers also send
  `X-CargoFlow-Warehouse-Ids` or `X-CargoFlow-Dispatch-Region-Ids`.
- `GET /api/shipments/{shipmentId}/latest-location` returns the bound cargo's
  latest accepted GPS location, update time, transport status, vehicle summary,
  and delay hint. The same development identity headers apply; cargo owners can
  only read their own shipments.
- `GET /api/shipments/{shipmentId}/eta` returns the transport task ETA,
  remaining distance, update time, destination, and unavailable reasons when
  current location or destination data is missing. The current development
  implementation uses the offline straight-line `EtaService` described in the
  architecture constraints.
- `GET /api/shipments/{shipmentId}/trajectory` returns replay-ready route
  points ordered by event time, including accepted GPS points, planned start
  and destination, alert points, and driver status report nodes. Key nodes are
  preserved and the current development response is not simplified.
- `POST /api/shipments/{shipmentId}/sign` lets the cargo owner sign for a
  shipment after the driver reports it delivered, moving the current
  development tracking status to `signed`.
- `GET /api/vehicles`, `GET /api/vehicles/{vehicleId}`,
  `POST /api/vehicles`, `PATCH /api/vehicles/{vehicleId}`,
  `POST /api/vehicles/{vehicleId}/disable`, and
  `POST /api/vehicles/{vehicleId}/unbind` provide the current development
  vehicle management contract. Warehouse admins and system admins can maintain
  vehicles; warehouse admins are scoped by `X-CargoFlow-Warehouse-Ids`.
  Vehicle number, plate number, and device ID uniqueness is enforced.
- `POST /api/cargo-bindings` lets a scoped warehouse admin or system admin bind
  cargo to an available vehicle. The binding creates or updates the active
  transport task, marks the vehicle as bound, registers the device-task binding,
  and makes later device locations visible through the shipment tracking APIs.
- `POST /api/device-events` accepts the current development contract for
  device GPS, heartbeat, and box-open/box-close events. Payloads must include
  `eventId`, `eventType`, `deviceId`, `taskId`, `occurredAt`, `reportedAt`, and
  `schemaVersion`; GPS events may update latest location only when coordinates,
  capture time, and the active device-task binding are valid. Accepted GPS and
  box-open events can return `generatedAlerts` when the current alert rules
  create or merge route-deviation, abnormal-stop, or box-open alerts.
- `GET /api/alerts`, `GET /api/alerts/{alertId}`,
  `POST /api/alerts/{alertId}/process`,
  `POST /api/alerts/{alertId}/dispatch-commands`,
  `POST /api/alerts/{alertId}/close`, and
  `POST /api/alerts/{alertId}/false-positive` provide the current development
  alert handling contract. Scoped dispatchers and system admins can inspect alert
  detail with notification and command chains, move open alerts into processing,
  create dispatch commands, close them with a required reason, or mark them as
  false positives; handler, close actor, timestamps, command status, and reason
  are returned for audit.
- `GET /api/alert-logs` and `GET /api/alert-logs/export` provide the current
  system-admin alert log contract. The endpoints support `type`, `severity`,
  `status`, `vehicleId`, `cargoId`, `triggeredFrom`, and `triggeredTo` filters
  and return each alert's notification and dispatch-command chain.
- `GET /api/dispatch/vehicle-distribution` provides the current dispatcher
  vehicle map contract. Scoped dispatchers and system admins can read vehicle
  points, online and transport states, bound cargo context, and active alert
  summaries; the development endpoint supports `status=online`,
  `status=in_transit`, and `status=alert` filters.
- `POST /api/qa/ask`, `GET /api/qa/records`,
  `GET /api/qa/records/{recordId}`, and
  `POST /api/qa/records/{recordId}/feedback` provide the current intelligent
  Q&A contract. The development service records every question, answer,
  citation, session, authorization summary, and feedback value; answers are
  deterministic until a model-backed retrieval layer is wired. Business-context
  answers first pass through the role-scoped Q&A context filter, and unanswered
  or unauthorized requests are recorded with a failure reason.
- `GET /api/driver/tasks`,
  `POST /api/driver/commands/{commandId}/acknowledge`, and
  `POST /api/driver/tasks/{taskId}/status-reports` provide the current driver
  workspace contract. Drivers can only read their own active transport tasks,
  confirm commands targeted to them, and submit forward-only `loaded`,
  `in_transit`, or `delivered` status reports with notes and optional
  attachment URLs.
- Future intelligent Q&A APIs must follow the knowledge source, citation,
  refusal, and privacy boundaries in
  [docs/knowledge/qa-knowledge-scope-and-citations.md](docs/knowledge/qa-knowledge-scope-and-citations.md).
  The current backend includes an authorization-scoped business context filter
  for Q&A retrieval; cargo, vehicle, transport task, and alert candidates must
  pass that filter before they are sent to any retrieval or generation layer.

## Core Domain Contracts

The initial domain model lives in `apps/api/cargoflow_api/domain/` and covers
cargo, vehicles, transport tasks, location points, alerts, dispatch commands,
driver status reports, and Q&A records. The matching PostgreSQL persistence
contract is the Alembic revision under `apps/api/migrations/versions/`.

`scripts/check.sh` validates architecture references, Markdown links, script
syntax, required skeleton files, Python unit and HTTP smoke tests, frontend
asset wiring, conflict markers, and common accidental secret patterns.

## Task Source

Work is tracked in the CargoFlow Feishu Base task table. Do not rely on chat history as the task source when selecting or updating execution work.
