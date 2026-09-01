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


class NewType11FinalDashboard(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "11 Final Dashboard"
    description = "Queries the full dashboard after any loop Done signal."
    name = "NewType11FinalDashboard"
    icon = "ClipboardCheck"

    inputs = [
        DataInput(name="loop_done", display_name="Loop Done", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="build_result", types=["Message"])]

    def build_result(self) -> Message:
        self._insert_log(0, "WORKFLOW", "11_FINAL_DASH", "INFO", "BUILD_RESULT", "START", "before build_result", 0, "")
        try:
            try:
                payload = self._parse_payload(getattr(self, "loop_done", ""))
                self._db_config = self._db_config_from_inputs()
                dashboard = self._query_dashboard()
                answer = self._build_answer(dashboard)
                self.status = {
                    **payload,
                    "component": "11_finalDashboard",
                    "dashboard_data": dashboard,
                    "answer_text": answer,
                    "final": True,
                }
                __log_result = Message(text=answer)
                self._insert_log(0, "WORKFLOW", "11_FINAL_DASH", "INFO", "BUILD_RESULT", "END", "after build_result", 0, "")
                return __log_result
            except Exception as exc:
                answer = f"## Final Dashboard\n\nDashboard refresh failed after loop completion.\n\nError: {exc}"
                self.status = {"ok": False, "component": "11_finalDashboard", "error": str(exc), "answer_text": answer}
                __log_result = Message(text=answer)
                self._insert_log(0, "WORKFLOW", "11_FINAL_DASH", "ERROR", "BUILD_RESULT", "ERROR", "error build_result", 0, "")
                return __log_result
            self._insert_log(0, "WORKFLOW", "11_FINAL_DASH", "INFO", "BUILD_RESULT", "END", "after build_result", 0, "")
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "11_FINAL_DASH", "ERROR", "BUILD_RESULT", "ERROR", f"error build_result: {exc}", 0, "")
            raise

    def _query_dashboard(self) -> dict[str, Any]:
        if not self._has_db_config():
            raise ValueError("Final Dashboard DB inputs are required")
        agents = {
            "db_migration": self._migration_summary(),
            "sql_conversion": self._sql_conversion_summary(),
            "sql_tuning": self._sql_tuning_summary(),
            "sql_formatting": self._sql_formatting_summary(),
        }
        return {"ok": True, "agents": agents, "recommendation": self._recommendation(agents)}

    def _migration_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_MIG_INFO")
        target_scope = "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'"
        pending_where = f"{target_scope} AND STATUS IS NULL"
        fail_where = f"{target_scope} AND ({self._fail_status_condition('STATUS')})"
        edited_fail_where = f"{target_scope} AND {self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, target_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        progress_base = total
        pass_count = self._count(table, f"{target_scope} AND UPPER(TRIM(NVL(STATUS, 'NULL'))) IN ('PASS', 'SUCCESS')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="DB_MIGRATION",
            table=table,
            target_condition="USE_YN='Y' AND (STATUS IS NULL OR (USER_EDITED='Y' AND STATUS LIKE 'FAIL-%'))",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=progress_base,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS", target_scope),
        )

    def _sql_conversion_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        target_scope = self._sql_status_target_condition("STATUS_CONVERSION", ("PASS", "PASS-CONVERSION"))
        pending_where = "STATUS_CONVERSION IS NULL"
        fail_where = f"{target_scope} AND ({self._fail_status_condition('STATUS_CONVERSION')})"
        edited_fail_where = f"{self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS_CONVERSION')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, target_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        pass_count = self._count(table, f"{target_scope} AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="SQL_CONVERSION",
            table=table,
            target_condition="total excludes STATUS_CONVERSION='NA'; remaining is NULL or USER_EDITED='Y' FAIL-*",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_CONVERSION", target_scope),
        )

    def _sql_tuning_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "STATUS_CONVERSION") if col not in columns]
        if missing:
            return self._unavailable("SQL_TUNING", table, f"missing columns: {', '.join(missing)}")
        total_scope = self._sql_status_target_condition("STATUS_TUNING", ("PASS", "PASS-TUNING"))
        base_where = "UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')"
        pending_where = f"{base_where} AND STATUS_TUNING IS NULL"
        fail_where = f"{base_where} AND ({self._fail_status_condition('STATUS_TUNING')})"
        edited_fail_where = f"{base_where} AND {self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS_TUNING')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, total_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        pass_count = self._count(table, f"{base_where} AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="SQL_TUNING",
            table=table,
            target_condition="total excludes STATUS_TUNING='NA'; remaining requires conversion PASS and NULL or USER_EDITED='Y' FAIL-*",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_TUNING", total_scope),
        )

    def _sql_formatting_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "FORMATTED_SQL") if col not in columns]
        if missing:
            return self._unavailable("SQL_FORMATTING", table, f"missing columns: {', '.join(missing)}")
        total_scope = self._sql_status_target_condition("STATUS_TUNING", ("PASS", "PASS-TUNING"))
        base_where = "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')"
        target_where = f"{base_where} AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        applied_where = f"{base_where} AND FORMATTED_SQL IS NOT NULL AND DBMS_LOB.GETLENGTH(FORMATTED_SQL) > 0"
        total = self._count(table, total_scope)
        target = self._count(table, target_where)
        applied = self._count(table, applied_where)
        return self._stage_summary(
            agent="SQL_FORMATTING",
            table=table,
            target_condition="total excludes STATUS_TUNING='NA'; remaining requires tuning PASS and FORMATTED_SQL empty",
            total=total,
            target_count=target,
            progress_count=applied,
            progress_base=total,
            success_count=applied,
            success_base=0,
            pass_count=applied,
            fail_count=0,
            status_counts={"APPLIED": applied, "PENDING": target},
            has_success_rate=False,
        )

    def _stage_summary(
        self,
        *,
        agent: str,
        table: str,
        target_condition: str,
        total: int,
        target_count: int,
        pending_count: int | None = None,
        progress_count: int,
        progress_base: int,
        success_count: int,
        success_base: int,
        pass_count: int,
        fail_count: int,
        status_counts: dict[str, int],
        has_success_rate: bool = True,
    ) -> dict[str, Any]:
        effective_pending_count = int(target_count if pending_count is None else pending_count or 0)
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
            "other_count": max(int(total or 0) - effective_pending_count - int(pass_count or 0) - int(fail_count or 0), 0),
            "progress": {"count": int(progress_count or 0), "base": int(progress_base or 0), "rate": self._pct(progress_count, progress_base)},
            "success": (
                {"count": int(success_count or 0), "base": int(success_base or 0), "rate": self._pct(success_count, success_base)}
                if has_success_rate
                else {"count": 0, "base": 0, "rate": "-", "not_applicable": True}
            ),
            "status_counts": status_counts,
        }

    def _build_answer(self, dashboard: dict[str, Any]) -> str:
        agents = dashboard.get("agents") or {}
        lines = [
            "# SmartMigrate Dashboard",
            "",
            "요청하신 작업이 완료되었습니다.",
            "아래는 현재 SmartMigrate 전체 작업 현황입니다.",
            "",
        ]
        lines.extend(
            [
                "## 작업 현황",
                "| 순서 | 단계 | 작업 대상 | 잔여 | 성공 | 실패 |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - | - | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | {label} | {self._num(summary.get('total'))} | "
                f"{self._num(summary.get('remaining_count', summary.get('target_count')))} | "
                f"{self._num(summary.get('pass_count'))} | {self._num(summary.get('fail_count'))} |"
            )
        lines.extend(
            [
                "",
                "## 진척률 / 성공률",
                "| 순서 | 단계 | 진척률 | 성공률 |",
                "|---:|---|---|---|",
            ]
        )
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
        return "\n".join(lines)

    def _recommendation(self, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            count = int(summary.get("target_count") or 0)
            if summary.get("available", True) and count > 0:
                return {"agent": summary.get("agent"), "label": label, "target_count": count}
        return {}

    def _count(self, table: str, where_clause: str = "1=1") -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
            row = cur.fetchone()
        return int(row[0] if row else 0)

    def _fail_status_condition(self, status_column: str) -> str:
        return f"UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"

    def _detailed_fail_status_condition(self, status_column: str) -> str:
        return f"UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"

    def _sql_status_target_condition(self, status_column: str, pass_statuses: tuple[str, ...]) -> str:
        pass_list = ", ".join(f"'{status}'" for status in pass_statuses)
        normalized = f"UPPER(TRIM(NVL({status_column}, 'NULL')))"
        return f"({status_column} IS NULL OR {normalized} IN ({pass_list}) OR {normalized} LIKE 'FAIL-%')"

    def _user_edited_condition(self) -> str:
        return "UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y'"

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

    def _available_columns(self, table_name: str) -> set[str]:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2", [schema, table])
            else:
                cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [table])
            rows = cur.fetchall()
        return {str(row[0]).upper() for row in rows}

    def _unavailable(self, agent: str, table: str, reason: str) -> dict[str, Any]:
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
        import oracledb

        dsn = oracledb.makedsn(
            str(self._db_config.get("db_host") or "").strip(),
            int(self._db_config.get("db_port") or 1521),
            service_name=str(self._db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(self._db_config.get("db_username") or "").strip(),
            password=str(self._db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _has_db_config(self) -> bool:
        return all(str(self._db_config.get(name) or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _db_config_from_inputs(self) -> dict[str, Any]:
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        return f"{self._clean_identifier(schema)}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _pct(self, numerator: int, denominator: int) -> str:
        denominator = int(denominator or 0)
        if denominator <= 0:
            return "-"
        return f"{(int(numerator or 0) / denominator) * 100:.1f}%"

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _rate(self, value: dict[str, Any]) -> str:
        if value.get("not_applicable"):
            return "-"
        count = self._num(value.get("count"))
        base = self._num(value.get("base"))
        if base <= 0:
            return f"{self._progress_bar(0, 0)} - ({count}/{base})"
        return f"{self._progress_bar(count, base)} {value.get('rate', '-')} ({count}/{base})"

    def _progress_bar(self, count: int, base: int, width: int = 10) -> str:
        if base <= 0:
            filled = 0
        else:
            filled = max(0, min(width, round((int(count or 0) / int(base)) * width)))
        return "■" * filled + "□" * (width - filled)

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("loop_done must be a JSON object")
        return parsed

    def _insert_log(
        self,
        map_id,
        mig_kind,
        log_type,
        log_level,
        step_name,
        status,
        message,
        retry_count,
        generated_sql="",
    ):
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO SFAADM.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    SFAADM.MIGRATION_LOG_SEQ.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8, CURRENT_TIMESTAMP
                )
                """,
                [
                    map_id,
                    str(mig_kind or "")[:100],
                    str(log_type or "")[:20],
                    str(log_level or "")[:20],
                    str(step_name or "")[:50],
                    str(status or "")[:20],
                    str(message or "")[:4000],
                    retry_count,
                ],
            )
            conn.commit()
        except Exception as exc:
            self.status = f"NEXT_MIG_LOG insert failed: {exc}"
        finally:
            if conn is not None:
                conn.close()
