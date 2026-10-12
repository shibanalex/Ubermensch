@echo off
rem Ядро с консолью — для диагностики (видно логи). Обычный запуск — start.bat.
cd /d "%~dp0"
.venv\Scripts\python.exe -m ubermensch
pause
