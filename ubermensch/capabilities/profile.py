from __future__ import annotations

import asyncio
from typing import Literal

from .. import interview
from .registry import registry


@registry.tool()
async def profile_feedback(claim_id: str, verdict: Literal["wrong", "outdated", "right"],
                           comment: str = "") -> str:
    """Поправка пользователя к выводу о нём из <user_profile> (id вида «values.03»): wrong -
    неверно, outdated: было, но уже не так, right; подтвердил. comment; его слова, как
    на самом деле. Неверный вывод сразу перестаёт попадать в разговоры, все поправки
    учитываются при перестройке профиля. Вызывай только по его словам, не по догадке."""
    try:
        await asyncio.to_thread(interview.add_feedback, claim_id, verdict, comment)
    except KeyError:
        return f"В текущем профиле нет вывода {claim_id}."
    return {"wrong": "Отметила как неверное — больше на это не опираюсь.",
            "outdated": "Отметила как устаревшее.",
            "right": "Отметила как подтверждённое."}[verdict]
