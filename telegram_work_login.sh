#!/usr/bin/env bash
# Первый вход в рабочий Telegram (TG_WORK_API_ID/TG_WORK_HASH в .env), нужен один раз.
cd "$(dirname "$0")" && exec .venv/bin/python -m ubermensch.collectors.telegram_login work
