@echo off
rem Запуск без консоли: ядро (API + Telegram) и приложение с котиком.
rem Повторный запуск безопасен: каждый процесс сам не даст запустить второй экземпляр.
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe -m ubermensch
start "" .venv\Scripts\pythonw.exe -m ubermensch.desktop
