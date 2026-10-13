@echo off
rem Однократное подключение Ани к Google Календарю (откроется браузер).
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python.exe -m ubermensch.google_calendar
