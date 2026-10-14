"""Verify the table preservation policy in the actual TOBE prompt sent to the LLM."""
import ast
from contextlib import contextmanager
import json
import logging
import os
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
namespace = dict(Component=object, contextmanager=contextmanager, json=json, re=re, os=os,
                 logging=logging, RAG_SEARCH="SEARCH", SQL_OUTPUT_FORMATTING_GUIDE="")
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
            "[SQL_CONVERSION_SEARCH_RAG_BY_SOURCE_TABLE]", 1)[0]
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


class ComplexMappingScopeTests(unittest.TestCase):
    def test_complex_query_exposes_physical_tables_and_projected_lineage_to_llm(self):
        source_query = "SELECT a.ID AS A_ID, b.ID AS B_ID FROM SRC.A a JOIN SRC.B b ON a.ID=b.REF_ID;"
        prompt = UnmappedTablePolicyTests().generate([
            {"map_type": "COMPLEX", "fr_table": source_query, "fr_col": "A_ID",
             "to_table": "MERGED_AB", "to_col": "MEMBER_ID", "condition": ""},
            {"map_type": "COMPLEX", "fr_table": source_query, "fr_col": "B_ID",
             "to_table": "MERGED_AB", "to_col": "DETAIL_ID", "condition": ""},
        ])
        self.assertIn("MAP_TYPE=COMPLEX | FR_TABLE_QUERY=(", prompt)
        self.assertIn("TO_TABLE=DST.MERGED_AB", prompt)
        self.assertIn("SOURCE_PHYSICAL_TABLES=A,B", prompt)
        self.assertIn("FR_COL=A_ID -> TO_COL=MEMBER_ID", prompt)
        self.assertIn("FR_COL=B_ID -> TO_COL=DETAIL_ID", prompt)
        self.assertIn("Trace each projected FR_COL/output alias", prompt)
        self.assertIn("not SQL to copy unchanged into TO-BE", prompt)
        self.assertIn("not independent one-to-one mappings", prompt)
        self.assertNotIn("Use the FR_TABLE_QUERY as one inline view", prompt)

    def test_unmapped_policy_requires_checking_complex_scope(self):
        prompt = UnmappedTablePolicyTests().generate([])
        self.assertIn("Check SIMPLE FR_TABLE and COMPLEX SOURCE_PHYSICAL_TABLES", prompt)
        self.assertIn("outside both SIMPLE mappings and applicable COMPLEX query scopes", prompt)
        self.assertIn("Internal join/filter columns are not automatically unused", prompt)

    def test_comma_joins_nested_queries_ctes_and_quoted_identifiers(self):
        query = '''WITH CTE_A (ID) AS (
            SELECT a.ID FROM "SRC"."A" a, B b WHERE a.ID=b.ID
        ), CTE_B AS (
            SELECT ID FROM CTE_A
        )
        SELECT q.ID FROM CTE_B q
        JOIN (SELECT ID FROM C WHERE EXISTS (SELECT 1 FROM D)) c ON q.ID=c.ID
        WHERE 'FROM GHOST' = q'[JOIN GHOST2]' /* FROM GHOST3 */ -- JOIN GHOST4
        '''
        self.assertEqual(Conversion()._query_physical_tables(query), {"A", "B", "C", "D"})

    def test_table_named_like_cte_in_its_definition_is_still_physical(self):
        query = "WITH A AS (SELECT ID FROM A) SELECT ID FROM A"
        self.assertEqual(Conversion()._query_physical_tables(query), {"A"})

    def test_from_inside_scalar_functions_is_not_a_table(self):
        query = "SELECT EXTRACT(YEAR FROM a.CREATED_AT), TRIM(BOTH 'x' FROM a.NAME) FROM A a"
        self.assertEqual(Conversion()._query_physical_tables(query), {"A"})

    def test_from_clause_state_does_not_leak_between_subqueries(self):
        query = "SELECT * FROM (SELECT ID FROM A) q, (SELECT ID FROM B) r WHERE EXISTS (SELECT 1 FROM C)"
        self.assertEqual(Conversion()._query_physical_tables(query), {"A", "B", "C"})

    def test_loader_selects_complex_by_physical_tables_not_sql_text_mentions(self):
        component = Conversion()
        component._qualify = Mock(side_effect=lambda name, schema: name)
        rows = [
            ("COMPLEX", "SELECT a.ID AS ALIAS_ID FROM SRC.A a JOIN B b ON a.ID=b.ID", "ALIAS_ID", "NEW_AB", "ID", ""),
            ("COMPLEX", "SELECT 'FROM A' AS ID FROM C /* JOIN B */", "ID", "NEW_C", "ID", ""),
            ("SIMPLE", "D", "ID", "NEW_D", "ID", ""),
        ]
        cursor = Mock()
        cursor.fetchall.return_value = rows
        connection = Mock()
        connection.cursor.return_value = cursor

        @contextmanager
        def connect(config):
            yield connection

        component._connect = connect
        rules = component._load_mapping_rules({}, '["A", "D"]')
        self.assertEqual([rule["to_table"] for rule in rules], ["NEW_AB", "NEW_D"])
        self.assertIn("UPPER(TRIM(M.STATUS)) = 'PASS'", cursor.execute.call_args.args[0])


