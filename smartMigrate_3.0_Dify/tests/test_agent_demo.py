"""Reproduce the supplied empty Agent trace and actual ReAct CALL contract."""
import json
import unittest
import yaml
from test_demo import ENGINE, COMMIT, ROOT, seed, call, fixture_artifacts, committed, pipeline_plan, exported_node


class AgentDemoTests(unittest.TestCase):
    def test_supplied_empty_round_is_not_a_tool_success_or_missing_mapping(self):
        logs = [
            {'label':'ROUND 1', 'status':'success', 'data':{'output':{'llm_response':'', 'tool_responses':[]}}},
            {'label':'gpt-5.5 Thought', 'status':'success', 'data':{'output':'', 'tool_input':[], 'tool_name':''}},
            {'data':[]},
        ]
        state = seed()
        output = COMMIT['main'](ENGINE['dumps'](state), 'user-trace', logs, '')
        self.assertEqual(output['commit_status'], 'NO_RESULT')
        facts = json.loads(output['narrative_json'])
        self.assertEqual(facts['tool_diagnostic'], 'EMPTY_AGENT_RESPONSE')
        self.assertEqual(json.loads(output['state_json']), state)
        self.assertIn('tool_responses=[]', output['answer'])

    def test_official_react_call_log_is_committed_but_thought_is_not(self):
        state = seed()
        result = call(state, {'action':'query','tables':['NEXT_MIG_INFO','NEXT_MIG_INFO_DTL']})
        response = json.dumps({'result_json':ENGINE['dumps'](result)})
        logs = [{'label':'CALL smart_migrate_tool','data':{'tool_name':'smart_migrate_tool','tool_call_args':{},'output':response}}]
        output = COMMIT['main'](ENGINE['dumps'](state), 'turn-1', logs, '')
        self.assertEqual(output['commit_status'], 'OK')
        self.assertIn('NEXT_MIG_INFO_DTL', output['answer'])
        logs[0]['label'] = 'gpt-5.5 Thought'
        self.assertEqual(COMMIT['main'](ENGINE['dumps'](state),'turn-1',logs,'')['commit_status'], 'NO_RESULT')

    def test_default_mapping_query_does_not_require_a_target_name(self):
        main = exported_node('01_smartmigrate_tool.yml', 'execute')
        output = main(ENGINE['dumps'](seed()), '{"action":"clarify","question":"target?"}', 'lookup', '매핑룰 조회해줘')
        result = json.loads(output['result_json'])
        self.assertEqual(result['status'], 'OK')
        self.assertIn('NEXT_MIG_INFO_DTL', result['answer'])
        self.assertEqual(result['migration_results'], [])

    def test_single_sample_has_two_tables_real_binds_and_tuning_structures(self):
        jobs = seed()['tables']['NEXT_SQL_INFO']['rows']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['TARGET_TABLE'], 'ASIS_HR_EMP,ASIS_HR_DEPT')
        self.assertIn(':dept_id', jobs[0]['FR_SQL'])
        self.assertIn(':min_salary', jobs[0]['FR_SQL'])
        self.assertIn('(+)', jobs[0]['FR_SQL'])
        self.assertIn('IN', ENGINE['sql_tokens'](jobs[0]['FR_SQL']))
        self.assertEqual(json.loads(jobs[0]['BIND_SET']), {'dept_id':20,'min_salary':7000})

    def test_compound_sql_request_executes_each_action_without_migration(self):
        state = seed()
        output = ENGINE['execute'](ENGINE['dumps'](state), json.dumps(pipeline_plan(state, ('conversion','tuning'))), 'compound', 'SQL 변환 및 튜닝 진행해줘')
        self.assertEqual(output['status'], 'OK', output['answer'])
        self.assertEqual([s['stage'] for s in output['steps']], ['conversion','tuning'])
        saved = committed(state, output)
        self.assertEqual(saved['commit_status'], 'OK')
        self.assertIn('요청 작업 1', saved['answer'])
        self.assertIn('요청 작업 2', saved['answer'])
        after = json.loads(saved['state_json'])
        for name in ['NEXT_MIG_INFO','TOBE_HR_EMP','TOBE_HR_DEPT']:
            self.assertEqual(after['tables'][name], state['tables'][name])
        for stage in ['full', 'conversion_tuning']:
            result = call(state, {'action':'run','stage':stage,'sql_results':fixture_artifacts(state)})
            self.assertEqual(result['status'], 'ERROR')

    def test_gpt55_uses_function_calling_without_stop_or_react_instructions(self):
        app = yaml.safe_load((ROOT/'dsl/02_smartmigrate_chatflow.yml').read_text(encoding='utf-8'))
        agent = next(n['data'] for n in app['workflow']['graph']['nodes'] if n['id']=='agent')
        self.assertEqual(agent['agent_strategy_name'], 'function_calling')
        self.assertEqual(agent['agent_parameters']['maximum_iterations']['value'], 1)
        self.assertNotIn('stop', agent['agent_parameters']['model']['value']['completion_params'])
        self.assertNotIn('ReAct', agent['agent_parameters']['instruction']['value'])
        self.assertFalse(agent['memory']['window']['enabled'])
        for node in app['workflow']['graph']['nodes']:
            if node['data']['type'] == 'llm':
                self.assertNotIn('stop', node['data']['model']['completion_params'])
