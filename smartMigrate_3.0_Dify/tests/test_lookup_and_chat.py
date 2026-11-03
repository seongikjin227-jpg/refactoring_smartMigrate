"""Source lookup and ordinary chat must not accidentally become SQL execution."""
import json
import unittest
from test_demo import ENGINE, COMMIT, seed, committed, exported_node, fixture_artifacts, pipeline_plan


class LookupAndChatTests(unittest.TestCase):
    def test_natural_sql_source_lookup_overrides_wrong_execution_plan(self):
        for request in ['SQL 변환 대상이 있는지 먼저 조회해줘', 'SQL 변환 대상 조회해줘', 'AS-IS SQL 원본 보여줘']:
            with self.subTest(request=request):
                state = seed()
                output = ENGINE['execute'](ENGINE['dumps'](state), json.dumps({'action':'run','stage':'migration'}), 'lookup-turn', request)
                self.assertEqual(output['status'], 'OK', output['answer'])
                saved = committed(state, output)
                self.assertEqual(saved['commit_status'], 'OK')
                after = json.loads(saved['state_json'])
                for name in state['tables']:
                    if name != 'NEXT_MIG_LOG':
                        self.assertEqual(state['tables'][name], after['tables'][name], name)
                self.assertIn('NEXT_SQL_INFO', saved['answer'])
                self.assertIn(state['tables']['NEXT_SQL_INFO']['rows'][0]['FR_SQL'], saved['answer'])
                self.assertNotIn('TUNED_TO_SQL', saved['answer'])
                self.assertEqual(json.loads(saved['narrative_json'])['sql_source_query']['count'], 1)

    def test_lookup_does_not_display_existing_conversion_or_tuning_results(self):
        state = seed()
        result = ENGINE['execute'](ENGINE['dumps'](state), json.dumps(pipeline_plan(state, ('conversion','tuning'))), 'convert', 'SQL 변환 및 튜닝 진행해줘')
        self.assertEqual(result['status'], 'OK')
        state = json.loads(result['state_json'])
        output = ENGINE['execute'](ENGINE['dumps'](state), '{}', 'source-only', 'SQL 변환 대상 조회해줘')
        saved = committed(state, output)
        job = state['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertIn(job['FR_SQL'], saved['answer'])
        self.assertNotIn(job['TO_SQL'], saved['answer'])
        self.assertNotIn(job['TUNED_TO_SQL'], saved['answer'])

    def test_empty_sql_source_table_is_zero_results_not_a_missing_target_error(self):
        state = seed()
        state['tables']['NEXT_SQL_INFO']['rows'] = []
        output = ENGINE['execute'](ENGINE['dumps'](state), '{}', 'empty-lookup', 'SQL 변환 대상 조회해줘')
        self.assertEqual(output['status'], 'OK')
        self.assertIn('0건', output['answer'])

    def test_actual_conversion_request_still_executes_conversion(self):
        state = seed()
        plan = {'action':'run','stage':'conversion','sql_results':fixture_artifacts(state)}
        output = ENGINE['execute'](ENGINE['dumps'](state), json.dumps(plan), 'convert-only', 'SQL 변환 실행해줘.')
        self.assertEqual(output['status'], 'OK', output['answer'])
        self.assertEqual(json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]['STATUS_CONVERSION'], 'DEMO-GENERATED')
        self.assertNotEqual(output['query_view'], 'sql_source')

    def test_greeting_identity_and_features_are_chat_without_tool_result_errors(self):
        state = seed()
        render = exported_node('02_smartmigrate_chatflow.yml', 'render')
        for request, expected in [('안녕','안녕하세요'), ('어떤 에이전트야?','SmartMigrate Agent'), ('어떤 기능이 있어?','SQL Conversion'), ('뭘 할 수 있어?','튜닝')]:
            with self.subTest(request=request):
                output = COMMIT['main'](ENGINE['dumps'](state), 'chat', [], '', user_request=request)
                self.assertEqual(output['commit_status'], 'CHAT')
                self.assertEqual(json.loads(output['state_json']), state)
                final = render(output['narrative_json'], 'Tool 결과가 없어 실패했습니다.', output['answer'])['text']
                self.assertIn(expected, final)
                self.assertNotIn('Tool 결과', final)
                self.assertNotIn('상태는 변경', final)

    def test_greeting_with_business_request_is_not_misclassified_as_chat(self):
        for request in ['안녕, 매핑룰 조회해줘', 'SQL 변환 실행해줘', '어떤 기능이 있어? SQL 변환 대상 조회해줘']:
            self.assertIsNone(COMMIT['general_reply'](request))

    def test_exported_commit_receives_original_user_request_for_general_chat(self):
        main = exported_node('02_smartmigrate_chatflow.yml','commit')
        output = main(ENGINE['dumps'](seed()), 'exported-chat', [], '', user_request='안녕')
        self.assertEqual(output['commit_status'], 'CHAT')

    def test_empty_agent_response_uses_read_only_source_snapshot(self):
        state = seed()
        job = state['tables']['NEXT_SQL_INFO']['rows'][0]
        agent_log = [{'label': 'ROUND 1', 'status': 'success',
                      'data': {'output': {'llm_response': '', 'tool_responses': []}}}]
        commit = exported_node('02_smartmigrate_chatflow.yml', 'commit')
        output = commit(ENGINE['dumps'](state), 'source-fallback', agent_log, '',
                        user_request='SQL 변환 대상 조회해줘')
        self.assertEqual(output['commit_status'], 'READ_ONLY')
        self.assertEqual(json.loads(output['state_json']), state)
        self.assertIn(job['FR_SQL'], output['answer'])
        self.assertNotIn('TUNED_TO_SQL', output['answer'])
        self.assertIn('Tool 호출은 발생하지 않았습니다', output['answer'])
        facts = json.loads(output['narrative_json'])
        self.assertFalse(facts['tool_called'])
        render = exported_node('02_smartmigrate_chatflow.yml', 'render')
        self.assertEqual(render(output['narrative_json'], 'Agent 호출 실패', output['answer'])['text'], output['answer'])

    def test_source_fallback_never_runs_sql_conversion_or_masks_tool_failure(self):
        state = seed()
        absent = COMMIT['main'](ENGINE['dumps'](state), 'conversion', [], '',
                                user_request='SQL 변환 실행해줘')
        self.assertEqual(absent['commit_status'], 'NO_RESULT')
        malformed_call = [{'label': 'CALL smart_migrate_tool', 'data': {'tool_name': 'smart_migrate_tool', 'output': '{}'}}]
        failed = COMMIT['main'](ENGINE['dumps'](state), 'source', malformed_call, '',
                                user_request='SQL 변환 대상 조회해줘')
        self.assertEqual(failed['commit_status'], 'NO_RESULT')
