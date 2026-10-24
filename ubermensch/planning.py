"""Measured goals, career pipeline and recommendation outcomes, independent of the LLM."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PLANNING_MIGRATION = """
CREATE TABLE planning_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE goal_plans (
    goal_id INTEGER PRIMARY KEY REFERENCES goals(id),
    metric TEXT NOT NULL, unit TEXT NOT NULL, baseline REAL NOT NULL, target REAL NOT NULL,
    weekly_minutes INTEGER NOT NULL, next_step TEXT NOT NULL, evidence TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE goal_measurements (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    value REAL NOT NULL, observed_on TEXT NOT NULL, evidence TEXT NOT NULL,
    UNIQUE(goal_id, observed_on, evidence)
);
CREATE TABLE career_opportunities (
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
    title TEXT NOT NULL, company TEXT NOT NULL, url TEXT NOT NULL, details TEXT NOT NULL,
    first_seen TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(source, external_id)
);
CREATE TABLE career_assessments (
    opportunity_id INTEGER PRIMARY KEY REFERENCES career_opportunities(id),
    goal_id INTEGER REFERENCES goals(id), fit TEXT NOT NULL, evidence TEXT NOT NULL,
    gaps TEXT NOT NULL, eligibility TEXT NOT NULL, next_step TEXT NOT NULL,
    minutes INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE career_drafts (
    id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES career_opportunities(id),
    resume TEXT NOT NULL, letter TEXT NOT NULL, evidence TEXT NOT NULL,
    digest TEXT NOT NULL, resume_digest TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(opportunity_id, digest)
);
CREATE TABLE career_events (
    id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES career_opportunities(id),
    stage TEXT NOT NULL CHECK(stage IN ('applied','reply','interview','offer','rejected','withdrawn')),
    draft_id INTEGER REFERENCES career_drafts(id), observed_on TEXT NOT NULL,
    evidence TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(opportunity_id, stage, observed_on, evidence)
);
CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    action TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL,
    success TEXT NOT NULL, minutes INTEGER NOT NULL, review_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed','accepted','done','declined')),
    outcome TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE recommendation_outcomes (
    id INTEGER PRIMARY KEY, recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    status TEXT NOT NULL, outcome TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX career_events_date ON career_events(observed_on, opportunity_id);
"""


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def required(value: str, name: str, limit=12000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name}: нужен непустой текст до {limit} символов')
    return value.strip()


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name}: нужно конечное число')
    return float(value)


def minutes(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10080:
        raise ValueError('Время должно быть целым числом от 0 до 10080 минут')
    return value


def day(value: str, *, past=False):
    result = date.fromisoformat(value)
    if result.isoformat() != value or (past and result > date.today()):
        raise ValueError('Нужна дата ГГГГ-ММ-ДД; наблюдение не может быть из будущего')
    return value


def canonical_url(value: str):
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('Нужна HTTPS-ссылка без учётных данных')
    query = [(k, v) for k, v in parse_qsl(url.query) if not k.startswith('utm_')]
    return urlunsplit(('https', url.netloc.lower(), url.path, urlencode(query), ''))


class PlanningStorage:
    def planning_setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM planning_settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_planning_setting(self, key, value):
        self.db.execute('INSERT INTO planning_settings VALUES (?,?) '
                        'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, encoded(value)))

    def _planning_goal(self, goal_id):
        row = self.db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
        if row is None or row['status'] != 'active':
            raise ValueError('Нужен ID существующей активной цели')
        return row

    def save_goal_plan(self, goal_id, metric, unit, baseline, target, weekly_minutes, next_step, evidence):
        self._planning_goal(goal_id)
        metric, unit = required(metric, 'Показатель', 200), required(unit, 'Единица', 80)
        baseline, target = number(baseline, 'Исходное значение'), number(target, 'Цель')
        if baseline == target:
            raise ValueError('Исходное и целевое значение должны отличаться')
        weekly_minutes = minutes(weekly_minutes)
        next_step, evidence = required(next_step, 'Следующий шаг'), required(evidence, 'Основание')
        old = self.db.execute('SELECT * FROM goal_plans WHERE goal_id=?', (goal_id,)).fetchone()
        if old and (old['metric'], old['unit'], old['baseline']) != (metric, unit, baseline):
            raise ValueError('Показатель, единицу и исходную точку нельзя менять у существующего '
                             'плана: заведите отдельную цель, чтобы сохранить смысл замеров')
        self.db.execute('INSERT INTO goal_plans VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(goal_id) '
                        'DO UPDATE SET target=excluded.target, weekly_minutes=excluded.weekly_minutes, '
                        'next_step=excluded.next_step,evidence=excluded.evidence,updated_at=excluded.updated_at',
                        (goal_id, metric, unit, baseline, target, weekly_minutes, next_step, evidence, stamp()))
        return self.measured_goals()

    def measure_goal(self, goal_id, value, observed_on, evidence):
        self._planning_goal(goal_id)
        if not self.db.execute('SELECT 1 FROM goal_plans WHERE goal_id=?', (goal_id,)).fetchone():
            raise ValueError('Сначала задайте измеримый план цели')
        value, observed_on = number(value, 'Замер'), day(observed_on, past=True)
        evidence = required(evidence, 'Источник замера')
        self.db.execute('INSERT INTO goal_measurements(goal_id,value,observed_on,evidence) VALUES (?,?,?,?) '
                        'ON CONFLICT(goal_id,observed_on,evidence) DO UPDATE SET value=excluded.value',
                        (goal_id, value, observed_on, evidence))
        return self.measured_goals()

    def measured_goals(self):
        result = []
        for row in self.db.execute('SELECT p.*,g.title,g.deadline FROM goal_plans p '
                                   "JOIN goals g ON g.id=p.goal_id WHERE g.status='active' ORDER BY p.goal_id"):
            item = dict(row)
            latest = self.db.execute('SELECT value,observed_on,evidence FROM goal_measurements '
                'WHERE goal_id=? ORDER BY observed_on DESC,id DESC LIMIT 1', (row['goal_id'],)).fetchone()
            item['measurement'] = dict(latest) if latest else None
            item['current'] = latest['value'] if latest else row['baseline']
            progress = (item['current'] - row['baseline']) / (row['target'] - row['baseline']) * 100
            item['progress_percent'] = round(progress, 1) if math.isfinite(progress) else None
            result.append(item)
        return result

    def save_opportunity(self, source, external_id, title, company, url, details):
        source, external_id = required(source, 'Источник', 40), required(external_id, 'ID', 500)
        title, company = required(title, 'Вакансия', 500), required(company, 'Компания', 500)
        url = canonical_url(url)
        serialized = encoded(details)
        if len(serialized) > 60000:
            raise ValueError('Описание вакансии слишком длинное')
        self.db.execute('INSERT INTO career_opportunities(source,external_id,title,company,url,details,'
            'first_seen,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id) '
            'DO UPDATE SET title=excluded.title,company=excluded.company,url=excluded.url,'
            'details=excluded.details,updated_at=excluded.updated_at',
            (source, external_id, title, company, url, serialized, stamp(), stamp()))
        row = self.db.execute('SELECT id FROM career_opportunities WHERE source=? AND external_id=?',
                              (source, external_id)).fetchone()
        return self.opportunity(row['id'])

    def opportunity(self, opportunity_id):
        row = self.db.execute('SELECT * FROM career_opportunities WHERE id=?', (opportunity_id,)).fetchone()
        if row is None:
            raise ValueError('Вакансия не найдена')
        item = dict(row)
        item['details'] = json.loads(item['details'])
        assessment = self.db.execute('SELECT * FROM career_assessments WHERE opportunity_id=?',
                                      (opportunity_id,)).fetchone()
        item['assessment'] = dict(assessment) if assessment else None
        item['events'] = [dict(r) for r in self.db.execute('SELECT * FROM career_events '
            'WHERE opportunity_id=? ORDER BY observed_on,id', (opportunity_id,))]
        item['drafts'] = [dict(r) for r in self.db.execute('SELECT id,digest,created_at FROM career_drafts '
            'WHERE opportunity_id=? ORDER BY id', (opportunity_id,))]
        return item

    def opportunities(self, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError('limit должен быть от 1 до 100')
        rows = self.db.execute('SELECT id,source,title,company,url,updated_at FROM career_opportunities '
                               'ORDER BY updated_at DESC,id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    def assess_opportunity(self, opportunity_id, goal_id, fit, evidence, gaps, eligibility, next_step, effort):
        self.opportunity(opportunity_id)
        self._planning_goal(goal_id)
        if eligibility not in {'yes', 'no', 'unknown'}:
            raise ValueError('Допуск: yes/no/unknown; отсутствие сведений означает unknown')
        fields = [required(v, name) for v, name in
                  ((fit, 'Почему подходит'), (evidence, 'Основание'), (gaps, 'Пробелы'), (next_step, 'Шаг'))]
        fit, evidence, gaps, next_step = fields
        self.db.execute('INSERT INTO career_assessments VALUES (?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(opportunity_id) DO UPDATE SET goal_id=excluded.goal_id,fit=excluded.fit,'
            'evidence=excluded.evidence,gaps=excluded.gaps,eligibility=excluded.eligibility,'
            'next_step=excluded.next_step,minutes=excluded.minutes,updated_at=excluded.updated_at',
            (opportunity_id, goal_id, fit, evidence, gaps, eligibility, next_step, minutes(effort), stamp()))
        return self.opportunity(opportunity_id)

    def save_career_draft(self, opportunity_id, resume, letter, evidence):
        self.opportunity(opportunity_id)
        resume, letter, evidence = (required(resume, 'Резюме', 30000), required(letter, 'Письмо'),
                                    required(evidence, 'Основания утверждений'))
        digest = hashlib.sha256(encoded([resume, letter, evidence]).encode()).hexdigest()
        resume_digest = hashlib.sha256(resume.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO career_drafts(opportunity_id,resume,letter,evidence,digest,'
                        'resume_digest,created_at) VALUES (?,?,?,?,?,?,?)',
                        (opportunity_id, resume, letter, evidence, digest, resume_digest, stamp()))
        return dict(self.db.execute('SELECT * FROM career_drafts WHERE opportunity_id=? AND digest=?',
                                    (opportunity_id, digest)).fetchone())

    def career_draft(self, draft_id):
        row = self.db.execute('SELECT * FROM career_drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise ValueError('Версия отклика не найдена')
        return dict(row)

    def record_career_event(self, opportunity_id, stage, evidence, observed_on, draft_id=None):
        item = self.opportunity(opportunity_id)
        if stage not in {'applied', 'reply', 'interview', 'offer', 'rejected', 'withdrawn'}:
            raise ValueError('Неизвестный этап отклика')
        evidence, observed_on = required(evidence, 'Подтверждение события'), day(observed_on, past=True)
        applied = [e for e in item['events'] if e['stage'] == 'applied']
        if stage != 'applied' and (not applied or observed_on < min(e['observed_on'] for e in applied)):
            raise ValueError('Сначала запишите отправленный отклик; последующие события не могут быть раньше него')
        if stage == 'applied' and applied:
            # One application per vacancy; edits are new draft versions, not more sends.
            return item
        if draft_id is not None and self.career_draft(draft_id)['opportunity_id'] != opportunity_id:
            raise ValueError('Версия материалов принадлежит другой вакансии')
        self.db.execute('INSERT OR IGNORE INTO career_events(opportunity_id,stage,draft_id,observed_on,'
            'evidence,created_at) VALUES (?,?,?,?,?,?)',
            (opportunity_id, stage, draft_id, observed_on, evidence, stamp()))
        return self.opportunity(opportunity_id)

    def career_funnel(self, *, today=None, maturity_days=7):
        today = today or date.today()
        groups = {}
        for app in self.db.execute("SELECT e.*,o.source,d.resume_digest FROM career_events e "
                                   "JOIN career_opportunities o ON o.id=e.opportunity_id "
                                   "LEFT JOIN career_drafts d ON d.id=e.draft_id WHERE e.stage='applied'"):
            key = (app['source'], app['resume_digest'])
            group = groups.setdefault(key, {'source': key[0], 'resume_version': key[1], 'applied': 0,
                'reply': 0, 'interview': 0, 'offer': 0, 'rejected': 0, 'mature': 0, 'mature_reply': 0})
            stages = {r[0] for r in self.db.execute('SELECT stage FROM career_events WHERE opportunity_id=? '
                                                   'AND observed_on<=?', (app['opportunity_id'], today.isoformat()))}
            if date.fromisoformat(app['observed_on']) > today:
                continue
            group['applied'] += 1
            replied = bool(stages & {'reply', 'interview', 'offer', 'rejected'})
            group['reply'] += int(replied)
            group['interview'] += int(bool(stages & {'interview', 'offer'}))
            group['offer'] += int('offer' in stages)
            group['rejected'] += int('rejected' in stages)
            if (today - date.fromisoformat(app['observed_on'])).days >= maturity_days:
                group['mature'] += 1
                group['mature_reply'] += int(replied)
        for group in groups.values():
            group['reply_rate'] = round(group['mature_reply'] / group['mature'], 3) if group['mature'] else None
        return {'maturity_days': maturity_days, 'groups': list(groups.values()),
                'note': 'Ответ включает отказ. Offer учитывается и как интервью. '
                        'Малые группы и разные вакансии не доказывают преимущество версии резюме.'}

    def save_recommendation(self, goal_id, action, reason, evidence, success, effort, review_on):
        self._planning_goal(goal_id)
        values = [required(v, name) for v, name in ((action, 'Действие'), (reason, 'Почему для пользователя'),
                  (evidence, 'Источники'), (success, 'Критерий результата'))]
        effort, review_on = minutes(effort), day(review_on)
        cursor = self.db.execute('INSERT INTO recommendations(goal_id,action,reason,evidence,success,minutes,'
            'review_on,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (goal_id, *values, effort, review_on, stamp(), stamp()))
        return cursor.lastrowid

    def recommendation_feedback(self, recommendation_id, status, outcome):
        if status not in {'accepted', 'done', 'declined'}:
            raise ValueError('Статус: accepted/done/declined')
        outcome = required(outcome, 'Ответ пользователя / результат')
        if not self.db.execute('SELECT 1 FROM recommendations WHERE id=?', (recommendation_id,)).fetchone():
            raise ValueError('Рекомендация не найдена')
        with self.transaction():
            self.db.execute('UPDATE recommendations SET status=?,outcome=?,updated_at=? WHERE id=?',
                            (status, outcome, stamp(), recommendation_id))
            self.db.execute('INSERT INTO recommendation_outcomes(recommendation_id,status,outcome,recorded_at) '
                            'VALUES (?,?,?,?)', (recommendation_id, status, outcome, stamp()))

    def planning_snapshot(self):
        goals = self.measured_goals()
        capacity = self.planning_setting('weekly_minutes')
        allocated = sum(g['weekly_minutes'] for g in goals)
        recommendations = [dict(r) for r in self.db.execute('SELECT * FROM recommendations '
            'ORDER BY updated_at DESC,id DESC LIMIT 20')]
        return {'career_profile': self.planning_setting('career_profile'), 'goals': goals,
                'time_budget': {'available_minutes': capacity, 'allocated_minutes': allocated,
                                'overbooked_minutes': max(0, allocated - capacity) if capacity is not None else None},
                'opportunities': self.opportunities(10), 'funnel': self.career_funnel(),
                'recommendations': recommendations}

    def planning_context(self):
        snapshot = self.planning_snapshot()
        # Raw job descriptions and complete drafts are fetched on demand, never all at once.
        snapshot['recommendations'] = snapshot['recommendations'][:5]
        snapshot['goals'] = snapshot['goals'][:20]
        snapshot['funnel']['groups'] = snapshot['funnel']['groups'][:20]
        def compact(value):
            if isinstance(value, str):
                return value[:800] + (' …' if len(value) > 800 else '')
            if isinstance(value, dict):
                return {k: compact(v) for k, v in value.items()}
            if isinstance(value, list):
                return [compact(v) for v in value]
            return value
        return '<personal_plan>\n' + encoded(compact(snapshot)) + '\n</personal_plan>'

    def planning_review(self, today=None):
        today = today or date.today()
        start = today - timedelta(days=6)
        measurements = [dict(r) for r in self.db.execute(
            'SELECT m.*,g.title FROM goal_measurements m JOIN goals g ON g.id=m.goal_id '
            'WHERE observed_on BETWEEN ? AND ? ORDER BY observed_on,id',
            (start.isoformat(), today.isoformat()))]
        events = [dict(r) for r in self.db.execute('SELECT stage,COUNT(DISTINCT opportunity_id) AS count '
            'FROM career_events WHERE observed_on BETWEEN ? AND ? GROUP BY stage',
            (start.isoformat(), today.isoformat()))]
        due = [dict(r) for r in self.db.execute("SELECT * FROM recommendations WHERE status IN ('proposed','accepted') "
            'AND review_on<=? ORDER BY review_on LIMIT 30', (today.isoformat(),))]
        outcomes = [dict(r) for r in self.db.execute('SELECT o.*,r.action,r.success FROM recommendation_outcomes o '
            'JOIN recommendations r ON r.id=o.recommendation_id WHERE substr(recorded_at,1,10) BETWEEN ? AND ? '
            'ORDER BY o.id LIMIT 50', (start.isoformat(), today.isoformat()))]
        return {'from': start.isoformat(), 'to': today.isoformat(), 'measurements': measurements,
                'career_events': events, 'due_recommendations': due, 'outcomes': outcomes,
                'note': 'Пропуск замера не равен нулю. Предложенные изменения требуют решения пользователя.'}
