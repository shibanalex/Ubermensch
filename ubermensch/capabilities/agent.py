from __future__ import annotations

import asyncio
from typing import Literal

from .. import agent
from .registry import registry


@registry.tool()
async def delegate_task(title: str, instructions: str, due: str = "") -> str:
    """Поручение себе на фоновую работу: разобраться, найти, сравнить, подготовить — в несколько
    шагов, с поиском и инструментами; по готовности — отчёт ему. instructions — что именно
    сделать и каким должен быть результат; due — срок ГГГГ-ММ-ДД, если есть. Для того, что
    дольше одного ответа в чате."""
    task = await asyncio.to_thread(agent.add_task, title, instructions, due=due or None)
    return f"Поручение {task['id']} «{task['title']}» принято, займусь в фоне."


@registry.tool()
async def schedule_task(title: str, instructions: str, every: Literal["day", "week", "hours"],
                        at: str = "09:00", weekday: int = 0, hours: int = 0) -> str:
    """Регулярное поручение: every=day — каждый день в at (ЧЧ:ММ); week — по дню недели weekday
    (0 — понедельник … 6 — воскресенье) в at; hours — каждые hours часов. Например, обзор
    недели по понедельникам в 10:00."""
    try:
        schedule = await asyncio.to_thread(agent.add_schedule, title, instructions, every, at,
                                           weekday if every == "week" else None,
                                           hours if every == "hours" else None)
    except ValueError as error:
        return f"Не поставила: {error}"
    return f"Поставила: {agent.describe_schedule(schedule)}"


@registry.tool()
async def situation_note(text: str) -> str:
    """Записать изменение в «Положение дел» — нить его событий между днями: новость, решение,
    перенос, отказ, новая дата («поездка к бабушке — 5–9.11, билеты не куплены»). Одна строка,
    с фактом и датой. Вечером ты вплетаешь изменения в полный текст."""
    if not text.strip():
        return "Пустая запись — нечего добавить"
    await asyncio.to_thread(agent.note_situation, text[:500])
    return "Записала в «Положение дел»"


@registry.tool()
async def agent_tasks() -> str:
    """Мои поручения (ждут и выполняются) и расписание."""
    items = agent.tasks("pending") + agent.tasks("running")
    lines = [f"{t['id']} [{t['status']}] {t['title']}" + (f", срок {t['due']}" if t["due"] else "")
             for t in items]
    lines += [f"расписание {agent.describe_schedule(s)}" for s in agent.schedules()]
    return "\n".join(lines) or "Поручений и расписания нет"


@registry.tool()
async def resume_agent_task(task_id: str) -> str:
    """Продолжить прерванное поручение — только когда он сказал продолжать (после того как
    проверил, что успело выполниться). Уже сделанное следующий заход не повторит."""
    return "Продолжу." if agent.resume_task(task_id) else "Нет прерванного поручения с таким id."


@registry.tool()
async def cancel_agent_task(task_id: str) -> str:
    """Отменить поручение или регулярное расписание по id (из agent_tasks)."""
    if agent.cancel_task(task_id) or agent.cancel_schedule(task_id):
        return f"Отменено: {task_id}"
    return f"Нет активного поручения или расписания {task_id}"


@registry.tool()
async def save_skill(name: str, when: str, steps: str) -> str:
    """Записать себе навык — удачный способ решать повторяющуюся задачу (шаги, инструменты,
    где что лежит, на что смотреть). when — в каких случаях пригодится."""
    path = await asyncio.to_thread(agent.save_skill, name, when, steps)
    return f"Навык записан: {path}"
