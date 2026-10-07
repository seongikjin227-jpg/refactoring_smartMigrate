from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

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
   Master key is MAP_ID; detail key is (MAP_ID, MAP_DTL).
3. Never modify execution/status/result columns such as STATUS, MIG_SQL,
   VERIFY_SQL, RETRY_COUNT, or USER_EDITED. USE_YN is a mapping field and
   may be changed only when requested.
4. For NEXT_MIG_INFO, an existing MAP_ID requires UPDATE and a missing MAP_ID
   requires INSERT. For NEXT_MIG_INFO_DTL, an existing (MAP_ID, MAP_DTL)
   requires UPDATE and a missing pair requires INSERT.
5. Never invent MAP_ID, tables, columns, or values absent from the
   request. If the request is ambiguous, return an empty sql_statements array
   and explain why in summary.
6. Use only explicit column lists and literal VALUES for INSERT; use literal
   SET values and exact PK equality predicates joined by AND for UPDATE.
   No expressions, functions, subqueries, aliases, or PK changes. Escape
   single quotes by doubling them. Qualify every table with system_schema.
   Master mapping columns: MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, CONDITION,
   USE_YN, PRIORITY, PRIOR_MAP_ID, TRUNC_YN. Detail columns: MAP_ID, FR_COL,
   TO_COL, MAP_DTL.
   Execution/status/result columns are never allowed on this mapping branch;
   use the dedicated Update Command Tool for those requests.
7. Insert new master rows before their details. Generate at most one statement
   per PK. Generate mapping DML; execution is controlled by the component setting.
"""


class NewType04MappingRuleUpdateSqlGenerate(Component):
    """Generate and transactionally apply mapping-rule DML from a Table snapshot."""

    display_name = "04 Mapping Rule Update SQL Generate"
    description = "Loads a PK-only mapping snapshot, then LLM-generates and applies validated mapping-rule DML."
    name = "NewType04MappingRuleUpdateSqlGenerate"
    icon = "FilePenLine"

    inputs = [
        MessageTextInput(name="user_request", display_name="User Request", required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"]),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        BoolInput(name="execute_updates", display_name="Execute Generated SQL", value=False, required=False),
        IntInput(name="max_snapshot_chars", display_name="Max Snapshot Characters", value=120000, required=False),
        IntInput(name="max_statements", display_name="Max SQL Statements", value=200, required=False),
    ]
    outputs = [Output(display_name="Result", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        generated = {"summary": "", "sql_statements": []}
        self._execution_results = []
        self._rollback_completed = False
        self._failed_statement_index = None
        self._sql_validated = False
        try:
            request = self._request_text()
            snapshot = self._load_mapping_key_snapshot()
            self._log("INPUT", "RECEIVE", "START", "Mapping-rule request and Table snapshot received", {
                "user_request": request, "snapshot": snapshot,
            })
            raw_response = self._generate(request, snapshot)
            generated = self._parse_generated(raw_response)
            statements = self._validate_statements(generated["sql_statements"], snapshot)
            generated["sql_statements"] = statements
            self._sql_validated = True
            self._log("GENERATE_SQL", "LLM", "PASS", generated["summary"], {
                "raw_llm_response": raw_response, "sql_statements": statements,
            })
            if not statements:
                return self._result(False, "No executable SQL was generated.", generated, [])
            if not bool(getattr(self, "execute_updates", False)):
                self._log("EXECUTE_SQL", "DRY_RUN", "PASS", "Generated SQL was not executed (execute_updates=false).", statements)
                return self._result(True, "Dry run completed; generated SQL was not executed.", generated, [])
            executions = self._execute(statements)
            self._log("COMPLETE", "TRANSACTION", "PASS", f"Committed {len(executions)} mapping-rule SQL statement(s).", executions)
            return self._result(True, "Mapping-rule update committed.", generated, executions)
        except Exception as exc:
            self._log("FAILED", "ERROR", "FAIL", str(exc), None, level="error")
            return self._result(False, "매핑 룰 처리가 실패했습니다.", generated,
                                self._execution_results, error=str(exc))

    def _request_text(self) -> str:
        value = getattr(self, "user_request", "")
        text = str(value.text if isinstance(value, Message) else value or "").strip()
        if not text:
            raise ValueError("user_request is empty. Connect the 04 Router Mapping Rule Update output.")
        return text

    def _load_mapping_key_snapshot(self) -> list[dict[str, Any]]:
        """SELECT existing mapping identifiers to decide INSERT versus UPDATE.

        Master key: MAP_ID. Detail key: (MAP_ID, MAP_DTL).
        No constraint/index metadata or file access is needed.
        """
        schema = self._schema()
        import oracledb

        connection = self._connect(oracledb)
        cursor = None
        sql = f"""
