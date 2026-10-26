"""Permissions for autonomous calls and explicit filesystem access zones."""
import os
from pathlib import Path

from .. import config
from .context import current

BACKGROUND_TOOLS = {
    'memory_search', 'read_file', 'read_document', 'read_excel', 'find_files', 'write_file', 'append_file', 'write_word',
    'edit_file', 'file_versions', 'restore_file_version', 'project_run',
    'write_excel', 'calendar_upcoming', 'telegram_find_chats', 'telegram_read',
    'email_inbox', 'email_read',
    'agent_tasks', 'delegate_task', 'save_skill', 'university_search', 'university_read', 'lk_find', 'system_status',
    'planning_overview', 'career_search', 'career_get', 'career_save', 'career_assess',
    'career_draft', 'career_draft_read', 'recommendation_save',
    'experiment_propose', 'experiment_observe', 'experiment_stop', 'experiment_conclude', 'hh_status', 'option_upsert', 'option_status',
}


# Части пути, за которыми ключи, сессии и входы: их не читает никто, даже с его «да» в чате
SECRET_PARTS = frozenset({
    '.ssh', '.gnupg', 'secrets', '.env', '.password-store', 'keyrings', 'kwalletd', '.pki',
    'tdata',  # сессия Telegram Desktop
    '.mozilla', 'google-chrome', 'chromium', 'BraveSoftware', 'yandex-browser',  # куки и пароли
    '.aws', '.kube', '.docker', 'gh', '.azure', '.gcloud', 'gcloud',
})
SECRET_FILES = frozenset({
    '.credentials.json', 'auth.json', 'credentials.json', '.netrc', 'hosts.yml', 'api_token',
    'id_rsa', 'id_ed25519', 'id_ecdsa',
})


def background() -> bool:
    context = current.get()
    return context is not None and context.mode == 'background'


def roots(setting: str, defaults) -> tuple[Path, ...]:
    value = os.getenv(setting)
    return tuple(Path(p).expanduser().resolve() for p in value.split(os.pathsep) if p) \
        if value is not None else tuple(p.resolve() for p in defaults)


def within(target: Path, directories) -> bool:
    return any(target == root or root in target.parents for root in directories)


def readable(target: Path) -> bool:
    target = target.expanduser().resolve()
    if any(p in SECRET_PARTS or p.startswith('.env.') or p.endswith('.session')
           for p in target.parts) or target.name in SECRET_FILES:
        return False
    if within(target, (config.DATA_DIR / 'db', config.DATA_DIR / 'secrets')):
        return False  # её база и ключи — только через её же инструменты
    # Читать можно всю домашнюю папку без вопросов (его решение 26.10.2026: «она не должна
    # спрашивать разрешения»), кроме ключей, сессий и входов выше.
    allowed = roots('UBERMENSCH_READ_ROOTS',
                    (Path.home(), *config.PERSONAL_DIRS, *config.EXTRA_ROOTS, *config.FREE_ZONES,
                     config.DATA_DIR / 'career',  # его резюме, если данные не в домашней папке
                     *((config.VAULT_DIR,) if config.VAULT_DIR else ())))
    return within(target, allowed)


def writable(target: Path) -> bool:
    target = target.expanduser().resolve()
    vault = (config.VAULT_DIR,) if config.VAULT_DIR else ()  # всё его хранилище — без вопросов
    defaults = (config.DOCUMENTS_DIR, config.OUTPUT_DIR, config.DATA_DIR / 'workspace' / 'artifacts', *vault) \
        if background() else (*config.FREE_ZONES, *config.PERSONAL_DIRS, *vault)
    setting = 'UBERMENSCH_BACKGROUND_WRITE_ROOTS' if background() else 'UBERMENSCH_WRITE_ROOTS'
    return within(target, roots(setting, defaults)) and not config.is_protected(target)


def check_tool(name: str) -> None:
    if background() and name not in BACKGROUND_TOOLS:
        raise PermissionError(f'Инструмент {name} недоступен в фоновом режиме. '
                              'Нужно прямое поручение в чате.')
