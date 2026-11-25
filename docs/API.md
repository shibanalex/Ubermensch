# Local HTTP API

`ubermensch/api.py` runs an `aiohttp` server bound to loopback (`127.0.0.1`) with constant-time `Authorization: Bearer <token>` middleware (`hmac.compare_digest`). No CORS headers are exposed, preventing arbitrary browser tabs from calling local endpoints.

All payloads are validated via Pydantic v2 models in `ubermensch/contracts.py`; validation failures return HTTP `400` with `code: "validation_error"` and offending field paths.

## Core Endpoints

| Method & Path | Purpose |
|---|---|
| `GET /api/state` | Snapshot of active state, metrics, pending approvals, and tasks |
| `GET /api/dashboard` | Aggregated Obsidian sidebar payload (goals, plans, experiments, career) |
| `GET /api/planning` | Goal snapshots and weekly review summary |
| `GET /api/events` | Server-Sent Events (`text/event-stream`) stream from `core.bus` with 15s keepalive |
| `GET /api/health` | Component health issues and supervised service status |
| `GET /api/metrics`, `/api/profile`, `/api/agent` | Behavioral metrics, profiler status, and autonomy schedule state |

## Async Jobs & Tool Runtime

| Method & Path | Purpose |
|---|---|
| `POST /api/runs/chat`, `POST /api/runs/onboard` | Submit async job run with client-supplied UUID `run_id` (`202 Accepted`) |
| `GET /api/runs`, `GET /api/runs/{id}` | Inspect queued/running/terminal job states |
| `POST /api/runs/{id}/cancel` | Cancel an active job run |
| `GET /api/tools` | Return JSON schemas of registered capabilities |
| `POST /api/tools/call` | Execute a tool through `Execution` (`run_id`, `call_id`, approval gating) |
| `GET /api/tool-calls/{id}` | Inspect call status (arguments and SHA-256 fingerprint stripped) |
| `POST /api/approval` | Block (up to 10m) waiting for user confirmation |

## Tasks, Approvals, and Actions

| Method & Path | Purpose |
|---|---|
| `GET /api/tasks`, `POST /api/tasks` | List open/completed-today tasks or create a task |
| `POST /api/tasks/{id}`, `POST /api/tasks/{id}/edit` | Update task status (`open`, `done`, `dropped`) or patch fields |
| `POST /api/actions/{id}` | Approve or reject a pending external action |
| `POST /api/deliveries/{id}/ack` | Acknowledge outbox message delivery |
| `POST /api/feedback` | Record rating/feedback tied to an assistant message or `run_id` |
| `POST /api/experiments/{id}/decide`, `POST /api/goals/{id}/plan`, `POST /api/options/{id}/status` | Commit user decisions on experiments, goal plans, and options |

## Background Triggers & Proxied Tools

- `POST /api/collect`, `POST /api/voice`, `POST /api/import/deep`, `POST /api/profile/rebuild`, `POST /api/agent/heartbeat` enqueue background runs (`202 Accepted`).
- `POST /api/tg/*`, `POST /api/email/*`, `POST /api/lk/*` expose audited Telegram, email, and university portal adapters to the tool gateway.
