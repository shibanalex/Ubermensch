#!/usr/bin/env bash
# Первый вход в Telegram для сбора переписок (нужен один раз).
cd "$(dirname "$0")" && exec .venv/bin/python -m ubermensch.collectors.telegram_login
