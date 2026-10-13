@echo off
rem Ручной вход в личный кабинет вуза — если автоматический (LK_LOGIN/LK_PASSWORD в .env) не прошёл.
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python.exe -m ubermensch.university.lk
pause
