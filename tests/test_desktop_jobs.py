"""Qt offscreen checks; no connection to a running core or desktop session."""
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton, QLabel

from ubermensch.desktop.answer import AnswerConsole
from ubermensch.desktop.client import Api


class FakeApi(QObject):
    state_changed = Signal(dict)
    state = {}
    online = True

    def __init__(self):
        super().__init__()
        self.cancel_run = Mock()
        self.feedback = Mock()
        self.planning = Mock()


class DesktopJobsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_run_status_cancel_and_feedback_are_visible(self):
        api = FakeApi()
        window = AnswerConsole(api)
        self.addCleanup(window.close)
        state = {'messages': [{'id': 1, 'role': 'assistant', 'channel': 'desktop', 'text': 'answer'}],
                 'runs': [{'id': 'a' * 32, 'kind': 'chat', 'status': 'waiting_approval'}]}
        window._render(state)
        self.assertTrue(any('ждёт подтверждения' in item.text() for item in window.findChildren(QLabel)))
        next(button for button in window.findChildren(QPushButton) if button.text() == 'Остановить').click()
        self.assertEqual(api.cancel_run.call_args.args[0], 'a' * 32)
        next(button for button in window.findChildren(QPushButton) if button.text() == 'Полезно').click()
        self.assertEqual(api.feedback.call_args.args[:2], (1, 'helpful'))

    def test_client_consumes_persisted_result_once(self):
        callback = Mock()
        api = Api.__new__(Api)
        QObject.__init__(api)
        api._runs = {'a': callback}
        api.refresh = Mock()
        api._on_run('a', {'status': 'completed', 'result': {'reply': 'answer'}})
        api._on_run('a', {'status': 'completed', 'result': {'reply': 'answer'}})
        callback.assert_called_once_with({'reply': 'answer'})

    def test_planning_button_loads_readable_snapshot(self):
        api = FakeApi()
        api.planning.side_effect = lambda callback: callback({'plan': {'time_budget':
            {'available_minutes':120,'allocated_minutes':180,'overbooked_minutes':60}}, 'week':{}})
        window = AnswerConsole(api)
        self.addCleanup(window.close)
        next(b for b in window.findChildren(QPushButton) if b.text() == 'План и карьера').click()
        self.addCleanup(window._plan_window.close)
        api.planning.assert_called_once()
        self.assertIn('Перегрузка: 60', window._plan_window.view.toPlainText())
