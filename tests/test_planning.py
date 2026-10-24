"""Outcome-based plans and career workflows, without accounts or paid model requests."""
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from ubermensch import career, config
from ubermensch.api import create_app
from ubermensch.capabilities import load_registry
from ubermensch.capabilities.registry import ToolArgumentsError
from ubermensch.core import Core, Bus
from ubermensch.desktop.planning import render_plan
from ubermensch.runtime.context import Execution, current
from ubermensch.runtime.policy import check_tool
from ubermensch.store import Store


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.goal = self.store.add_goal('career', 'Get a job', '', None)

    def opportunity(self, identifier='1'):
        return self.store.save_opportunity('hh', identifier, 'Python developer', 'Company',
                                           f'https://hh.ru/vacancy/{identifier}', {'description': 'Python'})

    def plan(self, baseline=0., target=10., effort=120):
        return self.store.save_goal_plan(self.goal, 'interviews', 'count', baseline, target,
                                          effort, 'prepare CV', 'user statement')

    def test_measured_goals_keep_history_and_handle_reduction(self):
        self.plan(10, 5)
        self.store.measure_goal(self.goal, 8, date.today().isoformat(), 'record today')
        self.store.measure_goal(self.goal, 9, (date.today()-timedelta(days=2)).isoformat(), 'older record')
        goal = self.store.measured_goals()[0]
        self.assertEqual(goal['current'], 8)
        self.assertEqual(goal['progress_percent'], 40)
        self.assertEqual(len(self.store.planning_review()['measurements']), 2)
        with self.assertRaises(ValueError):
            self.plan(0, 5)
        self.assertEqual(self.store.measured_goals()[0]['baseline'], 10)

    def test_invalid_measurement_and_missing_plan_do_not_mutate(self):
        with self.assertRaises(ValueError):
            self.store.measure_goal(self.goal, 1, date.today().isoformat(), 'source')
        self.plan()
        for value, observed, evidence in [(float('nan'), date.today().isoformat(), 'x'),
                                          (1, (date.today()+timedelta(days=1)).isoformat(), 'x'),
                                          (1, date.today().isoformat(), '')]:
            with self.assertRaises(ValueError):
                self.store.measure_goal(self.goal, value, observed, evidence)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM goal_measurements').fetchone()[0], 0)

    def test_time_budget_counts_active_goals_and_preserves_unknown(self):
        self.plan(effort=180)
        self.assertIsNone(self.store.planning_snapshot()['time_budget']['overbooked_minutes'])
        self.store.set_planning_setting('weekly_minutes', 100)
        self.assertEqual(self.store.planning_snapshot()['time_budget']['overbooked_minutes'], 80)
        self.store.db.execute("UPDATE goals SET status='done' WHERE id=?", (self.goal,))
        self.assertEqual(self.store.planning_snapshot()['time_budget']['allocated_minutes'], 0)

    def test_reimport_preserves_assessment_drafts_and_history(self):
        job = self.opportunity()
        self.store.assess_opportunity(job['id'], self.goal, 'Python experience', 'CV page 1',
                                      'location unknown', 'unknown', 'check location', 15)
        draft = self.store.save_career_draft(job['id'], 'resume', 'letter', 'CV')
        again = self.store.save_career_draft(job['id'], 'resume', 'letter', 'CV')
        self.assertEqual(draft['id'], again['id'])
        self.store.record_career_event(job['id'], 'applied', 'user confirms', date.today().isoformat(), draft['id'])
        reimported = self.opportunity()
        self.assertEqual(reimported['id'], job['id'])
        self.assertEqual(reimported['assessment']['eligibility'], 'unknown')
        self.assertEqual(len(reimported['drafts']), 1)
        self.assertEqual(len(reimported['events']), 1)

    def test_draft_is_not_application_and_revisions_are_immutable(self):
        job = self.opportunity()
        draft = self.store.save_career_draft(job['id'], 'resume', 'v1', 'CV')
        newer = self.store.save_career_draft(job['id'], 'resume', 'v2', 'CV')
        self.assertNotEqual(draft['id'], newer['id'])
        self.assertEqual(self.store.career_draft(draft['id'])['letter'], 'v1')
        self.assertEqual(self.store.career_funnel()['groups'], [])

    def test_funnel_uses_cohorts_and_resume_content_not_letter_version(self):
        today = date.today()
        earlier = (today - timedelta(days=10)).isoformat()
        for identifier, observed in [('1', earlier), ('2', earlier), ('3', today.isoformat())]:
            job = self.opportunity(identifier)
            draft = self.store.save_career_draft(job['id'], 'same resume', 'letter '+identifier, 'CV')
            self.store.record_career_event(job['id'], 'applied', 'sent', observed, draft['id'])
            self.store.record_career_event(job['id'], 'applied', 'duplicate', observed, draft['id'])
            if identifier == '1':
                self.store.record_career_event(job['id'], 'interview', 'invited', today.isoformat())
                self.store.record_career_event(job['id'], 'reply', 'another message', today.isoformat())
        group = self.store.career_funnel()['groups'][0]
        self.assertEqual(group['applied'], 3)
        self.assertEqual(group['reply'], 1)
        self.assertEqual(group['mature'], 2)
        self.assertEqual(group['reply_rate'], .5)

    def test_event_requires_sent_application_and_matching_draft(self):
        a, b = self.opportunity('1'), self.opportunity('2')
        draft = self.store.save_career_draft(a['id'], 'CV', 'letter', 'source')
        with self.assertRaises(ValueError):
            self.store.record_career_event(a['id'], 'offer', 'claim', date.today().isoformat())
        with self.assertRaises(ValueError):
            self.store.record_career_event(b['id'], 'applied', 'sent', date.today().isoformat(), draft['id'])
        self.assertEqual(self.store.career_funnel()['groups'], [])

    def test_recommendation_links_feedback_and_weekly_review(self):
        identifier = self.store.save_recommendation(self.goal, 'practice', 'gap in CV', 'CV page 1',
                                                     'complete sample', 60, date.today().isoformat())
        self.assertEqual(len(self.store.planning_review()['due_recommendations']), 1)
        self.store.recommendation_feedback(identifier, 'accepted', 'user agreed')
        self.store.recommendation_feedback(identifier, 'done', 'sample prepared')
        week = self.store.planning_review()
        self.assertEqual(len(week['outcomes']), 2)
        self.assertEqual(week['outcomes'][-1]['outcome'], 'sample prepared')
        self.assertEqual(week['due_recommendations'], [])
        self.assertIn('sample prepared', self.store.planning_context())

    def test_render_plan_explains_missing_data_and_does_not_render_html(self):
        job = self.opportunity()
        self.store.db.execute('UPDATE career_opportunities SET title=? WHERE id=?', ('<script>test</script>',job['id']))
        rendered = render_plan({'plan': self.store.planning_snapshot(), 'week': self.store.planning_review()})
        self.assertIn('Свободное время пока не указано', rendered)
        self.assertIn('<script>test</script>', rendered)  # displayed by QPlainTextEdit


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)

    async def test_hh_search_and_detail_preserve_source_and_deduplicate(self):
        short = {'id': '123', 'name': 'Python', 'employer': {'name': 'Company'}, 'area': {'name': 'City'},
                 'snippet': {'requirement': '<b>Python</b>'}}
        full = {**short, 'description': '<p>Python</p><script>untrusted()</script>', 'archived': False}
        with patch('ubermensch.career.request_json', new=AsyncMock(side_effect=[
                {'items':[short],'found':1,'pages':1}, full])) as request:
            found = await career.search(self.store, 'hh', 'Python', area='1')
            details = await career.get_opportunity(self.store, found['items'][0]['id'])
        self.assertEqual(details['details']['description'], 'Python')
        self.assertEqual(details['url'], 'https://hh.ru/vacancy/123')
        self.assertTrue(details['details']['full_description'])
        self.assertEqual(len(self.store.opportunities()), 1)
        self.assertEqual(request.call_args_list[0].args[1]['area'], '1')

    async def test_remotive_uses_shared_cache_and_keeps_geography(self):
        raw = {'id': 7, 'title': 'Python engineer', 'company_name': 'Example',
               'url':'https://remotive.com/remote-jobs/software-dev/example-7',
               'description':'Python and Django', 'candidate_required_location':'US only'}
        with patch('ubermensch.career.request_json', new=AsyncMock(return_value={'jobs':[raw]})) as request:
            first = await career.search(self.store, 'remotive', 'Python')
            second = await career.search(self.store, 'remotive', 'Django')
        request.assert_awaited_once()
        self.assertEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertEqual(first['items'][0]['location'], 'US only')
        self.assertIn('24 часа', first['note'])

    async def test_source_failure_is_not_an_empty_search(self):
        with patch('ubermensch.career.request_json', new=AsyncMock(side_effect=ValueError('HTTP 429'))):
            with self.assertRaisesRegex(ValueError, '429'):
                await career.search(self.store, 'hh', 'Python')
        self.assertEqual(self.store.opportunities(), [])


class PlanningToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = Core.__new__(Core)
        self.core.store = Store(':memory:')
        self.addCleanup(self.core.store.db.close)
        self.core.bus = Bus()
        self.core.tool_registry = load_registry()
        self.core.request_approval = AsyncMock(return_value=False)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = patch.object(config, 'DATA_DIR', Path(self.temp.name))
        setting.start()
        self.addCleanup(setting.stop)
        self.addAsyncCleanup(self.core.execution.close)
        self.client = TestClient(TestServer(create_app(self.core, 'test')))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def call(self, name, arguments):
        response = await self.client.post('/api/tools/call', json={'name':name,'arguments':arguments},
                                          headers={'Authorization':'Bearer test'})
        return response.status, await response.json()

    async def test_goal_create_plan_is_atomic_through_actual_api(self):
        args = dict(goal_id=None, metric='interviews',unit='count',baseline=0.,target=0.,
                    weekly_minutes=90,next_step='prepare',evidence='user',title='Job')
        status, _ = await self.call('goal_plan', args)
        self.assertEqual(status, 400)
        self.assertEqual(len(self.core.store.goals()), 0)
        status, data = await self.call('goal_plan', {**args, 'target':3.})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.core.store.goals()), 1)
        self.assertEqual(data['result']['goals'][0]['target'], 3)
        view = await self.client.get('/api/planning', headers={'Authorization':'Bearer test'})
        self.assertEqual((await view.json())['plan']['goals'][0]['title'], 'Job')
        self.assertEqual((await self.client.get('/api/planning')).status, 401)

    async def test_background_can_prepare_but_not_invent_user_decisions(self):
        run_id = self.core.store.create_run('agent', {}, 'background')
        token = current.set(Execution(self.core, run_id, 'background'))
        try:
            for name in ('career_search','career_assess','career_draft','recommendation_save','read_document'):
                check_tool(name)
            for name in ('planning_preferences','goal_plan','goal_measure','career_record','recommendation_feedback'):
                with self.assertRaises(PermissionError):
                    check_tool(name)
        finally:
            current.reset(token)
        with self.assertRaises(PermissionError):
            await self.core.call_tool('career_record', {'opportunity_id':1,'stage':'applied','evidence':'invented'}, run_id)

    async def test_pdf_docx_reading_uses_same_file_permissions(self):
        import docx
        document = docx.Document()
        document.add_paragraph('Python FastAPI verified resume text')
        path = Path(self.temp.name) / 'resume.docx'
        document.save(path)
        with patch.dict(os.environ, {'UBERMENSCH_READ_ROOTS':self.temp.name}):
            status, data = await self.call('read_document', {'path':str(path),'limit':6})
        self.assertEqual(status, 200)
        self.assertEqual(data['result']['text'], 'Python')
        self.assertEqual(data['result']['next_offset'], 6)
        with patch.dict(os.environ, {'UBERMENSCH_READ_ROOTS':'/nonexistent'}):
            status, data = await self.call('read_document', {'path':str(path)})
        self.core.request_approval.assert_awaited_once()
        self.assertIn('error', data['result'])
