#!/usr/bin/env bash
# Start background core daemon (API + Telegram) and desktop overlay.
# Uses systemd user units if installed via scripts/install_autostart.sh.
# Повторный запуск безопасен: каждый процесс сам не даст запустить второй экземпляр.
cd "$(dirname "$0")"
if systemctl --user cat ubermensch.service >/dev/null 2>&1; then
    exec systemctl --user start ubermensch.service ubermensch-desktop.service
fi
nohup .venv/bin/python -m ubermensch >/dev/null 2>&1 &
nohup .venv/bin/python -m ubermensch.desktop >/dev/null 2>&1 &
