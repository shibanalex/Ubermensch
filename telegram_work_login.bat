@echo off
rem Первый вход в рабочий Telegram (TG_WORK_API_ID/TG_WORK_HASH в .env), нужен один раз.
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python.exe -m ubermensch.collectors.telegram_login work
