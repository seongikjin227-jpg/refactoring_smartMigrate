"""Rule activation, staged sample outputs and sample-level semantic checks."""
import json
import sqlite3
import unittest

from test_demo import ENGINE, fixture_artifacts, seed


class RagTuningTests(unittest.TestCase):
    def run_stage(self, state, stage, ids, artifacts=None):
        return ENGINE['execute'](ENGINE['dumps'](state), json.dumps({
            'action': 'run', 'stage': stage, 'sql_seqs': ids,
            'sql_results': artifacts if artifacts is not None else fixture_artifacts(state, ids)
        }), 'rag-test', 'SQL Conversion 진행해줘' if stage == 'conversion' else 'SQL 튜닝 진행해줘')

    def converted(self, ids):
        output = self.run_stage(seed(), 'conversion', ids)
        self.assertEqual(output['status'], 'OK', output['answer'])
        return json.loads(output['state_json'])

    def test_conversion_keeps_demo_structures_and_tuning_changes_only_output(self):
        before = seed()
        state = self.converted([1])
        jobs = {r['SQL_SEQ']: r for r in state['tables']['NEXT_SQL_INFO']['rows']}
        self.assertIn('WHERE', jobs[1]['TO_SQL'])
        self.assertNotIn('WHERE 1 = 1', jobs[1]['TO_SQL'])
        self.assertIn('(+)', jobs[1]['TO_SQL'])
        self.assertIn('IN', ENGINE['sql_tokens'](jobs[1]['TO_SQL']))
        output = self.run_stage(state, 'tuning', [1])
        self.assertEqual(output['status'], 'OK', output['answer'])
        after = json.loads(output['state_json'])
        tuned = {r['SQL_SEQ']: r for r in after['tables']['NEXT_SQL_INFO']['rows']}
        self.assertIn('WHERE 1 = 1 AND (', tuned[1]['TUNED_TO_SQL'])
        self.assertIn('LEFT JOIN', tuned[1]['TUNED_TO_SQL'])
        self.assertNotIn('(+)', tuned[1]['TUNED_TO_SQL'])
        self.assertNotIn('IN', ENGINE['sql_tokens'](tuned[1]['TUNED_TO_SQL']))
        for seq in [1]:
            self.assertEqual(jobs[seq]['TO_SQL'], tuned[seq]['TO_SQL'])
            self.assertIn('RAG_ID', tuned[seq]['TUNED_RESULT'])
        for name in ['ASIS_HR_EMP', 'ASIS_HR_DEPT', 'TOBE_HR_EMP', 'TOBE_HR_DEPT', 'NEXT_MIG_INFO']:
            self.assertEqual(before['tables'][name], after['tables'][name])

    def test_no_active_rules_means_no_hidden_where_or_join_policy(self):
        state = self.converted([1])
        for rule in state['tables']['NEXT_MIG_RAG_INFO']['rows']:
            if rule['CATEGORY'] == 'SQL_TUNING':
                rule['USE_YN'] = 'N'
        artifacts = fixture_artifacts(state, [1])
        for a in artifacts:
            a['tuned_sql'] = a['to_sql']
        output = self.run_stage(state, 'tuning', [1], artifacts)
        self.assertEqual(output['status'], 'OK', output['answer'])
        after = json.loads(output['state_json'])
        for job in after['tables']['NEXT_SQL_INFO']['rows']:
            if job['SQL_SEQ'] in [1]:
                self.assertEqual(job['TO_SQL'], job['TUNED_TO_SQL'])
        guide = ENGINE['tuning_guide'](state)
        self.assertNotIn('WHERE 1 = 1', guide)
        self.assertEqual(ENGINE['tuning_rules'](state), [])
        changed = self.run_stage(state, 'tuning', [1])
        self.assertEqual(changed['status'], 'ERROR')

    def test_disabled_where_rule_is_neither_shown_nor_forced(self):
        state = self.converted([1])
        state['tables']['NEXT_MIG_RAG_INFO']['rows'][1]['USE_YN'] = 'N'
        output = self.run_stage(state, 'tuning', [1])
        self.assertEqual(output['status'], 'OK', output['answer'])
        job = json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertNotIn('WHERE 1 = 1', job['TUNED_TO_SQL'])
        self.assertNotIn('[WHERE_ONE]', ENGINE['tuning_guide'](state))
        self.assertNotIn('WHERE 1 = 1', ENGINE['tuning_guide'](state))

    def test_where_rule_uses_marker_and_matching_source_scope_not_rag_id(self):
        state = self.converted([1])
        rule = state['tables']['NEXT_MIG_RAG_INFO']['rows'][1]
        rule.update(RAG_ID=92, SOURCE_TABLES='UNRELATED_TABLE')
        output = self.run_stage(state, 'tuning', [1])
        job = json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertNotIn('WHERE 1 = 1', job['TUNED_TO_SQL'])
        rule['SOURCE_TABLES'] = 'ASIS_HR_EMP'
        output = self.run_stage(state, 'tuning', [1])
        self.assertEqual(output['status'], 'OK', output['answer'])
        job = json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertIn('WHERE 1 = 1', job['TUNED_TO_SQL'])
        self.assertIn('92', job['TUNED_RESULT'])

    def test_demo_conversion_does_not_block_on_nested_or_comma_references(self):
        state = seed()
        bad = fixture_artifacts(state, [1])
        bad[0]['to_sql'] = bad[0]['to_sql'].replace('FROM TOBE_HR_EMP s', 'FROM ASIS_HR_EMP s')
        self.assertEqual(self.run_stage(state, 'conversion', [1], bad)['status'], 'OK')
        bad = fixture_artifacts(state, [1])
        bad[0]['to_sql'] = bad[0]['to_sql'].replace('TOBE_HR_DEPT d', 'NO_SUCH_TABLE d')
        self.assertEqual(self.run_stage(state, 'conversion', [1], bad)['status'], 'OK')
        bad = fixture_artifacts(state, [1])
        bad[0]['to_sql'] = bad[0]['to_sql'].replace('s.EMP_ID', 's.NO_COLUMN')
        self.assertEqual(self.run_stage(state, 'conversion', [1], bad)['status'], 'OK')

    def test_rule_edit_is_management_and_invalidates_old_tuning_only(self):
        state = self.converted([1])
        tuned = self.run_stage(state, 'tuning', [1])
        state = json.loads(tuned['state_json'])
        result = ENGINE['execute'](ENGINE['dumps'](state), json.dumps({
            'action':'update', 'table':'NEXT_MIG_RAG_INFO', 'key':{'RAG_ID':2}, 'values':{'USE_YN':'N'}
        }), 'edit-rule', '튜닝 규칙 RAG_ID 2를 비활성화해줘')
        self.assertEqual(result['status'], 'OK', result['answer'])
        after = json.loads(result['state_json'])
        job = after['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertEqual(job['STATUS_TUNING'], 'STALE')
        self.assertEqual(job['STATUS_CONVERSION'], 'DEMO-GENERATED')
        rerun = self.run_stage(after, 'tuning', [1])
        self.assertEqual(rerun['status'], 'OK', rerun['answer'])
        job = json.loads(rerun['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        self.assertNotIn('WHERE 1 = 1', job['TUNED_TO_SQL'])

    def test_sample_tuned_values_match_pk_filter_and_join_reference(self):
        state = self.converted([1])
        output = self.run_stage(state, 'tuning', [1])
        self.assertEqual(output['status'], 'OK', output['answer'])
        jobs = {r['SQL_SEQ']: r for r in json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows']}
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE TOBE_HR_EMP (EMP_ID INTEGER PRIMARY KEY, GIVEN_NAME TEXT, BASE_SALARY REAL, DEPT_ID INTEGER)')
        db.execute('CREATE TABLE TOBE_HR_DEPT (DEPT_ID INTEGER PRIMARY KEY, DEPT_NAME TEXT)')
        db.execute("INSERT INTO TOBE_HR_DEPT VALUES (20, 'Engineering')")
        employees = [(1,'A',5000,20),(2,'B',8000,30),(3,'C',None,20),(4,'D',None,None),(5,'E',9000,None)]
        db.executemany('INSERT INTO TOBE_HR_EMP VALUES (?, ?, ?, ?)', employees)
        for dept in [20, 30, None]:
            for minimum in [0, 7000, 10000, None]:
                params = {'dept_id':dept, 'min_salary':minimum}
                expected = [(ident, name, 'Engineering' if department == 20 else None, salary)
                            for ident, name, salary, department in employees
                            if dept is not None and department == dept and minimum is not None and salary is not None and salary >= minimum]
                self.assertEqual(expected, db.execute(jobs[1]['TUNED_TO_SQL'], params).fetchall())
        db.close()

    def test_outer_join_fixture_retains_unmatched_and_null_departments(self):
        state = self.converted([1])
        output = self.run_stage(state, 'tuning', [1])
        job = json.loads(output['state_json'])['tables']['NEXT_SQL_INFO']['rows'][0]
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE TOBE_HR_EMP (EMP_ID INTEGER PRIMARY KEY, GIVEN_NAME TEXT, BASE_SALARY REAL, DEPT_ID INTEGER)')
        db.execute('CREATE TABLE TOBE_HR_DEPT (DEPT_ID INTEGER PRIMARY KEY, DEPT_NAME TEXT)')
        employees = [(1,'A',8000,20),(2,'B',9000,99),(3,'C',10000,None),(4,'D',4000,20)]
        db.executemany('INSERT INTO TOBE_HR_EMP VALUES (?, ?, ?, ?)', employees)
        db.execute("INSERT INTO TOBE_HR_DEPT VALUES (20, 'Engineering')")
        for dept, expected in [(20, [(1,'A','Engineering',8000)]), (99, [(2,'B',None,9000)]), (None, [])]:
            self.assertEqual(db.execute(job['TUNED_TO_SQL'], {'dept_id':dept, 'min_salary':7000}).fetchall(), expected)
        db.close()


if __name__ == '__main__':
    unittest.main()
