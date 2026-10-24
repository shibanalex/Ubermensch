"""Local validation at the HTTP and model-output boundaries."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=100_000)]
Identifier = Annotated[int, Field(gt=0)]
Importance = Annotated[int, Field(ge=1, le=5)]
Area = Literal['profile', 'career', 'health', 'learning', 'discipline', 'personal', 'growth']


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', str_strip_whitespace=True)


def calendar_date(value: str | None) -> str | None:
    if value is not None:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError('ожидалась дата YYYY-MM-DD')
    return value


class ChatRequest(StrictModel):
    text: Text


class ChatJobRequest(ChatRequest):
    run_id: str


class JobID(StrictModel):
    run_id: str


class TelegramQuery(StrictModel):
    query: Text
    account: Literal['personal', 'work'] = 'personal'


class TelegramHistory(StrictModel):
    chat_id: int
    limit: Annotated[int, Field(ge=1, le=200)] = 30
    account: Literal['personal', 'work'] = 'personal'


class TelegramSend(StrictModel):
    chat_id: int
    text: Text
    account: Literal['personal', 'work'] = 'personal'


class EmailSend(StrictModel):
    to: Text
    subject: Text
    body: Text


class SearchRequest(StrictModel):
    query: Text
    limit: Annotated[int, Field(ge=1, le=50)] = 5


class ReadRequest(StrictModel):
    url: Text


class TaskCreate(StrictModel):
    title: Text
    due: str | None = None
    _date = field_validator('due')(calendar_date)


class TaskEdit(StrictModel):
    title: Text | None = None
    due: str | None = None
    notes: str | None = None
    _date = field_validator('due')(calendar_date)

    @model_validator(mode='after')
    def fields_present(self):
        if not self.model_fields_set:
            raise ValueError('нужно хотя бы одно поле')
        if 'title' in self.model_fields_set and self.title is None:
            raise ValueError('название нельзя очистить')
        return self


class TaskStatus(StrictModel):
    status: Literal['open', 'done', 'dropped']


class Decision(StrictModel):
    approve: bool


class ApprovalRequest(StrictModel):
    title: Text
    details: str = ''


class ImportRequest(StrictModel):
    name: Text


class ToolRequest(StrictModel):
    name: Text
    arguments: dict
    run_id: str | None = None
    call_id: str | None = None


class MemoryOp(StrictModel):
    op: Literal['add', 'update', 'forget']
    id: Identifier | None = None
    area: Area | None = None
    content: Text | None = None
    importance: Importance | None = None

    @model_validator(mode='after')
    def required_fields(self):
        if self.op == 'add' and not self.content:
            raise ValueError('add memory требует content')
        if self.op != 'add' and self.id is None:
            raise ValueError('операция требует id')
        return self


class GoalOp(StrictModel):
    op: Literal['add', 'update', 'done', 'drop']
    id: Identifier | None = None
    area: Area | None = None
    title: Text | None = None
    details: str | None = None
    deadline: str | None = None
    _date = field_validator('deadline')(calendar_date)

    @model_validator(mode='after')
    def required_fields(self):
        if self.op == 'add' and not self.title:
            raise ValueError('add goal требует title')
        if self.op != 'add' and self.id is None:
            raise ValueError('операция требует id')
        return self


class TaskOp(StrictModel):
    op: Literal['add', 'update', 'done', 'drop']
    id: Identifier | None = None
    title: Text | None = None
    notes: str | None = None
    due: str | None = None
    goal_id: Identifier | None = None
    _date = field_validator('due')(calendar_date)

    @model_validator(mode='after')
    def required_fields(self):
        if self.op == 'add' and not self.title:
            raise ValueError('add task требует title')
        if self.op != 'add' and self.id is None:
            raise ValueError('операция требует id')
        return self


class PersonOp(StrictModel):
    op: Literal['add', 'update', 'merge']
    id: Identifier | None = None
    into: Identifier | None = None
    name: Text | None = None
    relation: str | None = None
    notes: str | None = None
    importance: Importance | None = None

    @model_validator(mode='after')
    def required_fields(self):
        if self.op == 'add' and not self.name:
            raise ValueError('add person требует name')
        if self.op != 'add' and self.id is None:
            raise ValueError('операция требует id')
        if self.op == 'merge' and (self.into is None or self.into == self.id):
            raise ValueError('merge требует другой into')
        return self


class ReminderParams(StrictModel):
    at: str
    text: Text

    @field_validator('at')
    @classmethod
    def valid_time(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        if parsed <= datetime.now():
            raise ValueError('время напоминания уже прошло')
        return value


class ActionOp(StrictModel):
    kind: Literal['reminder']
    summary: Text
    params: ReminderParams


class ModelAnswer(StrictModel):
    # Agent-specific fields (review, lessons, etc.) are not database commands.
    model_config = ConfigDict(strict=True, extra='allow', str_strip_whitespace=True)
    reply: str = ''
    emotion: str = ''
    memory: list[MemoryOp] = Field(default_factory=list)
    goals: list[GoalOp] = Field(default_factory=list)
    tasks: list[TaskOp] = Field(default_factory=list)
    people: list[PersonOp] = Field(default_factory=list)
    actions: list[ActionOp] = Field(default_factory=list)


class FeedbackRequest(StrictModel):
    message_id: Identifier | None = None
    run_id: str | None = None
    rating: Literal['helpful', 'unhelpful', 'correction']
    note: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def target(self):
        if (self.message_id is None) == (self.run_id is None):
            raise ValueError('нужен один из message_id или run_id')
        return self
