"""Эксперименты по целям: предложить, решить (только он), замер, остановка, вывод по данным."""
from __future__ import annotations

from typing import Literal

from ..runtime import policy
from .planning import checked, store
from .registry import ToolArgumentsError, registry


@registry.tool()
async def experiment_propose(goal_id: int, subject: Literal['anya', 'owner'], sensitive: bool,
                             hypothesis: str, basis: str, change: str, metric: str, success: str,
                             stop: str, cost_minutes: int, risk: str, min_observations: int,
                             review_on: str) -> dict:
    """Предложить контролируемый эксперимент по цели. subject: anya; меняется только твой
    способ работы (начинается сразу), owner: его действия или привычки (ждёт его согласия).
    sensitive: сон, питание, тренировки, здоровье (тогда только owner; без медицинских
    вмешательств и резких нагрузок). basis: на каких его данных и источниках основана гипотеза;
    metric: как меряем; success и stop; критерии; cost_minutes; его время в неделю;
    min_observations: сколько замеров нужно для вывода; review_on; ГГГГ-ММ-ДД пересмотра."""
    return checked(store().propose_experiment, goal_id, subject, sensitive, hypothesis, basis,
                   change, metric, success, stop, cost_minutes, risk, min_observations, review_on)


@registry.tool()
async def experiment_decide(experiment_id: int, accept: bool, consent: str) -> dict:
    """Его решение по предложенному эксперименту: только с его слов в разговоре.
    consent: его слова (согласие с условиями или отказ)."""
    return checked(store().decide_experiment, experiment_id, accept, consent)


@registry.tool()
async def experiment_observe(experiment_id: int, value: str, evidence: str, observed_on: str = '') -> dict:
    """Записать замер идущего эксперимента: значение показателя и источник (его слова,
    журнал, данные сервиса). Оценки и прогнозы: не замеры. observed_on; ГГГГ-ММ-ДД."""
    return checked(store().observe_experiment, experiment_id, value, evidence, observed_on or None)


def _own_or_interactive(experiment_id: int) -> None:
    if policy.background() and checked(store().experiment, experiment_id)['subject'] == 'owner':
        raise ToolArgumentsError('Эксперимент над ним останавливает и завершает он сам или в '
                                 'разговоре с ним; в фоне — только замеры и предложение вывода в reply')


@registry.tool()
async def experiment_stop(experiment_id: int, reason: str) -> dict:
    """Остановить эксперимент: сработал критерий остановки, вред, его решение."""
    _own_or_interactive(experiment_id)
    return checked(store().stop_experiment, experiment_id, reason)


@registry.tool()
async def experiment_conclude(experiment_id: int, verdict: Literal['keep', 'adjust', 'drop', 'inconclusive'],
                              result: str, next_step: str) -> dict:
    """Вывод по замерам: keep; оставить, adjust; изменить, drop; отказаться, inconclusive -
    данных мало. keep/adjust/drop: только после min_observations замеров."""
    _own_or_interactive(experiment_id)
    return checked(store().conclude_experiment, experiment_id, verdict, result, next_step)
