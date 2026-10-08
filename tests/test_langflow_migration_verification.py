"""Verify the production 10C result contract with a fake Oracle cursor."""
import ast
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import unittest
from unittest.mock import Mock


PATH = Path(__file__).resolve().parents[1] / "smartMigrate_2.0_Langflow/03_job_execution/10C_migOneJobPocExecutor3.py"
TREE = ast.parse(PATH.read_text(encoding="utf-8"))
PROMPTS = ast.literal_eval(next(n.value for n in TREE.body if isinstance(n, ast.AnnAssign) and n.target.id == "MIGRATION_PROMPT_TEMPLATE"))


def load_methods():
    node = next(n for n in TREE.body if isinstance(n, ast.ClassDef))
    node.bases = []
    node.body = [n for n in node.body if isinstance(n, ast.FunctionDef)]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    namespace = {"re": re, "Decimal": Decimal, "InvalidOperation": InvalidOperation,
                 "contextmanager": contextmanager}
    module = ast.Module(body=[future, node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(PATH), "exec"), namespace)
    return namespace[node.name]


Executor = load_methods()


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.component = Executor()

    def cursor_for(self, names, rows):
        cursor = Mock()
        cursor.description = [(name,) for name in names]
        cursor.fetchall.return_value = rows

        @contextmanager
        def connect(config):
            connection = Mock()
            connection.cursor.return_value = cursor
            yield connection

        self.component._connect = Mock(side_effect=connect)
        return cursor

    def run_verify(self, row, names=("SUCCESS_YN", "DIFF_TOT", "DIFF_C1")):
        self.cursor_for(names, [row])
        return self.component._execute_verification({}, "SELECT verification_result")

    def test_success_uses_column_names_and_numeric_zero(self):
        ok, _, rows = self.run_verify((Decimal("0"), " Y ", "0"), ("diff_c1", "success_yn", "diff_tot"))
        self.assertTrue(ok)
        self.assertEqual(rows, [[0, " Y ", 0]])

    def test_n_null_and_unexpected_success_values_fail_even_with_zero_diffs(self):
        for value in ("N", None, "", "YES", 0, 1):
            with self.subTest(value=value):
                ok, message, _ = self.run_verify((value, 0, 0))
                self.assertFalse(ok)
                self.assertIn("SUCCESS_YN", message)

    def test_y_does_not_hide_positive_negative_or_invalid_diffs(self):
        for value in (1, -1, None, "", "invalid", Decimal("NaN")):
            with self.subTest(value=value):
                ok, message, _ = self.run_verify(("Y", 0, value))
                self.assertFalse(ok)
                self.assertIn("DIFF_C1", message)

    def test_legacy_diff_only_format_is_rejected(self):
        ok, message, _ = self.run_verify((0, 0), ("DIFF_TOT", "DIFF_C1"))
        self.assertFalse(ok)
        self.assertIn("requires SUCCESS_YN", message)

    def test_missing_duplicate_and_unexpected_columns_fail(self):
        cases = [(("SUCCESS_YN",), ("Y",)),
                 (("SUCCESS_YN", "DIFF_TOT", "DIFF_TOT"), ("Y", 0, 0)),
                 (("SUCCESS_YN", "DIFF_TOT", "TOTAL"), ("Y", 0, 0))]
        for names, row in cases:
            with self.subTest(names=names):
                self.assertFalse(self.run_verify(row, names)[0])

    def test_tot_only_is_supported_when_no_columns_are_eligible(self):
        self.assertTrue(self.run_verify(("Y", 0), ("SUCCESS_YN", "DIFF_TOT"))[0])

    def test_requires_one_result_row(self):
        for rows in ([], [("Y", 0), ("Y", 0)]):
            with self.subTest(rows=rows):
                self.cursor_for(("SUCCESS_YN", "DIFF_TOT"), rows)
                self.assertFalse(self.component._execute_verification({}, "SELECT result")[0])

    def test_node_preserves_pass_fail_and_result_rows(self):
        for row, expected in ((('Y', 0, 0), "PASS"), (('N', 1, 0), "FAIL-TEST")):
            with self.subTest(row=row):
                self.cursor_for(("SUCCESS_YN", "DIFF_TOT", "DIFF_C1"), [row])
                output = self.component._node_verify({"verification_sql": "SELECT result"})
                self.assertEqual(output["status"], expected)
                self.assertEqual(output["stage"], "VERIFY_COUNT")
                self.assertEqual(output["outputs"]["verification_rows"], [list(row)])

    def test_updated_sql_shape_still_supports_record_verify_dataset_extraction(self):
        sql = """SELECT DECODE(ABS(S.TOT-T.TOT)+ABS(S.C1-T.C1),0,'Y','N') SUCCESS_YN,
                         S.TOT-T.TOT DIFF_TOT, S.C1-T.C1 DIFF_C1
                  FROM (SELECT COUNT(*) TOT, COUNT(A.MEM_ID) C1 FROM ASIS.TABLE1 A
                        WHERE EXISTS (SELECT 1 FROM ASIS.TABLE2 V WHERE V.ID=A.ID)) S,
                       (SELECT COUNT(*) TOT, COUNT(B.ID) C1 FROM TOBE.TABLE3 B) T"""
        source, target = self.component._extract_count_verify_datasets(sql)
        self.assertIn("WHERE EXISTS", source)
        self.assertEqual(self.component._count_verify_target_columns(target), ["ID"])


if __name__ == "__main__":
    unittest.main()
