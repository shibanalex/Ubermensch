"""Настройки: ключи из .env, пути к данным, поиск исполняемого файла Claude Code."""
from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

DATA_DIR = Path(os.getenv("UBERMENSCH_DATA", r"D:\Ubermensch"))
API_HOST = "127.0.0.1"
API_PORT = int(os.getenv("API_PORT", "8765"))
API_TOKEN_FILE = DATA_DIR / "secrets" / "api_token"

# Свободные зоны Освальда: здесь он создаёт и правит файлы сам, без подтверждения.
# Всё остальное на дисках — только после «да» пользователя.
OBSIDIAN_DIR = Path(os.getenv("OSWALD_OBSIDIAN", r"D:\Aleksey\Aleksey\Oswald"))
DOCUMENTS_DIR = Path(os.getenv("OSWALD_DOCUMENTS", str(Path.home() / "Documents" / "Oswald")))
FREE_ZONES = (DATA_DIR / "workspace", OBSIDIAN_DIR, DOCUMENTS_DIR)


def in_free_zone(path: Path) -> bool:
    target = path.expanduser().resolve()
    return any(zone.resolve() == target or zone.resolve() in target.parents for zone in FREE_ZONES)


def api_token(create: bool = False) -> str | None:
    """Токен локального API: ядро создаёт его при первом запуске, клиенты только читают."""
    if API_TOKEN_FILE.exists():
        return API_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not create:
        return None
    API_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    API_TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def find_claude() -> str:
    if explicit := os.getenv("CLAUDE_BIN"):
        return explicit
    if found := shutil.which("claude"):
        return found
    # отдельная установка Claude Code (claude.ai/install) — надёжнее встроенной в VS Code
    standalone = Path.home() / ".local" / "bin" / "claude.exe"
    if standalone.exists():
        return str(standalone)
    # Запасной вариант: бинарник, встроенный в расширение VS Code (берём самый свежий).
    bundled = sorted(
        (Path.home() / ".vscode" / "extensions").glob(
            "anthropic.claude-code-*/resources/native-binary/claude.exe"
        ),
        key=lambda p: p.stat().st_mtime,
    )
    if bundled:
        return str(bundled[-1])
    raise RuntimeError("Claude Code не найден: установите его или укажите CLAUDE_BIN в .env")


@dataclass(frozen=True)
class Settings:
    tg_token: str
    tg_chat_id: int
    data_dir: Path
    claude_bin: str
    claude_model: str
    claude_timeout: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "ubermensch.db"

    @property
    def workspace(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def load() -> Settings:
    missing = [k for k in ("TG_BOT_TOKEN", "TG_CHAT_ID") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"В .env не заданы: {', '.join(missing)}")
    try:
        claude_bin = find_claude()
    except RuntimeError:
        claude_bin = ""  # ядро всё равно стартует: мозг поищет Claude при вызове и сообщит о проблеме
    settings = Settings(
        tg_token=os.environ["TG_BOT_TOKEN"],
        tg_chat_id=int(os.environ["TG_CHAT_ID"]),
        data_dir=DATA_DIR,
        claude_bin=claude_bin,
        claude_model=os.getenv("CLAUDE_MODEL", "opus"),
        claude_timeout=int(os.getenv("CLAUDE_TIMEOUT", "300")),
    )
    for d in (settings.db_path.parent, settings.workspace, settings.logs_dir, *FREE_ZONES):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # например, хранилище Obsidian на другом диске сейчас недоступно
    return settings
