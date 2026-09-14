from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04UpdateCommandTool(Component):
    display_name = "04 Update Command Tool"
    description = "Transactional allow-listed UPDATE tool for SmartMigrate management requests."
    name = "NewType04UpdateCommandTool"
    icon = "DatabaseZap"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info=(
                "Transactional UPDATE JSON. Example: "
                '{"operations":[{"work_type":"DB_MIGRATION","map_id":101,"field":"USER_EDITED","value":"N"},'
                '{"work_type":"DB_MIGRATION","map_id":101,"field":"MIG_SQL","value":null}]}'
            ),
        ),
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        BoolInput(name="require_transaction", display_name="Require Transaction", value=True, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    DB_MIGRATION_FIELDS = {
        "STATUS",
        "RETRY_COUNT",
        "PRIORITY",
        "USER_EDITED",
        "USE_YN",
        "MIG_SQL",
        "VERIFY_SQL",
    }
    SQL_FIELDS = {
        "STATUS_CONVERSION",
        "STATUS_TUNING",
        "RETRY_COUNT",
        "PRIORITY",
        "USER_EDITED",
        "TO_SQL",
        "BIND_SQL",
        "TEST_SQL",
        "TUNED_TO_SQL",
        "FORMATTED_SQL",
    }
    YN_FIELDS = {"USER_EDITED", "USE_YN"}
    INT_FIELDS = {"RETRY_COUNT", "PRIORITY"}

    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 Update Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            result = self._apply_updates(command)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_updateCommandTool", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _apply_updates(self, command: dict[str, Any]) -> dict[str, Any]:
        operations = command.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty list")
        normalized = [self._normalize_operation(item, index) for index, item in enumerate(operations)]
        transaction = self._as_bool(command.get("transaction", getattr(self, "require_transaction", True)))
        results: list[dict[str, Any]] = []

        with self._connect() as conn:
            cur = conn.cursor()
            try:
                for index, operation in enumerate(normalized):
                    cur.execute(operation["sql"], operation["params"])
                    if cur.rowcount != 1:
                        raise ValueError(
                            f"Operation {index + 1} did not update exactly one row: "
                            f"{operation['identity']}, rowcount={cur.rowcount}"
                        )
                    results.append(
                        {
                            "index": index,
                            "work_type": operation["work_type"],
                            "identity": operation["identity"],
                            "field": operation["field"],
                            "value": operation["public_value"],
                            "updated_rows": cur.rowcount,
                        }
                    )
                    if not transaction:
                        conn.commit()
                if transaction:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

        answer = self._answer(results, transaction)
        logging.getLogger("smartmigrate.workflow").info(
            answer,
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "UPDATE", "PASS", len(results)]},
        )
        return {
            "ok": True,
            "component": "04_updateCommandTool",
            "action": "apply_updates",
            "transaction": transaction,
            "updated_count": len(results),
            "updates": results,
            "answer_text": answer,
            "final": True,
        }

    def _normalize_operation(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f"Operation {index + 1} must be an object")
        work_type = self._normalize_work_type(raw.get("work_type") or raw.get("domain"))
        table, where_sql, identity_params, identity = self._target(raw, work_type)
        field = self._clean_identifier(raw.get("field") or raw.get("column") or raw.get("sql_column"))
        self._validate_field(work_type, field)
        if "value" not in raw and "correct_sql" not in raw:
            raise ValueError(f"Operation {index + 1} requires value. Use JSON null explicitly for DB NULL.")
        value = self._normalize_value(field, raw.get("value") if "value" in raw else raw.get("correct_sql"))
        param_name = f"value_{index}"
        return {
            "work_type": work_type,
            "identity": identity,
            "field": field,
            "public_value": None if value is None else value,
            "sql": f"UPDATE {self._qualify(table)} SET {field} = :{param_name} WHERE {where_sql}",
            "params": {**identity_params, param_name: value},
        }

    def _target(self, raw: dict[str, Any], work_type: str) -> tuple[str, str, dict[str, Any], str]:
        if work_type == "DB_MIGRATION":
            map_id = str(raw.get("map_id") or "").strip()
            if not map_id:
                raise ValueError("DB_MIGRATION update requires map_id")
            return "NEXT_MIG_INFO", "TO_CHAR(MAP_ID) = :map_id", {"map_id": map_id}, f"MAP_ID={map_id}"

        sql_id = str(raw.get("sql_id") or "").strip()
        space_nm = str(raw.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError(f"{work_type} update requires sql_id and space_nm")
        return (
            "NEXT_SQL_INFO",
            "UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))",
            {"sql_id": sql_id, "space_nm": space_nm},
            f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
        )

    def _validate_field(self, work_type: str, field: str) -> None:
        allowed = self.DB_MIGRATION_FIELDS if work_type == "DB_MIGRATION" else self.SQL_FIELDS
        if field not in allowed:
            raise ValueError(f"Field {field} is not allowed for {work_type}")
        if field == "USE_YN" and work_type != "DB_MIGRATION":
            raise ValueError("USE_YN can only be updated on DB_MIGRATION jobs")

    def _normalize_value(self, field: str, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"<null>", "__null__"}:
            return None
        if field in self.YN_FIELDS:
            if value is None:
                return None
            text = str(value).strip().upper()
            if text not in {"Y", "N"}:
                raise ValueError(f"{field} must be Y, N, or null")
            return text
        if field in self.INT_FIELDS:
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a number or null") from exc
        return value

    def _normalize_work_type(self, value: Any) -> str:
        text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "MIG": "DB_MIGRATION",
            "DB": "DB_MIGRATION",
            "DB_MIG": "DB_MIGRATION",
            "MIGRATION": "DB_MIGRATION",
            "CONVERSION": "SQL_CONVERSION",
            "SQL": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
            "FORMAT": "SQL_FORMATTING",
        }
        normalized = aliases.get(text, text)
        if normalized not in {"DB_MIGRATION", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            raise ValueError(f"Unsupported work_type: {value}")
        return normalized

    def _answer(self, results: list[dict[str, Any]], transaction: bool) -> str:
        mode = "transaction" if transaction else "individual commits"
        lines = [f"Update Command 완료: {len(results)}건 ({mode})."]
        for item in results:
            value = "NULL" if item.get("value") is None else repr(item.get("value"))
            lines.append(f"- {item['work_type']} {item['identity']}: {item['field']} = {value}")
        return "\n".join(lines)

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

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if not raw:
            raw = getattr(self, "payload_json", "")
        if isinstance(raw, Data):
            parsed = dict(raw.data or {})
            if "operations" not in parsed and "updates" in parsed:
                parsed["operations"] = parsed.get("updates")
            return parsed
        if isinstance(raw, dict):
            parsed = dict(raw)
            if "operations" not in parsed and "updates" in parsed:
                parsed["operations"] = parsed.get("updates")
            return parsed
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        if "operations" not in parsed and "updates" in parsed:
            parsed["operations"] = parsed.get("updates")
        return parsed

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required")
        return f"{self._clean_identifier(schema)}.{table}"

    def _clean_identifier(self, value: Any) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
