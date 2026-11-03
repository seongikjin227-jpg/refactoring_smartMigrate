"""Separate generation guidance without replacing the LLM with a SQL converter."""
import json
import unittest
from test_demo import ENGINE, exported_node, seed


class ConversionContextTests(unittest.TestCase):
    def test_demo_conversion_includes_disabled_mappings_and_rules(self):
        state = seed()
        for mapping in state['tables']['NEXT_MIG_INFO']['rows']:
            mapping['USE_YN'] = 'N'
        rules = state['tables']['NEXT_MIG_RAG_INFO']['rows']
        conversion_rules = [r for r in rules if r['CATEGORY'] == 'SQL_CONVERSION']
        for rule in conversion_rules:
            rule['USE_YN'] = 'N'
            rule['SOURCE_TABLES'] = 'ASIS_HR_DEPT'
        output = self.prepare(state, 'SQL 변환 실행해줘')
        context = json.loads(output['context_json'])
        self.assertEqual(context['reference_rules'], conversion_rules)
        for name in ('NEXT_MIG_INFO', 'NEXT_MIG_INFO_DTL'):
            self.assertEqual(context['tables'][name], state['tables'][name])

    def test_demo_conversion_accepts_partial_mapping_and_complex_select(self):
        state = seed()
        for mapping in state['tables']['NEXT_MIG_INFO']['rows']:
            mapping['USE_YN'] = 'N'
        candidates = [
            'SELECT e.EMP_ID FROM TOBE_HR_EMP e',
            state['tables']['NEXT_SQL_INFO']['rows'][0]['FR_SQL'],
            'WITH x AS (SELECT e.UNKNOWN_COL FROM demo.OTHER_EMP e) SELECT * FROM x',
        ]
        for sql in candidates:
            with self.subTest(sql=sql):
                plan = {'action': 'run', 'stage': 'conversion', 'sql_seqs': [1],
                        'sql_results': [{'sql_seq': 1, 'to_sql': sql}]}
                result = ENGINE['execute'](json.dumps(state), json.dumps(plan), 'demo-conversion', 'SQL 변환 실행해줘')
                self.assertEqual(result['status'], 'OK', result['answer'])
                after = json.loads(result['state_json'])
                job = after['tables']['NEXT_SQL_INFO']['rows'][0]
                self.assertEqual(job['TO_SQL'], sql)
                self.assertEqual(job['STATUS_CONVERSION'], 'DEMO-GENERATED')
                self.assertEqual(after['tables']['NEXT_MIG_INFO'], state['tables']['NEXT_MIG_INFO'])

    def prepare(self, state, request):
        packet = {'kind':'smartmigrate.session.v2','state':state,'request_id':'context-test','user_request':request}
        return exported_node('01_smartmigrate_tool.yml','prepare')(json.dumps(packet), request)

    def test_conversion_gets_only_conversion_rules_and_original_sql(self):
        state = seed()
        job = state['tables']['NEXT_SQL_INFO']['rows'][0]
        job['TO_SQL'] = 'old converted SQL'
        job['TUNED_TO_SQL'] = 'old tuned SQL'
        job['TUNED_RESULT'] = 'old tuning explanation'
        before = ENGINE['dumps'](state)
        output = self.prepare(state, 'SQL 변환 실행해줘')
        context = json.loads(output['context_json'])
        self.assertEqual({r['CATEGORY'] for r in context['reference_rules']}, {'SQL_CONVERSION'})
        self.assertEqual({r['CATEGORY'] for r in context['tables']['NEXT_MIG_RAG_INFO']['rows']}, {'SQL_CONVERSION'})
        self.assertNotIn('[ANSI_JOIN]', output['context_json'])
        self.assertNotIn('[REDUCE_SUBQUERY]', output['context_json'])
        visible = context['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertEqual(visible['FR_SQL'], job['FR_SQL'])
        self.assertIsNone(visible['TO_SQL'])
        self.assertIsNone(visible['TUNED_TO_SQL'])
        self.assertEqual(output['state_json'], before)

    def test_tuning_gets_tuning_rules_and_saved_to_sql(self):
        state = seed()
        state['tables']['NEXT_SQL_INFO']['rows'][0]['TO_SQL'] = 'saved conversion output'
        output = self.prepare(state, 'SQL 튜닝 진행해줘')
        context = json.loads(output['context_json'])
        self.assertEqual({r['CATEGORY'] for r in context['reference_rules']}, {'SQL_TUNING'})
        self.assertEqual(context['tables']['NEXT_SQL_INFO']['rows'][0]['TO_SQL'], 'saved conversion output')

    def test_rule_query_and_combined_request_keep_both_rule_categories(self):
        for request in ['NEXT_MIG_RAG_INFO 조회해줘', 'SQL 변환 및 튜닝 진행해줘']:
            output = self.prepare(seed(), request)
            self.assertEqual({r['CATEGORY'] for r in json.loads(output['context_json'])['reference_rules']}, {'SQL_CONVERSION', 'SQL_TUNING'})
