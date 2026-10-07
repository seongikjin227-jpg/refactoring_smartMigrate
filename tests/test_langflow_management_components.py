"""Offline behavioral checks; no Oracle/LLM/Langflow installation required.

Load component methods via AST, omitting only Langflow UI declarations/imports.
These checks exercise the production methods with fake external boundaries.
"""
import ast
import asyncio
import json
import logging
from pathlib import Path
import re
import sys
import types
from typing import Any
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "smartMigrate_2.0_Langflow"


class Data:
    def __init__(self, data=None):
        self.data = data or {}


class Message:
    def __init__(self, text="", data=None, files=None):
        self.text = text
        self.data = data or {}
        self.files = files or []
        self.session_id = "session-1"


def load_component(relative_path, class_name):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    node.body = [n for n in node.body if not (
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"inputs", "outputs"} for t in n.targets)
    )]
    constants = [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)]
    module = ast.Module(body=constants + [node], type_ignores=[])
    namespace = {"Component": object, "Data": Data, "Message": Message, "Any": Any,
                 "json": json, "re": re, "logging": logging, "asyncio": asyncio}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[class_name]


Mapping = load_component("02_flow_management/04_mappingRuleUpdateSqlGenerate.py", "NewType04MappingRuleUpdateSqlGenerate")
Router = load_component("02_flow_management/04_managementRouter.py", "NewType04ManagementRouter")
Runtime = load_component("01_agent_start/00A_logRuntimeStart.py", "NewType00ALogRuntimeStart")


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.component = Mapping()
        self.component.system_schema = "SM"
        self.component._log = Mock()
        self.snapshot = [{"row_kind": "MASTER", "map_id": 101, "fr_col": None},
                         {"row_kind": "DETAIL", "map_id": 101, "fr_col": "ID"}]

    def test_literal_dml_and_schema_qualification(self):
        sql = self.component._validate_statements([
            "update NEXT_MIG_INFO set CONDITION='x; -- WHERE y, O''Brien' where MAP_ID=101;",
            "UPDATE SM.NEXT_MIG_INFO_DTL SET TO_COL=NULL WHERE FR_COL='ID' AND MAP_ID=101",
        ], self.snapshot)
        self.assertTrue(sql[0].startswith("UPDATE SM.NEXT_MIG_INFO "))
        self.assertIn("'x; -- WHERE y, O''Brien'", sql[0])
        self.assertIn("FR_COL = 'ID'", sql[1])

    def test_rejects_broad_or_non_mapping_dml(self):
        cases = [
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101 OR 1=1",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID>0",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE 'MAP_ID'='MAP_ID'",
            "UPDATE NEXT_MIG_INFO SET STATUS='PASS' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO SET MAP_ID=2 WHERE MAP_ID=101",
            "UPDATE OTHER.NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='X' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='X' WHERE MAP_ID=101 AND MAP_DTL=1",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE=(SELECT SECRET FROM OTHER) WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE=some_function() WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101; DELETE FROM NEXT_MIG_INFO",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101 -- comment",
            "INSERT INTO NEXT_MIG_INFO (MAP_ID) VALUES (2)",
            "INSERT INTO NEXT_MIG_INFO (MAP_ID,FR_TABLE,TO_TABLE) SELECT 2,'X','Y' FROM DUAL",
        ]
        for sql in cases:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.component._validate_statements([sql], self.snapshot)

    def test_snapshot_conflict_and_duplicate_detection(self):
        for sql in ["INSERT INTO NEXT_MIG_INFO (MAP_ID,FR_TABLE,TO_TABLE) VALUES (101,'X','Y')",
                    "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=999"]:
            with self.assertRaises(ValueError):
                self.component._validate_statements([sql], self.snapshot)
        sql = "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101"
        with self.assertRaises(ValueError):
            self.component._validate_statements([sql, sql], self.snapshot)

    def test_new_master_before_detail(self):
        master = "INSERT INTO NEXT_MIG_INFO (MAP_ID,FR_TABLE,TO_TABLE) VALUES (102,'X','Y')"
        detail = "INSERT INTO NEXT_MIG_INFO_DTL (MAP_ID,FR_COL,TO_COL) VALUES (102,'ID','NEW_ID')"
        self.assertEqual(len(self.component._validate_statements([master, detail], self.snapshot)), 2)
        with self.assertRaises(ValueError):
            self.component._validate_statements([detail, master], self.snapshot)

    def test_map_dtl_database_pk(self):
        self.component._detail_key_column = "MAP_DTL"
        snapshot = [{"row_kind": "MASTER", "map_id": 101, "map_dtl": None},
                    {"row_kind": "DETAIL", "map_id": 101, "map_dtl": 1}]
        sql = "UPDATE NEXT_MIG_INFO_DTL SET FR_COL='ID', TO_COL='NEW_ID' WHERE MAP_ID=101 AND MAP_DTL=1"
        self.assertEqual(len(self.component._validate_statements([sql], snapshot)), 1)
        with self.assertRaises(ValueError):
            self.component._validate_statements([
                "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='NEW_ID' WHERE MAP_ID=101 AND FR_COL='ID'"], snapshot)

    def test_database_pk_discovery(self):
        for key in ["MAP_DTL", "FR_COL"]:
            connection = Mock()
            cursor = connection.cursor.return_value
            cursor.fetchall.side_effect = [[("MAP_ID",), (key,)], [("MASTER", 101, None)]]
            cursor.description = [("ROW_KIND",), ("MAP_ID",), (key,)]
            self.component._connect = Mock(return_value=connection)
            with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
                self.assertEqual(len(self.component._load_pk_snapshot()), 1)
            self.assertEqual(self.component._detail_key_column, key)
            self.assertIn("D." + key, cursor.execute.call_args[0][0])
            connection.close.assert_called_once()

    def test_unknown_database_pk_fails_before_generation(self):
        connection = Mock()
        connection.cursor.return_value.fetchall.return_value = [("OTHER_KEY",)]
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            with self.assertRaises(ValueError):
                self.component._load_pk_snapshot()
        connection.close.assert_called_once()

    def test_user_request_is_passed_without_metadata(self):
        self.component.user_request = Message(text="MAP_ID 101 mapping information")
        self.assertEqual(self.component._request_text(), "MAP_ID 101 mapping information")
        self.component.user_request = "original mapping request"
        self.assertEqual(self.component._request_text(), "original mapping request")

    def test_empty_user_request_is_rejected(self):
        self.component.user_request = Message(text="")
        with self.assertRaises(ValueError):
            self.component._request_text()

    def test_llm_gets_user_request_and_snapshot_only(self):
        class LLMMessage:
            def __init__(self, content):
                self.content = content
        messages = types.ModuleType("langchain_core.messages")
        messages.HumanMessage = messages.SystemMessage = LLMMessage
        llm = Mock()
        llm.invoke.return_value = types.SimpleNamespace(content='{"summary":"ok","sql_statements":[]}')
        self.component.llm = llm
        with patch.dict(sys.modules, {"langchain_core.messages": messages}):
            self.component._generate("complete mapping information", self.snapshot)
        sent = llm.invoke.call_args[0][0]
        self.assertEqual(json.loads(sent[1].content), {
            "user_request": "complete mapping information", "current_mapping_rule_table": self.snapshot})
        self.assertIn("Configured system_schema: SM", sent[0].content)

    def test_default_dry_run_and_preview_with_execution_enabled(self):
        for execute, request in [(False, "apply mapping"), (True, "preview mapping")]:
            self.component.execute_updates = execute
            self.component.user_request = request
            self.component._load_pk_snapshot = Mock(return_value=self.snapshot)
            self.component._generate = Mock(return_value=json.dumps({"summary": "change", "sql_statements": [
                "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101"]}))
            self.component._execute = Mock()
            message = self.component.run()
            result = self.component.status
            self.assertIsInstance(message, Message)
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertIn(result["sql_statements"][0], message.text)
            self.assertIn("SM.NEXT_MIG_INFO", result["sql_statements"][0])
            self.component._execute.assert_not_called()

    def test_chat_output_contains_sql_and_transaction_failure(self):
        self.component.user_request = "apply mapping"
        self.component.execute_updates = True
        self.component._load_pk_snapshot = Mock(return_value=self.snapshot)
        sqls = ["UPDATE NEXT_MIG_INFO SET TO_TABLE='Y' WHERE MAP_ID=101",
                "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='Z' WHERE MAP_ID=101 AND FR_COL='ID'"]
        self.component._generate = Mock(return_value=json.dumps({"summary": "change", "sql_statements": sqls}))
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.rowcount = 1
        cursor.execute.side_effect = [None, RuntimeError("second SQL failed")]
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            message = self.component.run()
        self.assertFalse(self.component.status["ok"])
        self.assertTrue(self.component.status["rolled_back"])
        self.assertIn("second SQL failed", message.text)
        for sql in self.component.status["sql_statements"]:
            self.assertIn(sql, message.text)
        self.assertEqual(len(self.component.status["executions"]), 1)

    def test_chat_output_contains_commit_and_rowcount(self):
        self.component.user_request = "apply mapping"
        self.component.execute_updates = True
        self.component._load_pk_snapshot = Mock(return_value=self.snapshot)
        self.component._generate = Mock(return_value=json.dumps({"summary": "change", "sql_statements": [
            "UPDATE NEXT_MIG_INFO SET TO_TABLE='Y' WHERE MAP_ID=101"]}))
        connection = Mock()
        connection.cursor.return_value.rowcount = 1
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            message = self.component.run()
        self.assertTrue(self.component.status["database_executed"])
        self.assertIn("commit", message.text)
        self.assertIn("rowcount=1", message.text)

    def test_rowcount_and_sql_failure_rollback_whole_transaction(self):
        for rowcount, failure in [(0, None), (2, None), (1, RuntimeError("db error"))]:
            connection = Mock()
            cursor = connection.cursor.return_value
            cursor.rowcount = rowcount
            cursor.execute.side_effect = failure
            self.component._connect = Mock(return_value=connection)
            with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.component._execute(["UPDATE SM.NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101"])
            connection.rollback.assert_called_once()
            connection.commit.assert_not_called()
            cursor.close.assert_called_once()
            connection.close.assert_called_once()

    def test_commit_after_all_statements(self):
        connection = Mock()
        connection.cursor.return_value.rowcount = 1
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            result = self.component._execute(["first", "second"])
        self.assertEqual(len(result), 2)
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()


