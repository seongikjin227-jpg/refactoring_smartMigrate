from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


AGENT_ORDER = [
    ("db_migration", "DB Migration"),
    ("sql_conversion", "SQL Conversion"),
    ("sql_tuning", "SQL Tuning"),
    ("sql_formatting", "SQL Formatting"),
]


class NewType04Dashboard(Component):
    display_name = "04 Dashboard"
    description = "Queries dashboard counts/progress and formats a concise Gaia output message."
    name = "NewType04Dashboard"
    icon = "Gauge"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        # Execute the component and return a Langflow message.
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            dashboard = self._query_dashboard()
            answer = self._build_answer(payload, dashboard)
            self.status = {
                **payload,
                "component": "04_dashboard",
                "dashboard_data": dashboard,
                "answer_text": answer,
                "final": True,
            }
            return Message(text=answer)
        except Exception as exc:
            answer = f"[Dashboard 조회 결과]\nDashboard 조회 중 오류가 발생했습니다.\n오류: {exc}"
            self.status = {"ok": False, "component": "04_dashboard", "error": str(exc), "answer_text": answer}
            return Message(text=answer)

    def _query_dashboard(self) -> dict[str, Any]:
        # Query all dashboard metrics from the configured database.
        if not self._has_db_config():
            raise ValueError("DB connection settings are required for 04 Dashboard")
        agents = {
            "db_migration": self._migration_summary(),
            "sql_conversion": self._sql_conversion_summary(),
            "sql_tuning": self._sql_tuning_summary(),
            "sql_formatting": self._sql_formatting_summary(),
        }
        return {"ok": True, "agents": agents, "recommendation": self._recommendation(agents)}

    def _migration_summary(self) -> dict[str, Any]:
        # Build DB Migration dashboard counts and rates.
        table = self._qualify("NEXT_MIG_INFO")
        total = self._count(table)
        target = self._count(table, "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y' AND STATUS IS NULL")
        pass_count = self._count(table, "UPPER(TRIM(NVL(STATUS, 'NULL'))) IN ('PASS', 'SUCCESS')")
        fail_count = self._count(table, "UPPER(TRIM(NVL(STATUS, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'")
        return self._stage_summary(
            agent="DB_MIGRATION",
            table=table,
            target_condition="USE_YN='Y' AND STATUS IS NULL",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS"),
        )

    def _sql_conversion_summary(self) -> dict[str, Any]:
        # Build SQL Conversion dashboard counts and rates.
        table = self._qualify("NEXT_SQL_INFO")
        total = self._count(table)
        target = self._count(table, "STATUS_CONVERSION IS NULL")
        pass_count = self._count(table, "UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')")
        fail_count = self._count(
            table,
            "UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%'",
        )
        return self._stage_summary(
            agent="SQL_CONVERSION",
            table=table,
            target_condition="STATUS_CONVERSION IS NULL",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_CONVERSION"),
        )

    def _sql_tuning_summary(self) -> dict[str, Any]:
        # Build SQL Tuning dashboard counts and rates.
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "STATUS_CONVERSION") if col not in columns]
        if missing:
            return self._unavailable("SQL_TUNING", table, f"missing columns: {', '.join(missing)}")
        base_where = "UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')"
        total = self._count(table, base_where)
        target = self._count(table, f"{base_where} AND STATUS_TUNING IS NULL")
        pass_count = self._count(table, f"{base_where} AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')")
        fail_count = self._count(
            table,
            f"{base_where} AND (UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%')",
        )
        return self._stage_summary(
            agent="SQL_TUNING",
            table=table,
            target_condition="STATUS_TUNING IS NULL and STATUS_CONVERSION pass",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total - target,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_TUNING", base_where),
        )

    def _sql_formatting_summary(self) -> dict[str, Any]:
        # Build SQL Formatting dashboard counts and rates.
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "FORMATTED_SQL") if col not in columns]
        if missing:
            return self._unavailable("SQL_FORMATTING", table, f"missing columns: {', '.join(missing)}")
        base_where = "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')"
        target_where = f"{base_where} AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        applied_where = f"{base_where} AND FORMATTED_SQL IS NOT NULL AND DBMS_LOB.GETLENGTH(FORMATTED_SQL) > 0"
        total = self._count(table, base_where)
        target = self._count(table, target_where)
        applied = self._count(table, applied_where)
        return self._stage_summary(
            agent="SQL_FORMATTING",
            table=table,
            target_condition="STATUS_TUNING PASS and FORMATTED_SQL empty",
            total=total,
            target_count=target,
            progress_count=applied,
            progress_base=total,
            success_count=applied,
            success_base=total,
            pass_count=applied,
            fail_count=0,
            status_counts={"APPLIED": applied, "PENDING": target},
        )

    def _stage_summary(
        self,
        *,
        agent: str,
        table: str,
        target_condition: str,
        total: int,
        target_count: int,
        progress_count: int,
        progress_base: int,
        success_count: int,
        success_base: int,
        pass_count: int,
        fail_count: int,
        status_counts: dict[str, int],
    ) -> dict[str, Any]:
        # Assemble a normalized dashboard summary for one stage.
        return {
            "agent": agent,
            "available": True,
            "table": table,
            "target_condition": target_condition,
            "total": int(total or 0),
            "target_count": int(target_count or 0),
            "remaining_count": int(target_count or 0),
            "pass_count": int(pass_count or 0),
            "fail_count": int(fail_count or 0),
            "other_count": max(
                int(total or 0) - int(target_count or 0) - int(pass_count or 0) - int(fail_count or 0),
                0,
            ),
            "progress": {
                "count": int(progress_count or 0),
                "base": int(progress_base or 0),
                "rate": self._pct(progress_count, progress_base),
            },
            "success": {
                "count": int(success_count or 0),
                "base": int(success_base or 0),
                "rate": self._pct(success_count, success_base),
            },
            "status_counts": status_counts,
        }

    def _build_answer(self, payload: dict[str, Any], dashboard: dict[str, Any]) -> str:
        # Format dashboard data into a Markdown user-facing message.
        agents = dashboard.get("agents") or {}
        lines = ["# SmartMigrate Dashboard"]
        lines.append("## 작업 현황")
        lines.append("| 순서 | 단계 | 작업 대상 | 잔여 | 성공 | 실패 | 기타 |")
        lines.append("|---:|---|---:|---:|---:|---:|---:|")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - | - | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | "
                f"{label} | "
                f"{self._num(summary.get('total'))} | "
                f"{self._num(summary.get('remaining_count', summary.get('target_count')))} | "
                f"{self._num(summary.get('pass_count'))} | "
                f"{self._num(summary.get('fail_count'))} | "
                f"{self._num(summary.get('other_count'))} |"
            )

        lines.append("")
        lines.append("## 진척률 / 성공률")
        lines.append("| 순서 | 단계 | 진척률 | 성공률 |")
        lines.append("|---:|---|---:|---:|")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | {label} | "
                f"{self._rate(summary.get('progress') or {})} | "
                f"{self._rate(summary.get('success') or {})} |"
            )

        unavailable = [
            (label, (agents.get(key) or {}).get("reason"))
            for key, label in AGENT_ORDER
            if not (agents.get(key) or {}).get("available", True)
        ]
        if unavailable:
            lines.append("")
            lines.append("## 조회 불가")
            lines.append("")
            for label, reason in unavailable:
                lines.append(f"- **{label}**: {reason}")

        return "\n".join(lines)

    def _recommendation(self, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        # Choose the highest-priority stage with remaining targets.
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            count = int(summary.get("target_count") or 0)
            if summary.get("available", True) and count > 0:
                return {"agent": summary.get("agent"), "label": label, "target_count": count}
        return {}

    def _count(self, table: str, where_clause: str = "1=1") -> int:
        # Run a count query with the supplied table and condition.
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
            row = cur.fetchone()
        return int(row[0] if row else 0)

    def _status_counts(self, table: str, status_column: str, where_clause: str = "1=1") -> dict[str, int]:
        # Query grouped status counts for a dashboard stage.
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT NVL(TO_CHAR({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) AS CNT
                  FROM {table}
                 WHERE {where_clause}
                 GROUP BY NVL(TO_CHAR({status_column}), 'NULL')
                 ORDER BY CNT DESC, STATUS_VALUE ASC
                """
            )
            rows = cur.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def _available_columns(self, table_name: str) -> set[str]:
        # Load available Oracle column names for a table.
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                      FROM ALL_TAB_COLUMNS
                     WHERE OWNER = :1
                       AND TABLE_NAME = :2
                    """,
                    [schema, table],
                )
            else:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                      FROM USER_TAB_COLUMNS
                     WHERE TABLE_NAME = :1
                    """,
                    [table],
                )
            rows = cur.fetchall()
        return {str(row[0]).upper() for row in rows}

    def _unavailable(self, agent: str, table: str, reason: str) -> dict[str, Any]:
        # Return a standard unavailable-stage dashboard summary.
        return {
            "agent": agent,
            "available": False,
            "table": table,
            "reason": reason,
            "total": 0,
            "target_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "other_count": 0,
            "progress": {"count": 0, "base": 0, "rate": "-"},
            "success": {"count": 0, "base": 0, "rate": "-"},
            "status_counts": {},
        }

    @contextmanager
    def _connect(self):
        # Open and safely close an Oracle database connection.
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret_to_str(getattr(self, "db_password", None)),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _has_db_config(self) -> bool:
        # Check whether the minimum DB connection settings are present.
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        # Qualify a table name with the optional system schema.
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        # Validate and normalize an Oracle identifier.
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _pct(self, numerator: int, denominator: int) -> str:
        # Format a numerator and denominator as a percentage string.
        denominator = int(denominator or 0)
        if denominator <= 0:
            return "-"
        return f"{(int(numerator or 0) / denominator) * 100:.1f}%"

    def _num(self, value: Any) -> int:
        # Convert a display count value to an integer.
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _rate(self, value: dict[str, Any]) -> str:
        # Format a rate with count/base detail for Markdown table display.
        return f"{value.get('rate', '-')} ({value.get('count', 0)}/{value.get('base', 0)})"

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse a Langflow Data, dict, or JSON string payload.
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
            raise ValueError("payload_json must be a JSON object")
        return parsed

    def _secret_to_str(self, value: Any) -> str:
        # Convert a Langflow secret value into a plain string.
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
