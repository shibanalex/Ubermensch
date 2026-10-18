#!/usr/bin/env bash
# Ручной вход в личный кабинет вуза — если автоматический (LK_LOGIN/LK_PASSWORD в .env) не прошёл.
cd "$(dirname "$0")" && exec .venv/bin/python -m ubermensch.university.lk
