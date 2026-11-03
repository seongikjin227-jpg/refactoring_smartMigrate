"""Offline behavior tests. Explicit SQL fixtures replace the LLM, not the engine."""
import copy
import ast
import base64
import argparse
import json
import re
import runpy
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_node(filename):
    namespace = {}
    source = (ROOT / "nodes/engine.py").read_text(encoding="utf-8")
    if filename != "engine.py":
        source += "\n" + (ROOT / "nodes" / filename).read_text(encoding="utf-8")
    exec(compile(source, filename, "exec"), namespace)
    return namespace


ENGINE = load_node("engine.py")
COMMIT = load_node("commit.py")


def seed():
    return json.loads((ROOT / "samples/hr_demo.json").read_text(encoding="utf-8"))


def exported_node(filename, node_id):
    app = yaml.safe_load((ROOT / "dsl" / filename).read_text(encoding="utf-8"))
    source = next(n["data"]["code"] for n in app["workflow"]["graph"]["nodes"] if n["id"] == node_id)
    namespace = {}
    exec(compile(source, node_id, "exec"), namespace)
    return namespace["main"]


def binding_config(chat):
    agent = next(n['data'] for n in chat['workflow']['graph']['nodes'] if n['id'] == 'agent')
    return agent['agent_parameters']['tools']['value'][0]


def resolved_tools(initialized):
    chat = yaml.safe_load((ROOT / 'dsl/02_smartmigrate_chatflow.yml').read_text(encoding='utf-8'))
    tool = binding_config(chat)
    # Reproduce the official constant/mixed JSON/template/JSON path. V3 is
    # ASCII without quotes or newlines, including when state contains them.
    wire = json.dumps([tool], ensure_ascii=False)
    rendered = wire.replace('{{#initialize.session_transport#}}', initialized['session_transport'])
    tools = json.loads(rendered)
    return tools


def generated_logs(response):
    data = {'tool_call_name': 'smart_migrate_tool', 'tool_response': json.dumps(response)}
    return [{'data': {'output': data}}, {'data': {'output': {'tool_responses': [data]}}}]


def fixture_artifacts(state, ids=None):
    """Known sample's expected SQL; not a production SQL conversion algorithm."""
    replacements = {"ASIS_HR_EMP": "TOBE_HR_EMP", "ASIS_HR_DEPT": "TOBE_HR_DEPT"}
    for row in state["tables"]["NEXT_MIG_INFO_DTL"]["rows"]:
        replacements[row["FR_COL"]] = row["TO_COL"]
    pattern = re.compile(r"\b(" + "|".join(replacements) + r")\b")
    result = []
    for job in state["tables"]["NEXT_SQL_INFO"]["rows"]:
        if ids and job["SQL_SEQ"] not in ids:
            continue
        sql = pattern.sub(lambda m: replacements[m[0]], job.get("EDIT_FR_SQL") or job["FR_SQL"])
        tuned = ("SELECT e.EMP_ID, e.GIVEN_NAME, d.DEPT_NAME, e.BASE_SALARY FROM TOBE_HR_EMP e "
                 "LEFT JOIN TOBE_HR_DEPT d ON e.DEPT_ID = d.DEPT_ID WHERE e.DEPT_ID = :dept_id "
                 "AND e.BASE_SALARY >= :min_salary ORDER BY e.EMP_ID") if job['SQL_ID'] == 'EMP_DEPT_TUNING_DEMO' else sql
        formatted = job.get('TUNED_TO_SQL') or (ENGINE['where_one'](tuned) if ENGINE['rule_enabled'](ENGINE['tuning_rules'](state), 'WHERE_ONE') else tuned)
        result.append({"sql_seq": job["SQL_SEQ"], "to_sql": sql, "tuned_sql": tuned,
                       "formatted_sql": formatted.replace(" FROM ", "\nFROM ").replace(" WHERE ", "\nWHERE "),
                       "reason": "오프라인 테스트 고정 산출물. LLM 호출·성능 측정 없음"})
    return result


def call(state, plan, ident="turn-1"):
    return ENGINE["execute"](ENGINE["dumps"](state), json.dumps(plan, ensure_ascii=False), ident)


def pipeline_plan(state, stages=('migration', 'conversion', 'tuning', 'formatting')):
    return {'actions': [{'action':'run', 'stage':stage, 'sql_results':fixture_artifacts(state)} for stage in stages]}


def committed(state, result, duplicate=True):
    # Official Workflow Tool text: JSON object of end outputs; Agent log:
    # data.output.tool_response, repeated inside round.tool_responses.
    tool_response = json.dumps({"result_json": json.dumps(result, ensure_ascii=False)}, ensure_ascii=False)
    log = {"data": {"output": {"tool_call_name": "smartmigrate_execute", "tool_response": tool_response}}}
    logs = [log, {"data": {"output": {"tool_responses": [log["data"]["output"]]}}}] if duplicate else [log]
    return COMMIT["main"](ENGINE["dumps"](state), result["request_id"], logs, "도구 완료")


