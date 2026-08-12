from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


DEFAULT_DB_CONFIG = {
    "db_host": "10.0.0.1",
    "db_port": 1521,
    "db_service_name": "ORCL",
    "db_username": "SMARTMIGRATE",
    "db_password": "password",
    "system_schema": "SFAADM",
}


class ChatCommandTool(Component):
    display_name = "SmartMigrate Chat Command Tool"
    description = "Action-based DB tool for Chat Agent. It queues commands, stops supervisor, and reads summaries."
    name = "SmartMigrateChatCommandTool"
    icon = "Wrench"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info=(
                "JSON action. Examples: "
                '{"action":"enqueue_migration","map_id":101}, '
                '{"action":"enqueue_sql_conversion","sql_id":"SEL_001","space_nm":"userMapper"}, '
                '{"action":"request_stop"}, {"action":"status"}, {"action":"failure_summary","agent":"all"}'
            ),
        ),
        StrInput(name="db_host", display_name="DB Host", value=DEFAULT_DB_CONFIG["db_host"], required=False),
        IntInput(name="db_port", display_name="DB Port", value=DEFAULT_DB_CONFIG["db_port"], required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", value=DEFAULT_DB_CONFIG["db_service_name"], required=False),
        StrInput(name="db_username", display_name="DB Username", value=DEFAULT_DB_CONFIG["db_username"], required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", value=DEFAULT_DB_CONFIG["system_schema"], required=False),
    ]

    outputs = [
        Output(display_name="Result", name="result", method="run_command"),
    ]

    def run_command(self) -> Data:
        try:
            command = self._parse_command()
            result = self._dispatch(command)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "").strip().lower()
        if action in {"enqueue_migration", "run_data_migration", "migration"}:
            map_id = int(command["map_id"])
            command_id = self._enqueue_command(
                command_text=f"run_data_migration map_id={map_id}",
                command_json={"action": "run_data_migration", "map_id": map_id},
            )
            return {
                "ok": True,
                "action": "enqueue_migration",
                "command_id": command_id,
                "answer_text": f"map_id={map_id} 마이그레이션 실행 요청을 등록했습니다.",
            }

        if action in {"enqueue_sql_conversion", "run_sql_conversion", "sql_conversion", "conversion"}:
            sql_id = str(command.get("sql_id") or "").strip()
            if not sql_id:
                raise ValueError("sql_id is required")
            space_nm = str(command.get("space_nm") or command.get("namespace") or "").strip()
            payload = {"action": "run_sql_conversion", "sql_id": sql_id}
            if space_nm:
                payload["space_nm"] = space_nm
            command_text = f"run_sql_conversion sql_id={sql_id}" + (f" space_nm={space_nm}" if space_nm else "")
            command_id = self._enqueue_command(command_text=command_text, command_json=payload)
            return {
                "ok": True,
                "action": "enqueue_sql_conversion",
                "command_id": command_id,
                "answer_text": f"sql_id={sql_id} SQL 변환 실행 요청을 등록했습니다.",
            }

        if action in {"request_stop", "stop", "stop_supervisor"}:
            self._request_stop()
            return {
                "ok": True,
                "action": "request_stop",
                "answer_text": "Supervisor 중지 요청을 DB에 반영했습니다.",
            }

        if action in {"status", "summary", "dashboard_summary"}:
            summary = self._status_summary()
            return {
                "ok": True,
                "action": "status",
                "answer_text": self._format_status(summary),
                "data": summary,
            }

        if action in {"failure_summary", "fail_summary", "analyze_failures"}:
            agent = str(command.get("agent") or "all").strip().lower()
            summary = self._failure_summary(agent=agent, limit=int(command.get("limit") or 200))
            return {
                "ok": True,
                "action": "failure_summary",
                "answer_text": self._format_failure_summary(summary),
                "data": summary,
            }

        if action in {"help", "schema"}:
            return {"ok": True, "action": "help", "answer_text": self._help_text()}

        raise ValueError(f"Unsupported action: {action}")

    def _parse_command(self) -> dict[str, Any]:
        raw = str(getattr(self, "command_json", "") or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
        if not raw:
            raise ValueError("command_json is required")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _enqueue_command(self, command_text: str, command_json: dict[str, Any]) -> int:
        table = self._qualify("NEXT_BATCH_COMMAND")
        with self._connect() as conn:
            cur = conn.cursor()
            out_id = cur.var(int)
            cur.execute(
                f"""
                INSERT INTO {table} (
                    CONTROL_NAME, COMMAND_STATUS, COMMAND_TYPE, COMMAND_TEXT,
                    COMMAND_JSON, REQUESTED_BY, REQUESTED_AT
                ) VALUES (
                    'BATCH_AGENT', 'PENDING', 'USER_COMMAND', :1, :2,
                    'LANGFLOW_CHAT_AGENT', CURRENT_TIMESTAMP
                )
                RETURNING COMMAND_ID INTO :3
                """,
                [str(command_text or ""), json.dumps(command_json or {}, ensure_ascii=False), out_id],
            )
            conn.commit()
            value = out_id.getvalue()
            if isinstance(value, list):
                value = value[0]
            return int(value)

    def _request_stop(self) -> None:
        table = self._qualify("NEXT_BATCH_CONTROL")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = 'STOP_REQUESTED',
                       STOP_REQUESTED_YN = 'Y',
                       STOP_REQUESTED_AT = CURRENT_TIMESTAMP,
                       UPDATED_AT = CURRENT_TIMESTAMP,
                       LAST_EVENT = 'STOP_REQUESTED',
                       MESSAGE = 'Stop requested by Langflow Chat Agent.'
                 WHERE CONTROL_NAME = 'BATCH_AGENT'
                """
            )
            conn.commit()

    def _status_summary(self) -> dict[str, Any]:
        return {
            "control": self._control_status(),
            "migration": self._count_by_status("NEXT_MIG_INFO", "STATUS"),
            "sql_conversion": self._count_by_status("NEXT_SQL_INFO", "STATUS_CONVERSION"),
            "sql_tuning": self._count_by_status("NEXT_SQL_INFO", "STATUS_TUNING"),
            "formatting": self._formatting_summary(),
            "pending_commands": self._pending_command_count(),
        }

    def _control_status(self) -> dict[str, Any]:
        table = self._qualify("NEXT_BATCH_CONTROL")
        rows = self._query(
            f"""
            SELECT STATUS, RUN_ID, STOP_REQUESTED_YN, LOOP_NO, HEARTBEAT_AT,
                   LAST_EVENT, LAST_AGENT, LAST_JOB_ID, LAST_JOB_STATUS, MESSAGE
              FROM {table}
             WHERE CONTROL_NAME = 'BATCH_AGENT'
            """
        )
        if not rows:
            return {"exists": False}
        row = rows[0]
        return {
            "exists": True,
            "status": self._text(row[0]),
            "run_id": self._text(row[1]),
            "stop_requested_yn": self._text(row[2]),
            "loop_no": int(row[3] or 0),
            "heartbeat_at": self._text(row[4]),
            "last_event": self._text(row[5]),
            "last_agent": self._text(row[6]),
            "last_job_id": self._text(row[7]),
            "last_job_status": self._text(row[8]),
            "message": self._text(row[9]),
        }

    def _failure_summary(self, agent: str, limit: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if agent in {"all", "migration", "mig", "db"}:
            result["migration"] = self._recent_failures("NEXT_MIG_INFO", "STATUS", limit)
        if agent in {"all", "sql", "conversion", "sql_conversion"}:
            result["sql_conversion"] = self._recent_failures("NEXT_SQL_INFO", "STATUS_CONVERSION", limit)
        if agent in {"all", "tuning", "sql_tuning"}:
            result["sql_tuning"] = self._recent_failures("NEXT_SQL_INFO", "STATUS_TUNING", limit)
        return result

    def _count_by_status(self, table_name: str, status_column: str) -> dict[str, int]:
        table = self._qualify(table_name)
        column = self._clean_identifier(status_column)
        rows = self._query(
            f"""
            SELECT NVL(TRIM({column}), 'NULL') AS STATUS_VALUE, COUNT(*)
              FROM {table}
             GROUP BY NVL(TRIM({column}), 'NULL')
             ORDER BY COUNT(*) DESC, STATUS_VALUE
            """
        )
        return {self._text(status): int(count or 0) for status, count in rows}

    def _recent_failures(self, table_name: str, status_column: str, limit: int) -> dict[str, Any]:
        table = self._qualify(table_name)
        column = self._clean_identifier(status_column)
        rows = self._query(
            f"""
            SELECT *
              FROM (
                    SELECT NVL(TRIM({column}), 'NULL') AS STATUS_VALUE,
                           COUNT(*) AS CNT
                      FROM {table}
                     WHERE UPPER(TRIM(NVL({column}, ''))) LIKE 'FAIL%'
                     GROUP BY NVL(TRIM({column}), 'NULL')
                     ORDER BY COUNT(*) DESC
                   )
             WHERE ROWNUM <= :1
            """,
            [max(1, int(limit or 200))],
        )
        return {
            "total_fail": sum(int(row[1] or 0) for row in rows),
            "status_counts": [{"status": self._text(row[0]), "count": int(row[1] or 0)} for row in rows],
        }

    def _formatting_summary(self) -> dict[str, int]:
        table = self._qualify("NEXT_SQL_INFO")
        rows = self._query(
            f"""
            SELECT
                   COUNT(*) AS TOTAL,
                   SUM(CASE WHEN FORMATTED_SQL IS NOT NULL THEN 1 ELSE 0 END) AS APPLIED,
                   SUM(CASE WHEN FORMATTED_SQL IS NULL THEN 1 ELSE 0 END) AS PENDING
              FROM {table}
            """
        )
        if not rows:
            return {}
        row = rows[0]
        return {"TOTAL": int(row[0] or 0), "APPLIED": int(row[1] or 0), "PENDING": int(row[2] or 0)}

    def _pending_command_count(self) -> int:
        table = self._qualify("NEXT_BATCH_COMMAND")
        rows = self._query(
            f"""
            SELECT COUNT(*)
              FROM {table}
             WHERE CONTROL_NAME = 'BATCH_AGENT'
               AND UPPER(TRIM(COMMAND_STATUS)) = 'PENDING'
            """
        )
        return int(rows[0][0] or 0) if rows else 0

    def _format_status(self, summary: dict[str, Any]) -> str:
        control = summary.get("control") or {}
        return "\n".join(
            [
                f"Supervisor 상태: {control.get('status') or 'UNKNOWN'}",
                f"loop={control.get('loop_no', 0)}, last={control.get('last_event') or '-'}",
                f"대기 명령={summary.get('pending_commands', 0)}",
                f"Migration={self._compact_counts(summary.get('migration'))}",
                f"SQL Conversion={self._compact_counts(summary.get('sql_conversion'))}",
                f"SQL Tuning={self._compact_counts(summary.get('sql_tuning'))}",
                f"Formatting={self._compact_counts(summary.get('formatting'))}",
            ]
        )

    def _format_failure_summary(self, summary: dict[str, Any]) -> str:
        if not summary:
            return "FAIL 데이터를 조회할 대상이 없습니다."
        lines = []
        for name, data in summary.items():
            total = int((data or {}).get("total_fail") or 0)
            counts = ", ".join(
                f"{item.get('status') or '-'} {item.get('count')}"
                for item in (data or {}).get("status_counts", [])[:5]
            )
            lines.append(f"{name}: FAIL {total}건" + (f" ({counts})" if counts else ""))
        return "\n".join(lines)

    def _help_text(self) -> str:
        return (
            "지원 action: enqueue_migration, enqueue_sql_conversion, request_stop, status, failure_summary.\n"
            '예: {"action":"enqueue_migration","map_id":101}\n'
            '예: {"action":"enqueue_sql_conversion","sql_id":"SEL_001","space_nm":"userMapper"}'
        )

    @contextmanager
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or DEFAULT_DB_CONFIG["db_host"]).strip(),
            int(getattr(self, "db_port", None) or DEFAULT_DB_CONFIG["db_port"]),
            service_name=str(getattr(self, "db_service_name", "") or DEFAULT_DB_CONFIG["db_service_name"]).strip(),
        )
        password = self._secret_to_str(getattr(self, "db_password", None)) or str(DEFAULT_DB_CONFIG["db_password"])
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or DEFAULT_DB_CONFIG["db_username"]).strip(),
            password=password,
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _query(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            return cur.fetchall()

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or DEFAULT_DB_CONFIG["system_schema"]).strip().upper()
        if not schema:
            return table
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", schema):
            raise ValueError(f"Invalid schema: {schema}")
        return f"{schema}.{table}"

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _compact_counts(self, counts: Any) -> str:
        if not isinstance(counts, dict) or not counts:
            return "-"
        return ", ".join(f"{key}:{value}" for key, value in list(counts.items())[:5])

    def _text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        return str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
