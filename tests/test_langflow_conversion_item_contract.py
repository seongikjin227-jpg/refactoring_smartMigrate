"""Reject invalid loop handoffs before Conversion can silently pass through."""
import ast
import asyncio
import json
import logging
from pathlib import Path
import re
import time
import unittest
from unittest.mock import Mock, AsyncMock

from test_langflow_job_execution_components import Data, Message, load_component


def conversion_entry():
    path = Path(__file__).resolve().parents[1] / "smartMigrate_2.0_Langflow/03_job_execution/12C_sqlConversionOneJobPocExecutor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NewType12CSqlConversionOneJobPocExecutor")
    methods = {"run_job", "_parse_payload", "_job_name", "_pass_through", "_validate_job_item"}
    node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    namespace = dict(Component=object, Data=Data, Message=Message, time=time, json=json, logging=logging, re=re)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[tree.body[0], node], type_ignores=[])), str(path), "exec"), namespace)
    return namespace[node.name]


Conversion = conversion_entry()


class ConversionItemContractTests(unittest.TestCase):
    def test_seq6_survives_12a_rows_and_12b_dispatch_with_one_body_run(self):
        class Rows:
            def __init__(self, rows):
                self.rows = rows

            def to_data_list(self):
                return [Data(row) for row in self.rows]

        jobs = load_component("03_job_execution/12A_sqlConversionJobsToLoopTable.py")()
        jobs.build_jobs_table.__globals__["DataFrame"] = Rows
        jobs.payload_json = Data({"run_mode": "targeted", "target_filter": {"sql_seqs": [6]},
                                  "selected_jobs": [{"job_route": "SQL_CONVERSION", "sql_seq": 6}]})
        jobs._db_config = Mock(return_value={"db_password": "private-test-value"})
        jobs._require_db_config = Mock()
        table = jobs.build_jobs_table()
        self.assertEqual(table.rows[0]["sql_seq"], 6)
        self.assertEqual(table.rows[0]["job_route"], "SQL_CONVERSION")
        Conversion()._validate_job_item(table.rows[0])

        loop = load_component("03_job_execution/12B_sqlConversionLoop.py")()
        loop._validate_data.__globals__.update(DataFrame=Rows, validate_data_input=lambda data: data.to_data_list())
        loop.data = table
        loop.ctx = {}
        loop._id = "conversion-test"
        loop._vertex = object()
        loop._event_manager = None
        loop.update_ctx = loop.ctx.update
        loop.stop = Mock()
        loop.execute_loop_body = AsyncMock(return_value=[Data({"sql_seq": 6, "status": "PASS-CONVERSION"})])

        async def run_outputs():
            await loop.item_output()
            return await loop.done_output()

        result = asyncio.run(run_outputs()).data
        loop.execute_loop_body.assert_awaited_once()
        dispatched = loop.execute_loop_body.call_args.args[0]
        self.assertEqual(dispatched[0].data["sql_seq"], 6)
        self.assertEqual(dispatched[0].data["job_route"], "SQL_CONVERSION")
        self.assertTrue(result["loop_done"])

    def test_admitted_conversion_runs_without_migration_or_mapping_status_gate(self):
        for mode in ("targeted", "all_pending", "full_workflow"):
            with self.subTest(mode=mode):
                component = Conversion()
                component.job_item = Data({"job_route": "SQL_CONVERSION", "sql_seq": 6,
                                           "run_mode": mode, "full_workflow": mode == "full_workflow"})
                component._db_config = Mock(return_value={})
                component._require_db_config = Mock()
                component._migration_prerequisite_status = Mock(side_effect=AssertionError("Migration admission belongs upstream"))
                component._mapping_rule_readiness = Mock(side_effect=AssertionError("Mapping status must not prevent admitted execution"))
                component._load_sql_job = Mock(return_value={"sql_seq": 6, "status_conversion": "PASS-CONVERSION"})
                component._increment_batch_count = Mock()
                component._mark_running_status = Mock()
                component._run_conversion = Mock(return_value={"sql_seq": 6, "status": "PASS-CONVERSION"})
                result = component.run_job().data
                self.assertEqual(result["sql_seq"], 6)
                component._migration_prerequisite_status.assert_not_called()
                component._mapping_rule_readiness.assert_not_called()
                component._increment_batch_count.assert_called_once()
                component._mark_running_status.assert_called_once()
                component._run_conversion.assert_called_once()

    def test_invalid_handoffs_stop_before_db_or_downstream_execution(self):
        invalid = [
            {"count": 2, "items": [{"job_route": "SQL_CONVERSION", "sql_seq": 42}]},
            {"loop_done": True, "job_route": "SQL_CONVERSION"},
            {"job_type": "SQL_CONVERSION", "sql_id": "S42", "space_nm": "PAYMENT", "job_index": 1},
            {"job_route": "SQL_CONVERSION"},
            {},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                component = Conversion()
                component.job_item = Data(payload)
                component._db_config = Mock(side_effect=AssertionError("Invalid item must not reach DB"))
                with self.assertRaises(ValueError):
                    component.run_job()
                component._db_config.assert_not_called()

    def test_valid_conversion_rows_accept_both_identifier_forms(self):
        component = Conversion()
        component._validate_job_item({"job_route": "SQL_CONVERSION", "sql_seq": 42})
        component._validate_job_item({"job_name": "conversion", "sql_id": "S42", "space_nm": "PAYMENT"})
        component._validate_job_item({"planned_job_route": "SQL_CONVERSION", "sql_seq": 42, "full_workflow": True})

    def test_other_domain_keeps_full_workflow_pass_through(self):
        component = Conversion()
        component.job_item = Data({"planned_job_route": "MIG", "map_id": 59, "full_workflow": True})
        component._db_config = Mock(side_effect=AssertionError("Other domain must not reach DB"))
        result = component.run_job().data
        self.assertTrue(result["component_pass_through"])
        self.assertEqual(result["next_node"], "15C_sqlTuningOneJobPocExecutor")
        component._db_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
