#!/usr/bin/env bash
# Autostart Übermensch on KDE Plasma login via user systemd units:
#   ubermensch          : core daemon (API, Telegram, ingestion, session sensors)
#   ubermensch-desktop  : desktop overlay and task console
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
# Tray Exit exits with code 0; do not auto-restart until next login
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT

# Prevent suspend on AC power while core daemon runs (normal sleep on battery)
kwriteconfig6 --file powerdevilrc --group AC --group SuspendAndShutdown --key LidAction 0
kwriteconfig6 --file powerdevilrc --group AC --group SuspendAndShutdown --key AutoSuspendAction 0
systemctl --user restart plasma-powerdevil.service || true  # reparseConfiguration файл не перечитывает
# logind по умолчанию сам усыпляет по крышке, мимо KDE; разрешить ему не делать этого от сети
# может только root (см. docs/OPERATIONS.md):
#   /etc/systemd/logind.conf.d/ubermensch-lid.conf: HandleLidSwitchExternalPower=ignore

systemctl --user daemon-reload
systemctl --user enable ubermensch.service ubermensch-desktop.service
echo "Автозапуск установлен. Запустить сейчас: ./start.sh"
