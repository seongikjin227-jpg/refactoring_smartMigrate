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
        self.snapshot = [{"row_kind": "MASTER", "map_id": 101, "map_dtl": None},
                         {"row_kind": "DETAIL", "map_id": 101, "map_dtl": 1}]

    def test_literal_dml_and_schema_qualification(self):
        sql = self.component._validate_statements([
            "update NEXT_MIG_INFO set CONDITION='x; -- WHERE y, O''Brien' where MAP_ID=101;",
            "UPDATE SM.NEXT_MIG_INFO_DTL SET TO_COL=NULL WHERE MAP_DTL=1 AND MAP_ID=101",
        ], self.snapshot)
        self.assertTrue(sql[0].startswith("UPDATE SM.NEXT_MIG_INFO "))
        self.assertIn("'x; -- WHERE y, O''Brien'", sql[0])
        self.assertIn("MAP_DTL = 1", sql[1])

    def test_rejects_broad_or_non_mapping_dml(self):
        cases = [
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101 OR 1=1",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID>0",
            "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE 'MAP_ID'='MAP_ID'",
            "UPDATE NEXT_MIG_INFO SET STATUS='PASS' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO SET MAP_ID=2 WHERE MAP_ID=101",
            "UPDATE OTHER.NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='X' WHERE MAP_ID=101",
            "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='X' WHERE MAP_ID=101 AND MAP_DTL>0",
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
        detail = "INSERT INTO NEXT_MIG_INFO_DTL (MAP_ID,MAP_DTL,FR_COL,TO_COL) VALUES (102,1,'ID','NEW_ID')"
        self.assertEqual(len(self.component._validate_statements([master, detail], self.snapshot)), 2)
        with self.assertRaises(ValueError):
            self.component._validate_statements([detail, master], self.snapshot)

    def test_map_dtl_database_pk(self):
        snapshot = [{"row_kind": "MASTER", "map_id": 101, "map_dtl": None},
                    {"row_kind": "DETAIL", "map_id": 101, "map_dtl": 1}]
        sql = "UPDATE NEXT_MIG_INFO_DTL SET FR_COL='ID', TO_COL='NEW_ID' WHERE MAP_ID=101 AND MAP_DTL=1"
        self.assertEqual(len(self.component._validate_statements([sql], snapshot)), 1)
        with self.assertRaises(ValueError):
            self.component._validate_statements([
                "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='NEW_ID' WHERE MAP_ID=101 AND FR_COL='ID'"], snapshot)

    def test_mapping_snapshot_selects_only_existing_identifiers(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [("MASTER", "101", None), ("DETAIL", "101", "1")]
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            self.assertEqual(self.component._load_mapping_key_snapshot(), self.snapshot)
        cursor.execute.assert_called_once()
        query = cursor.execute.call_args.args[0]
        self.assertIn("UNION ALL", query)
        self.assertIn("CAST(NULL AS NUMBER)", query)
        for column in ("FR_TABLE", "TO_TABLE", "FR_COL", "TO_COL"):
            self.assertNotIn(column, query)
        self.assertNotIn("JOIN", query)
        self.assertNotIn("ALL_CONSTRAINTS", query)
        self.assertNotIn("ALL_INDEXES", query)
        connection.close.assert_called_once()

    def test_mapping_identifiers_do_not_truncate_or_accept_invalid_values(self):
        from decimal import Decimal
        for value in [101, "101", " 101 ", Decimal("101.0")]:
            self.assertEqual(self.component._mapping_identifier(value, "MAP_ID"), 101)
        for value in [None, "ABC", "", 1.5, "NaN", "Infinity"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.component._mapping_identifier(value, "MAP_ID")

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

    def test_execution_is_controlled_only_by_setting(self):
        for execute, request in [(False, "apply mapping"), (True, "preview validate 확인 후 적용 mapping")]:
            self.component.execute_updates = execute
            self.component.user_request = request
            self.component._load_mapping_key_snapshot = Mock(return_value=self.snapshot)
            self.component._generate = Mock(return_value=json.dumps({"summary": "change", "sql_statements": [
                "UPDATE NEXT_MIG_INFO SET FR_TABLE='X' WHERE MAP_ID=101"]}))
            self.component._execute = Mock(return_value=[{"index": 1, "sql": "generated SQL", "rowcount": 1}])
            message = self.component.run()
            result = self.component.status
            self.assertIsInstance(message, Message)
            self.assertTrue(result["ok"])
            self.assertEqual(result["dry_run"], not execute)
            self.assertEqual(result["database_executed"], execute)
            self.assertNotIn("UPDATE SM.", message.text)
            self.assertIn("| MAP_ID | 컬럼 매핑 | UPDATE | INSERT |", message.text)
            generated_log = next(call for call in self.component._log.call_args_list if call.args[:2] == ("GENERATE_SQL", "LLM"))
            sql_statements = generated_log.args[4]["sql_statements"]
            self.assertIn("SM.NEXT_MIG_INFO", sql_statements[0])
            if execute:
                self.component._execute.assert_called_once_with(sql_statements)
            else:
                self.component._execute.assert_not_called()

    def test_chat_output_summarizes_transaction_failure_without_sql(self):
        self.component.user_request = "apply mapping"
        self.component.execute_updates = True
        self.component._load_mapping_key_snapshot = Mock(return_value=self.snapshot)
        sqls = ["UPDATE NEXT_MIG_INFO SET TO_TABLE='Y' WHERE MAP_ID=101",
                "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='Z' WHERE MAP_ID=101 AND MAP_DTL=1"]
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
        for sql in sqls:
            self.assertNotIn(sql, message.text)
        self.assertEqual(len(self.component.status["executions"]), 1)
        self.assertEqual(self.component.status["counts"], {"columns": 1, "INSERT": 0, "UPDATE": 2,
                         "success": 0, "failure": 1, "rollback": 1, "pending": 0})

    def test_chat_output_summarizes_committed_changes(self):
        self.component.user_request = "apply mapping"
        self.component.execute_updates = True
        self.component._load_mapping_key_snapshot = Mock(return_value=self.snapshot)
        self.component._generate = Mock(return_value=json.dumps({"summary": "change", "sql_statements": [
            "UPDATE NEXT_MIG_INFO SET TO_TABLE='Y' WHERE MAP_ID=101"]}))
        connection = Mock()
        connection.cursor.return_value.rowcount = 1
        self.component._connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"oracledb": types.SimpleNamespace()}):
            message = self.component.run()
        self.assertTrue(self.component.status["database_executed"])
        self.assertEqual(self.component.status["counts"]["success"], 1)
        self.assertEqual(self.component.status["counts"]["UPDATE"], 1)
        self.assertNotIn("rowcount=", message.text)
        self.assertNotIn("UPDATE SM.", message.text)

    def test_master_insert_defaults_are_added_and_explicit_values_preserved(self):
        first = self.component._validate_statements([
            "INSERT INTO NEXT_MIG_INFO (MAP_ID,MAP_TYPE,FR_TABLE,TO_TABLE) VALUES (102,'RULE','X','Y')"], self.snapshot)[0]
        self.assertIn("USE_YN, PRIORITY", first)
        self.assertIn("'Y', 5", first)
        self.assertIn("'RULE'", first)
        explicit = self.component._validate_statements([
            "INSERT INTO NEXT_MIG_INFO (MAP_ID,FR_TABLE,TO_TABLE,USE_YN,PRIORITY) VALUES (102,'X','Y','N',2)"], self.snapshot)[0]
        self.assertIn("'N', 2", explicit)
        self.assertEqual(explicit.count("USE_YN"), 1)

    def test_target_table_groups_master_and_detail_counts(self):
        self.component._rollback_completed = False
        self.component._failed_statement_index = None
        self.component._sql_validated = True
        sql = self.component._validate_statements([
            "UPDATE NEXT_MIG_INFO_DTL SET TO_COL='NEW_ID' WHERE MAP_ID=101 AND MAP_DTL=1",
            "INSERT INTO NEXT_MIG_INFO (MAP_ID,FR_TABLE,TO_TABLE) VALUES (102,'NEW_SRC','NEW_DST')",
            "INSERT INTO NEXT_MIG_INFO_DTL (MAP_ID,MAP_DTL,FR_COL,TO_COL) VALUES (102,1,'ID','NEW_ID')"], self.snapshot)
        executions = [{"index": index, "sql": statement, "rowcount": 1} for index, statement in enumerate(sql, 1)]
        message = self.component._result(True, "committed", {"summary": "", "sql_statements": sql}, executions)
        self.assertIn("| 101 | 1건 | 1건 | 0건 |", message.text)
        self.assertIn("| 102 | 1건 | 0건 | 2건 |", message.text)
        self.assertNotIn("NEW_DST", message.text)
        self.assertIn("| 테이블 대상 | 컬럼 매핑 | INSERT | UPDATE | 성공 | 실패 | 미반영 |", message.text)
        self.assertIn("| 2건 | 2건 | 2건 | 1건 | 3건 | 0건 | 0건 |", message.text)
        self.assertEqual(self.component.status["counts"]["INSERT"], 2)
        self.assertEqual(self.component.status["counts"]["UPDATE"], 1)
        self.assertEqual(self.component.status["counts"]["success"], 3)
        self.assertNotIn("```sql", message.text)

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
