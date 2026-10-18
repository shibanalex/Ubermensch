#!/usr/bin/env bash
# Однократное подключение Ани к Google Календарю (откроется браузер).
cd "$(dirname "$0")" && exec .venv/bin/python -m ubermensch.google_calendar
