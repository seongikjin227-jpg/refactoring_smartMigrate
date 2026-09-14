from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04RagCommandTool(Component):
    display_name = "04 RAG Command Tool"
    description = "Tool-mode RAG Guide query/add/update/disable command component."
    name = "NewType04RagCommandTool"
    icon = "BookMarked"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info='Example: {"action":"query","category":"SQL_CONVERSION","keyword":"CUSTOMER","limit":10}',
        ),
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        IntInput(name="default_limit", display_name="Default Limit", value=10, required=False),
        IntInput(name="max_text_chars", display_name="Max Text Chars", value=4000, required=False),
    ]
    outputs = [Output(display_name="Result", name="result", method="run_command")]

    CATEGORIES = {"SQL_CONVERSION", "SQL_TUNING"}
    RULE_TYPES = {"GENERAL", "SEARCH"}

    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 RAG Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_RAG_COMMAND_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            action = str(command.get("action") or "").strip().lower() or "query"
            with self._connect() as conn:
                if action in {"query", "list", "get"}:
                    result = self._query(conn, command)
                elif action in {"add", "insert", "create"}:
                    result = self._insert(conn, command)
                elif action in {"update", "modify"}:
                    result = self._update(conn, command)
                elif action in {"disable", "delete", "soft_delete"}:
                    result = self._disable(conn, self._required_rag_id(command))
                else:
                    raise ValueError(f"Unsupported RAG action: {action}")
                conn.commit()
            result = {**result, "component": "04_ragCommandTool", "answer_text": self._answer(result), "final": True}
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_ragCommandTool", "error": str(exc), "answer_text": f"RAG Command failed: {exc}"}
            self.status = result
            return Data(data=result)

    def _query(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rag_id = str(command.get("rag_id") or "").strip()
        category = self._optional_category(command.get("category"))
        rule_type = self._optional_rule_type(command.get("rule_type"))
        use_yn = self._optional_use_yn(command.get("use_yn"))
        keyword = str(command.get("keyword") or "").strip()
        limit = self._limit(command.get("limit"))
        full_text = self._as_bool(command.get("full_text"))

        conditions = ["1=1"]
        params: dict[str, Any] = {"limit": limit}
        if rag_id:
            conditions.append("RAG_ID = :rag_id")
            params["rag_id"] = int(rag_id)
        if category:
            conditions.append("UPPER(TRIM(CATEGORY)) = :category")
            params["category"] = category
        if rule_type:
            conditions.append("UPPER(TRIM(RULE_TYPE)) = :rule_type")
            params["rule_type"] = rule_type
        if use_yn:
            conditions.append("UPPER(TRIM(USE_YN)) = :use_yn")
            params["use_yn"] = use_yn
        if keyword:
            conditions.append(
                "("
                "UPPER(TO_CHAR(SOURCE_TABLES)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(GUIDANCE_TEXT, 4000, 1)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(SOURCE_SQL, 4000, 1)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(TARGET_SQL, 4000, 1)) LIKE UPPER(:keyword)"
                ")"
            )
            params["keyword"] = f"%{keyword}%"

        text_length = min(self._positive_int(getattr(self, "max_text_chars", None), 4000), 4000)
        guidance_expr = "GUIDANCE_TEXT" if full_text else f"DBMS_LOB.SUBSTR(GUIDANCE_TEXT, {text_length}, 1)"
        source_expr = "SOURCE_SQL" if full_text else f"DBMS_LOB.SUBSTR(SOURCE_SQL, {text_length}, 1)"
        target_expr = "TARGET_SQL" if full_text else f"DBMS_LOB.SUBSTR(TARGET_SQL, {text_length}, 1)"
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT *
              FROM (
                    SELECT RAG_ID, CATEGORY, RULE_TYPE, SOURCE_TABLES, USE_YN,
                           {guidance_expr} AS GUIDANCE_TEXT,
                           {source_expr} AS SOURCE_SQL,
                           {target_expr} AS TARGET_SQL,
                           NVL(HIT_CNT, 0) AS HIT_CNT,
                           TO_CHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT,
                           TO_CHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS UPDATED_AT
                      FROM {self._qualify("NEXT_MIG_RAG_INFO")}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY UPDATED_AT DESC NULLS LAST, RAG_ID DESC
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
        )
        names = [str(item[0]).lower() for item in cur.description]
        rows = [{names[index]: self._json_value(value) for index, value in enumerate(row)} for row in cur.fetchall()]
        return {"ok": True, "action": "query", "row_count": len(rows), "data": {"rules": rows}, "needs_vector_sync": False}

    def _insert(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rule = self._normalized_rule(command, require_content=True, partial=False)
        cur = conn.cursor()
        cur.setinputsizes(guidance_text=self._clob_type(), source_sql=self._clob_type(), target_sql=self._clob_type())
        rag_id_var = cur.var(self._number_type())
        cur.execute(
            f"""
            INSERT INTO {self._qualify("NEXT_MIG_RAG_INFO")}
                (CATEGORY, RULE_TYPE, SOURCE_TABLES, USE_YN, GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL)
            VALUES
                (:category, :rule_type, :source_tables, :use_yn, :guidance_text, :source_sql, :target_sql)
            RETURNING RAG_ID INTO :rag_id
            """,
            {**rule, "rag_id": rag_id_var},
        )
        return {"ok": True, "action": "add", "rag_id": int(rag_id_var.getvalue()[0]), "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _update(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rag_id = self._required_rag_id(command)
        rule = self._normalized_rule(command, require_content=False, partial=True)
        assignments = []
        params: dict[str, Any] = {"rag_id": rag_id}
        for key, column in (
            ("category", "CATEGORY"),
            ("rule_type", "RULE_TYPE"),
            ("source_tables", "SOURCE_TABLES"),
            ("use_yn", "USE_YN"),
            ("guidance_text", "GUIDANCE_TEXT"),
            ("source_sql", "SOURCE_SQL"),
            ("target_sql", "TARGET_SQL"),
        ):
            if key in rule:
                assignments.append(f"{column} = :{key}")
                params[key] = rule[key]
        if not assignments:
            raise ValueError("No RAG fields to update")
        assignments.append("UPDATED_AT = SYSTIMESTAMP")
        cur = conn.cursor()
        cur.setinputsizes(**{key: self._clob_type() for key in ("guidance_text", "source_sql", "target_sql") if key in params})
        cur.execute(f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET {', '.join(assignments)} WHERE RAG_ID = :rag_id", params)
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row not found. rowcount={cur.rowcount}")
        return {"ok": True, "action": "update", "rag_id": rag_id, "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _disable(self, conn: Any, rag_id: int) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET USE_YN = 'N', UPDATED_AT = SYSTIMESTAMP WHERE RAG_ID = :rag_id", {"rag_id": rag_id})
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row not found. rowcount={cur.rowcount}")
        return {"ok": True, "action": "disable", "rag_id": rag_id, "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _answer(self, result: dict[str, Any]) -> str:
        action = result.get("action")
        if action == "query":
            return f"RAG Guide query completed: {result.get('row_count', 0)} row(s)."
        rag_id = result.get("rag_id")
        sync_sentence = "RAG Guide와 Correct SQL을 VectorDB에 동기화해줘"
        if rag_id:
            sync_sentence = f"RAG_ID {rag_id} 변경분을 포함해서 RAG Guide와 Correct SQL을 VectorDB에 동기화해줘"
        return (
            f"RAG Guide {action} completed: RAG_ID={rag_id}, updated_rows={result.get('updated_rows', 0)}.\n"
            f"VectorDB 반영은 자동으로 실행하지 않았습니다. 반영하려면 \"{sync_sentence}\"라고 요청하세요."
        )

    def _normalized_rule(self, command: dict[str, Any], *, require_content: bool, partial: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not partial or "category" in command:
            result["category"] = self._required_category(command.get("category"))
        if not partial or "rule_type" in command:
            result["rule_type"] = self._required_rule_type(command.get("rule_type") or "SEARCH")
        if not partial or "source_tables" in command:
            result["source_tables"] = self._source_tables(command.get("source_tables"))
        if not partial or "use_yn" in command:
            result["use_yn"] = self._required_use_yn(command.get("use_yn") or "Y")
        for key in ("guidance_text", "source_sql", "target_sql"):
            if not partial or key in command:
                result[key] = str(command.get(key) or "").strip()
        if require_content and not any(str(result.get(key) or "").strip() for key in ("guidance_text", "source_sql", "target_sql")):
            raise ValueError("At least one of guidance_text, source_sql, target_sql is required")
        self._validate_rule_shape(result, require_complete=require_content and not partial)
        if result.get("category") == "SQL_TUNING":
            result["source_tables"] = ""
        return result

    def _validate_rule_shape(self, rule: dict[str, Any], *, require_complete: bool) -> None:
        category = str(rule.get("category") or "").strip().upper()
        rule_type = str(rule.get("rule_type") or "").strip().upper()
        source_tables = str(rule.get("source_tables") or "").strip()
        guidance_text = str(rule.get("guidance_text") or "").strip()
        source_sql = str(rule.get("source_sql") or "").strip()
        target_sql = str(rule.get("target_sql") or "").strip()
        if category == "SQL_CONVERSION" and require_complete and not source_tables:
            raise ValueError("SQL_CONVERSION RAG requires source_tables")
        if category == "SQL_TUNING" and source_tables:
            raise ValueError("SQL_TUNING RAG does not use source_tables")
        if rule_type == "SEARCH" and require_complete and (not source_sql or not target_sql):
            raise ValueError("SEARCH RAG requires both source_sql and target_sql")
        if bool(source_sql) != bool(target_sql):
            raise ValueError("source_sql and target_sql must be both present or both empty")
        if category == "SQL_TUNING" and require_complete and not guidance_text:
            raise ValueError("SQL_TUNING RAG requires guidance_text")
        if rule_type == "GENERAL" and require_complete and not guidance_text:
            raise ValueError("GENERAL RAG requires guidance_text")

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if not raw:
            raw = getattr(self, "payload_json", "")
        if isinstance(raw, Data):
            parsed = dict(raw.data or {})
        elif isinstance(raw, dict):
            parsed = dict(raw)
        else:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
            parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        if "rag" in parsed and isinstance(parsed["rag"], dict):
            merged = {**parsed["rag"], **{key: value for key, value in parsed.items() if key != "rag"}}
            return merged
        return parsed

    @contextmanager
    def _connect(self):
        import oracledb

        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret_to_str(getattr(self, "db_password", None)),
            dsn=oracledb.makedsn(
                str(getattr(self, "db_host", "") or "").strip(),
                int(getattr(self, "db_port", None) or 1521),
                service_name=str(getattr(self, "db_service_name", "") or "").strip(),
            ),
        )
        try:
            yield conn
        finally:
            conn.close()

    def _required_rag_id(self, command: dict[str, Any]) -> int:
        try:
            rag_id = int(str(command.get("rag_id") or "").strip())
        except (TypeError, ValueError):
            rag_id = 0
        if rag_id <= 0:
            raise ValueError("rag_id is required")
        return rag_id

    def _required_category(self, value: Any) -> str:
        category = self._optional_category(value)
        if not category:
            raise ValueError("category must be SQL_CONVERSION or SQL_TUNING")
        return category

    def _optional_category(self, value: Any) -> str:
        category = str(value or "").strip().upper()
        return category if category in self.CATEGORIES else ""

    def _required_rule_type(self, value: Any) -> str:
        rule_type = self._optional_rule_type(value)
        if not rule_type:
            raise ValueError("rule_type must be GENERAL or SEARCH")
        return rule_type

    def _optional_rule_type(self, value: Any) -> str:
        rule_type = str(value or "").strip().upper()
        return rule_type if rule_type in self.RULE_TYPES else ""

    def _required_use_yn(self, value: Any) -> str:
        use_yn = self._optional_use_yn(value)
        if not use_yn:
            raise ValueError("use_yn must be Y or N")
        return use_yn

    def _optional_use_yn(self, value: Any) -> str:
        use_yn = str(value or "").strip().upper()
        return use_yn if use_yn in {"Y", "N"} else ""

    def _source_tables(self, value: Any) -> str:
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        return str(value or "").strip().upper()

    def _limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "default_limit", None), 10)
        return max(1, min(self._positive_int(value, default), 100))

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value or 0)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    def _qualify(self, table: str) -> str:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required")
        return f"{self._clean_identifier(schema)}.{self._clean_identifier(table)}"

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _clob_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_CLOB", getattr(oracledb, "CLOB", None))

    def _number_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_NUMBER", int)