class ConversionSearchTableScopeTests(unittest.TestCase):
    def component(self, batches):
        component = Conversion()
        component.rag_top_k = 1
        component._milvus_config = Mock(return_value={"rag_collection": "SM_RAG_RULES"})
        iterator = Mock()
        iterator.next.side_effect = batches
        client = Mock()
        client.query_iterator.return_value = iterator
        client.search.side_effect = AssertionError("Conversion must not run vector search")
        component._milvus_client = Mock(return_value=client)
        component._embed_texts = Mock(side_effect=AssertionError("Conversion must not embed SQL"))
        return component, client, iterator

    def row(self, rule_id, source_tables='["A"]'):
        return {
            "rag_id": str(rule_id), "category": "SQL_CONVERSION", "rule_type": "SEARCH",
            "source_tables": source_tables, "guidance_text": f"Guide {rule_id}",
            "source_sql": f"SELECT ID FROM A /* source {rule_id} */",
            "target_sql": f"SELECT ID FROM NEW_A /* target {rule_id} */",
        }

    def test_all_overlapping_rules_across_batches_reach_prompt_without_top_k(self):
        component, client, iterator = self.component([
            [self.row(i) for i in range(1000)],
            [self.row(1000, '["SRC.B", "C"]'), self.row(1001, '["OTHER"]'), self.row(1002, '')],
            [],
        ])
        examples = component._retrieve_rag_examples({}, {}, "SQL_CONVERSION", "SELECT ID FROM A", {"A", "B"}, "42")
        matches = examples[0]["top_rule_matches"]
        self.assertEqual(len(matches), 1001)
        self.assertEqual({rule["rule_id"] for rule in matches}, {str(i) for i in range(1001)})
        client.search.assert_not_called()
        component._embed_texts.assert_not_called()
        iterator.close.assert_called_once()
        query_args = client.query_iterator.call_args.kwargs
        self.assertEqual(query_args["limit"], -1)
        self.assertEqual(query_args["filter"], 'category == "SQL_CONVERSION" and rule_type == "SEARCH" and is_active == true')
        prompt = component._mapping_prompt_text([], [], examples, {"source_schema": "SRC", "target_schema": "DST"})
        self.assertIn("[SQL_CONVERSION_SEARCH_RAG_BY_SOURCE_TABLE]", prompt)
        for rule_id in (0, 999, 1000):
            self.assertIn(f"GUIDANCE_TEXT: Guide {rule_id}\n", prompt)
            self.assertIn(f"SOURCE_SQL: {self.row(rule_id)['source_sql']}", prompt)
            self.assertIn(f"TARGET_SQL: {self.row(rule_id)['target_sql']}", prompt)
        self.assertNotIn("SCORE=", prompt)

    def test_iterator_is_closed_and_errors_are_not_hidden(self):
        component, _, iterator = self.component([RuntimeError("Milvus read failed")])
        with self.assertRaisesRegex(RuntimeError, "Milvus read failed"):
            component._retrieve_rag_examples({}, {}, "SQL_CONVERSION", "SELECT ID FROM A", {"A"}, "42")
        iterator.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
