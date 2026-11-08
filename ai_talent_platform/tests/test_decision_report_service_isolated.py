"""Contract checks without a running database or model service."""
import asyncio
import runpy
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


class DecisionReportServiceTest(unittest.TestCase):
    def load_service(self, source, meta):
        context = types.ModuleType('app.ai.context')
        @contextmanager
        def context_scope(user):
            yield types.SimpleNamespace(data_scope='dept')
        context.context_scope = context_scope
        context.get_context = lambda: types.SimpleNamespace(data_scope='dept')
        workflow = types.ModuleType('app.ai.workflows.talent_decision_report')
        async def run_analysis(question, data_scope, report_type):
            self.assertEqual(data_scope, 'dept')
            return source
        workflow.run_analysis = run_analysis
        agent_module = types.ModuleType('app.ai.agents.analytics_agent')
        class Agent:
            def __init__(self, **kwargs): pass
            async def _build_business_meta(self, report): return meta
            async def parse_output(self, raw):
                return {'answered': True, 'charts': self._report['data']['charts'],
                        'suggestion_p0': [], 'suggestion_p1': []}
        agent_module.AnalyticsAgent = Agent
        modules = {'app.ai.context': context, 'app.ai.workflows.talent_decision_report': workflow,
                   'app.ai.agents.analytics_agent': agent_module}
        with patch.dict(sys.modules, modules):
            return runpy.run_path(str(Path(__file__).parents[1] / 'app/services/talent_decision_service.py'))['generate_decision_report']

    def test_model_failure_keeps_real_charts_and_warns(self):
        source = {'answered': True, 'data': {'charts': [{'title': '实际数据'}]}}
        generate = self.load_service(source, None)
        report = asyncio.run(generate(object(), '分析', 'weekly'))
        self.assertEqual(report['charts'], source['data']['charts'])
        self.assertEqual(report['data'], source['data'])
        self.assertIn('模型解读不可用', report['warnings'][0])
        self.assertIn('尚未按报告周期', report['warnings'][1])
        self.assertIn('suggestions', report['insight'])

    def test_no_data_stays_rejected(self):
        source = {'answered': False, 'message': '无数据'}
        generate = self.load_service(source, None)
        self.assertEqual(asyncio.run(generate(object(), '分析')), source)


if __name__ == '__main__':
    unittest.main()
