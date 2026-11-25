# Ubermensch

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![SQLite FTS5](https://img.shields.io/badge/storage-SQLite%20FTS5-0f766e.svg)](ubermensch/store.py)
[![MCP stdio](https://img.shields.io/badge/tools-MCP%20stdio-475569.svg)](ubermensch/tools.py)
[![Desktop PySide6](https://img.shields.io/badge/desktop-PySide6%20%2B%20QML-64748b.svg)](ubermensch/desktop/__main__.py)

**Ubermensch** is a local-first personal assistant daemon written in Python for Linux (KDE Plasma / Wayland). It ingests messages, mail, calendar events, notes, and host telemetry into a transactional SQLite + FTS5 store, exposes capabilities to multiple LLM providers behind an approval and path policy boundary, and drives Telegram, a `PySide6` desktop overlay, a KDE Plasma widget, and an Obsidian sidebar plugin over a localhost HTTP + SSE API.

## Architecture

```text
  Telegram bot         PySide6 overlay       Obsidian plugin / KDE Plasmoid
       |                      |                             |
       +----------------------+-----------------------------+
                              |  HTTP + SSE (127.0.0.1, Bearer token)
                              v
                    ubermensch/api.py -> core.py
                       |         |           |
          +------------+         |           +------------+
          v                      v                        v
  collectors/*.py      runtime/router.py        store.py + recall.py
  (Telegram, Gmail,    (Claude Code CLI,        (SQLite WAL, migrations,
   Obsidian, Cal,       Claude API,              FTS5 Cyrillic recall,
   activity/KWin)       OpenAI fallback)         runs/calls/outbox)
                                 |
                                 v
                       runtime/execution.py + policy.py
                       (Pydantic registry, approvals,
                        idempotent call log, bwrap shell)
```

Long-running work runs through detached job runs (`/api/runs/*`) backed by persistent execution state in SQLite rather than holding HTTP request sockets open.

## Repository Layout

| Path | Responsibility |
|---|---|
| `ubermensch/core.py`, `api.py` | Daemon wiring, local HTTP endpoints, SSE event bus (`core.bus`) |
| `ubermensch/assistant.py`, `agent.py` | Prompt assembly, scheduled heartbeats, delegated tasks, evening reflection |
| `ubermensch/brain.py`, `claude_api.py`, `openai_brain.py` | Claude Code headless CLI, Anthropic API, and OpenAI API adapters with token cost tracking |
| `ubermensch/runtime/` | Model tier routing (`LIGHT`, `STANDARD`, `STRONG`), fallback guardrails, durable tool runs, allowlists |
| `ubermensch/capabilities/`, `tools.py` | Pydantic-validated tool registry (`ToolRegistry`) and stdio MCP server adapter |
| `ubermensch/collectors/` | Incremental ingestion from Telegram, Gmail, Calendar, Drive, Obsidian, browser history, and desktop activity |
| `ubermensch/store.py`, `recall.py` | SQLite migrations (`PRAGMA user_version`), domain tables, Cyrillic-aware FTS5 search |
| `ubermensch/desktop/` | `PySide6` desktop HUD, prompt dialog, task console, metrics window |
| `obsidian-plugin/`, `plasmoid/` | Zero-build CommonJS Obsidian plugin and KDE Plasma 6 QML widgets |
| `string_stops_gui/` | Standalone Python + ES5 JS + QML playable double-stop/chord analyzer for bowed strings |
| `tests/` | `unittest` suite covering runtime recovery, permissions, collectors, API contracts, and memory search |

## Key Design Decisions

- **No silent tool replay on failure.** Once a provider invocation dispatches a tool call (`ToolJournal` records dispatch), `runtime/router.py` forbids switching providers mid-turn. Resumable side-effecting calls (`RESUMABLE` in `runtime/execution.py`) gate before execution using a deterministic argument `fingerprint` and `call_id`.
- **Single-daemon ownership of secrets and SQLite.** Secrets stay in `$DATA_DIR/.env` outside the repository. LLM runtimes invoke capabilities via `HttpToolGateway` (`tools.py` -> `/api/tools/call`) instead of touching SQLite or filesystem secrets directly (`runtime/policy.py` blocks `.env*`, `.session`, keys, and `db/` reads).
- **Tiered model routing.** Background scanning uses `LIGHT` models and read-only helper contexts; high-stakes planning and user dialogue route to `STRONG` models with `memory_search` instead of stuffing full tables into every prompt.

## Setup and Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

For runtime configuration:

```bash
cp .env.example .env
./start.sh
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/API.md](docs/API.md), [docs/SECURITY.md](docs/SECURITY.md), and [docs/OPERATIONS.md](docs/OPERATIONS.md) for subsystem details.
