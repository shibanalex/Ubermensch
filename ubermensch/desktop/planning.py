"""A readable view of persisted plans; opening it does not call a model."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton


def render_plan(data):
    if 'error' in data:
        return 'Не удалось загрузить план: ' + str(data['error'])
    plan, week = data.get('plan', {}), data.get('week', {})
    lines = ['ПЛАН И КАРЬЕРА', '']
    time = plan.get('time_budget', {})
    available = time.get('available_minutes')
    lines.append(f"На все цели: {available} мин/нед." if available is not None else 'Свободное время пока не указано.')
    lines.append(f"Распределено: {time.get('allocated_minutes', 0)} мин/нед.")
    if time.get('overbooked_minutes'):
        lines.append(f"Перегрузка: {time['overbooked_minutes']} мин/нед. Нужно выбрать приоритеты.")
    lines += ['', 'ЦЕЛИ']
    for goal in plan.get('goals', []):
        lines += [f"#{goal['goal_id']} {goal['title']}",
                  f"  {goal['metric']}: {goal['current']:g} → {goal['target']:g} {goal['unit']}",
                  f"  {goal['weekly_minutes']} мин/нед.; следующий шаг: {goal['next_step']}"]
        measurement = goal.get('measurement')
        lines.append('  Последний замер: ' + measurement['observed_on'] if measurement else '  Замеров пока нет; показана исходная точка.')
    if not plan.get('goals'):
        lines.append('Расскажи Ане о цели, желаемом результате и сроке. Она уточнит исходную точку и сохранит план.')
    profile = plan.get('career_profile')
    lines += ['', 'КАРЬЕРНЫЕ КРИТЕРИИ']
    if profile:
        for key, label in [('target_roles', 'Роли'), ('skills', 'Навыки и опыт'), ('constraints', 'Условия')]:
            lines.append(f"{label}: {profile.get(key, '')}")
    else:
        lines.append('Укажи роли, опыт, язык, географию и условия работы в разговоре с Аней.')
    lines += ['', 'ВОЗМОЖНОСТИ']
    for job in plan.get('opportunities', []):
        lines += [f"#{job['id']} {job['title']} — {job['company']} ({job['source']})", job['url']]
    if not plan.get('opportunities'):
        lines.append('Попроси Аню найти вакансии на hh или зарубежную удалённую работу на Remotive.')
    lines += ['', 'ОТКЛИКИ']
    for group in plan.get('funnel', {}).get('groups', []):
        version = (group.get('resume_version') or 'не указана')[:12]
        lines.append(f"{group['source']}, версия резюме {version}: отправлено {group['applied']}, "
                     f"ответов {group['reply']}, интервью {group['interview']}, офферов {group['offer']}")
        rate = group.get('reply_rate')
        lines.append(f"  Ответы среди откликов старше 7 дней: {rate:.0%} (n={group['mature']})" if rate is not None
                     else '  Для оценки ответов пока нет откликов старше 7 дней.')
    lines.append(plan.get('funnel', {}).get('note', ''))
    lines += ['', 'ПРЕДЛОЖЕНИЯ И РЕЗУЛЬТАТЫ']
    statuses = {'proposed': 'предложено', 'accepted': 'принято', 'done': 'выполнено', 'declined': 'отклонено'}
    for item in plan.get('recommendations', []):
        lines += [f"#{item['id']} [{statuses.get(item['status'], item['status'])}] {item['action']}",
                  f"  Почему: {item['reason']}", f"  Основание: {item['evidence']}",
                  f"  Проверка {item['review_on']}: {item['success']}; время: {item['minutes']} мин."]
        if item.get('outcome'):
            lines.append('  Результат / ответ: ' + item['outcome'])
    lines += ['', f"За неделю {week.get('from', '')} — {week.get('to', '')}: "
              f"замеров {len(week.get('measurements', []))}, "
              f"предложений к проверке {len(week.get('due_recommendations', []))}."]
    return '\n'.join(lines)


class PlanningWindow(QDialog):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle('Аня — план и карьера')
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        layout.addWidget(self.view)
        buttons = QHBoxLayout()
        refresh, close = QPushButton('Обновить'), QPushButton('Закрыть')
        refresh.clicked.connect(self.refresh)
        close.clicked.connect(self.close)
        buttons.addWidget(refresh)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def refresh(self):
        self.api.planning(lambda data: self.view.setPlainText(render_plan(data)))
