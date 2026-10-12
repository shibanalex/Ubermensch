@echo off
rem Первый вход в Telegram для сбора переписок (нужен один раз).
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python.exe -m ubermensch.collectors.telegram_login