SELECT 'MASTER' AS ROW_KIND, M.MAP_ID, CAST(NULL AS NUMBER) AS MAP_DTL
  FROM {schema}.NEXT_MIG_INFO M
UNION ALL
SELECT 'DETAIL' AS ROW_KIND, D.MAP_ID, D.MAP_DTL
  FROM {schema}.NEXT_MIG_INFO_DTL D
ORDER BY MAP_ID, MAP_DTL NULLS FIRST
""".strip()
        self._log("LOAD_PK", "SELECT", "START", "Loading existing mapping identifiers.", sql)
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            names = [str(column[0]).lower() for column in cursor.description]
            snapshot = [dict(zip(names, row)) for row in cursor.fetchall()]
            self._log("LOAD_PK", "SELECT", "PASS", f"Loaded {len(snapshot)} mapping identifier row(s).", snapshot)
        except Exception as exc:
            self._log("LOAD_PK", "SELECT", "FAIL", f"Mapping identifier SELECT failed: {exc}", {"error": str(exc)}, level="error")
            raise
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
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
        system_prompt = SQL_GENERATION_PROMPT + (
            f"\nConfigured system_schema: {self._schema()}"

        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps({"user_request": request, "current_mapping_rule_table": snapshot}, ensure_ascii=False, default=str)),
        ]
        self._log("GENERATE_SQL", "PROMPT", "START", "Mapping-rule SQL generation prompt prepared.", [
            {"role": "system", "content": messages[0].content},
            {"role": "user", "content": messages[1].content},
        ])
        response = llm.invoke(messages)
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

    @staticmethod
    def _sql_tokens(sql: str) -> list[str]:
        # A deliberately small DML grammar. Quoted mapping text may contain
        # commas, WHERE, semicolons or comment markers without being SQL code.
        pattern = re.compile(r"\s+|'(?:[^']|'')*'|[A-Za-z][A-Za-z0-9_$#]*|[+-]?\d+(?:\.\d+)?|[(),.=;]")
        tokens = []
        position = 0
        while position < len(sql):
            match = pattern.match(sql, position)
            if not match:
                raise ValueError("Generated SQL contains unsupported syntax or comments.")
            token = match.group(0)
            position = match.end()
            if not token.isspace():
                tokens.append(token)
        if tokens and tokens[-1] == ";":
            tokens.pop()
        if ";" in tokens:
            raise ValueError("Multiple statements are not allowed.")
        return tokens

    @staticmethod
    def _literal(token: str) -> Any:
        if token.upper() == "NULL":
            return None
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1].replace("''", "'")
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token):
            from decimal import Decimal
            return Decimal(token)
        raise ValueError("Mapping DML values must be string/number/NULL literals.")

    def _validate_statements(self, statements: list[str], snapshot: list[dict[str, Any]] | None = None) -> list[str]:
        schema = self._schema()
        allowed = {
            "NEXT_MIG_INFO": {"MAP_ID", "MAP_TYPE", "FR_TABLE", "TO_TABLE", "CONDITION", "USE_YN", "PRIORITY", "PRIOR_MAP_ID", "TRUNC_YN"},
            "NEXT_MIG_INFO_DTL": {"MAP_ID", "MAP_DTL", "FR_COL", "TO_COL"},
        }
        detail_key = "MAP_DTL"
        existing = set()
        for row in snapshot or []:
            kind = str(row["row_kind"]).upper()
            existing.add(("NEXT_MIG_INFO" if kind == "MASTER" else "NEXT_MIG_INFO_DTL", row["map_id"], row.get(detail_key.lower()) if kind == "DETAIL" else None))
        accepted = []
        seen = set()
        for raw in statements:
            tokens = self._sql_tokens(raw.strip())
            position = 0

            def take(expected: str | None = None) -> str:
                nonlocal position
                if position >= len(tokens):
                    raise ValueError("Generated SQL is incomplete.")
                token = tokens[position]
                position += 1
                if expected is not None and token.upper() != expected:
                    raise ValueError(f"Expected {expected} in mapping DML, received {token}.")
                return token

            operation = take().upper()
            if operation not in {"UPDATE", "INSERT"}:
                raise ValueError("Only mapping INSERT/UPDATE is allowed.")
            if operation == "INSERT":
                take("INTO")
            table = take().upper()
            if position < len(tokens) and tokens[position] == ".":
                take(".")
                if table != schema:
                    raise ValueError("Generated SQL targets another schema.")
                table = take().upper()
            if table not in allowed:
                raise ValueError("Generated SQL targets a non-mapping table.")
            keys = {"MAP_ID", detail_key} if table.endswith("_DTL") else {"MAP_ID"}
            values = {}
            predicates = {}
            if operation == "UPDATE":
                take("SET")
                while True:
                    column = take().upper()
                    take("=")
                    if column not in allowed[table] - keys or column in values:
                        raise ValueError(f"Unsupported, duplicate or PK SET column: {column}.")
                    values[column] = self._literal(take())
                    if position < len(tokens) and tokens[position] == ",":
                        take(",")
                    else:
                        break
                take("WHERE")
                while True:
                    column = take().upper()
                    take("=")
                    if column not in keys or column in predicates:
                        raise ValueError("WHERE must identify each PK exactly once.")
                    predicates[column] = self._literal(take())
                    if position < len(tokens) and tokens[position].upper() == "AND":
                        take("AND")
                    else:
                        break
                if set(predicates) != keys:
                    raise ValueError("UPDATE requires exact equality for the complete PK.")
            else:
                take("(")
                columns = []
                while True:
                    column = take().upper()
                    if column not in allowed[table] or column in columns:
                        raise ValueError(f"Unsupported or duplicate INSERT column: {column}.")
                    columns.append(column)
                    if position < len(tokens) and tokens[position] == ",":
                        take(",")
                    else:
                        break
                take(")")
                take("VALUES")
                take("(")
                for index, column in enumerate(columns):
                    if index:
                        take(",")
                    values[column] = self._literal(take())
                take(")")
                required = keys | ({"FR_TABLE", "TO_TABLE"} if table == "NEXT_MIG_INFO" else {"FR_COL"})
                if not required.issubset(values) or any(values[key] is None or values[key] == "" for key in required):
                    raise ValueError("INSERT is missing required mapping values.")
                predicates = {key: values[key] for key in keys}
            if position != len(tokens):
                raise ValueError("Trailing SQL, expressions, subqueries or broad WHERE predicates are not allowed.")
            map_id = predicates["MAP_ID"]
            from decimal import Decimal
            if not isinstance(map_id, Decimal) or map_id != map_id.to_integral_value():
                raise ValueError("MAP_ID must be an integer literal.")
            detail_value = predicates.get(detail_key)
            if "MAP_DTL" in keys and (not isinstance(detail_value, Decimal) or detail_value != detail_value.to_integral_value()):
                raise ValueError("MAP_DTL must be an integer literal.")
            key = (table, map_id, detail_value)
            if key in seen:
                raise ValueError("Multiple statements for the same mapping PK are not allowed.")
            if snapshot is not None and ((operation == "UPDATE") != (key in existing)):
                raise ValueError("INSERT/UPDATE does not match the current PK snapshot.")
            if snapshot is not None and table.endswith("_DTL") and ("NEXT_MIG_INFO", map_id, None) not in existing:
                raise ValueError("Detail mapping requires an existing or previously inserted master.")
            seen.add(key)
            existing.add(key)
            # Always resolve an unqualified target against the selected system
            # schema, rather than the connection user's default schema.
            prefix = f"{operation} " + ("INTO " if operation == "INSERT" else "")
            # Locate the target lexically; token casing is preserved in values.
            target_end = 2 if operation == "UPDATE" else 3
            target_start = 1 if operation == "UPDATE" else 2
            if tokens[target_start + 1:target_start + 2] == ["."]:
                target_end += 2
            sql = prefix + schema + "." + table + " " + " ".join(tokens[target_end:])
            accepted.append(sql)
        return accepted

    def _execute(self, statements: list[str]) -> list[dict[str, Any]]:
        import oracledb

        results: list[dict[str, Any]] = []
        self._execution_results = results
        self._rollback_completed = False
        self._failed_statement_index = None
        connection = self._connect(oracledb)
        cursor = None
        try:
            cursor = connection.cursor()
            for index, sql in enumerate(statements, start=1):
                self._failed_statement_index = index
                self._log("EXECUTE_SQL", "STATEMENT", "START", f"Executing statement {index}/{len(statements)}.", sql)
                cursor.execute(sql)
                if cursor.rowcount != 1:
                    raise ValueError(f"Mapping statement {index} affected {cursor.rowcount} rows; expected exactly one.")
                outcome = {"index": index, "sql": sql, "rowcount": cursor.rowcount}
                results.append(outcome)
                self._log("EXECUTE_SQL", "STATEMENT", "PASS", f"Statement {index}/{len(statements)} executed; rowcount={cursor.rowcount}.", outcome)
            self._failed_statement_index = None
            connection.commit()
            return results
        except Exception as exc:
            connection.rollback()
            self._rollback_completed = True
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
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def _result(self, ok: bool, answer: str, generated: dict[str, Any], executions: list[dict[str, Any]], *, error: str | None = None) -> Message:
        committed = ok and bool(executions)
        dry_run = ok and not executions
        result = {"ok": ok, "component": self.name, "summary": generated["summary"],
                  "sql_statements": generated["sql_statements"], "executions": executions,
                  "database_executed": committed, "dry_run": dry_run,
                  "rolled_back": self._rollback_completed, "error": error,
                  "detail_key_column": "MAP_DTL", "final": True}
        if committed:
            lines = [f"매핑 룰 적용 완료: {len(executions)}개 SQL을 실행하고 commit했습니다."]
        elif dry_run:
            lines = ["매핑 룰 SQL 생성 완료: 실행 옵션이 꺼져 있어 매핑 DML은 실행하지 않았습니다."]
        elif self._rollback_completed:
            lines = ["매핑 룰 적용 실패: 이번 transaction의 변경을 모두 rollback했습니다."]
        elif executions or self._failed_statement_index is not None:
            lines = ["매핑 룰 적용 실패: transaction 결과를 확인하지 못했습니다."]
        else:
            lines = [answer]
        if generated["summary"]:
            lines += ["", "요약: " + generated["summary"]]
        if error:
            lines += ["", "오류: " + error]
        lines += ["", "생성 SQL 및 실행 결과"]
        if not generated["sql_statements"]:
            lines.append("생성된 SQL이 없습니다.")
        completed = {item["index"]: item for item in executions}
        for index, sql in enumerate(generated["sql_statements"], start=1):
            if index in completed:
                outcome = f"rowcount={completed[index]['rowcount']} / " + ("commit 완료" if committed else "rollback 완료" if self._rollback_completed else "transaction 결과 미확인")
            elif index == self._failed_statement_index:
                outcome = "실행 실패 / " + ("rollback 완료" if self._rollback_completed else "transaction 결과 미확인")
            elif dry_run:
                outcome = "미실행 (execute_updates=false)"
            else:
                outcome = "미실행"
            if not self._sql_validated:
                outcome += " / SQL 검증 미완료"
            lines += ["", f"{index}. {outcome}", "```sql", sql, "```"]
        text = "\n".join(lines)
        result["answer_text"] = text
        self.status = result
        return Message(text=text)

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

    @staticmethod
    def _secret(value: Any) -> str:
        return str(value.get_secret_value() or "") if hasattr(value, "get_secret_value") else str(value or "")

    def _log(self, log_type: str, step: str, status: str, message: str, detail: Any, *, level: str = "info") -> None:
        event = [0, "WORKFLOW", "04_MAPPING_RULE", level.upper(), f"{log_type}:{step}", status, 0, json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None]
        getattr(logging.getLogger("smartmigrate.workflow"), level)(message, extra={"workflow_log": event})
