#!/usr/bin/env bash
# Запуск в фоне: ядро (API + Telegram) и приложение с маскотом.
# Если установлен автозапуск (scripts/install_autostart.sh) — через systemd, иначе напрямую.
# Повторный запуск безопасен: каждый процесс сам не даст запустить второй экземпляр.
cd "$(dirname "$0")"
if systemctl --user cat ubermensch.service >/dev/null 2>&1; then
    exec systemctl --user start ubermensch.service ubermensch-desktop.service
fi
nohup .venv/bin/python -m ubermensch >/dev/null 2>&1 &
nohup .venv/bin/python -m ubermensch.desktop >/dev/null 2>&1 &
