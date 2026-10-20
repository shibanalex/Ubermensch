#!/usr/bin/env bash
# Автозапуск Übermensch при входе в KDE Plasma — сервисы systemd пользователя:
#   ubermensch          — ядро (API, Telegram, сбор и разбор, сенсоры сеанса)
#   ubermensch-desktop  — маскот и лист задач
# Упавший сервис перезапускается. Логи: journalctl --user -u ubermensch -f
# Удалить: systemctl --user disable --now ubermensch ubermensch-desktop
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units"

cat > "$units/ubermensch.service" <<UNIT
[Unit]
Description=Übermensch: ядро Ани
PartOf=graphical-session.target
After=graphical-session.target network-online.target

[Service]
WorkingDirectory=$project
ExecStart=$project/.venv/bin/python -m ubermensch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
UNIT

cat > "$units/ubermensch-desktop.service" <<UNIT
[Unit]
Description=Übermensch: маскот и лист задач
PartOf=graphical-session.target
After=graphical-session.target ubermensch.service

[Service]
WorkingDirectory=$project
ExecStart=$project/.venv/bin/python -m ubermensch.desktop
# «Выход» в меню маскота — код 0: не поднимать заново до следующего входа
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT

# Ядро — сервер: от сети закрытая крышка и простой не усыпляют ноутбук (от батареи — как обычно)
kwriteconfig6 --file powerdevilrc --group AC --group SuspendAndShutdown --key LidAction 0
kwriteconfig6 --file powerdevilrc --group AC --group SuspendAndShutdown --key AutoSuspendAction 0
qdbus6 org.kde.Solid.PowerManagement /org/kde/Solid/PowerManagement \
    org.kde.Solid.PowerManagement.reparseConfiguration >/dev/null 2>&1 || true

systemctl --user daemon-reload
systemctl --user enable ubermensch.service ubermensch-desktop.service
echo "Автозапуск установлен. Запустить сейчас: ./start.sh"
