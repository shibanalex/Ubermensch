#!/usr/bin/env bash
# Виджет «Задачи Ани» для KDE Plasma: ставит (или обновляет) плазмоид и, если его ещё нет,
# кладёт на рабочий стол справа сверху. Повторный запуск обновляет виджет.
#   --no-desktop   только установить, на рабочий стол не добавлять
# Удалить: kpackagetool6 -t Plasma/Applet -r org.ubermensch.tasks
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
plugin="org.ubermensch.tasks"

env_value() {  # значение из .env, иначе $2
    local value
    value="$(grep -E "^$1=" "$project/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    echo "${value:-$2}"
}
data_dir="$(env_value UBERMENSCH_DATA "$(dirname "$project")/Ubermensch")"
data_dir="${data_dir/#\~/$HOME}"
port="$(env_value API_PORT 8765)"

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
cp -r "$project/plasmoid/$plugin" "$build/"
link="cd '$project' \&\& .venv/bin/python -m ubermensch.desktop.link"
sed -i -e "s|@API@|http://127.0.0.1:$port|" -e "s|@TOKEN_FILE@|$data_dir/secrets/api_token|" \
    -e "s|@LINK@|$link|" "$build/$plugin/contents/ui/main.qml"

if kpackagetool6 -t Plasma/Applet -s "$plugin" >/dev/null 2>&1; then
    kpackagetool6 -t Plasma/Applet -u "$build/$plugin"
    echo "Виджет обновлён. Чтобы Plasma подхватила новую версию: plasmashell --replace & disown"
else
    kpackagetool6 -t Plasma/Applet -i "$build/$plugin"
fi

if [[ "${1:-}" != "--no-desktop" ]]; then
    qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "
        var desks = desktops(), present = false;
        for (var i = 0; i < desks.length; i++)
            if (desks[i].widgets('$plugin').length) present = true;
        if (!present) {
            var g = screenGeometry(desks[0].screen);
            desks[0].addWidget('$plugin', g.width - 380, 60, 340, 460);
        }" >/dev/null
    echo "Виджет на рабочем столе (если его там не было — добавлен справа сверху)."
fi
