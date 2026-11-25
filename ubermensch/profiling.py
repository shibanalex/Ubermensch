"""Профиль пользователя со всех сторон: ценности, цели, психика, тело, мышление, люди, среда,
ресурсы и расхождения слов с делами: с опорой на конкретные записи.

Как строится (фоновая служба «профиль», раз в неделю, когда очередь разбора пуста):
1. Корпус: неизменяемый снимок базы (profile_corpus) плюс метрики поведения (behavior).
2. Окна: порции по ~60 тыс. символов: собственные слова пользователя и переписки целиком,
   память ассистента, сводки прежнего разбора и метрики. Объёмные шумные источники (просмотры YouTube,
   действия Google, поиск, страницы браузера, трекер окон): только через метрики и сводки.
3. Наблюдения (map, sonnet): из каждого окна; наблюдения по измерениям с номерами записей.
   Результат кешируется по содержимому окна: при перестройке новые месяцы разбираются,
   старые: нет.
4. Разделы (reduce, основная модель): все наблюдения измерения → утверждения с видом знания,
   уверенностью, опорами, цитатами, противоречиями и смыслом для помощи.
5. Правила поддержки и открытые вопросы: по всем утверждениям; затем проверка ссылок
   и атомарная публикация (personalization.save_profile). Чат получает выжимку сам.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import behavior, config, interview
from .brain import BrainError
from .runtime import models
from .personalization import (AnalysisClaim, AnalysisSection, EvidenceQuote, SupportRule,
                              UserProfile, load_profile, render_report, save_profile)
from .profile_corpus import _readonly, prepare_corpus, validate_refs

log = logging.getLogger(__name__)

MAP_VERSION = 1  # меняется вместе с промптом наблюдений: старый кеш тогда не годится
WINDOW_CHARS = 60_000
ITEM_CHARS = {"document": 20_000, "imports": 20_000, "ai_chat": 3_000, "obsidian": 20_000}
DEFAULT_ITEM_CHARS = 2_500
REDUCE_CHARS = 150_000
MAX_OBSERVATIONS = 30
REBUILD_EVERY = timedelta(days=7)
CHECK_EVERY = 3600
MAX_DEFER = timedelta(hours=6)  # очередь разбора важнее, но не бесконечно
MAP_ATTEMPTS = 3
MAP_MODEL = models.cli("profile")[0]  # уровни моделей: runtime/models.py
CALL_TIMEOUT = 900
KEEP_CORPORA = 1

# (id, название, что искать)
DIMENSIONS = (
    ("values", "Ценности",
     "что для него важно само по себе: заявленные ценности и проявленные — на что тратит время "
     "и силы, за что спорит, чем гордится, что злит и стыдит; иерархия и конфликты ценностей"),
    ("goals", "Цели и мотивация",
     "чего он хочет добиться и зачем: заявленные цели, глубинные мотивы, что зажигает и что гасит, "
     "как ставит цели и доводит ли до конца"),
    ("psyche", "Психика и характер",
     "устойчивые склонности в поведении, эмоциональные паттерны, что вызывает стресс и как он с ним "
     "справляется, самооценка и внутренний критик, как принимает решения, реакция на неудачи"),
    ("body", "Тело и энергия",
     "сон и ритм дня, физическая активность и тренировки, питание, вещества, самочувствие и "
     "здоровье с его слов, энергия в течение дня и недели"),
    ("mind", "Мышление, интересы и обучение",
     "чем интересуется и во что погружается, как учится, внимание и фокус, информационная диета, "
     "творчество, стиль мышления"),
    ("relationships", "Люди и отношения",
     "ключевые люди и их роль, близость и напряжения, как он общается, кто его поддерживает, "
     "социальная энергия, одиночество или включённость"),
    ("environment", "Среда и обстоятельства",
     "где и как живёт, учёба, работа, деньги, распорядок и обязательства, цифровая среда, "
     "внешние ограничения и давление"),
    ("resources", "Ресурсы и возможности",
     "навыки, сильные стороны, активы и связи, открывающиеся окна возможностей, риски и угрозы "
     "на ближайшие месяцы"),
    ("alignment", "Слова и дела",
     "где поведение расходится с его ценностями и целями, что мешает, какие привычки работают "
     "против него, а какие — на него"),
)
DIMENSION_IDS = tuple(d[0] for d in DIMENSIONS)
KINDS = ("explicit_statement", "observed_behavior", "hypothesis", "secondary_report")
CONFIDENCE = ("high", "medium", "low")

# Источники, которые читаются целиком; остальное: только собственные реплики пользователя.
MAPPED_SOURCES = {"telegram", "ai_chat", "gmail", "document", "creative", "notification",
                  "screen", "messages", "facts", "people", "goals", "tasks", "job_summaries",
                  "metrics", "imports", "obsidian", "feedback"}
BY_MONTH = {"telegram", "gmail", "ai_chat", "notification", "messages", "obsidian"}
VIA_SUMMARIES = ("youtube", "google_activity", "google_search", "chrome", "activity")
LABELS = {"telegram": "Telegram", "ai_chat": "разговоры с ИИ", "gmail": "почта",
          "document": "документы", "creative": "творчество", "notification": "уведомления",
          "screen": "экран", "messages": "разговоры с Аней", "facts": "память Ани: факты",
          "people": "память Ани: люди", "goals": "цели", "tasks": "задачи",
          "job_summaries": "сводки прежнего разбора", "metrics": "поведение в цифрах",
          "imports": "импортированные файлы", "youtube": "свои комментарии на YouTube",
          "obsidian": "его заметки и дневник в Obsidian",
          "feedback": "его поправки к прошлому профилю"}

MAP_TASK = (
    "[Системная задача: наблюдения для профиля.] В <data> — порция записей; у каждой номер в "
    "квадратных скобках. Выпиши самые значимые наблюдения о пользователе по измерениям:\n"
    "{dimensions}\n\n"
    "Правила:\n"
    "- До {limit} наблюдений; важное и повторяющееся вперёд, бытовые мелочи и шум — пропускай.\n"
    "- text — 1–3 предложения, конкретно: с датами, числами, именами, его словами.\n"
    "- refs — номера записей (1–6), на которых держится наблюдение. Только номера из <data>.\n"
    "- quote — короткая дословная цитата (до 200 символов) из записи quote_ref, если она "
    "показывает суть; иначе пусто.\n"
    "- Чужие сообщения — про окружение и отношения; о нём со слов других — secondary_report.\n"
    "- Если в порции нет ничего значимого — пустой список."
)
SECTION_TASK = (
    "[Системная задача: раздел профиля «{title}».] Что входит в раздел: {focus}.\n"
    "В <data> — наблюдения за всё время (o-номера, вид, уверенность, период, источник). "
    "Сведи их в раздел профиля:\n"
    "- summary — 4–8 предложений: главное о пользователе в этом измерении сейчас и как менялось.\n"
    "- claims — до 25 утверждений, каждое самостоятельное, конкретное, с периодом (as_of). "
    "basis — o-номера наблюдений, на которых оно держится (только из <data>). Несколько согласных "
    "наблюдений — одно утверждение с большей уверенностью. kind и confidence — по правилам "
    "системного промпта; вывод из многих наблюдений — hypothesis, если он сам этого не говорил.\n"
    "- contradictions — что в данных противоречит утверждению. implication — что это значит для "
    "того, как Ане ему помогать (конкретно). needs_confirmation — стоит ли спросить его самого.\n"
    "- unknowns — чего в данных не видно, но важно для этого измерения. questions — до 5 "
    "вопросов, которые Аня может задать ему, чтобы понять лучше (бережно, по-человечески)."
)
RULES_TASK = (
    "[Системная задача: правила поддержки.] В <data> — утверждения профиля (id, вид, уверенность) "
    "и, если есть, прежние формулировки. Составь:\n"
    "- rules — до 30 правил, как Ане с ним работать, чтобы он жил по своим ценностям и шёл к своим "
    "целям: when — ситуация (конкретно), action — что делать (тон, глубина, формат, когда "
    "подтолкнуть, когда поддержать, что предложить), avoid — чего не делать, scope — область "
    "(чат, Telegram, планирование, учёба, сон, …), basis — id утверждений-оснований. Правило не "
    "ставит ему новых целей и не обходит подтверждения действий.\n"
    "- open_questions — до 10 главных вопросов, ответы на которые сильнее всего улучшат помощь.\n"
    "- recent_changes — что изменилось по сравнению с прежним профилем (если он дан), иначе пусто.\n"
    "- summary — 2–3 предложения: кто он и что для него сейчас главное."
)

_OBSERVATION = {
    "type": "object", "additionalProperties": False,
    "required": ["section", "text", "kind", "confidence", "refs", "quote", "quote_ref"],
    "properties": {
        "section": {"type": "string", "enum": list(DIMENSION_IDS)},
        "text": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "refs": {"type": "array", "items": {"type": "integer"}},
        "quote": {"type": "string"},
        "quote_ref": {"type": "integer"},
    },
}
MAP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["observations"],
              "properties": {"observations": {"type": "array", "items": _OBSERVATION}}}
_STRINGS = {"type": "array", "items": {"type": "string"}}
SECTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "claims", "unknowns", "questions"],
    "properties": {
        "summary": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "kind", "confidence", "as_of", "basis", "contradictions",
                         "implication", "needs_confirmation"],
            "properties": {
                "text": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
                "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                "as_of": {"type": "string"}, "basis": _STRINGS, "contradictions": _STRINGS,
                "implication": {"type": "string"}, "needs_confirmation": {"type": "boolean"},
            }}},
        "unknowns": _STRINGS, "questions": _STRINGS,
    },
}
RULES_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["rules", "open_questions", "recent_changes", "summary"],
    "properties": {
        "rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["scope", "when", "action", "avoid", "basis"],
            "properties": {"scope": {"type": "string"}, "when": {"type": "string"},
                           "action": {"type": "string"}, "avoid": {"type": "string"},
                           "basis": _STRINGS}}},
        "open_questions": _STRINGS, "recent_changes": _STRINGS, "summary": {"type": "string"},
    },
}


@dataclass
class Window:
    source: str
    period: str  # месяц или «весь период»
    text: str = ""
    refs: list[str] = field(default_factory=list)  # номер записи в окне (с 1) → ref корпуса
    texts: dict[str, str] = field(default_factory=dict)  # ref → полный текст (для цитат)

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{MAP_VERSION}\n{self.text}".encode("utf-8")).hexdigest()

    @property
    def title(self) -> str:
        return f"{LABELS.get(self.source, self.source)}, {self.period}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " […]"


def _speaker(source: str, role: str, metadata: dict) -> str:
    if role == "user":
        return "Я"  # в заголовке окна: «Я»; пользователь
    if source == "messages":
        return "Аня" if role == "assistant" else role
    if role in ("secondary", "derived"):
        return "запись"
    return str(metadata.get("author") or ("собеседник" if source == "telegram" else "запись"))


def build_windows(corpus_dir: Path) -> list[Window]:
    """Окна для наблюдений: источник → (месяц) → беседа → время."""
    db = _readonly(corpus_dir / "evidence.sqlite")
    try:
        rows = db.execute("SELECT ref, source, ts, role, text, metadata FROM evidence "
                          "WHERE represented_by IS NULL").fetchall()
    finally:
        db.close()
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        source, role = row["source"], row["role"]
        if source not in MAPPED_SOURCES and role != "user":
            continue
        if not (row["text"] or "").strip():
            continue
        metadata = json.loads(row["metadata"] or "{}")
        period = (row["ts"] or "")[:7] if source in BY_MONTH else "весь период"
        groups[(source, period or "без даты")].append((row, metadata))
    windows = []
    for (source, period), items in sorted(groups.items()):
        items.sort(key=lambda it: (str(it[1].get("thread") or ""), it[0]["ts"] or "", it[0]["ref"]))
        window, thread = Window(source, period), None
        for row, metadata in items:
            current = str(metadata.get("thread") or metadata.get("title") or "")
            header = f"\n## {current}\n" if current and current != thread else ""
            limit = ITEM_CHARS.get(source, DEFAULT_ITEM_CHARS)
            body = " ".join(_clip(row["text"], limit).split()) if source != "document" \
                else _clip(row["text"], limit)
            # месяц уже в заголовке окна: день и время короче
            when = (row["ts"] or "")[8:16].replace("T", " ") if source in BY_MONTH \
                else (row["ts"] or "")[:10]
            line = f"[{len(window.refs) + 1}] {when} {_speaker(source, row['role'], metadata)}: {body}\n"
            if window.refs and len(window.text) + len(header) + len(line) > WINDOW_CHARS:
                windows.append(window)
                window, header = Window(source, period), f"\n## {current}\n" if current else ""
                line = f"[1] {when} {_speaker(source, row['role'], metadata)}: {body}\n"
            window.text += header + line
            window.refs.append(row["ref"])
            window.texts[row["ref"]] = row["text"]
            thread = current
        if window.refs:
            windows.append(window)
    return windows


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def clean_observations(window: Window, raw: list[dict]) -> list[dict]:
    """Номера записей → ссылки корпуса; без опоры: отброшено; цитата; только дословная."""
    result = []
    for item in raw[:MAX_OBSERVATIONS]:
        if item.get("section") not in DIMENSION_IDS or not str(item.get("text", "")).strip():
            continue
        refs = [window.refs[n - 1] for n in dict.fromkeys(item.get("refs") or [])
                if isinstance(n, int) and 1 <= n <= len(window.refs)][:6]
        if not refs:
            continue
        observation = {"section": item["section"], "text": str(item["text"]).strip()[:1500],
                       "kind": item.get("kind") if item.get("kind") in KINDS else "hypothesis",
                       "confidence": item.get("confidence") if item.get("confidence") in CONFIDENCE
                       else "low",
                       "refs": refs, "source": window.source, "period": window.period}
        quote, number = str(item.get("quote") or "").strip(), item.get("quote_ref")
        if quote and isinstance(number, int) and 1 <= number <= len(window.refs):
            ref = window.refs[number - 1]
            if _normal(quote) in _normal(window.texts.get(ref, "")):
                observation["quote"] = {"ref": ref, "quote": quote[:300]}
                if ref not in refs:
                    observation["refs"].append(ref)
        result.append(observation)
    return result


def _observation_lines(observations: list[dict]) -> tuple[str, dict[str, dict]]:
    """Наблюдения раздела для модели (o-номера); не влезает: сначала уверенное и свежее."""
    rank = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(observations, key=lambda o: (rank[o["confidence"]], o["period"]),
                     reverse=False)
    chosen, size = [], 0
    for item in ordered:
        line = f"[{item['kind']}, {item['confidence']}] ({item['period']}, " \
               f"{LABELS.get(item['source'], item['source'])}) {item['text']}"
        if size + len(line) > REDUCE_CHARS:
            continue
        chosen.append((item, line))
        size += len(line)
    chosen.sort(key=lambda pair: pair[0]["period"])  # хронология: видно, как менялось
    by_id, lines = {}, []
    for index, (item, line) in enumerate(chosen, 1):
        by_id[f"o{index}"] = item
        quote = f" Цитата: «{item['quote']['quote']}»" if item.get("quote") else ""
        lines.append(f"o{index} {line}{quote}")
    return "\n".join(lines), by_id


def build_section(dimension: tuple[str, str, str], data: dict, by_id: dict[str, dict]) -> AnalysisSection:
    """Ответ модели → проверенный раздел: опоры только из наблюдений, цитаты; их же."""
    section_id, title, _ = dimension
    claims = []
    for item in data.get("claims") or []:
        basis = [by_id[b] for b in dict.fromkeys(item.get("basis") or []) if b in by_id]
        text = str(item.get("text", "")).strip()
        if not basis or not text:
            continue
        refs = list(dict.fromkeys(ref for o in basis for ref in o["refs"]))[:100]
        quotes = []
        for o in basis:
            quote = o.get("quote")
            if quote and quote["ref"] in refs and all(q.ref != quote["ref"] for q in quotes):
                quotes.append(EvidenceQuote(ref=quote["ref"], quote=quote["quote"]))
        claims.append(AnalysisClaim(
            id=f"{section_id}.{len(claims) + 1:02d}", text=text[:6000],
            kind=item.get("kind") if item.get("kind") in KINDS else "hypothesis",
            confidence=item.get("confidence") if item.get("confidence") in CONFIDENCE else "low",
            as_of=str(item.get("as_of") or "")[:100] or None, evidence_refs=refs,
            evidence_quotes=quotes[:30],
            contradictions=[str(c)[:500] for c in item.get("contradictions") or []][:50],
            assistant_implication=str(item.get("implication") or "")[:3000],
            needs_confirmation=bool(item.get("needs_confirmation"))))
    return AnalysisSection(
        id=section_id, title=title, summary=str(data.get("summary") or "")[:12_000],
        claims=claims[:500], unknowns=[str(u)[:1000] for u in data.get("unknowns") or []][:100],
        questions=[str(q)[:1000] for q in data.get("questions") or []][:50])


def build_rules(data: dict, claim_ids: set[str]) -> list[SupportRule]:
    rules = []
    for item in data.get("rules") or []:
        basis = [b for b in dict.fromkeys(item.get("basis") or []) if b in claim_ids]
        if not basis or not str(item.get("when", "")).strip() or not str(item.get("action", "")).strip():
            continue
        rules.append(SupportRule(id=f"rule.{len(rules) + 1:02d}", when=str(item["when"])[:2000],
                                 action=str(item["action"])[:3000],
                                 avoid=str(item.get("avoid") or "")[:2000], basis_claim_ids=basis,
                                 scope=str(item.get("scope") or "общее")[:300]))
    return rules[:200]


def _claims_listing(sections: list[AnalysisSection]) -> str:
    lines = []
    for section in sections:
        lines.append(f"## {section.title}\n{section.summary}")
        lines += [f"{c.id} [{c.kind}, {c.confidence}] {c.text}"
                  + (f" → {c.assistant_implication}" if c.assistant_implication else "")
                  for c in section.claims]
    return "\n".join(lines)


def _derive(db) -> list[dict]:
    """Производные записи снимка: метрики поведения и поправки пользователя к профилю."""
    return behavior.derive(db) + interview.feedback_evidence()


class Profiler:
    """Фоновая служба: строит и раз в неделю обновляет профиль пользователя."""

    def __init__(self, settings, store, assistant, investigation, notify):
        self.settings, self.store, self.assistant, self.investigation, self.notify = \
            settings, store, assistant, investigation, notify
        self.base = config.DATA_DIR / "personalization"
        self.cache = self.base / "map"
        self.prompt_file = settings.workspace / "system_prompt_profile.md"
        self.prompt_file.write_text((Path(__file__).parent / "prompts" / "profiling.md")
                                    .read_text(encoding="utf-8"), encoding="utf-8")
        self.state = {"stage": "ждёт", "done": 0, "total": 0}
        self.requested = False
        self._wake: asyncio.Event | None = None
        self._build_lock = asyncio.Lock()

    def request(self) -> None:
        """Перестроить сейчас, не дожидаясь недели (кеш наблюдений сохраняется)."""
        self.requested = True
        if self._wake is not None:
            self._wake.set()

    def status(self) -> dict:
        profile = load_profile(config.DATA_DIR)
        return {**self.state, "built_at": self.store.cursor("profile_at"),
                "profile_id": profile.profile_id if profile else None,
                "claims": sum(len(s.claims) for s in profile.sections) if profile else 0,
                "rules": len(profile.support_rules) if profile else 0,
                "report": str(config.OUTPUT_DIR / "Профиль.md") if profile else None}

    # --- расписание ---

    def _due(self) -> bool:
        if self.requested:
            return True
        made = self.store.cursor("profile_at")
        if made is None or load_profile(config.DATA_DIR) is None:
            return True
        old = datetime.now() - datetime.fromisoformat(made) > REBUILD_EVERY
        grown = sum(self.store.event_counts().values()) > int(self.store.cursor("profile_events") or 0)
        return old and grown

    async def run(self) -> None:
        self._wake = asyncio.Event()
        await asyncio.sleep(600)
        while True:
            if self._due():
                self.requested = False
                try:
                    await self.build()
                except BrainError as e:
                    log.error("Профиль не построен: %s", e)
                    self.state["stage"] = f"ошибка: {e.friendly()}"
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), CHECK_EVERY)
            except TimeoutError:
                pass

    async def _turn(self) -> None:
        """Лимит подписки общий: уступить чату и очереди разбора."""
        waited = datetime.now()
        while True:
            quiet_left = (self.investigation._last_chat + timedelta(minutes=10)
                          - datetime.now()).total_seconds()
            if quiet_left > 0:
                await asyncio.sleep(quiet_left)
            elif self.store.job_counts().get("pending", 0) and datetime.now() - waited < MAX_DEFER:
                await asyncio.sleep(600)
            else:
                return

    async def _ask(self, prompt: str, schema: dict, model: str | None = None) -> dict:
        while True:
            try:
                async with self.assistant._analysis_lock:
                    answer = await self.assistant.brain.ask(
                        prompt, system_prompt_file=self.prompt_file, schema=schema,
                        model=model, timeout=CALL_TIMEOUT)
                return answer.data
            except BrainError as e:
                if not await self.assistant.brain.wait_out(e):
                    raise

    async def _rest(self) -> None:
        await asyncio.sleep(self.investigation._pause())

    # --- построение ---

    async def _observe(self, window: Window) -> list[dict] | None:
        path = self.cache / f"{window.key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["observations"]
        dimensions = "\n".join(f"- {d[0]} — {d[1]}: {d[2]}" for d in DIMENSIONS)
        prompt = (f"<data source=\"{window.title}\" note=\"«Я» — пользователь; время — день и "
                  f"часы\">\n{window.text}\n</data>\n\n<message>\n"
                  f"{MAP_TASK.format(dimensions=dimensions, limit=MAX_OBSERVATIONS)}\n</message>")
        for attempt in range(1, MAP_ATTEMPTS + 1):
            await self._turn()
            try:
                data = await self._ask(prompt, MAP_SCHEMA, MAP_MODEL)
            except BrainError as e:
                log.warning("Профиль: окно «%s», попытка %d: %s", window.title, attempt, e)
                await self._rest()
                continue
            observations = clean_observations(window, data.get("observations") or [])
            self.cache.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": MAP_VERSION, "title": window.title,
                                        "observations": observations}, ensure_ascii=False),
                            encoding="utf-8")
            log.info("Профиль: %s — наблюдений %d", window.title, len(observations))
            await self._rest()
            return observations
        return None

    async def build(self) -> Path | None:
        async with self._build_lock:
            return await self._build()

    async def _build(self) -> Path | None:
        started = datetime.now()
        stamp = started.strftime("%Y%m%d-%H%M%S-%f")
        corpus_dir = self.base / "corpus" / stamp
        self.state = {"stage": "снимок данных", "done": 0, "total": 0}
        # прерванные построения оставляют снимки (~0,4 ГБ); разобранное хранит кеш наблюдений
        for old in (self.base / "corpus").glob("*") if (self.base / "corpus").is_dir() else ():
            await asyncio.to_thread(shutil.rmtree, old, True)
        manifest = await asyncio.to_thread(prepare_corpus, self.settings.db_path,
                                           corpus_dir, derive=_derive)
        windows = await asyncio.to_thread(build_windows, corpus_dir)
        self.state = {"stage": "наблюдения", "done": 0, "total": len(windows)}
        log.info("Профиль: снимок %s, окон %d", manifest["corpus_id"][:8], len(windows))

        observations, skipped = [], []
        for window in windows:
            found = await self._observe(window)
            if found is None:
                skipped.append(window.title)
            else:
                observations += found
            self.state["done"] += 1

        by_section: dict[str, list[dict]] = defaultdict(list)
        for item in observations:
            by_section[item["section"]].append(item)
        sections = []
        self.state = {"stage": "разделы", "done": 0, "total": len(DIMENSIONS)}
        for dimension in DIMENSIONS:
            listing, by_id = _observation_lines(by_section.get(dimension[0], []))
            if not by_id:
                sections.append(AnalysisSection(id=dimension[0], title=dimension[1],
                                                unknowns=["В данных нет наблюдений по этому измерению."]))
                continue
            rejected = [f"- {i['claim']}" + (f" (он: {i['comment']})" if i.get("comment") else "")
                        for i in interview.feedback() if i["verdict"] == "wrong"
                        and i["claim_id"].split(".")[0] == dimension[0]]
            rejected_block = ("\n\n<rejected note=\"он сказал, что это про него неверно: не "
                              "повторяй без новых, более сильных оснований\">\n"
                              + "\n".join(rejected) + "\n</rejected>") if rejected else ""
            await self._turn()
            data = await self._ask(
                f"<data source=\"наблюдения: {dimension[1]}\">\n{listing}\n</data>{rejected_block}"
                f"\n\n<message>\n"
                f"{SECTION_TASK.format(title=dimension[1], focus=dimension[2])}\n</message>",
                SECTION_SCHEMA)
            sections.append(build_section(dimension, data, by_id))
            self.state["done"] += 1
            await self._rest()

        self.state = {"stage": "правила поддержки", "done": 0, "total": 1}
        previous = load_profile(config.DATA_DIR)
        before = f"\n\n<previous_profile>\n{_claims_listing(previous.sections)[:40_000]}\n" \
                 f"</previous_profile>" if previous else ""
        await self._turn()
        data = await self._ask(f"<data source=\"профиль\">\n{_claims_listing(sections)}\n</data>"
                               f"{before}\n\n<message>\n{RULES_TASK}\n</message>", RULES_SCHEMA)
        claim_ids = {c.id for s in sections for c in s.claims}

        used = sorted({ref for s in sections for c in s.claims for ref in c.evidence_refs})
        if not await asyncio.to_thread(validate_refs, corpus_dir, used):
            raise BrainError("в профиле есть ссылки вне снимка данных")
        totals = manifest["sources"]
        profile = UserProfile(
            profile_id=f"p{stamp}-{manifest['corpus_id'][:8]}",
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            data_cutoff_at=max((w.period for w in windows if w.period[:1].isdigit()), default=stamp),
            status="complete_for_snapshot",
            coverage={
                "corpus_id": manifest["corpus_id"],
                "summary": str(data.get("summary") or "")[:2000],
                "read_in_full": {s: totals[s]["items"] for s in sorted(totals)
                                 if s in MAPPED_SOURCES},
                "own_words_only": [s for s in sorted(totals) if s not in MAPPED_SOURCES],
                "via_metrics_and_summaries": [s for s in VIA_SUMMARIES if s in totals],
                "windows": len(windows), "observations": len(observations),
                "skipped_windows": skipped,
                "not_visible": ["жизнь вне компьютера и Telegram видна только косвенно",
                                "сон — оценка по цифровым следам, не измерение",
                                "голос, лицо, тело, интонации не анализируются"],
            },
            sections=sections, support_rules=build_rules(data, claim_ids),
            open_questions=[str(q)[:1000] for q in data.get("open_questions") or []][:200],
            recent_changes=[str(c)[:1000] for c in data.get("recent_changes") or []][:200])
        report = render_report(profile)
        target = await asyncio.to_thread(save_profile, config.DATA_DIR, profile,
                                         available_refs=used, report=report)
        try:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (config.OUTPUT_DIR / "Профиль.md").write_text(report, encoding="utf-8")
        except OSError:
            log.warning("Не удалось положить профиль в %s", config.OUTPUT_DIR)
        self.store.set_cursor("profile_at", datetime.now().isoformat(timespec="seconds"))
        self.store.set_cursor("profile_events", str(sum(self.store.event_counts().values())))
        await asyncio.to_thread(self._cleanup, corpus_dir)
        claims = sum(len(s.claims) for s in sections)
        self.state = {"stage": "готов", "done": claims, "total": claims,
                      "profile_id": profile.profile_id}
        self.notify(f"🧭 Профиль обновлён: {claims} утверждений, правил поддержки — "
                    f"{len(profile.support_rules)}.\n{data.get('summary', '')}\n"
                    f"Файл: {config.OUTPUT_DIR / 'Профиль.md'}")
        log.info("Профиль опубликован: %s (%s)", target, datetime.now() - started)
        return target

    def _cleanup(self, keep: Path) -> None:
        """Снимки данных большие (копия базы): хранить только последние."""
        corpora = sorted(p for p in (self.base / "corpus").iterdir() if p.is_dir())
        for old in corpora[:-KEEP_CORPORA]:
            if old != keep:
                shutil.rmtree(old, ignore_errors=True)
        live = {w.key for w in build_windows(keep)}
        for path in self.cache.glob("*.json"):
            if path.stem not in live and \
                    datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) > timedelta(days=60):
                path.unlink(missing_ok=True)
