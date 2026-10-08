"""Exercise intent -> real query builders -> routing with fake Oracle/LLM boundaries."""
import ast
from collections.abc import Mapping
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import re
import sys
import traceback
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "smartMigrate_2.0_Langflow"


class Data:
    def __init__(self, data=None):
        self.data = data or {}


class Message:
    def __init__(self, text="", data=None):
        self.text = text
        self.data = data or {}
        self.files = []
        self.session_id = "job-session"


def load_component(relative_path):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    node.body = [n for n in node.body if not (
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"inputs", "outputs"} for t in n.targets)
    )]
    constants = [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)]
    namespace = {"Component": object, "Data": Data, "Message": Message, "json": json,
                 "logging": logging, "re": re, "traceback": traceback,
                 "contextmanager": contextmanager, "Mapping": Mapping}
    # Preserve postponed annotations while loading the actual production methods.
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.Module(body=[future] + constants + [node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[node.name]


Intent = load_component("01_agent_start/02_intentRouter.py")
Remaining = load_component("03_job_execution/06_getRemainingJobs.py")
Router = load_component("03_job_execution/08_jobExecutionRouter.py")


class Cursor:
    def __init__(self, runnable=True, found=True, mig_count=1):
        self.calls = []
        self.runnable = runnable
        self.found = found
        self.mig_count = mig_count
        self.rows = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "COUNT(*)" in sql:
            self.rows = [(self.mig_count if "NEXT_MIG_INFO" in sql else 0,)]
        elif "SELECT MAP_ID, STATUS" in sql:
            self.rows = [(59, None if self.runnable else "PASS", "N", None, "Y", 1, 0)] if self.found else []
        elif "SELECT MAP_ID, PRIORITY" in sql:
            self.rows = [(59, 1, None)] if self.found and self.runnable else []
        elif "SELECT TO_CHAR(SPACE_NM)" in sql:
            self.rows = [("PAYMENT", "S59", None, None, "N", 1, 59, 0, 0)] if self.found else []
        elif "SELECT SQL_SEQ" in sql:
            self.rows = [(59, "S59", "PAYMENT", 1)] if self.found and self.runnable else []
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class JobExecutionTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("smartmigrate.workflow")
        self.old_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.capture = Capture()
        self.logger.addHandler(self.capture)
        self.addCleanup(self.logger.removeHandler, self.capture)
        self.addCleanup(self.logger.setLevel, self.old_level)

    def intent(self, scope="targeted", domain="MIG", targets=None, action="EXECUTE"):
        return {"route": "JOB_EXECUTION", "request_action": action,
                "requested_domain": domain, "execution_scope": scope,
                "target_filter": targets if targets is not None else {"map_ids": [59]},
                "clarification_required": False, "clarification_message": ""}

    def interpret(self, response=None, text="마이그레이션 59번 다시 실행해줘"):
        component = Intent()
        component.input_message = Message(text)
        component._classify = Mock(return_value=json.dumps(response or self.intent(), ensure_ascii=False))
        component.stop = Mock()
        return component.job_execution_response().data

    def query(self, payload, cursor=None):
        cursor = cursor or Cursor()
        component = Remaining()
        component.payload_json = Data(payload)
        component.system_schema = "SM"
        component._has_db_config = lambda: True

        @contextmanager
        def connect():
            yield type("Connection", (), {"cursor": lambda _: cursor})()

        component._connect = Mock(side_effect=connect)
        return component.get_remaining_jobs().data, component, cursor

    def router(self, payload):
        component = Router()
        component.payload_json = Data(payload)
        component.llm = Mock(side_effect=AssertionError("08 must not reinterpret intent"))
        component.stop = Mock()
        return component

    def logged(self, kind):
        return [json.loads(r.workflow_log[7]) for r in self.capture.records
                if getattr(r, "workflow_log", [None] * 3)[2] == kind and len(r.workflow_log) > 7]

    def test_korean_target_reaches_db_and_runs_without_second_llm(self):
        payload = self.interpret()
        self.assertEqual(payload["target_filter"]["map_ids"], [59])
        result, _, cursor = self.query(payload)
        route = self.router(result)
        output = route.mig_response().data
        self.assertEqual(output["selected_jobs"][0]["map_id"], 59)
        self.assertEqual(output["run_mode"], "targeted")
        route.llm.assert_not_called()
        self.assertTrue(any("MAP_ID IN" in sql and params == [59] for sql, params in cursor.calls))
        self.assertEqual(result["requested_target_status"]["migration"][0]["retry_count"], 0)
        self.assertEqual(self.logged("02_TO_06_PAYLOAD")[0], self.logged("06_INPUT_PAYLOAD")[0])
        self.assertEqual(self.logged("06_FINAL_OUTPUT")[0], self.logged("08_INPUT_PAYLOAD")[0])
        self.assertEqual(self.logged("08_FINAL_OUTPUT")[0]["selected_jobs"], output["selected_jobs"])

    def test_full_execution_needs_no_identifier_or_target_query(self):
        payload = self.interpret(self.intent("all", "FULL_WORKFLOW", {}))
        result, _, cursor = self.query(payload)
        output = self.router(result).full_workflow_response().data
        self.assertTrue(output["run_all_pending"])
        self.assertEqual(output["job_route"], "FULL_WORKFLOW")
        self.assertEqual(output["selected_jobs"], [])
        self.assertEqual(len(cursor.calls), 5)

    def test_domain_execution_stays_in_requested_domain(self):
        payload = self.interpret(self.intent("domain", "MIG", {}))
        result, _, _ = self.query(payload)
        self.assertEqual(self.router(result)._get_routed_payload()["job_route"], "MIG")

    def test_targeted_conversion_is_blocked_before_12a_when_migration_incomplete(self):
        payload = self.interpret(self.intent(domain="SQL_CONVERSION", targets={"sql_seqs": [6]}))
        result, _, _ = self.query(payload, Cursor(mig_count=1))
        component = self.router(result)
        output = component._get_routed_payload()
        self.assertEqual(output["job_route"], "PREREQUISITE_REQUIRED")
        self.assertEqual(output["selected_jobs"], [])
        self.assertEqual(output["next_node"], "chat_output")
        self.assertFalse(output["run_all_pending"])
        self.assertIn("Migration", output["routing_reason"])
        self.assertFalse(output["should_execute"])
        self.assertEqual(component.sql_conversion_response().data, {})
        component.stop.assert_called_with("sql_conversion_job")

    def test_exhausted_migration_retry_still_blocks_sql_and_does_not_inflate_job_total(self):
        payload = self.interpret(self.intent(domain="SQL_CONVERSION", targets={"sql_seqs": [6]}))
        cursor = Cursor(mig_count=0)
        original_execute = cursor.execute

        def execute(sql, params=None):
            original_execute(sql, params)
            if "COUNT(*)" in sql and "NEXT_MIG_INFO" in sql and "RETRY_COUNT" not in sql:
                cursor.rows = [(1,)]

        cursor.execute = execute
        result, _, _ = self.query(payload, cursor)
        self.assertEqual(result["job_availability"]["migration_total"], 0)
        self.assertEqual(result["job_availability"]["migration_incomplete_total"], 1)
        self.assertEqual(result["job_availability"]["total"], 0)
        self.assertEqual(self.router(result)._get_routed_payload()["job_route"], "PREREQUISITE_REQUIRED")

    def test_missing_or_conflicting_targets_never_expand_to_all(self):
        cases = [self.intent(targets={}), self.intent("all", "FULL_WORKFLOW", {"map_ids": [59]}),
                 self.intent("unknown", "UNKNOWN", {}), self.intent(domain="SQL_TUNING"),
                 self.intent(domain="SQL_CONVERSION", targets={"sql_ids": ["S59"]})]
        for response in cases:
            with self.subTest(response=response):
                payload = self.interpret(response)
                self.assertTrue(payload["clarification_required"])
                self.assertFalse(payload["should_execute"])
                result, query, cursor = self.query(payload)
                query._connect.assert_not_called()
                self.assertFalse(cursor.calls)
                routed = self.router(result)._get_routed_payload()
                self.assertFalse(routed["run_all_pending"])
                self.assertTrue(routed["routing_reason"])

    def test_status_query_does_not_execute_even_if_llm_route_conflicts(self):
        response = self.intent(action="STATUS_QUERY")
        component = Intent()
        component.input_message = Message("마이그레이션 59번 현재 상태를 다시 확인해주세요")
        component._classify = Mock(return_value=json.dumps(response))
        output = component.management_response().data
        self.assertEqual(output["route"], "MANAGEMENT")
        self.assertFalse(output["should_execute"])
        self.assertEqual(output["target_filter"]["map_ids"], [59])

    def test_db_failure_is_an_error_not_no_runnable(self):
        payload = self.interpret()
        _, query, _ = self.query(payload)
        query._connect = Mock(side_effect=RuntimeError("Oracle unavailable"))
        result = query.get_remaining_jobs().data
        output = self.router(result).mig_response().data
        self.assertFalse(output["ok"])
        self.assertIn("Oracle unavailable", output["error"])
        self.assertIn("RuntimeError", self.logged("06_GET_JOBS")[-1]["traceback"])
        self.assertIn("ValueError", self.logged("08_JOB_ROUTER")[-1]["traceback"])

    def test_found_but_excluded_and_not_found_have_different_reasons(self):
        reasons = []
        for found in (True, False):
            result, _, _ = self.query(self.interpret(), Cursor(runnable=False, found=found))
            reasons.append(self.router(result)._get_routed_payload()["routing_reason"])
        self.assertNotEqual(*reasons)

    def test_unqueried_target_is_not_reported_as_no_runnable(self):
        result, _, _ = self.query(self.interpret())
        result["job_detail_mode"] = "counts_only"
        output = self.router(result).mig_response().data
        self.assertFalse(output["ok"])
        self.assertIn("did not query", output["error"])

    def test_no_regex_fallback_and_invalid_ids_fail_before_db(self):
        for targets in ({}, {"map_ids": [59.5]}, {"map_ids": [True]}, {"map_ids": "59"}):
            with self.subTest(targets=targets):
                payload = {**self.intent(targets=targets), "should_execute": True,
                           "user_request": "MAP_ID 59 실행"}
                result, query, _ = self.query(payload)
                self.assertFalse(result["ok"])
                query._connect.assert_not_called()

    def test_sql_targets_are_queried_and_kept_in_requested_domain(self):
        for domain in ("SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"):
            for target in ({"sql_seqs": [59]}, {"sql_ids": ["S59"], "space_nms": ["PAYMENT"]}):
                with self.subTest(domain=domain, target=target):
                    payload = self.interpret(self.intent(domain=domain, targets=target))
                    result, _, cursor = self.query(payload, Cursor(mig_count=0))
                    output = self.router(result)._get_routed_payload()
                    self.assertEqual(output["job_route"], domain)
                    self.assertEqual(output["selected_jobs"][0]["sql_seq"], 59)
                    binds = [59] if "sql_seqs" in target else ["S59", "PAYMENT"]
                    self.assertTrue(any(params == binds for _, params in cursor.calls))
                    self.assertEqual(result["requested_target_status"]["sql"][0]["retry_count"], 0)

    def test_02_prompt_logs_are_exact_messages_sent_to_llm(self):
        class ChatMessage:
            def __init__(self, content):
                self.content = content

        messages_module = types.ModuleType("langchain_core.messages")
        messages_module.HumanMessage = messages_module.SystemMessage = ChatMessage
        component = Intent()
        component.llm = Mock()
        component.llm.invoke.return_value = ChatMessage(json.dumps(self.intent()))
        with patch.dict(sys.modules, {"langchain_core.messages": messages_module}):
            component._classify("마이그레이션 59번 다시 실행해줘", [])
        actual = component.llm.invoke.call_args.args[0]
        snapshot = self.logged("02_LLM_ROUTE_PROMPT")[-1]
        self.assertEqual([m.content for m in actual], [m["content"] for m in snapshot])
        self.assertIn('"target_filter"', snapshot[0]["content"])
        self.assertIn("마이그레이션 59번", snapshot[0]["content"])

    def test_invalid_llm_response_is_logged_before_parse_error(self):
        component = Intent()
        component.input_message = Message("마이그레이션 59번 다시 실행해줘")
        component._classify = Mock(return_value="invalid JSON response")
        output = component.job_execution_response().data
        self.assertFalse(output["ok"])
        raw = [r.workflow_log[7] for r in self.capture.records if getattr(r, "workflow_log", [None] * 3)[2] == "02_LLM_ROUTE_RESPONSE"]
        self.assertIn("invalid JSON response", raw)
        self.assertIn("JSONDecodeError", self.logged("02_INTENT_ROUTER")[-1]["traceback"])


if __name__ == "__main__":
    unittest.main()