class AttachmentTests(unittest.TestCase):
    def test_attachment_does_not_override_mapping_route(self):
        router = Router()
        router.payload_json = Data({"user_request": "apply mapping", "files": ["session/a.xlsx"]})
        router._route_with_llm = Mock(return_value={"management_route": "MAPPING_RULE_UPDATE"})
        result = router._get_routed_payload()
        self.assertEqual(result["management_route"], "MAPPING_RULE_UPDATE")
        self.assertEqual(result["attachment_file_reference"], "session/a.xlsx")

    def test_mapping_router_outputs_exact_original_user_request(self):
        router = Router()
        router._get_routed_payload = Mock(return_value={"management_route": "MAPPING_RULE_UPDATE",
            "user_request": "original mapping data", "resolved_user_request": "other request",
            "uploaded_attachment": {"ignored": True}})
        result = router.mapping_rule_update_response()
        self.assertIsInstance(result, Message)
        self.assertEqual(result.text, "original mapping data")
        router.stop = Mock()
        router._get_routed_payload.return_value = {"management_route": "DASHBOARD"}
        self.assertEqual(router.mapping_rule_update_response().text, "")
        router.stop.assert_called_once_with("mapping_rule_update")

    def test_runtime_preserves_message_and_adds_workbook(self):
        runtime = Runtime()
        runtime.status = {"ok": True}
        message = Message("apply mapping", {"downloadURL": "https://example.test/a", "keep": 42})
        async def download(url):
            return b"PKtest"
        runtime._download = download
        runtime._parse_excel = Mock(return_value={"sheets": [{"name": "master"}]})
        runtime._parse_uploaded_attachment(message, Mock())
        self.assertEqual(message.text, "apply mapping")
        self.assertEqual(message.data["keep"], 42)
        self.assertIn("parsed_excel", message.data["uploaded_attachment"])

    def test_parse_failure_is_propagated(self):
        runtime = Runtime()
        runtime.status = {}
        message = Message(data={"downloadURL": "https://example.test/a"})
        async def download(url):
            raise ValueError("invalid workbook")
        runtime._download = download
        runtime._parse_uploaded_attachment(message, Mock())
        self.assertEqual(message.data["uploaded_attachment"]["error"], "invalid workbook")
        self.assertEqual(runtime.status["attachment_parse_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
