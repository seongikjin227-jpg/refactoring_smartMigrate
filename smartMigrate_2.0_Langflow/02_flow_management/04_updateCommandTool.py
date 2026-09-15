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


class NewType04UpdateCommandTool(Component):
    display_name = "04 Update Command Tool"
    description = "Runs fixed SQL update actions for SmartMigrate management requests."
    name = "NewType04UpdateCommandTool"
    icon = "DatabaseZap"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info=(
                "Fixed UPDATE action JSON. Example: "
                '{"actions":[{"action":"set_migration_user_edited","map_id":101,"user_edited":"N"},'
                '{"action":"clear_migration_mig_sql","map_id":101}]}'
            ),
        ),
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 Update Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            result = self._apply_actions(command)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_updateCommandTool", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _apply_actions(self, command: dict[str, Any]) -> dict[str, Any]:
        actions = command.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must be a non-empty list")

        statements = [self._build_statement(item, index) for index, item in enumerate(actions)]
        results: list[dict[str, Any]] = []

        with self._connect() as conn:
            cur = conn.cursor()
            try:
                for index, statement in enumerate(statements):
                    cur.execute(statement["sql"], statement["params"])
                    skipped = bool(statement.get("skip_when_not_matched")) and cur.rowcount == 0
                    if cur.rowcount != 1 and not skipped:
                        raise ValueError(
                            f"Action {index + 1} did not update exactly one row: "
                            f"{statement['identity']}, rowcount={cur.rowcount}"
                        )
                    results.append(
                        {
                            "index": index,
                            "action": statement["action"],
                            "identity": statement["identity"],
                            "updated_rows": cur.rowcount,
                            "summary": "Skipped: status is no longer FAIL-*" if skipped else statement["summary"],
                            "skipped": skipped,
                        }
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        answer = self._answer(results)
        logging.getLogger("smartmigrate.workflow").info(
            answer,
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "UPDATE", "PASS", len(results)]},
        )
        return {
            "ok": True,
            "component": "04_updateCommandTool",
            "action": "apply_actions",
            "transaction": True,
            "updated_count": sum(int(item["updated_rows"]) for item in results),
            "actions": results,
            "answer_text": answer,
            "final": True,
        }

    def _build_statement(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f"actions[{index}] must be an object")
        action = str(raw.get("action") or "").strip().lower()
        if not action:
            raise ValueError(f"actions[{index}].action is required")

        if action == "reset_migration_status":
            map_id = self._map_id(raw)
            retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
            set_sql = "STATUS = NULL, RETRY_COUNT = :retry_count"
            params: dict[str, Any] = {"map_id": map_id, "retry_count": retry_count}
            if "priority" in raw and raw.get("priority") not in (None, ""):
                set_sql += ", PRIORITY = :priority"
                params["priority"] = self._int_value(raw.get("priority"), "priority")
            return self._migration_statement(action, map_id, set_sql, params, "STATUS=NULL, RETRY_COUNT reset")

        if action == "set_migration_user_edited":
            map_id = self._map_id(raw)
            value = self._yn_value(raw.get("user_edited"), "user_edited")
            return self._migration_statement(action, map_id, "USER_EDITED = :user_edited", {"map_id": map_id, "user_edited": value}, f"USER_EDITED={value}")

        if action == "set_migration_use_yn":
            map_id = self._map_id(raw)
            value = self._yn_value(raw.get("use_yn"), "use_yn")
            return self._migration_statement(action, map_id, "USE_YN = :use_yn", {"map_id": map_id, "use_yn": value}, f"USE_YN={value}")

        if action == "set_migration_priority":
            map_id = self._map_id(raw)
            value = self._int_value(raw.get("priority"), "priority")
            return self._migration_statement(action, map_id, "PRIORITY = :priority", {"map_id": map_id, "priority": value}, f"PRIORITY={value}")

        if action == "clear_migration_mig_sql":
            map_id = self._map_id(raw)
            return self._migration_statement(action, map_id, "MIG_SQL = NULL", {"map_id": map_id}, "MIG_SQL=NULL")

        if action == "clear_migration_verify_sql":
            map_id = self._map_id(raw)
            return self._migration_statement(action, map_id, "VERIFY_SQL = NULL", {"map_id": map_id}, "VERIFY_SQL=NULL")

        if action == "save_migration_mig_sql":
            map_id = self._map_id(raw)
            sql_text = self._text_value(raw, "sql_text", "mig_sql")
            return self._migration_statement(action, map_id, "MIG_SQL = :sql_text", {"map_id": map_id, "sql_text": sql_text}, "MIG_SQL saved")

        if action == "save_migration_verify_sql":
            map_id = self._map_id(raw)
            sql_text = self._text_value(raw, "sql_text", "verify_sql")
            return self._migration_statement(action, map_id, "VERIFY_SQL = :sql_text", {"map_id": map_id, "sql_text": sql_text}, "VERIFY_SQL saved")

        if action == "reset_sql_conversion_status":
            return self._sql_reset_statement(raw, action, "STATUS_CONVERSION")

        if action == "reset_sql_tuning_status":
            return self._sql_reset_statement(raw, action, "STATUS_TUNING")

        if action == "retry_failed_sql_conversion":
            return self._sql_retry_failed_statement(raw, action, "STATUS_CONVERSION")

        if action == "retry_failed_sql_tuning":
            return self._sql_retry_failed_statement(raw, action, "STATUS_TUNING")

        if action == "reset_sql_formatting_result":
            sql_id, space_nm = self._sql_identity(raw)
            retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
            return self._sql_statement(
                action,
                sql_id,
                space_nm,
                "FORMATTED_SQL = NULL, RETRY_COUNT = :retry_count",
                {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count},
                "FORMATTED_SQL=NULL, RETRY_COUNT reset",
            )

        if action == "set_sql_user_edited":
            sql_id, space_nm = self._sql_identity(raw)
            value = self._yn_value(raw.get("user_edited"), "user_edited")
            return self._sql_statement(action, sql_id, space_nm, "USER_EDITED = :user_edited", {"sql_id": sql_id, "space_nm": space_nm, "user_edited": value}, f"USER_EDITED={value}")

        if action == "set_sql_priority":
            sql_id, space_nm = self._sql_identity(raw)
            value = self._int_value(raw.get("priority"), "priority")
            return self._sql_statement(action, sql_id, space_nm, "PRIORITY = :priority", {"sql_id": sql_id, "space_nm": space_nm, "priority": value}, f"PRIORITY={value}")

        sql_text_actions = {
            "clear_sql_to_sql": ("TO_SQL", None),
            "clear_sql_bind_sql": ("BIND_SQL", None),
            "clear_sql_test_sql": ("TEST_SQL", None),
            "clear_sql_tuned_to_sql": ("TUNED_TO_SQL", None),
            "clear_sql_formatted_sql": ("FORMATTED_SQL", None),
            "save_sql_to_sql": ("TO_SQL", "to_sql"),
            "save_sql_bind_sql": ("BIND_SQL", "bind_sql"),
            "save_sql_test_sql": ("TEST_SQL", "test_sql"),
            "save_sql_tuned_to_sql": ("TUNED_TO_SQL", "tuned_to_sql"),
            "save_sql_formatted_sql": ("FORMATTED_SQL", "formatted_sql"),
        }
        if action in sql_text_actions:
            column, text_key = sql_text_actions[action]
            sql_id, space_nm = self._sql_identity(raw)
            if text_key is None:
                return self._sql_statement(action, sql_id, space_nm, f"{column} = NULL", {"sql_id": sql_id, "space_nm": space_nm}, f"{column}=NULL")
            sql_text = self._text_value(raw, "sql_text", text_key)
            return self._sql_statement(action, sql_id, space_nm, f"{column} = :sql_text", {"sql_id": sql_id, "space_nm": space_nm, "sql_text": sql_text}, f"{column} saved")

        raise ValueError(f"Unsupported update action: {action}")

    def _sql_reset_statement(self, raw: dict[str, Any], action: str, status_column: str) -> dict[str, Any]:
        sql_id, space_nm = self._sql_identity(raw)
        retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
        set_sql = f"{status_column} = NULL, RETRY_COUNT = :retry_count"
        params: dict[str, Any] = {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count}
        if "priority" in raw and raw.get("priority") not in (None, ""):
            set_sql += ", PRIORITY = :priority"
            params["priority"] = self._int_value(raw.get("priority"), "priority")
        return self._sql_statement(action, sql_id, space_nm, set_sql, params, f"{status_column}=NULL, RETRY_COUNT reset")

    def _sql_retry_failed_statement(self, raw: dict[str, Any], action: str, status_column: str) -> dict[str, Any]:
        """Reset a SQL status only when its current value is FAIL-*.

        The predicate is evaluated at write time, rather than trusting a preceding
        vector search result, so a concurrently completed PASS row is protected.
        """
        sql_id, space_nm = self._sql_identity(raw)
        retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
        return {
            "action": action,
            "identity": f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
            "summary": f"{status_column}=NULL, RETRY_COUNT reset (only if current status is FAIL-*)",
            "sql": (
                f"UPDATE {self._qualify('NEXT_SQL_INFO')} "
                f"SET {status_column} = NULL, RETRY_COUNT = :retry_count "
                "WHERE UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) "
                "AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm)) "
                f"AND UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"
            ),
            "params": {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count},
            "skip_when_not_matched": True,
        }

    def _migration_statement(self, action: str, map_id: str, set_sql: str, params: dict[str, Any], summary: str) -> dict[str, Any]:
        params.setdefault("map_id", map_id)
        return {
            "action": action,
            "identity": f"MAP_ID={map_id}",
            "summary": summary,
            "sql": f"UPDATE {self._qualify('NEXT_MIG_INFO')} SET {set_sql} WHERE TO_CHAR(MAP_ID) = :map_id",
            "params": params,
        }

    def _sql_statement(self, action: str, sql_id: str, space_nm: str, set_sql: str, params: dict[str, Any], summary: str) -> dict[str, Any]:
        params.setdefault("sql_id", sql_id)
        params.setdefault("space_nm", space_nm)
        return {
            "action": action,
            "identity": f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
            "summary": summary,
            "sql": (
                f"UPDATE {self._qualify('NEXT_SQL_INFO')} SET {set_sql} "
                "WHERE UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) "
                "AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))"
            ),
            "params": params,
        }

    def _map_id(self, raw: dict[str, Any]) -> str:
        value = str(raw.get("map_id") or "").strip()
        if not value:
            raise ValueError("map_id is required")
        return value

    def _sql_identity(self, raw: dict[str, Any]) -> tuple[str, str]:
        sql_id = str(raw.get("sql_id") or "").strip()
        space_nm = str(raw.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError("sql_id and space_nm are required")
        return sql_id, space_nm

    def _yn_value(self, value: Any, name: str) -> str:
        text = str(value or "").strip().upper()
        if text not in {"Y", "N"}:
            raise ValueError(f"{name} must be Y or N")
        return text

    def _int_value(self, value: Any, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc

    def _text_value(self, raw: dict[str, Any], *names: str) -> str:
        for name in names:
            if name in raw and raw.get(name) is not None:
                return str(raw.get(name))
        raise ValueError(f"One of {', '.join(names)} is required")

    def _answer(self, results: list[dict[str, Any]]) -> str:
        lines = [f"Update Command completed: {len(results)} update(s) in one transaction."]
        for item in results:
            lines.append(f"- {item['identity']}: {item['summary']} ({item['action']})")
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
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
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

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
