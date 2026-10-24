"""Настройки: ключи из .env, пути к данным, поиск исполняемого файла Claude Code."""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")



def _user_dir(kind: str, fallback: str) -> Path:
    """Папка пользователя по XDG: на русской системе это «Документы», «Загрузки»…"""
    try:
        found = subprocess.run(["xdg-user-dir", kind], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        found = ""
    path = Path(found) if found else Path.home() / fallback
    return path if path != Path.home() else Path.home() / fallback


# Данные рядом с проектом: ~/Projects/Ubermensch (база, сессии, импорт, досье, логи).
DATA_DIR = Path(os.getenv("UBERMENSCH_DATA", str(PROJECT_DIR.parent / "Ubermensch"))).expanduser()
API_HOST = "127.0.0.1"
API_PORT = int(os.getenv("API_PORT", "8765"))
API_TOKEN_FILE = DATA_DIR / "secrets" / "api_token"

DESKTOP_DIR = _user_dir("DESKTOP", "Desktop")
DOWNLOADS_DIR = _user_dir("DOWNLOAD", "Downloads")
USER_DOCUMENTS = _user_dir("DOCUMENTS", "Documents")
# Личные папки, где лежат документы, конспекты и работы пользователя.
PERSONAL_DIRS = tuple(dict.fromkeys(
    d for d in (DESKTOP_DIR, USER_DOCUMENTS, DOWNLOADS_DIR, Path.home() / "Documents",
                Path.home() / "Учеба", Path.home() / "Мои Работы") if d.is_dir()))
# Дополнительные места для поиска и сбора (диски с данными): через «:» в UBERMENSCH_ROOTS.
EXTRA_ROOTS = tuple(Path(p).expanduser() for p in os.getenv("UBERMENSCH_ROOTS", "").split(":") if p)

# Хранилище Obsidian пользователя: дневник, ориентиры, проекты. Аня читает всё, кроме
# вложений и шаблонов, а пишет только свои выводы — в «08 AI/Reports» (его правила для агента).
VAULT_DIR = Path(v).expanduser() if (v := os.getenv("OBSIDIAN_VAULT")) else None
REPORTS_DIR = VAULT_DIR / "08 AI" / "Reports" if VAULT_DIR else None
# Свободные зоны Ани: здесь она создаёт и правит файлы сама, без подтверждения.
DOCUMENTS_DIR = Path(os.getenv("ANYA_DOCUMENTS", str(USER_DOCUMENTS / "Аня"))).expanduser()
# Куда класть отчёты (профиль, досье, конспекты): в хранилище, а без него — в папку Ани.
OUTPUT_DIR = REPORTS_DIR or DOCUMENTS_DIR
FREE_ZONES = (DATA_DIR / "workspace", DOCUMENTS_DIR, *((REPORTS_DIR,) if REPORTS_DIR else ()))


# С ПК и системой Аня работает без подтверждения. Исключение — места, где ошибка ломает
# систему, программы, доступ или самой Ани (её память, ключи и код): там — «да/нет».
PROTECTED = (*(Path(p) for p in ("/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib64",
                                 "/opt", "/var", "/root", "/snap", "/sys", "/proc", "/dev")),
             Path.home() / ".ssh", Path.home() / ".gnupg", Path.home() / ".local" / "bin",
             DATA_DIR, PROJECT_DIR, *((VAULT_DIR,) if VAULT_DIR else ()))


def is_protected(path: Path) -> bool:
    """Системные папки, память и код Ани — кроме её свободных зон (рабочая папка и т. п.)."""
    target = path.expanduser().resolve()
    if in_free_zone(target):
        return False
    return any(p.resolve() == target or p.resolve() in target.parents for p in PROTECTED)


def in_free_zone(path: Path) -> bool:
    target = path.expanduser().resolve()
    return any(zone.resolve() == target or zone.resolve() in target.parents for zone in FREE_ZONES)


@dataclass(frozen=True)
class MailAccount:
    key: str  # «1» — основной ящик, «2», «3»… — дополнительные
    address: str
    password: str
    label: str


def mail_accounts() -> list[MailAccount]:
    """Ящики Gmail из .env: GMAIL_ADDRESS/GMAIL_PASSWORD — основной, GMAIL_ADDRESS_2 и
    GMAIL_PASSWORD_2 … _9 — дополнительные; GMAIL_LABEL[_N] — имя ящика («работа»)."""
    accounts = []
    for n in range(1, 10):
        suffix = "" if n == 1 else f"_{n}"
        address = os.getenv("GMAIL_ADDRESS" + suffix, "").strip()
        password = os.getenv("GMAIL_PASSWORD" + suffix, "").strip()
        if address and password:
            label = os.getenv("GMAIL_LABEL" + suffix, "").strip() or address.split("@")[0]
            accounts.append(MailAccount(str(n), address, password, label))
    return accounts


def mail_account(name: str = "") -> MailAccount:
    """Ящик по номеру, адресу, имени до @ или метке; пустое имя — основной."""
    accounts = mail_accounts()
    if not accounts:
        raise RuntimeError("Почта не подключена: нужны GMAIL_ADDRESS и GMAIL_PASSWORD в .env")
    if not name:
        return accounts[0]
    wanted = name.strip().casefold()
    for account in accounts:
        if wanted in {account.key, account.address.casefold(), account.label.casefold(),
                      account.address.split("@")[0].casefold()}:
            return account
    raise ValueError(f"Нет ящика «{name}». Подключены: "
                     + ", ".join(f"{a.label} ({a.address})" for a in accounts))


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
    # отдельная установка Claude Code (claude.ai/install) — ядро под systemd может не видеть PATH
    standalone = Path.home() / ".local" / "bin" / "claude"
    if standalone.exists():
        return str(standalone)
    # Запасной вариант: бинарник, встроенный в расширение VS Code (берём самый свежий).
    bundled = sorted(
        (Path.home() / ".vscode" / "extensions").glob(
            "anthropic.claude-code-*/resources/native-binary/claude"
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
    try:  # сессии Telegram и токены — полный доступ к аккаунтам: только владельцу
        secrets_dir = settings.data_dir / "secrets"
        secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        secrets_dir.chmod(0o700)
    except OSError:
        pass
    for d in (settings.db_path.parent, settings.workspace, settings.logs_dir, *FREE_ZONES):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # например, диск с данными сейчас не подключён
    return settings
