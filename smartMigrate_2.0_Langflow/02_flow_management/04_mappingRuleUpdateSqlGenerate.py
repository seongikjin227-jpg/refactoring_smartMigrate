from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


SQL_GENERATION_PROMPT = """You generate Oracle SQL for SmartMigrate mapping-rule updates.

The supplied current mapping-rule snapshot contains only primary keys. Use it
solely to decide INSERT versus UPDATE; take every SET/INSERT value from the
user's mapping-rule request.
Return exactly one JSON object, no Markdown:
{"summary":"...", "sql_statements":["UPDATE ...", "INSERT ..."]}

Rules:
1. Generate only INSERT INTO or UPDATE statements for NEXT_MIG_INFO and
   NEXT_MIG_INFO_DTL. Do not generate DELETE, MERGE, DDL, PL/SQL, COMMIT,
   ROLLBACK, SELECT, or statements for other tables.
2. An UPDATE must have WHERE MAP_ID = ...; an update to NEXT_MIG_INFO_DTL must
   also identify MAP_DTL in its WHERE clause.
3. Do not modify execution/status/result columns such as STATUS, MIG_SQL,
   VERIFY_SQL, RETRY_COUNT, USER_EDITED, or USE_YN unless the user explicitly
   requested that exact mapping-rule change.
4. For NEXT_MIG_INFO, an existing MAP_ID requires UPDATE and a missing MAP_ID
   requires INSERT. For NEXT_MIG_INFO_DTL, an existing (MAP_ID, MAP_DTL)
   requires UPDATE and a missing pair requires INSERT.
5. Never invent MAP_ID, MAP_DTL, tables, columns, or values absent from the
   request. If the request is ambiguous, return an empty sql_statements array
   and explain why in summary.
"""


