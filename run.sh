#!/usr/bin/env bash
# Ядро в терминале — для диагностики (логи видно сразу). Обычный запуск — ./start.sh.
cd "$(dirname "$0")" && exec .venv/bin/python -m ubermensch
