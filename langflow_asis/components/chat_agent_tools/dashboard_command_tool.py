from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data



class DashboardCommandTool(Component):
    display_name = "Dashboard Command Tool"
    description = "Summarizes SmartMigration agent job queues and recommends the next agent action."
    name = "DashboardCommandTool"
    icon = "LayoutDashboard"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info='JSON command. Example: {"action":"summary"}',
        ),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="list_limit", display_name="Default List Limit", value=5, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        try:
            cmd = self._parse_command()
            action = str(cmd.get("action") or "summary").strip().lower()
            if action == "summary":
                res = self._summary(cmd)
            else:
                raise ValueError(f"Unsupported action: {action}")
            self.status = res
            return Data(data=res)
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
            self.status = res
            return Data(data=res)

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {"action": "summary"}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _summary(self, cmd: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(cmd.get("limit") or self.list_limit or 5), 20))
        agents = {
            "db_migration": self._migration_summary(limit),
            "sql_conversion": self._sql_conversion_summary(limit),
            "sql_tuning": self._sql_tuning_summary(limit),
            "sql_formatting": self._sql_formatting_summary(limit),
        }
        return {
            "ok": True,
            "action": "summary",
            "recommendations": self._recommend_next_actions(agents),
            "agents": agents,
        }

    def _migration_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_MIG_INFO")
        status_counts = self._status_counts(table, "STATUS", "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'")
        target_count = self._count(table, "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y' AND STATUS IS NULL")
        next_jobs = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, PRIORITY, STATUS, BATCH_CNT, RETRY_COUNT, UPD_TS
                      FROM {table}
                     WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                       AND STATUS IS NULL
                     ORDER BY PRIORITY ASC, MAP_ID ASC
                   )
             WHERE ROWNUM <= :1
            """,
            [limit],
            ["map_id", "map_type", "fr_table", "to_table", "priority", "status", "batch_cnt", "retry_count", "upd_ts"],
        )
        return {
            "agent": "DB_MIGRATION",
            "table": table,
            "target_count": target_count,
            "target_condition": "USE_YN='Y' AND STATUS IS NULL",
            "status_counts": status_counts,
            "next_jobs": next_jobs,
        }

    def _sql_conversion_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        status_counts = self._status_counts(table, "STATUS_CONVERSION")
        target_count = self._count(table, "STATUS_CONVERSION IS NULL")
        next_jobs = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT TAG_KIND, SPACE_NM, SQL_ID, STATUS_CONVERSION, PRIORITY, BATCH_CNT, UPD_TS
                      FROM {table}
                     WHERE STATUS_CONVERSION IS NULL
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
                   )
             WHERE ROWNUM <= :1
            """,
            [limit],
            ["tag_kind", "space_nm", "sql_id", "status_conversion", "priority", "batch_cnt", "upd_ts"],
        )
        return {
            "agent": "SQL_CONVERSION",
            "table": table,
            "target_count": target_count,
            "target_condition": "STATUS_CONVERSION IS NULL",
            "status_counts": status_counts,
            "next_jobs": next_jobs,
        }

    def _sql_tuning_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        if "STATUS_TUNING" not in columns:
            return self._unavailable("SQL_TUNING", table, "STATUS_TUNING column not found")
        if "TO_SQL" not in columns:
            return self._unavailable("SQL_TUNING", table, "TO_SQL column not found")
        status_counts = self._status_counts(table, "STATUS_TUNING")
        where_clause = (
            "UPPER(TRIM(STATUS_TUNING)) IN ('READY', 'URGENT', 'FAIL', 'FAIL-TUNED', 'FAIL-BIND', 'FAIL-TEST') "
            "AND TO_SQL IS NOT NULL "
            "AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS-CONVERSION', 'PASS')"
        )
        target_count = self._count(table, where_clause)
        next_jobs = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT TAG_KIND, SPACE_NM, SQL_ID, STATUS_CONVERSION, STATUS_TUNING, PRIORITY, BATCH_CNT, UPD_TS
                      FROM {table}
                     WHERE {where_clause}
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
                   )
             WHERE ROWNUM <= :1
            """,
            [limit],
            ["tag_kind", "space_nm", "sql_id", "status_conversion", "status_tuning", "priority", "batch_cnt", "upd_ts"],
        )
        return {
            "agent": "SQL_TUNING",
            "table": table,
            "target_count": target_count,
            "target_condition": "STATUS_TUNING in retryable states, TO_SQL exists, conversion passed",
            "status_counts": status_counts,
            "next_jobs": next_jobs,
        }

    def _sql_formatting_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        if "FORMATTED_SQL" not in columns:
            return self._unavailable("SQL_FORMATTING", table, "FORMATTED_SQL column not found")
        where_clause = (
            "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING') "
            "AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        )
        target_count = self._count(table, where_clause)
        next_jobs = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT TAG_KIND, SPACE_NM, SQL_ID, STATUS_CONVERSION, STATUS_TUNING, PRIORITY, BATCH_CNT, UPD_TS
                      FROM {table}
                     WHERE {where_clause}
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
                   )
             WHERE ROWNUM <= :1
            """,
            [limit],
            ["tag_kind", "space_nm", "sql_id", "status_conversion", "status_tuning", "priority", "batch_cnt", "upd_ts"],
        )
        return {
            "agent": "SQL_FORMATTING",
            "table": table,
            "target_count": target_count,
            "target_condition": "STATUS_TUNING PASS and FORMATTED_SQL empty",
            "status_counts": {"FORMATTED_SQL_EMPTY": target_count},
            "next_jobs": next_jobs,
        }

    def _recommend_next_actions(self, agents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        recommendations = []
        for key in ["db_migration", "sql_conversion", "sql_tuning", "sql_formatting"]:
            summary = agents.get(key, {})
            count = int(summary.get("target_count") or 0)
            if count <= 0:
                continue
            recommendations.append(
                {
                    "agent": summary.get("agent"),
                    "target_count": count,
                    "reason": f"{summary.get('agent')} target count is {count}.",
                    "first_job": (summary.get("next_jobs") or [None])[0],
                }
            )
        return recommendations

    # --- helpers (lightweight copy from ChatCommandTool) ---
    @contextmanager
    def _connect(self):
        self._ensure_runtime_dependencies()
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        password = self._secret_to_str(getattr(self, "db_password", None)) or ""
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=password,
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_runtime_dependencies(self) -> None:
        AUTO_INSTALL_MISSING_PACKAGES = True
        missing = []
        try:
            import oracledb  # type: ignore
        except ModuleNotFoundError:
            missing.append("oracledb")
        if not missing:
            return
        if not AUTO_INSTALL_MISSING_PACKAGES:
            raise ModuleNotFoundError("Missing packages: " + ", ".join(missing))
        for pkg in missing:
            self._pip_install(pkg)

    def _pip_install(self, package: str) -> None:
        import subprocess, sys

        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

    def _query(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            return cur.fetchall()

    def _qualify(self, table_name: str) -> str:
        table = str(table_name or "").strip().upper()
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            return table
        return f"{schema}.{table}"

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

    def _count(self, table: str, where_clause: str) -> int:
        rows = self._query(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
        return int(rows[0][0] or 0) if rows else 0

    def _status_counts(self, table: str, column: str, where_clause: str = "1 = 1") -> dict[str, int]:
        rows = self._query(
            f"""
            SELECT NVL(TO_CHAR({column}), 'NULL') AS STATUS_VALUE, COUNT(*)
              FROM {table}
             WHERE {where_clause}
             GROUP BY NVL(TO_CHAR({column}), 'NULL')
             ORDER BY STATUS_VALUE
            """
        )
        return {str(row[0] or "NULL"): int(row[1] or 0) for row in rows}

    def _query_rows(self, query: str, params: list[Any], columns: list[str]) -> list[dict[str, Any]]:
        rows = self._query(query, params)
        return [
            {
                columns[index]: self._text(value) if not isinstance(value, (int, float)) else value
                for index, value in enumerate(row)
            }
            for row in rows
        ]

    def _available_columns(self, table_name: str) -> set[str]:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        clean_table = str(table_name or "").strip().upper()
        if schema:
            rows = self._query(
                """
                SELECT COLUMN_NAME
                  FROM ALL_TAB_COLUMNS
                 WHERE OWNER = :1
                   AND TABLE_NAME = :2
                """,
                [schema, clean_table],
            )
        else:
            rows = self._query(
                """
                SELECT COLUMN_NAME
                  FROM USER_TAB_COLUMNS
                 WHERE TABLE_NAME = :1
                """,
                [clean_table],
            )
        return {str(row[0]).upper() for row in rows}

    def _unavailable(self, agent: str, table: str, reason: str) -> dict[str, Any]:
        return {
            "agent": agent,
            "table": table,
            "available": False,
            "target_count": 0,
            "reason": reason,
            "status_counts": {},
            "next_jobs": [],
        }

    def _count_by_status(self, table_name: str, status_column: str) -> dict[str, int]:
        table = self._qualify(table_name)
        rows = self._query(f"SELECT NVL(TRIM({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) FROM {table} GROUP BY NVL(TRIM({status_column}), 'NULL')")
        return {str(r[0] or 'NULL'): int(r[1] or 0) for r in rows}