class NewType04MappingRuleUpdateSqlGenerate(Component):
    """Generate and transactionally apply mapping-rule DML from a Table snapshot."""

    display_name = "04 Mapping Rule Update SQL Generate"
    description = "Loads a PK-only mapping snapshot, then LLM-generates and applies validated mapping-rule DML."
    name = "NewType04MappingRuleUpdateSqlGenerate"
    icon = "FilePenLine"

    inputs = [
        DataInput(name="router_payload", display_name="04 Router Payload", required=False),
        MessageTextInput(name="mapping_rule_text", display_name="Mapping Rule Text", required=False),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"]),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        BoolInput(name="execute_updates", display_name="Execute Generated SQL", value=True, required=False),
        IntInput(name="max_snapshot_chars", display_name="Max Snapshot Characters", value=120000, required=False),
        IntInput(name="max_statements", display_name="Max SQL Statements", value=200, required=False),
    ]
    outputs = [Output(display_name="Result", name="result", method="run")]

    def run(self) -> Data:
        try:
            request = self._request_text()
            snapshot = self._load_pk_snapshot()
            self._log("INPUT", "RECEIVE", "START", "Mapping-rule request and Table snapshot received", {
                "request": request, "snapshot": snapshot,
            })
            raw_response = self._generate(request, snapshot)
            generated = self._parse_generated(raw_response)
            statements = self._validate_statements(generated["sql_statements"])
            self._log("GENERATE_SQL", "LLM", "PASS", generated["summary"], {
                "raw_llm_response": raw_response, "sql_statements": statements,
            })
            if not statements:
                return self._result(False, "No executable SQL was generated.", generated, [])
            if not bool(getattr(self, "execute_updates", True)):
                self._log("EXECUTE_SQL", "DRY_RUN", "PASS", "Generated SQL was not executed (execute_updates=false).", statements)
                return self._result(True, "Dry run completed; generated SQL was not executed.", generated, [])
            executions = self._execute(statements)
            self._log("COMPLETE", "TRANSACTION", "PASS", f"Committed {len(executions)} mapping-rule SQL statement(s).", executions)
            return self._result(True, "Mapping-rule update committed.", generated, executions)
        except Exception as exc:
            self._log("FAILED", "ERROR", "FAIL", str(exc), None, level="error")
            result = {"ok": False, "component": self.name, "error": str(exc), "final": True}
            self.status = result
            return Data(data=result)

    def _request_text(self) -> str:
        direct = str(getattr(self, "mapping_rule_text", "") or "").strip()
        if direct:
            return direct
        raw = getattr(self, "router_payload", None)
        payload = raw.data if isinstance(raw, Data) else raw
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return payload.strip()
        if not isinstance(payload, dict):
            raise ValueError("Provide Mapping Rule Text or connect the 04 Router Payload.")
        text = str(payload.get("effective_user_request") or payload.get("resolved_user_request") or payload.get("user_request") or "").strip()
        if not text:
            raise ValueError("Mapping rule request text is empty.")
        return text

    def _load_pk_snapshot(self) -> list[dict[str, Any]]:
        """Read only the two mapping-rule primary-key shapes from Oracle.

        This intentionally does not load FR_TABLE/FR_COL or any existing mapping
        values: the snapshot decides INSERT vs UPDATE only.
        """
        schema = self._schema()
        sql = f"""
SELECT 'MASTER' AS ROW_KIND, M.MAP_ID, CAST(NULL AS NUMBER) AS MAP_DTL
  FROM {schema}.NEXT_MIG_INFO M
UNION ALL
SELECT 'DETAIL' AS ROW_KIND, D.MAP_ID, D.MAP_DTL
  FROM {schema}.NEXT_MIG_INFO_DTL D
ORDER BY MAP_ID, MAP_DTL NULLS FIRST
""".strip()
        self._log("LOAD_PK", "SELECT", "START", "Loading mapping-rule primary keys.", sql)
        import oracledb

        connection = self._connect(oracledb)
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            names = [str(column[0]).lower() for column in cursor.description]
            snapshot = [dict(zip(names, row)) for row in cursor.fetchall()]
            self._log("LOAD_PK", "SELECT", "PASS", f"Loaded {len(snapshot)} mapping-rule primary-key row(s).", snapshot)
        except Exception as exc:
            self._log("LOAD_PK", "SELECT", "FAIL", f"Primary-key snapshot query failed: {exc}", {"sql": sql, "error": str(exc)}, level="error")
            raise
        finally:
            cursor.close()
            connection.close()
        encoded = json.dumps(snapshot, ensure_ascii=False, default=str)
        limit = max(1000, int(getattr(self, "max_snapshot_chars", 120000) or 120000))
        if len(encoded) > limit:
            raise ValueError(
                f"Mapping Rule Table is {len(encoded):,} characters; it exceeds max_snapshot_chars={limit:,}. "
                "Filter the SELECT scope or raise the limit so the LLM does not receive a partial snapshot."
            )
        return snapshot

    def _generate(self, request: str, snapshot: Any) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("Connect a Language Model to 04 Mapping Rule Update SQL Generate.")
        response = llm.invoke([
            SystemMessage(content=SQL_GENERATION_PROMPT),
            HumanMessage(content=json.dumps({"mapping_rule_request": request, "current_mapping_rule_table": snapshot}, ensure_ascii=False, default=str)),
        ])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(item if isinstance(item, str) else str(item.get("text") or "") for item in content).strip()
        return str(content or "").strip()

    def _parse_generated(self, raw: str) -> dict[str, Any]:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        try:
            value = json.loads(match.group(0) if match else clean)
        except ValueError as exc:
            raise ValueError("LLM must return one JSON object containing sql_statements.") from exc
        statements = value.get("sql_statements") if isinstance(value, dict) else None
        if not isinstance(statements, list) or not all(isinstance(item, str) for item in statements):
            raise ValueError("LLM response sql_statements must be an array of SQL strings.")
        maximum = max(1, int(getattr(self, "max_statements", 200) or 200))
        if len(statements) > maximum:
            raise ValueError(f"LLM generated {len(statements)} statements; max_statements is {maximum}.")
        return {"summary": str(value.get("summary") or ""), "sql_statements": statements}

    def _validate_statements(self, statements: list[str]) -> list[str]:
        schema = self._schema()
        accepted: list[str] = []
        for index, raw in enumerate(statements, start=1):
            sql = raw.strip().rstrip(";").strip()
            if not sql:
                raise ValueError(f"sql_statements[{index}] is empty.")
            if ";" in sql or re.search(r"--|/\*|\*/", sql):
                raise ValueError(f"sql_statements[{index}] contains multiple SQL or comments and is rejected.")
            target_match = re.match(r"^(UPDATE|INSERT\s+INTO)\s+((?:[A-Za-z][A-Za-z0-9_$#]*\.)?NEXT_MIG_INFO(?:_DTL)?)\b", sql, flags=re.I)
            if not target_match:
                raise ValueError(f"sql_statements[{index}] may only INSERT/UPDATE NEXT_MIG_INFO or NEXT_MIG_INFO_DTL.")
            table = target_match.group(2).upper()
            if "." in table and table.split(".", 1)[0] != schema:
                raise ValueError(f"sql_statements[{index}] targets a schema other than {schema}.")
            is_update = target_match.group(1).upper() == "UPDATE"
            if is_update:
                where = re.search(r"\bWHERE\b(.+)$", sql, flags=re.I | re.S)
                if not where or not re.search(r"\bMAP_ID\b", where.group(1), flags=re.I):
                    raise ValueError(f"sql_statements[{index}] UPDATE requires a WHERE clause identifying MAP_ID.")
                if table.endswith("NEXT_MIG_INFO_DTL") and not re.search(r"\bMAP_DTL\b", where.group(1), flags=re.I):
                    raise ValueError(f"sql_statements[{index}] detail UPDATE must identify MAP_DTL.")
            accepted.append(sql)
        return accepted

    def _execute(self, statements: list[str]) -> list[dict[str, Any]]:
        import oracledb

        results: list[dict[str, Any]] = []
        connection = self._connect(oracledb)
        cursor = connection.cursor()
        try:
            for index, sql in enumerate(statements, start=1):
                self._log("EXECUTE_SQL", "STATEMENT", "START", f"Executing statement {index}/{len(statements)}.", sql)
                cursor.execute(sql)
                outcome = {"index": index, "sql": sql, "rowcount": cursor.rowcount}
                results.append(outcome)
                self._log("EXECUTE_SQL", "STATEMENT", "PASS", f"Statement {index}/{len(statements)} executed; rowcount={cursor.rowcount}.", outcome)
            connection.commit()
            return results
        except Exception as exc:
            connection.rollback()
            failed_sql = statements[len(results)] if len(results) < len(statements) else None
            self._log(
                "EXECUTE_SQL",
                "STATEMENT",
                "FAIL",
                f"Statement {len(results) + 1}/{len(statements)} failed; transaction will be rolled back: {exc}",
                {"index": len(results) + 1, "sql": failed_sql, "error": str(exc)},
                level="error",
            )
            self._log(
                "ROLLBACK",
                "TRANSACTION",
                "PASS",
                "All mapping-rule SQL changes were rolled back.",
                {
                    "executed_before_failure": results,
                    "failed_index": len(results) + 1,
                    "failed_sql": failed_sql,
                },
                level="warning",
            )
            raise
        finally:
            cursor.close()
            connection.close()

    def _result(self, ok: bool, answer: str, generated: dict[str, Any], executions: list[dict[str, Any]]) -> Data:
        result = {"ok": ok, "component": self.name, "summary": generated["summary"], "sql_statements": generated["sql_statements"], "executions": executions, "answer_text": answer, "final": True}
        self.status = result
        return Data(data=result)

    def _schema(self) -> str:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", schema):
            raise ValueError("System Schema is required and must be an Oracle identifier.")
        return schema

    def _connect(self, oracledb: Any) -> Any:
        return oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret(getattr(self, "db_password", "")),
            dsn=oracledb.makedsn(
                str(getattr(self, "db_host", "") or "").strip(),
                int(getattr(self, "db_port", 1521) or 1521),
                service_name=str(getattr(self, "db_service_name", "") or "").strip(),
            ),
        )

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Data):
            return cls._json_value(value.data)
        if isinstance(value, dict):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_value(item) for item in value]
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return cls._json_value(dump())
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return cls._json_value(to_dict())
        return str(value)

    @staticmethod
    def _secret(value: Any) -> str:
        return str(value.get_secret_value() or "") if hasattr(value, "get_secret_value") else str(value or "")

    def _log(self, log_type: str, step: str, status: str, message: str, detail: Any, *, level: str = "info") -> None:
        event = [0, "WORKFLOW", f"04_MAPPING_RULE_{log_type}", level.upper(), step, status, 0, json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None]
        getattr(logging.getLogger("smartmigrate.workflow"), level)(message, extra={"workflow_log": event})
