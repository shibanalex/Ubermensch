# Übermensch — Local-First Autonomous Personal AI Agent

**Übermensch** is a persistent, local-first personal AI system built in Python for Linux (KDE Plasma / Wayland). It runs as a background `systemd` daemon, connects personal communication channels and developer tools via the **Model Context Protocol (MCP)**, maintains searchable long-term memory in SQLite (FTS5), and provides a desktop HUD mascot (`PySide6`), an **Obsidian** plugin, and a **KDE Plasma** QML widget.

Also included in `string_stops_gui/`: a standalone **Python + QML** double-stop / chord fingering solver for bowed string instruments (violin, viola, cello).

## Architecture Highlights

- **Multi-Brain Routing & Cost Control (`ubermensch/brain.py`, `claude_api.py`, `openai_brain.py`):** Tiered task routing (`light` / `standard` / `strong`), structured schema outputs (`Pydantic v2`), automatic fallback on quota/network failures, and compact context assembly (~50% token reduction via targeted `memory_search`).
- **Event Collectors & Background Analysis (`observer.py`, `analysis.py`):** Incremental ingestion from Telegram (`Telethon` / `aiogram`), IMAP/SMTP email, Google Calendar, Google Drive, Obsidian vaults, and university portals, processed asynchronously in batched background jobs.
- **Long-Term Memory (`store.py`, `capabilities/memory.py`):** Transactional SQLite storage with Cyrillic-aware stemming and FTS5 search across facts, people, goals, reminders, and conversation history.
- **Autonomous Loop (`agent.py`, `goal_execution.py`):** Scheduled briefings, evening reflection, reminder dispatch, task delegation, and career pipeline automation (`Playwright` + email/Telegram outreach).
- **Policy & Safety Boundary (`runtime/policy.py`):** Explicit capability registry, shell sandboxing, PII filtering before external model calls, and human-in-the-loop confirmation for sensitive actions.
- **Desktop & Ecosystem Surfaces:**
  - `ubermensch/desktop/` — `PySide6` desktop overlay & metrics console
  - `obsidian-plugin/` — live Obsidian sidebar panel
  - `plasmoid/org.ubermensch.hud/` — native KDE Plasma 6 widget (QML)
  - `string_stops_gui/` — Python + QML bowed-string fingering & chord playability checker

## Tech Stack

`Python 3.12+` · `asyncio` · `SQLite (FTS5)` · `MCP` · `Pydantic v2` · `aiogram` · `Telethon` · `Playwright` · `PySide6` · `QML`

## Running Tests

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

## Configuration

Copy `.env.example` to your data directory `.env` and fill in the required credentials. Secrets, databases, and personal logs live outside the code repository (`DATA_DIR`).
