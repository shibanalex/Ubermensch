# Operations

## Requirements

- Linux desktop session (tested on Arch Linux and Ubuntu 26.04 with KDE Plasma 6 / Wayland)
- Python 3.12+
- Optional system tools: `claude` CLI (if using Claude Code subscription), `bwrap` (`bubblewrap` for shell sandboxing), `7z` (encrypted backups), `pdftoppm` / `libreoffice` (document rendering)

## Initial Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Configure environment variables outside the repository (`DATA_DIR/.env`, or `.env` next to checkout for local testing):

   ```bash
   cp .env.example ~/Projects/Ubermensch/.env
   ```

   Minimum required values: `TG_BOT_TOKEN`, `TG_CHAT_ID`, plus at least one LLM credential (Claude Code CLI session, `CLAUDE_AI_API_KEY`, or `OPENAI_API_KEY`).

3. Bootstrap external sessions once interactively:

   ```bash
   ./telegram_login.sh        # Telethon personal session
   ./telegram_work_login.sh   # Optional work account
   ./google_login.sh          # Google Calendar / Drive OAuth token
   ./lk_login.sh              # Optional university portal cookies
   ```

## Running as systemd User Services

Install user units (`ubermensch.service` and `ubermensch-desktop.service`):

```bash
./scripts/install_autostart.sh
systemctl --user status ubermensch.service
journalctl --user -u ubermensch.service -f
```

Optional integrations:

```bash
./scripts/install_obsidian_plugin.sh
./scripts/install_widget.sh
```

## Backups and Maintenance

- Nightly backups (`ubermensch/backups.py`) write encrypted 7-Zip archives containing a `manifest`, consistent SQLite snapshot, and configuration files using `BACKUP_PASSWORD`.
- Run tests after any change:

  ```bash
  .venv/bin/python -m unittest discover -s tests
  ```
