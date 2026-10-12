# Übermensch

Личная AI-система: ядро с долговременной памятью, приложение на ПК (маскот, окна-консоли для запросов и ответов, лист задач на рабочем столе) и Telegram для уведомлений. Мозг — Claude через Claude Code на подписке.

## Запуск

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
start.bat
```

- `start.bat` — ядро и приложение без окон консоли (повторный запуск безопасен).
- `run.bat` — только ядро с консолью, для диагностики.
- Автозапуск при входе в Windows: `powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1`.

## Устройство

Ядро (`python -m ubermensch`) — единственный владелец данных. Клиенты ходят к нему через локальный API.

| Модуль | Что делает |
|---|---|
| `ubermensch/core.py` | Операции для всех клиентов и шина событий (думает / изменилось / напоминание / задача выполнена) |
| `ubermensch/api.py` | HTTP API на `127.0.0.1:8765` по токену из `D:\Ubermensch\secrets\api_token`, события через SSE |
| `ubermensch/bot.py` | Telegram: чат с телефона, напоминания, кнопки подтверждения |
| `ubermensch/assistant.py` | Собирает контекст (цели, задачи, факты, история), вызывает мозг, применяет изменения |
| `ubermensch/brain.py` | Изолированный вызов `claude -p` со схемой ответа |
| `ubermensch/actions.py` | Реестр действий и уровень риска; всё, что не `local`, — только после подтверждения |
| `ubermensch/store.py` | SQLite на `D:\Ubermensch\db` с миграциями |
| `ubermensch/prompts/system.md` | Системный промпт |
| `ubermensch/desktop/` | Приложение на PySide6: `cat.py` — маскот :3 с эмоциями, `prompt.py` — окно запроса, `answer.py` — консоль ответов, `console.py` — общий вид окон-консолей, `sheet.py` — бумажный лист задач на рабочем столе, `client.py` — клиент API |

Данные: `D:\Ubermensch\` (`db`, `logs`, `secrets`, `workspace` — рабочая папка мозга).

## Правила безопасности

- Telegram-бот отвечает только на `TG_CHAT_ID`; API — только с токеном.
- Риск действия задаёт реестр, а не модель.
- Внешние данные (переписки, письма) передаются мозгу в блоках `<data>` и не считаются командами.
