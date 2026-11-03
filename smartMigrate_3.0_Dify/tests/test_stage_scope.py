"""Regression coverage for independent demo stages and rule application."""
import json
import sqlite3
import unittest

from test_demo import ENGINE, ROOT, committed, exported_node, fixture_artifacts, seed, pipeline_plan
import yaml


class StageScopeTests(unittest.TestCase):
    def run_plan(self, state, request, plan):
        return ENGINE['execute'](ENGINE['dumps'](state), json.dumps(plan), 'scope-turn', request)

    def assert_business_unchanged(self, before, result):
        after = json.loads(result['state_json'])
        for name in before['tables']:
            if name != 'NEXT_MIG_LOG':
                self.assertEqual(before['tables'][name], after['tables'][name], name)

    def test_sql_combined_request_rejects_full_before_migration(self):
        state = seed()
        result = self.run_plan(state, 'SQL 변환 및 튜닝 진행해줘',
                               {'action': 'run', 'stage': 'full', 'sql_results': fixture_artifacts(state)})
        self.assertEqual(result['status'], 'ERROR')
        self.assert_business_unchanged(state, result)
        self.assertEqual(result['migration_results'], [])

    def test_sql_ordered_actions_do_not_migrate_or_format(self):
        state = seed()
        result = self.run_plan(state, 'SQL 변환 및 튜닝 진행해줘',
                               pipeline_plan(state, ('conversion', 'tuning')))
        self.assertEqual(result['status'], 'OK', result['answer'])
        after = json.loads(result['state_json'])
        for name in ('NEXT_MIG_INFO', 'ASIS_HR_EMP', 'ASIS_HR_DEPT', 'TOBE_HR_EMP', 'TOBE_HR_DEPT'):
            self.assertEqual(state['tables'][name], after['tables'][name])
        for job in after['tables']['NEXT_SQL_INFO']['rows']:
            self.assertIsNone(job.get('FORMATTED_SQL'))
            self.assertIn('WHERE 1 = 1', job['TUNED_TO_SQL'])

    def test_migration_only_and_complete_output_survives_commit(self):
        state = seed()
        result = self.run_plan(state, 'DB Migration 진행해줘', {'action': 'run', 'stage': 'migration'})
        self.assertEqual(result['status'], 'OK')
        saved = committed(state, result)
        after = json.loads(saved['state_json'])
        self.assertEqual(state['tables']['NEXT_SQL_INFO'], after['tables']['NEXT_SQL_INFO'])
        for text in ('ASIS_HR_EMP', 'TOBE_HR_EMP', 'COUNT', 'CONCAT', '최종 결과: DEMO-PASS'):
            self.assertIn(text, saved['answer'])

    def test_conversion_only_keeps_data_and_tuning_untouched(self):
        state = seed()
        result = self.run_plan(state, 'SQL Conversion 진행해줘',
                               {'action': 'run', 'stage': 'conversion', 'sql_results': fixture_artifacts(state)})
        self.assertEqual(result['status'], 'OK')
        after = json.loads(result['state_json'])
        for name in ('NEXT_MIG_INFO', 'TOBE_HR_EMP', 'TOBE_HR_DEPT'):
            self.assertEqual(state['tables'][name], after['tables'][name])
        for job in after['tables']['NEXT_SQL_INFO']['rows']:
            self.assertIsNone(job.get('TUNED_TO_SQL'))

    def test_guide_query_is_read_only_and_category_filtered(self):
        state = seed()
        result = self.run_plan(state, 'SQL 튜닝 가이드 조회해줘', {'action': 'query', 'rule_category': 'SQL_TUNING'})
        self.assertEqual(result['status'], 'OK')
        self.assert_business_unchanged(state, result)
        self.assertIn('WHERE 1 = 1', result['answer'])
        self.assertIn('ANSI', result['answer'])
        self.assertIn('서브쿼리', result['answer'])
        self.assertNotIn('1: 활성 매핑', result['answer'])
        for action in ('run', 'update'):
            blocked = self.run_plan(state, 'SQL 튜닝 가이드 조회해줘', {'action': action, 'stage': 'tuning'})
            self.assertEqual(blocked['status'], 'ERROR')
            self.assert_business_unchanged(state, blocked)

    def test_conversion_result_query_cannot_trigger_run(self):
        state = seed()
        result = self.run_plan(state, 'SQL 변환 결과 보여줘', {'action': 'run', 'stage': 'conversion'})
        self.assertEqual(result['status'], 'ERROR')
        self.assert_business_unchanged(state, result)

    def test_tuning_uses_saved_to_sql_and_preserves_conversion_and_data(self):
        state = seed()
        converted = self.run_plan(state, 'SQL Conversion 진행해줘',
                                  {'action': 'run', 'stage': 'conversion', 'sql_results': fixture_artifacts(state)})
        state = json.loads(converted['state_json'])
        result = self.run_plan(state, 'SQL 튜닝 진행해줘',
                               {'action': 'run', 'stage': 'tuning', 'sql_results': fixture_artifacts(state)})
        self.assertEqual(result['status'], 'OK', result['answer'])
        after = json.loads(result['state_json'])
        for old, new in zip(state['tables']['NEXT_SQL_INFO']['rows'], after['tables']['NEXT_SQL_INFO']['rows']):
            self.assertEqual(old['TO_SQL'], new['TO_SQL'])
            self.assertEqual(old['TO_SQL'], new['TUNED_FR_SQL'])
            self.assertEqual(old['FR_SQL'], new['FR_SQL'])
            self.assertIn('WHERE 1 = 1', new['TUNED_TO_SQL'])
        for name in ('NEXT_MIG_INFO', 'TOBE_HR_EMP', 'TOBE_HR_DEPT'):
            self.assertEqual(state['tables'][name], after['tables'][name])

    def test_ambiguous_request_and_stage_mismatch_do_not_run(self):
        for request, stage in [('작업 실행해줘', 'full'), ('전체 작업 실행해줘', 'full'),
                               ('SQL 튜닝 진행해줘', 'migration'), ('DB Migration 진행해줘', 'conversion')]:
            with self.subTest(request=request):
                state = seed()
                result = self.run_plan(state, request, {'action': 'run', 'stage': stage})
                self.assertEqual(result['status'], 'ERROR')
                self.assert_business_unchanged(state, result)

    def test_where_rule_preserves_or_null_literals_comments_and_order(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE t (id INTEGER, value TEXT)')
        connection.executemany('INSERT INTO t VALUES (?, ?)', [(1, 'WHERE ORDER'), (2, None), (3, 'x')])
        queries = ["SELECT id FROM t WHERE value = 'WHERE ORDER' OR value IS NULL ORDER BY id",
                   "SELECT id FROM t -- trailing comment",
                   "SELECT id FROM t WHERE id > 1 -- keep comment\nORDER BY id",
                   "SELECT value, COUNT(*) FROM t GROUP BY value HAVING COUNT(*) > 0 ORDER BY value"]
        for query in queries:
            with self.subTest(query=query):
                tuned = ENGINE['where_one'](query)
                self.assertEqual(connection.execute(query).fetchall(), connection.execute(tuned).fetchall())
                self.assertEqual(tuned, ENGINE['where_one'](tuned))
        connection.close()

    def test_ansi_proposal_on_saved_legacy_join_is_accepted_as_unverified(self):
        state = seed()
        job = state['tables']['NEXT_SQL_INFO']['rows'][0]
        job.update(TARGET_TABLE='ASIS_HR_EMP,ASIS_HR_DEPT', STATUS_CONVERSION='DEMO-GENERATED',
                   TO_SQL='SELECT E.EMP_ID, D.DEPT_NAME FROM TOBE_HR_EMP E, TOBE_HR_DEPT D WHERE E.DEPT_ID = D.DEPT_ID(+)')
        proposal = 'SELECT E.EMP_ID, D.DEPT_NAME FROM TOBE_HR_EMP E LEFT JOIN TOBE_HR_DEPT D ON E.DEPT_ID = D.DEPT_ID'
        result = self.run_plan(state, 'SQL 튜닝 진행해줘', {'action': 'run', 'stage': 'tuning', 'sql_seqs': [job['SQL_SEQ']],
                                'sql_results': [{'sql_seq': job['SQL_SEQ'], 'tuned_sql': proposal, 'reason': 'ANSI LEFT JOIN 제안. Oracle 미검증'}]})
        self.assertEqual(result['status'], 'OK', result['answer'])
        new = json.loads(result['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertEqual(new['TO_SQL'], job['TO_SQL'])
        self.assertIn('LEFT JOIN', new['TUNED_TO_SQL'])
        self.assertNotIn('(+)', new['TUNED_TO_SQL'])
        self.assertIn('미검증', new['TUNED_RESULT'])

    def test_exported_dsl_wires_request_guard_and_blocks_wrong_stage(self):
        app = yaml.safe_load((ROOT / 'dsl/01_smartmigrate_tool.yml').read_text(encoding='utf-8'))
        node = next(n['data'] for n in app['workflow']['graph']['nodes'] if n['id'] == 'execute')
        self.assertIn({'variable': 'request', 'value_selector': ['prepare', 'request'], 'value_type': 'string'}, node['variables'])
        main = exported_node('01_smartmigrate_tool.yml', 'execute')
        output = main(ENGINE['dumps'](seed()), json.dumps({'action': 'run', 'stage': 'migration'}), 'exported-scope', 'SQL 변환 및 튜닝 진행해줘')
        self.assertEqual(json.loads(output['result_json'])['status'], 'ERROR')

    def test_authoritative_chat_request_survives_agent_rewrite(self):
        initialize = exported_node('02_smartmigrate_chatflow.yml', 'initialize')
        prepare = exported_node('01_smartmigrate_tool.yml', 'prepare')
        execute = exported_node('01_smartmigrate_tool.yml', 'execute')
        current = initialize('', '', 'raw-turn', user_request='SQL 변환 및 튜닝 진행해줘')
        prepared = prepare(current['session_transport'], '이관부터 SQL 변환 튜닝까지 전체 실행해줘')
        self.assertEqual(prepared['request'], 'SQL 변환 및 튜닝 진행해줘')
        result = execute(prepared['state_json'], json.dumps({'action': 'run', 'stage': 'full'}), prepared['request_id'], prepared['request'])
        self.assertEqual(json.loads(result['result_json'])['status'], 'ERROR')
        with self.assertRaisesRegex(ValueError, 'request'):
            execute(prepared['state_json'], '{}', prepared['request_id'], '')
        app = yaml.safe_load((ROOT / 'dsl/02_smartmigrate_chatflow.yml').read_text(encoding='utf-8'))
        node = next(n['data'] for n in app['workflow']['graph']['nodes'] if n['id'] == 'initialize')
        self.assertIn({'variable': 'user_request', 'value_selector': ['sys', 'query'], 'value_type': 'string'}, node['variables'])


if __name__ == '__main__':
    unittest.main()
