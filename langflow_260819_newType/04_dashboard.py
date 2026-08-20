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
    description = "Queries dashboard job targets and formats a concise Gaia output message."
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
        IntInput(name="list_limit", display_name="List Limit", value=5, required=False),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
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
        if not self._has_db_config():
            raise ValueError("DB connection settings are required for 04 Dashboard")
        limit = max(1, min(int(getattr(self, "list_limit", None) or 5), 50))
        agents = {
            "db_migration": self._migration_summary(limit),
            "sql_conversion": self._sql_conversion_summary(limit),
            "sql_tuning": self._sql_tuning_summary(limit),
            "sql_formatting": self._sql_formatting_summary(limit),
        }
        return {
            "ok": True,
            "agents": agents,
            "recommendations": self._recommendations(agents),
        }

    def _migration_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_MIG_INFO")
        where_clause = "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y' AND STATUS IS NULL"
        return {
            "agent": "DB_MIGRATION",
            "available": True,
            "table": table,
            "target_condition": "USE_YN='Y' AND STATUS IS NULL",
            "target_count": self._count(table, where_clause),
            "status_counts": self._status_counts(table, "STATUS", "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'"),
            "next_jobs": self._query_rows(
                f"""
                SELECT *
                  FROM (
                        SELECT MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, PRIORITY, STATUS, BATCH_CNT, RETRY_COUNT, UPD_TS
                          FROM {table}
                         WHERE {where_clause}
                         ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                       )
                 WHERE ROWNUM <= :1
                """,
                [limit],
                ["map_id", "map_type", "fr_table", "to_table", "priority", "status", "batch_cnt", "retry_count", "upd_ts"],
            ),
        }

    def _sql_conversion_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        where_clause = "STATUS_CONVERSION IS NULL"
        return {
            "agent": "SQL_CONVERSION",
            "available": True,
            "table": table,
            "target_condition": "STATUS_CONVERSION IS NULL",
            "target_count": self._count(table, where_clause),
            "status_counts": self._status_counts(table, "STATUS_CONVERSION"),
            "next_jobs": self._query_rows(
                f"""
                SELECT *
                  FROM (
                        SELECT TAG_KIND, SPACE_NM, SQL_ID, STATUS_CONVERSION, PRIORITY, BATCH_CNT, UPD_TS
                          FROM {table}
                         WHERE {where_clause}
                         ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
                       )
                 WHERE ROWNUM <= :1
                """,
                [limit],
                ["tag_kind", "space_nm", "sql_id", "status_conversion", "priority", "batch_cnt", "upd_ts"],
            ),
        }

    def _sql_tuning_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "STATUS_CONVERSION") if col not in columns]
        if missing:
            return self._unavailable("SQL_TUNING", table, f"missing columns: {', '.join(missing)}")
        where_clause = (
            "STATUS_TUNING IS NULL "
            "AND UPPER(TRIM(STATUS_CONVERSION)) = 'PASS-CONVERSION'"
        )
        return {
            "agent": "SQL_TUNING",
            "available": True,
            "table": table,
            "target_condition": "STATUS_TUNING IS NULL AND STATUS_CONVERSION='PASS-CONVERSION'",
            "target_count": self._count(table, where_clause),
            "status_counts": self._status_counts(table, "STATUS_TUNING"),
            "next_jobs": self._query_rows(
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
            ),
        }

    def _sql_formatting_summary(self, limit: int) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "FORMATTED_SQL") if col not in columns]
        if missing:
            return self._unavailable("SQL_FORMATTING", table, f"missing columns: {', '.join(missing)}")
        where_clause = (
            "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING') "
            "AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        )
        return {
            "agent": "SQL_FORMATTING",
            "available": True,
            "table": table,
            "target_condition": "STATUS_TUNING PASS and FORMATTED_SQL empty",
            "target_count": self._count(table, where_clause),
            "status_counts": {"FORMATTED_SQL_EMPTY": self._count(table, where_clause)},
            "next_jobs": self._query_rows(
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
            ),
        }

    def _build_answer(self, payload: dict[str, Any], dashboard: dict[str, Any]) -> str:
        user_request = str(payload.get("user_request") or payload.get("original_request") or "").strip()
        agents = dashboard.get("agents") or {}
        recommendations = dashboard.get("recommendations") or []

        lines = ["[Dashboard 조회 결과]"]
        if user_request:
            lines.append(f"요청: {user_request}")
        lines.append("")
        lines.append("작업 대상 현황")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            if not summary.get("available", True):
                lines.append(f"- {label}: 조회 불가 ({summary.get('reason')})")
                continue
            lines.append(f"- {label}: 작업 대상 {int(summary.get('target_count') or 0)}건")

        lines.append("")
        if recommendations:
            rec = recommendations[0]
            lines.append(
                f"우선 실행 추천: {rec.get('label')} "
                f"{self._job_label(rec.get('first_job') or {})} "
                f"(대상 {rec.get('target_count')}건)"
            )
        else:
            lines.append("우선 실행 추천: 현재 실행 가능한 작업 대상이 없습니다.")

        lines.append("")
        lines.append("우선순위별 다음 작업")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            if not summary.get("available", True):
                lines.append(f"- {label}: 조회 불가")
                continue
            jobs = list(summary.get("next_jobs") or [])
            if not jobs:
                lines.append(f"- {label}: 대상 없음")
                continue
            lines.append(f"- {label}: {', '.join(self._job_summary(job) for job in jobs)}")

        return "\n".join(lines)

    def _recommendations(self, agents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            count = int(summary.get("target_count") or 0)
            if not summary.get("available", True) or count <= 0:
                continue
            out.append(
                {
                    "agent": summary.get("agent"),
                    "label": label,
                    "target_count": count,
                    "first_job": (summary.get("next_jobs") or [None])[0],
                }
            )
        return out

    def _count(self, table: str, where_clause: str = "1=1") -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
            row = cur.fetchone()
        return int(row[0] if row else 0)

    def _status_counts(self, table: str, status_column: str, where_clause: str = "1=1") -> dict[str, int]:
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

    def _query_rows(self, sql: str, params: list[Any], columns: list[str]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [{columns[idx]: self._json_value(value) for idx, value in enumerate(row)} for row in rows]

    def _available_columns(self, table_name: str) -> set[str]:
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
        return {
            "agent": agent,
            "available": False,
            "table": table,
            "reason": reason,
            "target_count": 0,
            "status_counts": {},
            "next_jobs": [],
        }

    @contextmanager
    def _connect(self):
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
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _job_summary(self, job: dict[str, Any]) -> str:
        return f"{self._job_label(job)}(P{self._display(job.get('priority'))})"

    def _job_label(self, job: dict[str, Any]) -> str:
        if job.get("map_id") is not None:
            return f"map_id={job.get('map_id')}"
        sql_id = job.get("sql_id")
        space_nm = job.get("space_nm")
        if sql_id and space_nm:
            return f"{space_nm}/{sql_id}"
        if sql_id:
            return f"sql_id={sql_id}"
        if space_nm:
            return f"space_nm={space_nm}"
        return "대상 미지정"

    def _display(self, value: Any) -> str:
        return "-" if value is None or str(value).strip() == "" else str(value)

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
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

    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
