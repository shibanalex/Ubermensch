# Architecture

Ubermensch separates long-lived daemon state (`Core` in `ubermensch/core.py`) from stateless LLM adapters (`Brain`, `ClaudeApiBrain`, `OpenAIBrain`) and client UIs.

## Subsystems

1. **Core & Storage (`core.py`, `store.py`, `runtime/storage.py`)**
   - Single SQLite database managed through ordered schema migrations in `store.py` (`MIGRATIONS` tracked via `PRAGMA user_version`).
   - Core domain tables: `messages`, `facts`, `goals`, `reminders`, `actions`, `tasks`, `imports`, `events`, `cursors`.
   - Runtime reliability tables: durable job runs, idempotent tool calls (`call_id` + SHA-256 `fingerprint`), approval states, delivery outbox, and assistant message feedback.
   - `recall.py` provides FTS5 indexing with Cyrillic stemming so prompts pull relevant historical facts instead of full dumps.

2. **Model Routing & Execution (`runtime/router.py`, `runtime/execution.py`, `runtime/models.py`)**
   - Tasks select a capability tier (`LIGHT`, `STANDARD`, `STRONG`) independent of foreground vs. background execution.
   - `HttpToolGateway` (`runtime/http_tools.py`) bridges the stdio MCP server (`ubermensch/tools.py`) and direct API tool loops back into Core's `/api/tools/call` handler.
   - If the primary provider fails before invoking any tool, `router.py` falls back across configured backends (`Claude Code CLI -> Claude API -> OpenAI API`). Once `ToolJournal` marks a tool call as dispatched, fallback is blocked so partially executed side effects cannot be duplicated by a second model.

3. **Collectors (`collectors/`)**
   - Incremental watermark tracking via `cursors` table (`external_id` deduplication on `(source, external_id)`).
   - Sources: dual Telegram accounts (`Telethon`), multiple Gmail accounts (IMAP), Google Calendar/Drive, Obsidian Markdown vaults, Chrome history, KWin Wayland active-window tracker (`system/kwin_tracker.js`), and university portal sessions (`university/lk.py`).

4. **Desktop & Clients**
   - `ubermensch/desktop/`: `PySide6` supervisor and HUD (`mascot_app.py`, `console.py`, `answer.py`, `metrics.py`, `planning.py`).
   - `obsidian-plugin/ubermensch-anya/`: CommonJS plugin polling `/api/dashboard` and SSE `/api/events`.
   - `plasmoid/`: KDE Plasma 6 QML widgets communicating via `desktop/link.py`.
