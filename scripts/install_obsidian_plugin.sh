#!/usr/bin/env bash
# Плагин «Аня» для Obsidian: копирует obsidian-plugin/ubermensch-anya в хранилище из
# OBSIDIAN_VAULT (.env) и включает его. Повторный запуск обновляет плагин.
# After installation: run "Reload app without saving" in Obsidian or restart.
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
# Target vault path matching config.VAULT_DIR (OBSIDIAN_VAULT in .env)
vault="$(cd "$project" && .venv/bin/python -c 'from ubermensch import config; print(config.VAULT_DIR or "")')"
[[ -n "$vault" && -d "$vault/.obsidian" ]] || { echo "Не найдено хранилище Obsidian (OBSIDIAN_VAULT в .env)"; exit 1; }
target="$vault/.obsidian/plugins/ubermensch-anya"
mkdir -p "$target"
cp "$project/obsidian-plugin/ubermensch-anya/"{manifest.json,main.js,styles.css} "$target/"
python3 - "$vault/.obsidian/community-plugins.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
plugins = json.loads(path.read_text()) if path.exists() else []
if "ubermensch-anya" not in plugins:
    path.write_text(json.dumps(plugins + ["ubermensch-anya"], indent=2) + "\n")
PY
echo "Плагин установлен: $target"
echo "В Obsidian: Ctrl+P → «Reload app without saving» — и слева появятся значки Ани."