class DemoTests(unittest.TestCase):
    def test_commit_outputs_match_dsl_on_query_migration_and_missing_result(self):
        commit = exported_node("02_smartmigrate_chatflow.yml", "commit")
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        declared = next(n["data"]["outputs"] for n in chat["workflow"]["graph"]["nodes"] if n["id"] == "commit")
        before = seed()
        state_json = ENGINE["dumps"](before)
        for plan in ({"action": "query", "tables": ["NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL"]},
                     {"action": "run", "stage": "migration"}):
            result = call(before, plan, "contract-turn")
            self.assertEqual(result["status"], "OK")
            output = commit(state_json, "contract-turn", generated_logs({"result_json": json.dumps(result)}), "")
            self.assertEqual(set(output), set(declared))
            self.assertTrue(all(isinstance(value, str) for value in output.values()))
            facts = json.loads(output["narrative_json"])
            if plan["action"] == "run":
                self.assertEqual(len(facts["migration_results"]), 2)
        missing = commit(state_json, "missing-result", [], "")
        self.assertEqual(set(missing), set(declared))
        self.assertEqual(missing["commit_status"], "NO_RESULT")

    def test_agent_fallback_json_is_editor_text_and_decodes_to_empty_array(self):
        # Dify 1.15 error-handle/default-value.tsx forwards form.value to
        # CodeEditor without JSON.stringify. Monaco accepts text, not a list.
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        agent = next(n["data"] for n in chat["workflow"]["graph"]["nodes"] if n["id"] == "agent")
        fallback = next(item for item in agent["default_value"] if item["key"] == "json")
        self.assertEqual(fallback["type"], "array[object]")
        self.assertIsInstance(fallback["value"], str)
        self.assertEqual(json.loads(fallback["value"]), [])
        for node in chat["workflow"]["graph"]["nodes"]:
            for item in node["data"].get("default_value", []):
                if item["type"] == "object" or item["type"].startswith("array"):
                    self.assertIsInstance(item["value"], str)
                    json.loads(item["value"])

    def test_executing_tool_with_generated_state_preserves_management_next_turn(self):
        initialize = exported_node('02_smartmigrate_chatflow.yml', 'initialize')
        prepare = exported_node('01_smartmigrate_tool.yml', 'prepare')
        execute = exported_node('01_smartmigrate_tool.yml', 'execute')
        commit = exported_node('02_smartmigrate_chatflow.yml', 'commit')
        current = initialize('', '', 'migration-turn')
        tools = resolved_tools(current)
        self.assertEqual(tools[0]['parameters']['request'], {'auto': 1, 'value': None})
        packet = tools[0]['parameters']['session_json']['value']['value']
        prepared = prepare(packet, '전체 작업 실행해줘')
        self.assertIn('reference_rules', json.loads(prepared['context_json']))
        plan = pipeline_plan(seed())
        output = execute(prepared['state_json'], json.dumps(plan), prepared['request_id'], '이관부터 SQL 변환 튜닝 서식까지 전체 실행해줘')
        result = json.loads(output['result_json'])
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(len(json.loads(result['state_json'])['tables']['TOBE_HR_EMP']['rows']), 10)
        saved = commit(current['state_json'], current['request_id'], generated_logs(output), '')
        self.assertEqual(saved['commit_status'], 'OK')
        second = initialize(saved['state_json'], '', 'management-turn')
        packet2 = resolved_tools(second)[0]['parameters']['session_json']['value']['value']
        self.assertNotEqual(packet, packet2)
        prep2 = prepare(packet2, '매핑 상태 변경해줘')
        self.assertEqual(len(json.loads(prep2['context_json'])['tables']['TOBE_HR_EMP']['rows']), 10)
        update = {'action':'update','table':'NEXT_MIG_INFO','key':{'MAP_ID':2},'values':{'STATUS':'READY'}}
        changed = execute(prep2['state_json'], json.dumps(update), prep2['request_id'], '매핑 조건 수정해줘')
        saved2 = commit(second['state_json'], second['request_id'], generated_logs(changed), '')
        after = json.loads(saved2['state_json'])
        self.assertEqual(after['tables']['NEXT_MIG_INFO']['rows'][1]['STATUS'], 'READY')
        self.assertEqual(len(after['tables']['TOBE_HR_EMP']['rows']), 10)

    def test_encoded_tool_input_survives_template_parsing_with_arbitrary_state(self):
        state = seed()
        special = 'quotes " slash \\ newline\n{{#initialize.missing#}} 한글'
        state['tables']['ASIS_HR_EMP']['rows'][0]['FIRST_NAME'] = special
        current = exported_node('02_smartmigrate_chatflow.yml','initialize')(json.dumps(state), '', 'typed-turn')
        tools = resolved_tools(current)
        # Resolve auto/manual slots after the constant-array template round trip.
        resolved = {}
        for name, param in tools[0]['parameters'].items():
            resolved[name] = None if param['auto'] else param['value']['value']
        self.assertIsNone(resolved['request'])
        prepared = exported_node('01_smartmigrate_tool.yml','prepare')(resolved['session_json'], '매핑룰 조회')
        self.assertEqual(json.loads(prepared['state_json'])['tables']['ASIS_HR_EMP']['rows'][0]['FIRST_NAME'], special)

    def test_tool_missing_or_corrupt_state_cannot_reset_to_sample(self):
        prepare = exported_node('01_smartmigrate_tool.yml','prepare')
        for value in ('', 'not json', '[]', '{}'):
            with self.assertRaises((ValueError, TypeError)):
                prepare(value, '이관')

    def test_main_only_commits_verified_tool_output_and_contains_no_executor(self):
        chat = yaml.safe_load((ROOT/'dsl/02_smartmigrate_chatflow.yml').read_text(encoding='utf-8'))
        nodes = {n['id']:n['data'] for n in chat['workflow']['graph']['nodes']}
        self.assertNotIn('execute', nodes)
        self.assertNotIn('planner', nodes)
        self.assertEqual(nodes['agent']['agent_parameters']['tools']['type'], 'constant')
        self.assertNotIn('tool_binding', nodes)
        self.assertTrue(all(isinstance(t, dict) for t in nodes['agent']['agent_parameters']['tools']['value']))
        self.assertNotIn('execution_json', {v['variable'] for v in nodes['commit']['variables']})
        state = ENGINE['dumps'](seed())
        result = exported_node('02_smartmigrate_chatflow.yml','commit')(state, 'missing', [], '완료했다고 주장')
        self.assertEqual(result['commit_status'], 'NO_RESULT')
        self.assertEqual(json.loads(result['state_json']), seed())

    def test_local_planner_failure_keeps_business_tables_and_reports_error(self):
        state = seed()
        result = call(state, {"action": "error", "message": "planner unavailable"})
        self.assertEqual(result["status"], "ERROR")
        after = json.loads(result["state_json"])
        for name in state["tables"]:
            if name != "NEXT_MIG_LOG":
                self.assertEqual(after["tables"][name], state["tables"][name])
        self.assertIn("planner unavailable", result["answer"])











    def test_migration_generates_all_sql_and_reports_count_concat(self):
        before = seed()
        result = call(before, {"action": "run", "stage": "migration"}, "verify-turn")
        self.assertEqual(result["status"], "OK")
        after = json.loads(result["state_json"])
        for mapping, expected in zip(after["tables"]["NEXT_MIG_INFO"]["rows"], (3, 10)):
            self.assertIn("MERGE INTO", mapping["MIG_SQL"])
            self.assertIn("COUNT_RESULT", mapping["VERIFY_SQL"])
            self.assertIn("ROW_CONCAT", mapping["VERIFY2_SQL"])
            verification = json.loads(mapping["LOG"])["verification"]
            self.assertEqual(verification["source_count"], expected)
            self.assertEqual(verification["target_count"], expected)
            self.assertEqual(verification["matched_count"], expected)
            self.assertEqual(verification["mismatch_count"], 0)
            self.assertEqual((verification["count_result"], verification["concat_result"]), ("PASS", "PASS"))
        output = committed(before, result)
        self.assertIn("COUNT 비교", output["answer"])
        self.assertIn("CONCAT 비교", output["answer"])
        self.assertIn("**VERIFY2_SQL**", output["answer"])
        facts = json.loads(output["narrative_json"])
        self.assertIn("migration_results", facts)
        self.assertNotIn("migration_preview", facts)

    def test_count_can_pass_while_concat_detects_changed_row(self):
        migrated = json.loads(call(seed(), {"action": "run", "stage": "migration"})["state_json"])
        mapping = migrated["tables"]["NEXT_MIG_INFO"]["rows"][1]
        migrated["tables"]["TOBE_HR_EMP"]["rows"][0]["GIVEN_NAME"] = "Different"
        migrated["tables"]["TOBE_HR_EMP"]["rows"].reverse()
        checked = ENGINE["migration_verification"](migrated, mapping)
        self.assertEqual(checked["count_result"], "PASS")
        self.assertEqual(checked["concat_result"], "FAIL")
        self.assertEqual((checked["matched_count"], checked["mismatch_count"]), (9, 1))
        self.assertEqual(checked["mismatches"][0]["columns"], ["GIVEN_NAME"])
        migrated["tables"]["TOBE_HR_EMP"]["rows"].pop()
        self.assertEqual(ENGINE["migration_verification"](migrated, mapping)["count_result"], "FAIL")

    def test_verification_respects_filter_and_preserves_unrelated_targets(self):
        state = seed()
        state["tables"]["NEXT_MIG_INFO"]["rows"][0]["CONDITION"] = "DEPARTMENT_ID = 20"
        state["tables"]["TOBE_HR_DEPT"]["rows"].append({"DEPT_ID": 99, "DEPT_NAME": "Existing"})
        result = call(state, {"action": "run", "stage": "migration", "map_ids": [1]})
        self.assertEqual(result["status"], "OK")
        verification = result["migration_results"][0]["verification"]
        self.assertEqual((verification["source_count"], verification["target_count"], verification["target_total_count"]), (1, 1, 2))
        self.assertEqual(verification["preserved_extra_rows"], 1)
        mapping = json.loads(result["state_json"])["tables"]["NEXT_MIG_INFO"]["rows"][0]
        self.assertIn("WHERE DEPARTMENT_ID = 20", mapping["VERIFY_SQL"])
        self.assertIn("A.DEPT_ID = T.DEPT_ID", mapping["VERIFY2_SQL"])

    def test_concat_encoding_distinguishes_null_text_and_delimiter_collisions(self):
        concat = ENGINE["row_concat"]
        examples = [{"A": None, "B": ""}, {"A": "", "B": None}, {"A": "a|b", "B": "c"},
                    {"A": "a", "B": "b|c"}, {"A": 1, "B": "null"}, {"A": "1", "B": "null"}]
        self.assertEqual(len({concat(r, ["A", "B"]) for r in examples}), len(examples))

    def test_v3_transport_passes_template_roundtrip_and_rejects_corruption(self):
        initialize = load_node("initialize.py")
        initialize["DEFAULT_SAMPLE_JSON"] = ENGINE["dumps"](seed())
        output = initialize["main"]("", "", "transport-turn")
        transport = output["session_transport"]
        self.assertTrue(transport.startswith("SM_STATE_V3:"))
        self.assertNotIn('"', transport)
        self.assertNotIn("\n", transport)
        empty = {name: "" for name in ENGINE["TABLE_INPUTS"].values()}
        prepare = load_node("prepare.py")["main"]
        prepared = prepare(transport, "query", "", **empty)
        self.assertEqual(json.loads(prepared["state_json"]), seed())
        self.assertEqual(prepared["request_id"], "transport-turn")
        with self.assertRaisesRegex(ValueError, "패킷"):
            prepare(transport[:-5] + "!!!", "query", "", **empty)

    def test_migration_sql_query_shows_stored_artifacts(self):
        migrated = json.loads(call(seed(), {"action": "run", "stage": "migration"})["state_json"])
        result = call(migrated, {"action": "query", "tables": ["NEXT_MIG_INFO"], "include_sql": True}, "sql-query")
        output = committed(migrated, result)
        self.assertEqual(output["commit_status"], "OK")
        self.assertIn("**MIG_SQL**", output["answer"])
        self.assertIn("**VERIFY_SQL**", output["answer"])
        self.assertIn("**VERIFY2_SQL**", output["answer"])
        self.assertNotIn("migration_preview", json.loads(output["narrative_json"]))

    def test_mapping_presentation_has_valid_table_lines_and_insert_preview(self):
        before = seed()
        result = call(before, {"action": "query", "tables": ["NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL"]})
        # Replay an older Tool answer with invalid blank lines between rows.
        result["answer"] = result["answer"].replace("|\n|", "|\n\n|")
        output = committed(before, result)
        self.assertEqual(output["commit_status"], "OK")
        facts = json.loads(output["narrative_json"])
        self.assertEqual([(r["inserted"], r["updated"]) for r in facts["migration_preview"]], [(3, 0), (10, 0)])
        self.assertTrue(all(r["status"] is None and r["target_rows"] == 0 for r in facts["mapping_status"]))
        self.assertIn("이관 실행해줘", facts["suggested_requests"])
        self.assertNotIn("|\n\n|", output["answer"])
        self.assertIn("| MAP_ID | FR_TABLE | TO_TABLE | USE_YN | STATUS |\n| ---", output["answer"])
        self.assertIn("| 2 | 8 | DEPARTMENT_ID | DEPT_ID |", output["answer"])
        after = json.loads(output["state_json"])
        self.assertEqual(after["tables"]["TOBE_HR_EMP"]["rows"], [])
        self.assertTrue(all(r["STATUS"] is None for r in after["tables"]["NEXT_MIG_INFO"]["rows"]))

    def test_preview_uses_target_keys_and_values_instead_of_null_status(self):
        migrated = call(seed(), {"action": "run", "stage": "migration"})
        state = json.loads(migrated["state_json"])
        for row in state["tables"]["NEXT_MIG_INFO"]["rows"]:
            row["STATUS"] = None
        state["tables"]["ASIS_HR_EMP"]["rows"][0]["SALARY"] += 1
        result = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO"]}, "preview-updated")
        output = committed(state, result)
        stats = json.loads(output["narrative_json"])["migration_preview"]
        self.assertEqual([(r["inserted"], r["updated"], r["unchanged"]) for r in stats], [(0, 0, 3), (0, 1, 9)])
        self.assertEqual(json.loads(output["state_json"])["tables"]["TOBE_HR_EMP"], state["tables"]["TOBE_HR_EMP"])

    def test_failed_result_does_not_suggest_execution(self):
        state = seed()
        result = call(state, {"action": "run", "stage": "does-not-exist"})
        output = committed(state, result)
        facts = json.loads(output["narrative_json"])
        self.assertEqual(facts["commit_status"], "ERROR")
        self.assertEqual(facts["suggested_requests"], [])
        self.assertNotIn("migration_preview", facts)

    def test_narrator_reads_verified_summary_and_answer_keeps_code_tables(self):
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        nodes = {n["id"]: n["data"] for n in chat["workflow"]["graph"]["nodes"]}
        narrator = nodes["narrator"]
        self.assertEqual(nodes["agent"]["agent_parameters"]["maximum_iterations"]["value"], 1)
        self.assertEqual(nodes['agent']['agent_strategy_name'], 'function_calling')
        self.assertEqual(narrator["type"], "llm")
        self.assertIn("{{#commit.narrative_json#}}", narrator["prompt_template"][1]["text"])
        self.assertEqual(narrator["model"]["completion_params"]["max_tokens"], 4096)
        self.assertEqual(narrator["error_strategy"], "default-value")
        self.assertEqual(nodes["answer"]["answer"], '{{#render.text#}}')
        self.assertIn({'variable':'answer','value_selector':['commit','answer'],'value_type':'string'}, nodes['render']['variables'])
        edges = {(e["source"], e["target"]) for e in chat["workflow"]["graph"]["edges"]}
        self.assertIn(("commit", "save"), edges)
        self.assertIn(("save", "narrator"), edges)
        self.assertIn(("narrator", "render"), edges)
        self.assertIn(("render", "answer"), edges)

    def test_variable_message_repr_before_workflow_result_does_not_hide_result(self):
        state = seed()
        result = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL"]}, "repr-turn")
        # Installed agent 0.0.18 concatenates VARIABLE repr and Workflow TEXT.
        # The repr contains over 32 braces, apostrophes in SQL, escaped quotes
        # and parentheses. It is opaque; only the real trailing JSON counts.
        response = ("tool response: VariableMessage(variable_name='result_json', variable_value="
                    + repr(json.dumps(result, ensure_ascii=False)) + ", stream=False)."
                    + json.dumps({"result_json": json.dumps(result, ensure_ascii=False)}, ensure_ascii=False) + ".")
        logs = [{"data": {"output": {"tool_call_name": "smart_migrate_tool", "tool_response": response}}}]
        output = COMMIT["main"](ENGINE["dumps"](state), "repr-turn", logs, "현재 매핑룰 조회 결과입니다.\n\n### NEXT_MIG")
        self.assertEqual(output["commit_status"], "OK")
        self.assertIn("총 2행", output["answer"])
        self.assertIn("총 10행", output["answer"])
        self.assertEqual(json.loads(output["state_json"])["revision"], 1)
        # A VARIABLE representation alone is not a Workflow result envelope.
        self.assertEqual(COMMIT["envelopes"](response.split(").", 1)[0] + ")."), [])

    def test_packet_preserves_updated_state_with_all_legacy_inputs_missing(self):
        initialize = load_node("initialize.py")
        initialize["DEFAULT_SAMPLE_JSON"] = ENGINE["dumps"](seed())
        prepare = load_node("prepare.py")["main"]
        empty = {name: "" for name in ENGINE["TABLE_INPUTS"].values()}
        current = initialize["main"]("", "", "packet-turn-1")
        # No table binding and no separate request ID binding are required.
        prepared = prepare(current["session_transport"], "migration", "", **empty)
        self.assertEqual(prepared["request_id"], "packet-turn-1")
        self.assertEqual(json.loads(prepared["state_json"]), seed())
        result = call(json.loads(prepared["state_json"]), {"action": "run", "stage": "migration"}, "packet-turn-1")
        saved = committed(seed(), result)
        self.assertEqual(saved["commit_status"], "OK")
        next_turn = initialize["main"](saved["state_json"], "", "packet-turn-2")
        resumed = prepare(next_turn["session_transport"], "query", "", **empty)
        state = json.loads(resumed["state_json"])
        self.assertEqual(state["revision"], 1)
        self.assertEqual(len(state["tables"]["TOBE_HR_EMP"]["rows"]), 10)
        self.assertEqual(len(state["tables"]["NEXT_MIG_LOG"]["rows"]), len(json.loads(saved["state_json"])["tables"]["NEXT_MIG_LOG"]["rows"]))
        with self.assertRaisesRegex(ValueError, "request_id"):
            prepare(next_turn["session_json"], "query", "wrong-turn", **empty)

    def test_legacy_missing_table_reports_its_name_and_empty_session_is_rejected(self):
        state = seed()
        prepare = load_node("prepare.py")["main"]
        inputs = {variable: ENGINE["dumps"](state["tables"][name]) for name, variable in ENGINE["TABLE_INPUTS"].items()}
        inputs["next_mig_log_json"] = ""
        session = ENGINE["dumps"]({k: v for k, v in state.items() if k != "tables"})
        with self.assertRaisesRegex(ValueError, "next_mig_log_json"):
            prepare(session, "query", "legacy-turn", **inputs)
        with self.assertRaisesRegex(ValueError, "session_json"):
            prepare("", "query", "empty-turn", **inputs)

    def test_registered_tool_alias_result_is_committed(self):
        state = seed()
        result = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO_DTL"]})
        logs = [{"data": {"output": {"tool_call_name": "smart_migrate_tool",
                 "tool_response": json.dumps({"result_json": json.dumps(result)})}}}]
        output = COMMIT["main"](ENGINE["dumps"](state), result["request_id"], logs, "완료")
        self.assertEqual(output["commit_status"], "OK")
        self.assertEqual(json.loads(output["state_json"])["revision"], state["revision"] + 1)

    def test_sample_contract(self):
        state = ENGINE["validate"](seed())
        self.assertEqual(len(state["tables"]["ASIS_HR_EMP"]["rows"]), 10)
        self.assertEqual(len(state["tables"]["NEXT_SQL_INFO"]["rows"]), 1)
        self.assertTrue(all(j["FR_SQL"] and j["TO_SQL"] is None for j in state["tables"]["NEXT_SQL_INFO"]["rows"]))

    def test_ordered_pipeline_commit_and_upsert(self):
        state = seed()
        result = call(state, pipeline_plan(state))
        self.assertEqual(result["status"], "OK", result["answer"])
        output = committed(state, result)
        self.assertEqual(output["commit_status"], "OK")
        state = json.loads(output["state_json"])
        self.assertEqual(len(state["tables"]["TOBE_HR_EMP"]["rows"]), 10)
        self.assertEqual(len(state["tables"]["TOBE_HR_DEPT"]["rows"]), 3)
        job = state["tables"]["NEXT_SQL_INFO"]["rows"][0]
        self.assertIn("TOBE_HR_DEPT", job["TO_SQL"])
        self.assertIn("DEPT_NAME", job["TO_SQL"])
        self.assertIn("ASIS_HR_EMP", job["FR_SQL"])
        self.assertTrue(all(j["FORMATTED_SQL"] for j in state["tables"]["NEXT_SQL_INFO"]["rows"]))
        state["tables"]["ASIS_HR_EMP"]["rows"][0]["SALARY"] = 12000
        rerun = call(state, {"action": "run", "stage": "migration"}, "turn-2")
        after = json.loads(rerun["state_json"])
        self.assertEqual(len(after["tables"]["TOBE_HR_EMP"]["rows"]), 10)
        self.assertEqual(after["tables"]["TOBE_HR_EMP"]["rows"][0]["BASE_SALARY"], 12000)
        self.assertEqual(rerun["migration_results"][1]["updated"], 1)

    def test_management_changes_next_turn_and_delete(self):
        state = seed()
        update = call(state, {"action": "update", "table": "NEXT_MIG_INFO_DTL", "key": {"MAP_ID": 2, "MAP_DTL": 2}, "values": {"FR_COL": "LAST_NAME"}})
        self.assertEqual(update["status"], "OK")
        state = json.loads(committed(state, update)["state_json"])
        result = call(state, {"action": "run", "stage": "migration"}, "turn-2")
        state = json.loads(committed(state, result)["state_json"])
        self.assertEqual(state["tables"]["TOBE_HR_EMP"]["rows"][0]["GIVEN_NAME"], "Kim")
        bad = call(state, {"action": "delete", "table": "NEXT_MIG_INFO", "key": {"MAP_ID": 1}}, "turn-3")
        self.assertEqual(bad["status"], "ERROR")
        removed = call(state, {"action": "delete", "table": "NEXT_MIG_INFO", "key": {"MAP_ID": 2}}, "turn-4")
        self.assertEqual(removed["status"], "OK")
        state = json.loads(removed["state_json"])
        self.assertFalse(any(d["MAP_ID"] == 2 for d in state["tables"]["NEXT_MIG_INFO_DTL"]["rows"]))
        self.assertEqual(call(state, {"action": "run", "stage": "migration", "map_ids": [2]}, "turn-5")["status"], "ERROR")

    def test_add_update_delete_detail_and_rule(self):
        state = seed()
        removed = call(state, {"action": "delete", "table": "NEXT_MIG_INFO_DTL", "key": {"MAP_ID": 2, "MAP_DTL": 8}})
        state = json.loads(removed["state_json"])
        added = call(state, {"action": "insert", "table": "NEXT_MIG_INFO_DTL", "values": {"MAP_ID": 2, "MAP_DTL": 8, "FR_COL": "DEPARTMENT_ID", "TO_COL": "DEPT_ID"}}, "turn-2")
        self.assertEqual(added["status"], "OK")
        state = json.loads(added["state_json"])
        self.assertEqual(len(state["tables"]["NEXT_MIG_INFO_DTL"]["rows"]), 10)
        invalid = call(state, {"action": "insert", "table": "NEXT_MIG_INFO_DTL", "values": {"MAP_ID": 2, "MAP_DTL": 8, "FR_COL": "SALARY", "TO_COL": "BASE_SALARY"}}, "turn-3")
        self.assertEqual(invalid["status"], "ERROR")
        self.assertEqual(state["tables"]["NEXT_MIG_INFO_DTL"], json.loads(invalid["state_json"])["tables"]["NEXT_MIG_INFO_DTL"])

    def test_atomic_rollback_when_sql_generation_missing(self):
        state = seed()
        result = call(state, {'actions':[{'action':'run','stage':'migration'}, {'action':'run','stage':'conversion','sql_results':[]}]})
        after = json.loads(result["state_json"])
        self.assertEqual(result["status"], "ERROR")
        for name in ("TOBE_HR_EMP", "TOBE_HR_DEPT", "NEXT_MIG_INFO"):
            self.assertEqual(state["tables"][name], after["tables"][name])

    def test_pending_action_query_and_sql_edit(self):
        state = seed()
        clarify = call(state, {"action": "clarify", "question": "어느 MAP_ID인가요?", "context": "매핑 삭제"})
        state = json.loads(committed(state, clarify)["state_json"])
        self.assertEqual(state["pending_action"]["context"], "매핑 삭제")
        queried = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO"]}, "turn-2")
        self.assertIn("| MAP_ID |", queried["answer"])
        update = call(state, {"action": "update", "table": "NEXT_SQL_INFO", "key": {"SQL_SEQ": 1}, "values": {"EDIT_FR_SQL": "SELECT EMPLOYEE_ID FROM ASIS_HR_EMP"}}, "turn-3")
        edited = json.loads(update["state_json"])["tables"]["NEXT_SQL_INFO"]["rows"][0]
        self.assertEqual(edited["STATUS_CONVERSION"], "STALE")
        self.assertEqual(edited["EDIT_FR_SQL"], "SELECT EMPLOYEE_ID FROM ASIS_HR_EMP")

    def test_duplicate_key_bad_column_and_unsupported_condition(self):
        state = seed()
        broken = copy.deepcopy(state)
        broken["tables"]["ASIS_HR_EMP"]["rows"].append(broken["tables"]["ASIS_HR_EMP"]["rows"][0].copy())
        with self.assertRaises(ValueError):
            ENGINE["validate"](broken)
        bad = call(state, {"action": "update", "table": "NEXT_MIG_INFO_DTL", "key": {"MAP_ID": 2, "MAP_DTL": 1}, "values": {"TO_COL": "NO_COLUMN"}})
        self.assertEqual(bad["status"], "ERROR")
        state["tables"]["NEXT_MIG_INFO"]["rows"][1]["CONDITION"] = "1=1 OR 1=1"
        result = call(state, {"action": "run", "stage": "migration"})
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(json.loads(result["state_json"])["tables"]["TOBE_HR_DEPT"]["rows"], [])

    def test_stage_dependency_and_select_guard(self):
        state = seed()
        result = call(state, {"action": "run", "stage": "tuning", "sql_results": fixture_artifacts(state)})
        self.assertEqual(result["status"], "ERROR")
        artifacts = fixture_artifacts(state)
        artifacts[0]["to_sql"] = "SELECT 1 FROM DUAL; DROP TABLE TOBE_HR_EMP"
        result = call(state, {"action": "run", "stage": "conversion", "sql_results": artifacts})
        self.assertEqual(result["status"], "ERROR")
        valid = call(state, {"action": "run", "stage": "conversion", "sql_results": fixture_artifacts(state)})
        self.assertEqual(valid["status"], "OK")
        state = json.loads(valid["state_json"])
        tuned = call(state, {"action": "run", "stage": "tuning", "sql_results": fixture_artifacts(state)}, "turn-2")
        self.assertEqual(tuned["status"], "OK")
        state = json.loads(tuned["state_json"])
        formatted = call(state, {"action": "run", "stage": "formatting", "sql_results": fixture_artifacts(state)}, "turn-3")
        self.assertEqual(formatted["status"], "OK")

    def test_generated_sql_mapping_bind_and_format_checks(self):
        state = seed()
        converted = call(state, {"action": "run", "stage": "conversion", "sql_results": fixture_artifacts(state)})
        converted_state = json.loads(converted["state_json"])
        for bad_sql in ("SELECT 1 FROM DUAL", "SELECT e.NO_SUCH_COLUMN FROM TOBE_HR_EMP e", "SELECT e.EMP_ID FROM TOBE_HR_EMP e WHERE e.EMP_ID=:wrong"):
            artifacts = fixture_artifacts(state, [1])
            artifacts[0]["to_sql"] = bad_sql
            result = call(state, {"action": "run", "stage": "conversion", "sql_seqs": [1], "sql_results": artifacts})
            self.assertEqual(result["status"], "OK", bad_sql)
            artifacts[0]["tuned_sql"] = bad_sql
            result = call(converted_state, {"action": "run", "stage": "tuning", "sql_seqs": [1], "sql_results": artifacts})
            self.assertEqual(result["status"], "ERROR", bad_sql)
        artifacts = fixture_artifacts(state)
        artifacts[0]["formatted_sql"] = artifacts[0]["tuned_sql"].replace("EMP_ID", "DEPT_ID")
        result = call(state, {'actions':[{'action':'run','stage':'migration'}, {'action':'run','stage':'conversion','sql_results':artifacts}, {'action':'run','stage':'tuning','sql_results':artifacts}, {'action':'run','stage':'formatting','sql_results':artifacts}]})
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(json.loads(result["state_json"])["tables"]["TOBE_HR_EMP"]["rows"], [])

    def test_management_status_and_reference_integrity(self):
        state = seed()
        result = call(state, {"action": "update", "table": "NEXT_MIG_INFO", "key": {"MAP_ID": 2}, "values": {"STATUS": "READY"}})
        self.assertEqual(result["status"], "OK")
        self.assertEqual(json.loads(result["state_json"])["tables"]["NEXT_MIG_INFO"]["rows"][1]["STATUS"], "READY")
        state["tables"]["NEXT_SQL_INFO"]["rows"][0]["REF_SEQ"] = 999
        with self.assertRaises(ValueError):
            ENGINE["validate"](state)

    def test_commit_rejects_stale_conflicting_and_prose_results(self):
        state = seed()
        result = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO"]})
        stale = copy.deepcopy(state)
        stale["revision"] += 1
        self.assertEqual(committed(stale, result)["commit_status"], "REJECTED")
        second = call(state, {"action": "query", "tables": ["NEXT_SQL_INFO"]})
        logs = [{"data": {"output": {"tool_call_name": "smartmigrate_execute", "tool_response": {"result_json": ENGINE["dumps"](r)}}}} for r in (result, second)]
        conflict = COMMIT["main"](ENGINE["dumps"](state), "turn-1", logs, "")
        self.assertEqual(conflict["commit_status"], "CONFLICT")
        text_only = COMMIT["main"](ENGINE["dumps"](state), "turn-1", [], ENGINE["dumps"](result))
        self.assertEqual(text_only["commit_status"], "NO_RESULT")
        self.assertEqual(json.loads(text_only["state_json"])["revision"], 0)

    def test_official_strategy_old_concatenation_and_log_isolation(self):
        state = seed()
        result = call(state, {"action": "query", "tables": ["NEXT_MIG_INFO"]})
        payload = json.dumps({"result_json": ENGINE["dumps"](result)}, ensure_ascii=False)
        old_response = payload + "tool response: " + payload + "."
        log = {"data": {"output": {"tool_call_name": "smartmigrate_execute", "tool_response": old_response}}}
        accepted = COMMIT["main"](ENGINE["dumps"](state), "turn-1", [log], "")
        self.assertEqual(accepted["commit_status"], "OK")
        fake_log = {"data": {"output": {"llm_response": payload, "tool_call_input": {"request": payload}}}}
        refused = COMMIT["main"](ENGINE["dumps"](state), "turn-1", [fake_log], "")
        self.assertEqual(refused["commit_status"], "NO_RESULT")

    def test_dsl_embedded_code_and_state_wiring(self):
        for path in (ROOT / "dsl").glob("*.yml"):
            dsl = yaml.safe_load(path.read_text(encoding="utf-8"))
            nodes = dsl["workflow"]["graph"]["nodes"]
            known = {n["id"] for n in nodes}
            for edge in dsl["workflow"]["graph"]["edges"]:
                self.assertIn(edge["source"], known)
                self.assertIn(edge["target"], known)
            for n in nodes:
                if n["data"]["type"] == "code":
                    namespace = {}
                    exec(compile(n["data"]["code"], n["id"], "exec"), namespace)
                    if n["id"] == "initialize":
                        first = namespace["main"]("", "", "first-turn")
                        after = json.loads(first["state_json"])
                        after["revision"] = 9
                        resumed = namespace["main"](json.dumps(after), first["state_json"], "next-turn")
                        self.assertEqual(json.loads(resumed["state_json"])["revision"], 9)
            if dsl["app"]["mode"] == "advanced-chat":
                agent = next(n["data"] for n in nodes if n["id"] == "agent")
                self.assertEqual(agent["agent_parameters"]["tools"]["type"], "constant")
                self.assertTrue(all(isinstance(t, dict) for t in agent["agent_parameters"]["tools"]["value"]))
                self.assertEqual(next(n for n in nodes if n["id"] == "save")["data"]["items"][0]["value"], ["commit", "state_json"])



    def test_workspace_export_configuration_discards_old_state_inputs(self):
        configure = runpy.run_path(str(ROOT / "scripts/configure.py"))["configure"]
        with tempfile.TemporaryDirectory(prefix="smartmigrate-test-") as folder:
            target = Path(folder).resolve()
            (target / "dsl").mkdir()
            for filename in ("01_smartmigrate_tool.yml", "02_smartmigrate_chatflow.yml", "build_manifest.json"):
                (target / "dsl" / filename).write_text((ROOT / "dsl" / filename).read_text(encoding="utf-8"), encoding="utf-8")
            reference = yaml.safe_load((target / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
            agent = next(n["data"] for n in reference["workflow"]["graph"]["nodes"] if n["id"] == "agent")
            agent["agent_parameters"]["model"]["value"].update(provider="example/provider", model="example-model")
            agent["agent_parameters"]["model"]["value"]["completion_params"]["temperature"] = 0.1
            agent['agent_strategy_name'] = 'ReAct'
            agent['agent_strategy_label'] = 'ReAct'
            agent['agent_parameters']['model']['value']['completion_params']['stop'] = ['Observation']
            # Actual UI export: only some fields are manual, mixed JSON
            # templates, and EMP accidentally points to DEPT.
            reference_tool = binding_config(reference)
            agent["agent_parameters"]["tools"] = {"type": "constant", "value": [reference_tool]}
            reference_tool.update(provider_name="workspace-specific-id", parameters={
                "asis_hr_emp_json": {"auto": 0, "value": {"type": "mixed", "value": "{{#initialize.asis_hr_dept_json#}}"}},
                "next_mig_info_dtl_json": {"auto": 1, "value": None}})
            export = target / "reference.yml"
            export.write_text(yaml.safe_dump(reference), encoding="utf-8")
            configure(export, target)
            actual = yaml.safe_load((target / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
            bound = next(n["data"] for n in actual["workflow"]["graph"]["nodes"] if n["id"] == "agent")
            bound_tool = binding_config(actual)
            self.assertEqual(bound_tool["provider_name"], "workspace-specific-id")
            self.assertEqual(bound_tool["parameters"]["request"], {"auto": 1, "value": None})
            self.assertEqual(bound_tool["parameters"]["session_json"]["auto"], 0)
            self.assertNotIn("{{#initialize.asis_", json.dumps(bound_tool["parameters"]))
            self.assertEqual(bound["agent_parameters"]["model"]["value"]["model"], "example-model")
            self.assertEqual(bound["agent_parameters"]["model"]["value"]["completion_params"]["temperature"], 1)
            self.assertEqual(bound['agent_strategy_name'], 'function_calling')
            self.assertEqual(bound['agent_parameters']['maximum_iterations']['value'], 1)
            self.assertNotIn('stop', bound['agent_parameters']['model']['value']['completion_params'])
            tool_app = yaml.safe_load((target / "dsl/01_smartmigrate_tool.yml").read_text(encoding="utf-8"))
            self.assertTrue(any(n["data"]["type"] == "llm" for n in tool_app["workflow"]["graph"]["nodes"]))
            planner = next(n["data"] for n in tool_app["workflow"]["graph"]["nodes"] if n["id"] == "planner")
            self.assertEqual(planner["model"]["completion_params"]["temperature"], 1)

            # Reconfiguration must preserve the static list and prewired state.
            configure(target / "dsl/02_smartmigrate_chatflow.yml", target)
            again = yaml.safe_load((target / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
            self.assertEqual(binding_config(again)["provider_name"], "workspace-specific-id")



    def test_per_table_files_are_authoritative_and_not_replaced_by_build(self):
        sample = runpy.run_path(str(ROOT / "scripts/build.py"))["sample"]
        with tempfile.TemporaryDirectory(prefix="smartmigrate-table-test-") as folder:
            target = Path(folder).resolve()
            (target / "samples/tables").mkdir(parents=True)
            for path in (ROOT / "samples/tables").glob("*.json"):
                (target / "samples/tables" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            (target / "samples/session.json").write_text((ROOT / "samples/session.json").read_text(encoding="utf-8"), encoding="utf-8")
            employees = target / "samples/tables/ASIS_HR_EMP.json"
            spec = json.loads(employees.read_text(encoding="utf-8"))
            spec["rows"][0]["SALARY"] = 12345
            employees.write_text(json.dumps(spec), encoding="utf-8")
            loaded = sample(target)
            self.assertEqual(loaded["tables"]["ASIS_HR_EMP"]["rows"][0]["SALARY"], 12345)
            self.assertEqual(json.loads(employees.read_text(encoding="utf-8"))["rows"][0]["SALARY"], 12345)

    def test_per_table_override_then_saved_state_roundtrip(self):
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        n = next(n for n in chat["workflow"]["graph"]["nodes"] if n["id"] == "initialize")
        namespace = {}
        exec(compile(n["data"]["code"], "initialize", "exec"), namespace)
        main = namespace["main"]
        employees = seed()["tables"]["ASIS_HR_EMP"]
        employees["rows"][0]["SALARY"] = 11111
        first = main("", "", "turn-1", asis_hr_emp_json=json.dumps(employees))
        after = call(json.loads(first["state_json"]), {"action": "run", "stage": "migration"})
        second = main(after["state_json"], "", "turn-2", asis_hr_emp_json=json.dumps(seed()["tables"]["ASIS_HR_EMP"]))
        self.assertEqual(json.loads(second["asis_hr_emp_json"])["rows"][0]["SALARY"], 11111)
        self.assertEqual(len(json.loads(second["tobe_hr_emp_json"])["rows"]), 10)
        prepared = load_node("prepare.py")["main"](
            session_json=second["session_json"], request="조회", request_id="turn-2",
            **{name: second[name] for name in ENGINE["TABLE_INPUTS"].values()})
        self.assertEqual(ENGINE["digest"](json.loads(prepared["state_json"])), ENGINE["digest"](json.loads(second["state_json"])))

    def test_imported_chat_defaults_contain_samples_and_tool_accepts_two_inputs(self):
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        workflow = yaml.safe_load((ROOT / "dsl/01_smartmigrate_tool.yml").read_text(encoding="utf-8"))
        def defaults(dsl):
            start = next(n["data"] for n in dsl["workflow"]["graph"]["nodes"] if n["data"]["type"] == "start")
            return {f["variable"]: f.get("default") for f in start["variables"]}
        chat_inputs, tool_inputs = defaults(chat), defaults(workflow)
        expected = seed()
        self.assertEqual(json.loads(chat_inputs["sample_json"]), expected)
        for table_name, variable in ENGINE["TABLE_INPUTS"].items():
            self.assertEqual(json.loads(chat_inputs[variable]), expected["tables"][table_name])
        self.assertEqual(set(tool_inputs), {"request", "session_json"})

    def test_visible_table_defaults_do_not_overwrite_edited_sample_json(self):
        chat = yaml.safe_load((ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8"))
        nodes = chat["workflow"]["graph"]["nodes"]
        start = next(n["data"] for n in nodes if n["id"] == "start")
        values = {f["variable"]: f["default"] for f in start["variables"]}
        source = next(n["data"]["code"] for n in nodes if n["id"] == "initialize")
        namespace = {}
        exec(compile(source, "initialize", "exec"), namespace)
        edited = json.loads(values["sample_json"])
        edited["tables"]["ASIS_HR_EMP"]["rows"][0]["SALARY"] = 13000
        values["sample_json"] = json.dumps(edited)
        result = namespace["main"](saved_state="", turn_id="default-turn", **values)
        self.assertEqual(json.loads(result["asis_hr_emp_json"])["rows"][0]["SALARY"], 13000)
        # An explicitly changed individual table takes precedence on first run.
        individual = json.loads(values["asis_hr_emp_json"])
        individual["rows"][0]["SALARY"] = 15000
        values["asis_hr_emp_json"] = json.dumps(individual)
        result = namespace["main"](saved_state="", turn_id="individual-turn", **values)
        self.assertEqual(json.loads(result["asis_hr_emp_json"])["rows"][0]["SALARY"], 15000)
        # Reopening the initial inputs in an existing chat must not reset state.
        values["asis_hr_emp_json"] = json.dumps(seed()["tables"]["ASIS_HR_EMP"])
        resumed = namespace["main"](saved_state=result["state_json"], turn_id="later-turn", **values)
        self.assertEqual(json.loads(resumed["asis_hr_emp_json"])["rows"][0]["SALARY"], 15000)

    def test_workspace_binding_repairs_auto_null_slots_in_the_main_app(self):
        bind = runpy.run_path(str(ROOT / "scripts/bind_workspace.py"))["bind_workspace"]
        with tempfile.TemporaryDirectory(prefix="smartmigrate-bind-test-") as folder:
            target = Path(folder).resolve()
            (target / "dsl").mkdir()
            original = (ROOT / "dsl/02_smartmigrate_chatflow.yml").read_text(encoding="utf-8")
            reference = yaml.safe_load(original)
            agent = next(n["data"] for n in reference["workflow"]["graph"]["nodes"] if n["id"] == "agent")
            # Re-adding a Tool can return its entire parameter list to auto mode.
            tool_config = binding_config(reference)
            agent["agent_parameters"]["tools"] = {"type": "constant", "value": [tool_config]}
            parameters = tool_config["parameters"]
            for name in parameters:
                parameters[name] = {"auto": 1, "value": None}
            generic_path = target / "dsl/02_smartmigrate_chatflow.yml"
            generic_path.write_text(yaml.safe_dump(reference), encoding="utf-8")
            before = generic_path.read_bytes()
            ident = "c2355c90-0ec5-4e40-9b95-109df546eee4"
            destination = bind(ident, "example/provider", "example-model", target)
            self.assertNotEqual(generic_path.read_bytes(), before)
            self.assertEqual(destination, generic_path)
            self.assertFalse((target / "dsl/03_smartmigrate_chatflow_bound.yml").exists())
            actual = yaml.safe_load(destination.read_text(encoding="utf-8"))
            bound_agent = next(n["data"] for n in actual["workflow"]["graph"]["nodes"] if n["id"] == "agent")
            tool = binding_config(actual)
            self.assertEqual(tool["provider_name"], ident)
            self.assertEqual(tool["parameters"]["request"], {"auto": 1, "value": None})
            self.assertEqual(len(tool["parameters"]), 2)
            self.assertEqual(tool["parameters"]["session_json"], {"auto": 0, "value": {"type": "mixed", "value": "{{#initialize.session_transport#}}"}})

    def test_rebuild_preserves_saved_workspace_and_allows_explicit_overrides(self):
        resolve = runpy.run_path(str(ROOT / "scripts/build.py"))["resolve_workspace"]
        with tempfile.TemporaryDirectory(prefix="smartmigrate-profile-test-") as folder:
            target = Path(folder).resolve()
            (target / "dsl").mkdir()
            saved = {"tool_provider_id": "existing-tool-id", "model_provider": "saved/provider", "model": "saved-model"}
            (target / "dsl/workspace_binding.json").write_text(json.dumps(saved), encoding="utf-8")
            args = resolve(argparse.Namespace(), target)
            self.assertEqual(args.tool_provider_id, "existing-tool-id")
            self.assertEqual(args.model, "saved-model")
            args = resolve(argparse.Namespace(model="explicit-model"), target)
            self.assertEqual(args.model, "explicit-model")
            self.assertEqual(args.model_provider, "saved/provider")


if __name__ == "__main__":
    unittest.main()
