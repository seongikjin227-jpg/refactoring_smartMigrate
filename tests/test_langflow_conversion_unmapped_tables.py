"""Verify the table preservation policy in the actual TOBE prompt sent to the LLM."""
import ast
from contextlib import contextmanager
import json
import re
from pathlib import Path
import unittest
from unittest.mock import Mock

PATH = Path(__file__).resolve().parents[1] / "smartMigrate_2.0_Langflow/03_job_execution/12C_sqlConversionOneJobPocExecutor.py"
tree = ast.parse(PATH.read_text(encoding="utf-8"))
component_node = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "NewType12CSqlConversionOneJobPocExecutor")
component_node.body = [node for node in component_node.body if isinstance(node, ast.FunctionDef)]
prompt_nodes = [
    node for node in tree.body
    if (isinstance(node, ast.ClassDef) and node.name == "_PromptValues")
    or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "SQL_PROMPT_TEMPLATES")
]
namespace = dict(Component=object, contextmanager=contextmanager, json=json, re=re,
                 SQL_OUTPUT_FORMATTING_GUIDE="")
future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
module = ast.Module(body=[future] + prompt_nodes + [component_node], type_ignores=[])
exec(compile(ast.fix_missing_locations(module), str(PATH), "exec"), namespace)
Conversion = namespace[component_node.name]


class UnmappedTablePolicyTests(unittest.TestCase):
    def generate(self, rules, general_rules=None):
        component = Conversion()
        component._workflow_log_job_id = Mock(return_value="42")
        component._log_prompt = Mock()
        component._call_llm_text = Mock(return_value=("SELECT a.ID, b.ID FROM NEW_A a JOIN B b ON a.ID = b.ID", "test"))
        component._increment_rag_hits = Mock()
        component._generate_tobe_sql(
            job={"sql_seq": 42}, db_config={"source_schema": "SRC", "target_schema": "DST"},
            llm_config={}, rag_config={},
            source_sql="SELECT a.ID, b.ID FROM A a JOIN B b ON a.ID = b.ID",
            mapping_rules=rules, target_table='[  "A", "B"]', last_error="", retry_count=0,
            general_rules=general_rules or [], examples=[], correct_sql_hint_text="- (empty)",
        )
        component._call_llm_text.assert_called_once()
        return component._call_llm_text.call_args.args[0]

    def test_unmapped_table_is_preserved_in_mixed_mapping_prompt(self):
        prompt = self.generate([{
            "map_type": "SIMPLE", "fr_table": "A", "fr_col": "ID",
            "to_table": "NEW_A", "to_col": "ID", "condition": "",
        }])
        self.assertIn("FR_TABLE=SRC.A | TO_TABLE=DST.NEW_A", prompt)
        self.assertIn("Preserve original table names, column names, aliases, joins, filters", prompt)
        self.assertIn("Never invent table names", prompt)
        self.assertIn("This does not apply to columns of unmapped tables", prompt)
        self.assertNotIn("Source tables not listed in MIGRATION_MAPPING_RULES are unused", prompt)

    def test_no_mapping_still_reaches_llm_with_preservation_policy(self):
        prompt = self.generate([])
        self.assertIn("[MIGRATION_MAPPING_RULES]\n- (empty)", prompt)
        self.assertIn("Preserve original table names", prompt)

    def test_explicit_unused_column_policy_is_retained(self):
        prompt = self.generate([{
            "map_type": "SIMPLE", "fr_table": "A", "fr_col": "OLD_FLAG",
            "to_table": "NEW_A", "to_col": "NULL", "condition": "",
        }])
        self.assertIn("FR_COL=OLD_FLAG -> TO_COL=__UNUSED__", prompt)
        self.assertIn("Source columns mapped to TO_COL=__UNUSED__ are unused", prompt)

    def test_general_sql_pair_reaches_llm_when_guidance_is_empty(self):
        rule = Conversion()._milvus_rag_entity({"entity": {
            "rag_id": "7", "category": "SQL_CONVERSION", "rule_type": "GENERAL",
            "source_tables": '["A"]', "guidance_text": "",
            "source_sql": "SELECT\n ID FROM A WHERE ID = #{id}",
            "target_sql": "SELECT\n MEMBER_ID FROM NEW_A WHERE MEMBER_ID = #{id}",
        }})
        prompt = self.generate([], general_rules=[rule])
        general_section = prompt.split("[SQL_CONVERSION_GENERAL_RAG_GUIDANCE]", 1)[1].split(
            "[SQL_CONVERSION_SEARCH_RAG_TOP_3_BY_FULL_SQL]", 1)[0]
        self.assertIn("RAG_ID=7 | SOURCE_TABLES=A", general_section)
        self.assertIn(f"SOURCE_SQL: {rule['source_sql']}", general_section)
        self.assertIn(f"TARGET_SQL: {rule['target_sql']}", general_section)

    def test_guidance_only_general_remains_valid(self):
        prompt = self.generate([], general_rules=[{
            "rule_id": "8", "source_tables": [], "guidance": ["Preserve join semantics."],
            "source_sql": "", "target_sql": "",
        }])
        self.assertIn("RAG_ID=8 | SOURCE_TABLES=ALL", prompt)
        self.assertIn("Preserve join semantics.", prompt)
        self.assertNotIn("SOURCE_SQL:", prompt)
        self.assertNotIn("TARGET_SQL:", prompt)


if __name__ == "__main__":
    unittest.main()
