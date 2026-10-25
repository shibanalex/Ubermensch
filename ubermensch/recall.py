"""Поиск по смыслу слов без тяжёлых моделей: основы слов (Snowball) и связанный прошлый опыт.

Основа — без окончания («общежитием» → «общежит»), длинные основы короче ещё на букву
(«откликаюсь» → «отклик»), поиск — по началу слова. На синтетическом наборе с разными формами
слов это подняло hit@5 с 0.3 до 0.9 (scripts/evaluate_memory.py). Синонимы и пересказ
этим не ловятся — для них нужны эмбеддинги, если появится измеримая польза.
"""
from __future__ import annotations

import re

import snowballstemmer

_RU, _EN = snowballstemmer.stemmer("russian"), snowballstemmer.stemmer("english")
STOP = {"давай", "хочу", "что", "как", "это", "мне", "меня", "тебя", "про", "для", "или", "при", "там", "почему",
        "стоит", "когда", "каким", "какую", "какой", "помоги", "снова", "расскажи", "пожалуйста",
        "можно", "нужно", "надо", "есть", "был", "была", "было", "все", "всё", "его", "она", "они",
        "the", "and", "you", "with", "what", "how"}


def stems(text: str, limit: int = 24) -> list[str]:
    """Основы значимых слов текста, без повторов."""
    result = []
    for word in re.findall(r"\w+", text.casefold()):
        # двухбуквенные латинские сокращения (hh, cv, ai) значимы, русские («по», «на») — нет
        if word in STOP or word.isdigit() or len(word) < 2 or (len(word) == 2 and not word.isascii()):
            continue
        stem = (_RU if re.search(r"[а-яё]", word) else _EN).stemWord(word)
        if len(stem) >= 6:
            stem = stem[:-1]
        if (len(stem) >= 3 or len(word) == 2) and stem not in result:
            result.append(stem)
    return result[:limit]


def fts_query(text: str) -> str:
    """Выражение FTS5: любое из начал слов. Кавычки — чтобы основа не читалась как оператор."""
    return " OR ".join(f'"{s}"*' for s in stems(text))


def overlap(query_stems: list[str], text: str) -> int:
    """Сколько основ запроса начинают слова текста."""
    words = re.findall(r"\w+", text.casefold())
    return sum(any(w.startswith(s) for w in words) for s in query_stems)


def related_experience(store, query: str, limit: int = 6) -> list[dict]:
    """Прошлые попытки по теме запроса: заходы по целям (что делала, что мешало), советы с его
    решением и итогом, эксперименты с выводом. Новые первыми при равном совпадении."""
    wanted = stems(query)
    if not wanted:
        return []
    found = []
    for row in store.db.execute(
            "SELECT c.created_at, c.status, c.action, c.result, c.blocker, g.title FROM goal_cycles c "
            "JOIN goals g ON g.id=c.goal_id WHERE c.kind='step' ORDER BY c.id DESC LIMIT 300"):
        text = f"{row['title']} {row['action']} {row['result']} {row['blocker']}"
        found.append((overlap(wanted, text), row["created_at"], {
            "kind": "шаг по цели", "goal": row["title"], "when": row["created_at"][:10],
            "status": row["status"], "what": row["action"][:300], "result": row["result"][:300],
            "blocker": row["blocker"][:200]}))
    for row in store.db.execute(
            "SELECT r.updated_at, r.status, r.action, r.outcome, g.title FROM recommendations r "
            "JOIN goals g ON g.id=r.goal_id ORDER BY r.id DESC LIMIT 300"):
        found.append((overlap(wanted, f"{row['title']} {row['action']} {row['outcome']}"),
                      row["updated_at"], {
                          "kind": "совет", "goal": row["title"], "when": row["updated_at"][:10],
                          "status": row["status"], "what": row["action"][:300],
                          "result": row["outcome"][:300]}))
    for row in store.db.execute(
            "SELECT e.updated_at, e.status, e.verdict, e.hypothesis, e.change, e.result, g.title "
            "FROM experiments e JOIN goals g ON g.id=e.goal_id ORDER BY e.id DESC LIMIT 300"):
        found.append((overlap(wanted, f"{row['title']} {row['hypothesis']} {row['change']} {row['result']}"),
                      row["updated_at"], {
                          "kind": "эксперимент", "goal": row["title"], "when": row["updated_at"][:10],
                          "status": row["verdict"] or row["status"], "what": row["hypothesis"][:300],
                          "result": row["result"][:300]}))
    # в коротком запросе хватает одной общей основы, в длинном — нужны две
    threshold = 1 if len(wanted) <= 3 else 2
    found = [f for f in found if f[0] >= threshold]
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    return [f[2] for f in found[:limit]]
