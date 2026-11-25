"""Автоматические отклики на вакансии в Telegram-каналах рабочего аккаунта (WORK).

Читает новые посты из подписанных каналов рабочего Telegram-аккаунта, отфильтровывает рекламу,
чужие резюме и неподходящие грейды/стеки, определяет язык вакансии (русский или английский),
выбирает готовое сопроводительное письмо из DATA_DIR/career/cover_letters.json и подходящий
PDF-файл резюме с диска (русский или английский) и отправляет рекрутеру (@username) в личку.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from telethon.errors import FloodWaitError, PeerFloodError, RPCError, UsernameInvalidError, UsernameNotOccupiedError
from telethon.tl.types import MessageEntityMention, MessageEntityTextUrl, User

from . import config, mail
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_RESUME_DIR = Path.home() / "Документы" / "Мои ДОКУМЕНТЫ" / "резюме"
CHECK_EVERY_SECONDS = 30 * 60  # проверять каналы каждые 30 минут
ACTIVE_HOURS = (9, 22)         # писать рекрутерам только в дневное время (9:00–22:00)
INITIAL_LOOKBACK_DAYS = 10     # при первом проходе канала смотреть посты не старше 10 дней
INITIAL_CHANNEL_LIMIT = 60     # максимум последних постов при первом проходе канала
PAUSE_BETWEEN_SENDS = (12, 22) # пауза между откликами разным HR (секунды)

INCLUDE_WORDS = (
    "python", "питон", "fastapi", "django", "aiohttp", "asyncio", "celery", "sqlalchemy",
)
# В самом заголовке вакансии обязательно должно быть указание на Python/Backend-разработку
REQUIRED_TITLE_WORDS = (
    "python", "питон", "fastapi", "django", "aiohttp", "asyncio",
    "backend", "бэкенд", "бэк-енд", "back-end",
)
PYTHON_TITLE_WORDS = (
    "python", "питон", "fastapi", "django", "aiohttp", "asyncio", "flask", "litestar",
)
EXCLUDE_TITLE_WORDS = (
    "senior", "сеньор", "синьор", "ведущий", "старший", "sr.", "sr ",
    "lead", "лид", "team lead", "tech lead", "тимлид", "техлид",
    "главный", "head", "principal", "staff", "architect", "архитектор",
    "director", "директор", "cto", "qa", "sdet", "тестиров", "test automation", "test engineer",
    "аналитик", "analyst", "data scientist", "ml engineer", "ml-инженер", "mlops", "computer vision",
    "nlp", "c++", "с++", "rust", "golang", " go ", "go-", "(go)", "php", "1с", "1c", "java", "kotlin",
    "c#", ".net", "ruby", "scala", "elixir", "node", "nestjs", "typescript", "foxpro",
    "битрикс", "bitrix", "react", "vue", "angular", "frontend", "фронтенд",
    "fullstack", "full-stack", "full stack", "фулстек", "фуллстек", "fulstack",
    "ios", "android", "mobile", "мобильн", "sdk", "flutter", "devops", "sre",
    "crypto", "сrypto", "крипт", "defi", "web3", "blockchain", "блокчейн", "solidity", "microstructure",
    "intern", "стажер", "стажёр", "преподават", "педагог", "учител", "тьютор", "наставник", "ментор", "автор",
    "english", "английск", "репетитор", "tutor", "курсы", "маркетолог", "реклам", "hunter", "hireway",
)
AD_MARKERS = (
    "#реклама", "о рекламодателе", "erid:", "erid=", "бесплатной регистрации",
    "обучаем с нуля", "зарегистрироваться", "курс ", "вебинар", "#резюме", "#ищуработу",
    r"#cv\b", "ищу работу", "моё резюме", "мое резюме",
    "english for", "1-on-1", "репетитор", "по вопросам рекламы", "размещение рекламы",
    "разместить вакансию", "для рф нет", "не рф", "вне рф", "кроме рф", "рф и рб не",
    "outside russia", "not in russia",
)
SENIOR_GRADE_RE = re.compile(
    r"(?im)(?:уровень|грейд|grade|level)\s*[:—-]\s*#?(?:senior|сеньор|синьор|ведущий|старший|lead|principal|staff|head|лид)\b"
    r"|(?:^|\s)#(?:senior|сеньор|lead)\b"
)
HANDLE_RE = re.compile(r"(?<![\w.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
TME_LINK_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,31})\b")
EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
MAILTO_RE = re.compile(r"^mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)
IGNORED_EMAIL_SUBSTRINGS = (
    "career_user", "shibanalex", "noreply", "no-reply", "telegram.org", "example.com",
)
IGNORED_EMAIL_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
CONTACT_HINT_RE = re.compile(
    r"(?im)(?:контакт\w*|contact\w*|telegram|телеграм|\btg\b|писать|отклик\w*|"
    r"hr\b|рекрутер\w*|recruiter\w*|apply|связ\w*)[^\n]{0,120}"
)
POSITION_LINE_RE = re.compile(
    r"(?im)^(?:вакансия|позиция|должность|title|position|role|роль)\s*[:—-]\s*(.+)$"
)
COMPANY_LINE_RE = re.compile(
    r"(?im)^(?:компания|работодатель|company(?:\s+name)?|employer)\s*[:—-]\s*(.+)$"
)

IGNORED_HANDLES = {
    "schubanwork", "pydevjob", "job_python", "python_vakansii_jobs", "remocate",
    "relocateme", "careernoborders", "remoteit", "forpython", "it_vacancy_relocation",
    "it_work_remote", "remote_python_jobs", "habr_career", "budujobs", "geekjobs",
    "it_vakansii_jobs", "slivaker_it", "siivaker", "prizrakgroup", "thesafeannaanon",
    "allegrosite", "ccnagf", "jobde", "it_match", "getmatch", "finder_vc",
    "freeit_job", "a_alex_contextreclama_organika", "alexchddx", "ithuntergusev", "hireway",
}
BAD_HANDLE_SUBSTRINGS = (
    "reclama", "reklama", "advert", "poster", "freeit", "admin", "support",
    "vakansii", "channel",
)


@dataclass(frozen=True)
class ParsedVacancy:
    position: str
    company: str
    language: str       # "ru" | "en"
    hr_username: str    # без @ (пусто, если отклик только на email)
    text: str
    hr_email: str = ""


def resume_paths() -> dict[str, Path]:
    """Пути к русскому и английскому PDF-резюме на диске."""
    ru_env = os.getenv("TG_RESUME_RU", "").strip()
    en_env = os.getenv("TG_RESUME_EN", "").strip()
    ru_clean = DEFAULT_RESUME_DIR / "Шубанн_Алексей_Python_разработчик.pdf"
    ru_orig = DEFAULT_RESUME_DIR / "Шубанн Алексей (1).pdf"
    en_clean = DEFAULT_RESUME_DIR / "Alexey_Shubann_Python_Backend_CV.pdf"
    en_orig = DEFAULT_RESUME_DIR / "Alex Schuabn CV.pdf"
    return {
        "ru": Path(ru_env).expanduser() if ru_env else (ru_clean if ru_clean.exists() else ru_orig),
        "en": Path(en_env).expanduser() if en_env else (en_clean if en_clean.exists() else en_orig),
    }


def live() -> bool:
    """По умолчанию включено (1); можно отключить через TG_CAREER_LIVE=0 в .env."""
    return os.getenv("TG_CAREER_LIVE", "1").strip() == "1"


def daily_limit() -> int:
    try:
        return max(1, min(50, int(os.getenv("TG_CAREER_LIMIT", "12"))))
    except ValueError:
        return 12


def email_daily_limit() -> int:
    try:
        return max(1, min(100, int(os.getenv("TG_CAREER_EMAIL_LIMIT", "25"))))
    except ValueError:
        return 25


def load_templates() -> dict[str, list[dict]]:
    path = config.DATA_DIR / "career" / "cover_letters.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"ru": data.get("ru") or [], "en": data.get("en") or []}


def detect_language(text: str) -> str:
    latin = len(re.findall(r"[A-Za-z]", text))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    return "en" if latin > 2 * max(cyrillic, 1) else "ru"


def _clean_field(value: str, max_len: int = 90) -> str:
    # Убираем markdown, эмодзи и служебные префиксы («📑 Вакансия: Backend Developer» -> «Backend Developer»)
    value = re.sub(r"[*_`~#\[\]]+", "", value)
    value = re.sub(r"^[^\wА-Яа-яЁё]+", "", value)
    value = re.sub(
        r"(?i)^(?:вакансия|позиция|должность|title|position|role|роль)\s*[:—-]?\s*",
        "",
        value,
    )
    value = re.sub(r"^[^\wА-Яа-яЁё]+", "", value)
    value = " ".join(value.split())
    if value.lower() in {"nda", "не указана", "не указано", "hidden", "confidential", "-"}:
        return ""
    return value[:max_len].strip()


def extract_position_and_company(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    position = ""
    company = ""

    if m := POSITION_LINE_RE.search(text):
        position = _clean_field(m.group(1))
    if m := COMPANY_LINE_RE.search(text):
        company = _clean_field(m.group(1))

    if not position:
        for line in lines[:5]:
            if line.startswith("#") or line.lower().startswith(("уровень:", "локация:", "страна:", "компания:")):
                continue
            cleaned = _clean_field(line)
            if 3 <= len(cleaned) <= 95:
                position = cleaned
                break

    if not position:
        position = "Python Backend Engineer" if detect_language(text) == "en" else "Python-разработчик"
    return position, company


def extract_hr_username(text: str, entities=None, channel_username: str | None = None) -> str | None:
    """Найти @username рекрутера в тексте или ссылках поста (исключая ботов и сам канал)."""
    ignored = set(IGNORED_HANDLES)
    if channel_username:
        ignored.add(channel_username.lstrip("@").casefold())

    def valid(handle: str) -> bool:
        h = handle.lstrip("@").casefold()
        if len(h) < 4 or h in ignored:
            return False
        if h.endswith("bot") or h.startswith("joinchat"):
            return False
        if any(sub in h for sub in BAD_HANDLE_SUBSTRINGS):
            return False
        return True

    # 1. Сначала ищем рядом со словами «Контакты / Telegram / HR / Писать»
    for match in CONTACT_HINT_RE.finditer(text):
        chunk = text[match.start():match.end() + 100]
        for h in HANDLE_RE.findall(chunk) + TME_LINK_RE.findall(chunk):
            if valid(h):
                return h.lstrip("@")

    # 2. Ищем во всём тексте (с конца поста, где обычно указывают контакт)
    all_handles = HANDLE_RE.findall(text) + TME_LINK_RE.findall(text)
    for h in reversed(all_handles):
        if valid(h):
            return h.lstrip("@")

    # 3. Проверяем зашитые ссылки в MessageEntityTextUrl / MessageEntityMention
    for ent in entities or []:
        if isinstance(ent, MessageEntityTextUrl) and ent.url:
            if m := TME_LINK_RE.search(ent.url):
                if valid(m.group(1)):
                    return m.group(1).lstrip("@")
        elif isinstance(ent, MessageEntityMention):
            segment = text[ent.offset:ent.offset + ent.length]
            if valid(segment):
                return segment.lstrip("@")
    return None


def extract_hr_email(text: str, entities=None) -> str | None:
    """Найти email рекрутера/HR в тексте поста или mailto-ссылках."""

    def valid(raw: str) -> bool:
        el = raw.strip(".,;:<>\"'").casefold()
        if "@" not in el or el.endswith(IGNORED_EMAIL_EXT):
            return False
        if any(bad in el for bad in IGNORED_EMAIL_SUBSTRINGS):
            return False
        return "." in el.split("@", 1)[1]

    for match in CONTACT_HINT_RE.finditer(text):
        chunk = text[match.start():match.end() + 120]
        for em in EMAIL_RE.findall(chunk):
            if valid(em):
                return em.strip(".,;:<>\"'").lower()

    for em in reversed(EMAIL_RE.findall(text)):
        if valid(em):
            return em.strip(".,;:<>\"'").lower()

    for ent in entities or []:
        if isinstance(ent, MessageEntityTextUrl) and ent.url:
            if m := MAILTO_RE.match(ent.url.strip()):
                if valid(m.group(1)):
                    return m.group(1).lower()
    return None


def parse_and_filter_vacancy(text: str, entities=None, channel_username: str | None = None) -> ParsedVacancy | None:
    """Проверить, подходит ли пост под критерии вакансии Python Backend, и извлечь данные."""
    if not text or len(text.strip()) < 80:
        return None
    lower = text.casefold()

    # Отсеиваем рекламу, курсы и чужие резюме
    for marker in AD_MARKERS:
        if marker.endswith(r"\b"):
            if re.search(marker, lower):
                return None
        elif marker in lower:
            return None

    # Должен упоминаться Python-стек
    if not any(w in lower for w in INCLUDE_WORDS):
        return None

    position, company = extract_position_and_company(text)
    pos_lower = position.casefold()
    is_middle_combo = "middle" in pos_lower or "мидл" in pos_lower

    # Если явно помечено как #senior / #lead: пропускаем (кроме смешанных вилок Middle+/Senior)
    if SENIOR_GRADE_RE.search(text) and not is_middle_combo:
        return None

    # В заголовке обязательно должна быть профильная Python/Backend роль (не реклама и не название агентства)
    if not any(req in pos_lower for req in REQUIRED_TITLE_WORDS):
        return None

    # В заголовке вакансии не должно быть стоп-слов; для Middle+/Senior разрешаем соседство senior/старший
    mixed_senior_ok = {"senior", "сеньор", "синьор", "старший", "sr.", "sr "}
    for bad in EXCLUDE_TITLE_WORDS:
        if bad in pos_lower:
            if is_middle_combo and bad in mixed_senior_ok:
                continue
            return None

    # Если в заголовке написано только «Backend Developer» без «Python», проверяем, что основной стек не чужой
    if not any(py in pos_lower for py in PYTHON_TITLE_WORDS):
        head_lower = lower[:350]
        if any(foreign in head_lower for foreign in ("golang", "node.js", "nodejs", "nestjs", "typescript", "php", "java ", "kotlin", "c#", ".net", "rust", "ruby")):
            return None

    hr_username = extract_hr_username(text, entities, channel_username) or ""
    hr_email = extract_hr_email(text, entities) or ""
    if not hr_username and not hr_email:
        return None

    lang = detect_language(text)
    return ParsedVacancy(
        position=position,
        company=company,
        language=lang,
        hr_username=hr_username,
        text=text,
        hr_email=hr_email,
    )


def render_cover_letter(vacancy: ParsedVacancy, templates: dict[str, list[dict]], seed_key: str) -> tuple[str, str]:
    """Выбрать шаблон сопроводительного письма по языку вакансии и подставить {position}/{company}.
    Возвращает (template_id, letter_text).
    """
    lang = "en" if vacancy.language == "en" and templates.get("en") else "ru"
    items = templates[lang]
    idx = int(hashlib.sha1(seed_key.encode("utf-8")).hexdigest(), 16) % len(items)
    chosen = items[idx]
    text = chosen["text"].replace("{position}", vacancy.position)
    if vacancy.company:
        text = text.replace("{company}", vacancy.company)
    else:
        if lang == "ru":
            text = (text.replace(" в {company}", "")
                        .replace(" {company}", " вашей компании")
                        .replace("{company}", "вашей компании"))
        else:
            text = (text.replace(" at {company}", "")
                        .replace(" {company}", " your company")
                        .replace("{company}", "your company"))
    return chosen["id"], text


class TelegramCareerBot:
    """Сервис мониторинга Telegram-каналов и автоматических откликов с выбором резюме."""

    def __init__(self, store: Store, core) -> None:
        self.store = store
        self.core = core
        self._lock = asyncio.Lock()

    def _contacted_hrs(self) -> set[str]:
        raw = self.store.cursor("tg_career:contacted")
        try:
            return {h.casefold() for h in json.loads(raw or "[]")}
        except ValueError:
            return set()

    def _mark_contacted(self, hr_username: str) -> None:
        contacted = self._contacted_hrs()
        contacted.add(hr_username.casefold())
        self.store.set_cursor("tg_career:contacted", json.dumps(sorted(contacted), ensure_ascii=False))

    def _emailed_hrs(self) -> set[str]:
        raw = self.store.cursor("tg_career:emailed")
        try:
            emails = {e.casefold() for e in json.loads(raw or "[]")}
        except ValueError:
            emails = set()
        rows = self.store.db.execute(
            "SELECT json_extract(details, '$.to') FROM career_opportunities WHERE source = 'email'"
        ).fetchall()
        for (addr,) in rows:
            if addr:
                emails.add(str(addr).casefold())
        return emails

    def _mark_emailed(self, hr_email: str) -> None:
        emailed = self._emailed_hrs()
        emailed.add(hr_email.casefold())
        self.store.set_cursor("tg_career:emailed", json.dumps(sorted(emailed), ensure_ascii=False))

    def _sent_today_count(self) -> int:
        today = date.today().isoformat()
        row = self.store.db.execute(
            "SELECT COUNT(*) FROM career_events e JOIN career_opportunities o ON o.id = e.opportunity_id "
            "WHERE o.source = 'tg' AND e.stage = 'applied' AND e.observed_on = ?",
            (today,),
        ).fetchone()
        return int(row[0]) if row else 0

    def _sent_emails_today_count(self) -> int:
        today = date.today().isoformat()
        row = self.store.db.execute(
            "SELECT COUNT(*) FROM career_events e JOIN career_opportunities o ON o.id = e.opportunity_id "
            "WHERE o.source = 'email' AND json_extract(o.details, '$.via') = 'tg_career_bot' "
            "AND e.stage = 'applied' AND e.observed_on = ?",
            (today,),
        ).fetchone()
        return int(row[0]) if row else 0

    def status(self) -> dict:
        paths = resume_paths()
        return {
            "live": live(),
            "daily_limit": daily_limit(),
            "email_daily_limit": email_daily_limit(),
            "sent_today": self._sent_today_count(),
            "sent_emails_today": self._sent_emails_today_count(),
            "total_contacted_hrs": len(self._contacted_hrs()),
            "total_emailed_hrs": len(self._emailed_hrs()),
            "resume_ru": str(paths["ru"]),
            "resume_ru_exists": paths["ru"].exists(),
            "resume_en": str(paths["en"]),
            "resume_en_exists": paths["en"].exists(),
            "last_scan": self.store.cursor("tg_career:last_scan"),
        }

    async def _send_email_application(
        self,
        parsed: ParsedVacancy,
        letter: str,
        resume_file: Path,
    ) -> str:
        """Отправить отклик на email с прикреплённым PDF резюме через рабочий ящик."""
        try:
            box = config.mail_account("работа")
        except ValueError:
            box = config.mail_account()
        if parsed.language == "en":
            subject = f"Application for {parsed.position} — Alexey Shubann"
        else:
            subject = f"Отклик на вакансию {parsed.position} — Алексей Шубанн"
        await asyncio.to_thread(
            mail.send,
            box.address,
            box.password,
            parsed.hr_email,
            subject,
            letter.strip() + "\n",
            attachments=[resume_file],
        )
        return box.address

    async def scan_once(self, *, max_send: int | None = None, ignore_hours: bool = False) -> dict:
        """Проверить новые сообщения во всех каналах рабочего Telegram и отправить отклики."""
        async with self._lock:
            now = datetime.now()
            if not ignore_hours and not (ACTIVE_HOURS[0] <= now.hour < ACTIVE_HOURS[1]):
                return {"status": "outside_active_hours", "sent": 0, "checked": 0}

            paths = resume_paths()
            for lang, p in paths.items():
                if not p.exists():
                    raise RuntimeError(f"Не найден файл резюме ({lang}): {p}")

            templates = load_templates()
            account = self.core.telegrams["work"]
            tg = await account._require()

            limit = max_send if max_send is not None else max(0, daily_limit() - self._sent_today_count())
            email_limit = max_send if max_send is not None else max(0, email_daily_limit() - self._sent_emails_today_count())
            contacted = self._contacted_hrs()
            emailed = self._emailed_hrs()
            checked = 0
            matched = 0
            sent_items: list[dict] = []
            sent_email_items: list[dict] = []
            skipped_reasons: list[str] = []
            cutoff = datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS)

            dialogs = []
            seen_dialog_ids: set[int] = set()
            channels_to_archive = []
            for fld in (0, 1):
                async for dialog in tg.iter_dialogs(folder=fld):
                    if dialog.id in seen_dialog_ids:
                        continue
                    seen_dialog_ids.add(dialog.id)
                    if dialog.is_channel and not dialog.is_group:
                        dialogs.append(dialog)
                        if fld == 0:
                            channels_to_archive.append(dialog.entity)

            if channels_to_archive:
                try:
                    await tg.edit_folder(channels_to_archive, folder=1)
                except RPCError:
                    pass

            for dialog in dialogs:
                cursor_key = f"tg_career:last_id:{dialog.id}"
                last_id = int(self.store.cursor(cursor_key) or 0)
                newest_id = last_id
                channel_uname = getattr(dialog.entity, "username", None)

                if last_id:
                    messages = [m async for m in tg.iter_messages(dialog.entity, min_id=last_id, limit=50)]
                else:
                    messages = [
                        m async for m in tg.iter_messages(dialog.entity, limit=INITIAL_CHANNEL_LIMIT)
                        if m.date and m.date >= cutoff
                    ]

                # Обрабатываем от старых к новым, чтобы курсор двигался последовательно
                for message in reversed(messages):
                    checked += 1
                    text = message.message or ""
                    parsed = parse_and_filter_vacancy(text, message.entities, channel_uname)
                    if parsed is None:
                        newest_id = max(newest_id, message.id)
                        continue

                    matched += 1
                    post_url = (
                        f"https://t.me/{channel_uname}/{message.id}"
                        if channel_uname
                        else f"https://t.me/c/{str(dialog.id).removeprefix('-100')}/{message.id}"
                    )
                    resume_file = paths[parsed.language]

                    # Если у вакансии нет @username (или он не подходит), но есть email: отправляем на почту
                    if not parsed.hr_username and parsed.hr_email:
                        email_key = parsed.hr_email.casefold()
                        if email_key in emailed:
                            skipped_reasons.append(f"{parsed.hr_email}: уже отправляли ранее")
                            newest_id = max(newest_id, message.id)
                            continue
                        if live() and len(sent_email_items) >= email_limit:
                            break
                        seed_key = f"{dialog.id}:{message.id}:{email_key}"
                        template_id, letter = render_cover_letter(parsed, templates, seed_key)
                        if not live():
                            sent_email_items.append({
                                "mode": "dry_run",
                                "channel": dialog.name,
                                "position": parsed.position,
                                "company": parsed.company,
                                "hr": parsed.hr_email,
                                "language": parsed.language,
                                "template": template_id,
                                "resume": resume_file.name,
                                "url": post_url,
                            })
                            newest_id = max(newest_id, message.id)
                            continue
                        try:
                            from_addr = await self._send_email_application(parsed, letter, resume_file)
                        except Exception as e:
                            log.warning("TG career: ошибка отправки email %s: %s", parsed.hr_email, e)
                            skipped_reasons.append(f"{parsed.hr_email}: ошибка SMTP ({type(e).__name__})")
                            newest_id = max(newest_id, message.id)
                            continue

                        emailed.add(email_key)
                        self._mark_emailed(email_key)
                        newest_id = max(newest_id, message.id)
                        ext_id = f"email:{email_key}:{date.today().isoformat()}"
                        with self.store.transaction():
                            opp = self.store.save_opportunity(
                                "email",
                                ext_id,
                                parsed.position,
                                parsed.company or f"TG ({dialog.name})",
                                post_url,
                                {
                                    "via": "tg_career_bot",
                                    "from": from_addr,
                                    "to": email_key,
                                    "channel": dialog.name,
                                    "language": parsed.language,
                                    "template": template_id,
                                    "resume": resume_file.name,
                                },
                            )
                            self.store.record_career_event(
                                opp["id"],
                                "applied",
                                f"Email ({parsed.language.upper()}) из TG: {from_addr} -> {email_key}, "
                                f"шаблон {template_id}, резюме {resume_file.name}",
                                date.today().isoformat(),
                            )
                        sent_email_items.append({
                            "mode": "sent",
                            "channel": dialog.name,
                            "position": parsed.position,
                            "company": parsed.company or dialog.name,
                            "hr": email_key,
                            "language": parsed.language,
                            "template": template_id,
                            "resume": resume_file.name,
                            "url": post_url,
                        })
                        log.info(
                            "TG->Email отклик отправлен: %s (%s) -> %s [%s, %s]",
                            parsed.position, dialog.name, email_key, template_id, resume_file.name,
                        )
                        if len(sent_email_items) < email_limit:
                            await asyncio.sleep(random.uniform(3, 6))
                        continue

                    hr_key = parsed.hr_username.casefold()
                    if hr_key in contacted:
                        skipped_reasons.append(f"@{parsed.hr_username}: уже связывались ранее")
                        newest_id = max(newest_id, message.id)
                        continue

                    if live() and len(sent_items) >= limit:
                        # Дневной лимит достигнут: не двигаем курсор дальше этого сообщения,
                        # чтобы откликнуться на него в следующей сессии
                        break

                    seed_key = f"{dialog.id}:{message.id}:{hr_key}"
                    template_id, letter = render_cover_letter(parsed, templates, seed_key)

                    if not live():
                        sent_items.append({
                            "mode": "dry_run",
                            "channel": dialog.name,
                            "position": parsed.position,
                            "company": parsed.company,
                            "hr": f"@{parsed.hr_username}",
                            "language": parsed.language,
                            "template": template_id,
                            "resume": resume_file.name,
                            "url": post_url,
                        })
                        newest_id = max(newest_id, message.id)
                        continue

                    # Проверяем в Telegram, что это живой пользователь и переписки с ним ещё нет
                    try:
                        hr_entity = await tg.get_entity(parsed.hr_username)
                    except (UsernameNotOccupiedError, UsernameInvalidError, ValueError):
                        skipped_reasons.append(f"@{parsed.hr_username}: аккаунт не найден")
                        newest_id = max(newest_id, message.id)
                        continue
                    except RPCError as e:
                        log.warning("TG career: ошибка получения @%s: %s", parsed.hr_username, e)
                        newest_id = max(newest_id, message.id)
                        continue

                    if not isinstance(hr_entity, User) or hr_entity.bot:
                        skipped_reasons.append(f"@{parsed.hr_username}: не личный аккаунт (бот/канал)")
                        contacted.add(hr_key)
                        self._mark_contacted(parsed.hr_username)
                        newest_id = max(newest_id, message.id)
                        continue

                    existing_msgs = await tg.get_messages(hr_entity, limit=1)
                    if existing_msgs:
                        skipped_reasons.append(f"@{parsed.hr_username}: в Telegram уже есть переписка")
                        contacted.add(hr_key)
                        self._mark_contacted(parsed.hr_username)
                        newest_id = max(newest_id, message.id)
                        continue

                    # Отправляем отклик (сопроводительное + PDF резюме) и убираем холодный чат в Архив,
                    # пока рекрутер реально не ответит (чтобы не захламлять основной список чатов).
                    try:
                        if len(letter) <= 1024:
                            await tg.send_file(hr_entity, file=str(resume_file), caption=letter)
                        else:
                            await tg.send_message(hr_entity, letter)
                            await tg.send_file(hr_entity, file=str(resume_file))
                        try:
                            await tg.edit_folder([hr_entity], folder=1)
                        except RPCError:
                            pass
                    except (PeerFloodError, FloodWaitError) as e:
                        log.warning("TG career: ограничение Telegram при отправке @%s: %s", parsed.hr_username, e)
                        self.core.notify(
                            f"⚠️ Telegram временно ограничил написание новым контактам ({type(e).__name__}). "
                            "Автоотклики в TG приостановлены до следующего цикла."
                        )
                        break
                    except RPCError as e:
                        log.warning("TG career: не удалось отправить @%s: %s", parsed.hr_username, e)
                        skipped_reasons.append(f"@{parsed.hr_username}: ошибка отправки ({type(e).__name__})")
                        newest_id = max(newest_id, message.id)
                        continue

                    contacted.add(hr_key)
                    self._mark_contacted(parsed.hr_username)
                    newest_id = max(newest_id, message.id)

                    ext_id = f"{dialog.id}:{message.id}"
                    with self.store.transaction():
                        opp = self.store.save_opportunity(
                            "tg",
                            ext_id,
                            parsed.position,
                            parsed.company or f"TG ({dialog.name})",
                            post_url,
                            {
                                "via": "tg_career_bot",
                                "hr": f"@{parsed.hr_username}",
                                "channel": dialog.name,
                                "language": parsed.language,
                                "template": template_id,
                                "resume": resume_file.name,
                            },
                        )
                        self.store.record_career_event(
                            opp["id"],
                            "applied",
                            f"TG ({parsed.language.upper()}): отправлено @{parsed.hr_username}, "
                            f"шаблон {template_id}, резюме {resume_file.name}",
                            date.today().isoformat(),
                        )

                    item_info = {
                        "mode": "sent",
                        "channel": dialog.name,
                        "position": parsed.position,
                        "company": parsed.company or dialog.name,
                        "hr": f"@{parsed.hr_username}",
                        "language": parsed.language,
                        "template": template_id,
                        "resume": resume_file.name,
                        "url": post_url,
                    }
                    sent_items.append(item_info)
                    log.info(
                        "TG отклик отправлен: %s (%s) -> @%s [%s, %s]",
                        parsed.position, dialog.name, parsed.hr_username, template_id, resume_file.name,
                    )

                    if len(sent_items) < limit:
                        await asyncio.sleep(random.uniform(*PAUSE_BETWEEN_SENDS))

                if newest_id > last_id:
                    self.store.set_cursor(cursor_key, str(newest_id))

            self.store.set_cursor("tg_career:last_scan", datetime.now().isoformat(timespec="seconds"))
            if sent_items and live():
                lines = [
                    f"• {i['position']} ({i['company']}) → {i['hr']} [{i['template']}, {i['resume']}] {i['url']}"
                    for i in sent_items
                ]
                self.core.notify(f"📨 Отправила отклики в Telegram ({len(sent_items)}):\n" + "\n".join(lines))
            if sent_email_items and live():
                elines = [
                    f"• {i['position']} ({i['company']}) → {i['hr']} [{i['template']}, {i['resume']}] {i['url']}"
                    for i in sent_email_items
                ]
                self.core.notify(f"📧 Отправила отклики на email из TG ({len(sent_email_items)}):\n" + "\n".join(elines))

            return {
                "checked": checked,
                "matched": matched,
                "sent": len(sent_items),
                "sent_emails": len(sent_email_items),
                "items": sent_items + sent_email_items,
                "skipped": skipped_reasons[:20],
            }

    async def check_hr_replies(self) -> list[dict]:
        """Радар входящих от живых людей/HR в рабочем Telegram.

        При появлении нового входящего сообщения сразу присылает уведомление в личный TG,
        чтобы не нужно было постоянно проверять второй аккаунт руками.
        """
        async with self._lock:
            account = self.core.telegrams.get("work")
            if account is None:
                return []
            tg = await account._require()
            alerts: list[dict] = []
            seen: set[int] = set()
            for fld in (0, 1):
                async for dialog in tg.iter_dialogs(folder=fld):
                    if dialog.id in seen:
                        continue
                    seen.add(dialog.id)
                    entity = dialog.entity
                    if not isinstance(entity, User) or entity.bot or entity.is_self:
                        continue
                    msg = dialog.message
                    if msg is None or msg.out or msg.action is not None:
                        continue
                    cursor_key = f"tg_career:hr_reply:{dialog.id}"
                    last_seen = int(self.store.cursor(cursor_key) or 0)
                    if last_seen == 0:
                        # При первом знакомстве с уже существующим диалогом запоминаем последний id,
                        # если непрочитанных нет, чтобы не слать старую историю.
                        if dialog.unread_count == 0:
                            self.store.set_cursor(cursor_key, str(msg.id))
                            continue
                    if msg.id <= last_seen:
                        continue
                    self.store.set_cursor(cursor_key, str(msg.id))
                    if fld == 1:
                        try:
                            await tg.edit_folder([entity], folder=0)
                        except RPCError:
                            pass
                    uname = getattr(entity, "username", None)
                    handle = f" (@{uname})" if uname else ""
                    text = (msg.message or "[вложение]").strip()
                    self.core.notify(
                        f"💼 Новый ответ в рабочем Telegram от {dialog.name}{handle}:\n"
                        f"«{text[:600]}»"
                    )
                    alerts.append({"name": dialog.name, "username": uname, "text": text[:600]})
            return alerts

    async def run(self) -> None:
        """Фоновый цикл мониторинга ответов HR (раз в 5 мин) и каналов с вакансиями (раз в 30 мин)."""
        await asyncio.sleep(25)  # дать ядру спокойно подняться при старте
        step = 0
        while True:
            try:
                await self.check_hr_replies()
                if step % 6 == 0:
                    await self.scan_once()
            except Exception as e:
                log.warning("TG career: сбой цикла: %s", e)
            step += 1
            await asyncio.sleep(300)

