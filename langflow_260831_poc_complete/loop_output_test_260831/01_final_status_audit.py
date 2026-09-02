from __future__ import annotations

import logging
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


SUCCESS_STATUSES = {
    "PASS",
    "SUCCESS",
    "PASS-CONVERSION",
    "PASS-TUNING",
    "SUCCESS-TEST",
    "FORMATTED",
}
SKIP_STATUSES = {"PASS-THROUGH", "SKIP", "SKIPPED", "PREREQUISITE_REQUIRED"}



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "LOOP_TEST_01_FINAL_STATUS_AUDIT", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], 0]})

class LoopOutputTest01FinalStatusAudit(Component):
    display_name = "Loop Output Test 01 Final Status Audit"
    description = "Audits final INFO statuses and latest logs so prior FAIL logs do not override a later PASS."
    name = "LoopOutputTest01FinalStatusAudit"
    icon = "ClipboardCheck"

    inputs = [
        DataInput(name="loop_done", display_name="Loop Done", required=False),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="max_log_rows", display_name="Max Log Rows", value=500, required=False),
    ]

    outputs = [
        Output(display_name="Audit Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Audit Data", name="audit_data", method="build_data", types=["Data"]),
    ]

    def build_message(self) -> Message:
        audit = self._build()
        self.status = audit
        return Message(text=audit["answer_text"])

    def build_data(self) -> Data:
        audit = self._build()
        self.status = audit
        return Data(data=audit)

    def _build(self) -> dict[str, Any]:
        _workflow_log("_BUILD", "START", "before _build")
        cached = getattr(self, "_cached_audit", None)
        if cached is not None:
            return cached
        payload = self._parse_payload(getattr(self, "loop_done", ""))
        self._db_config = self._db_config_from_inputs(payload)
        self._require_db_config(self._db_config)

        info_rows = self._collect_info_rows()
        log_rows = self._collect_latest_logs()
        summary = self._summarize(info_rows)
        audit = {
            "component": "LoopOutputTest01FinalStatusAudit",
            "ok": summary["fail"] == 0,
            "summary": summary,
            "info_rows": info_rows,
            "latest_log_rows": log_rows,
            "answer_text": self._render(summary, info_rows, log_rows),
        }
        self._cached_audit = audit
        _workflow_log("_BUILD", "END", "after _build")
        return audit

    def _collect_info_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        rows.extend(self._query_mig_info())
        rows.extend(self._query_sql_info())
        return rows

    def _query_mig_info(self) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_MIG_INFO")
        columns = self._available_columns("NEXT_MIG_INFO")
        if not {"STATUS"}.issubset(columns):
            return []
        use_expr = "USE_YN" if "USE_YN" in columns else "'Y'"
        map_expr = "MAP_ID" if "MAP_ID" in columns else "CAST(NULL AS VARCHAR2(100))"
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT {map_expr}, STATUS
                  FROM {table}
                 WHERE UPPER(TRIM(NVL({use_expr}, 'Y'))) = 'Y'
                """
            )
            fetched = cur.fetchall()
        return [
            self._final_row(
                domain="DB_MIGRATION",
                job_key=f"MIG:{self._to_text(row[0])}",
                identifier=f"map_id={self._to_text(row[0]) or '-'}",
                status=row[1],
            )
            for row in fetched
        ]

    def _query_sql_info(self) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        if not columns:
            return []
        select_items = [
            self._select_expr(columns, "SPACE_NM"),
            self._select_expr(columns, "SQL_ID"),
            self._select_expr(columns, "STATUS_CONVERSION"),
            self._select_expr(columns, "STATUS_TUNING"),
            self._formatted_expr(columns),
        ]
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {', '.join(select_items)} FROM {table}")
            fetched = cur.fetchall()

        rows: list[dict[str, Any]] = []
        for row in fetched:
            space_nm = self._to_text(row[0])
            sql_id = self._to_text(row[1])
            ident = f"space_nm={space_nm or '-'}, sql_id={sql_id or '-'}"
            key = f"SQL:{space_nm}:{sql_id}"
            rows.append(self._final_row("SQL_CONVERSION", f"{key}:CONVERSION", ident, row[2]))
            rows.append(self._final_row("SQL_TUNING", f"{key}:TUNING", ident, row[3]))
            formatted_status = "FORMATTED" if self._num(row[4]) > 0 else ""
            rows.append(self._final_row("SQL_FORMATTING", f"{key}:FORMATTING", ident, formatted_status))
        return rows

    def _collect_latest_logs(self) -> list[dict[str, Any]]:
        logs = self._query_mig_logs() + self._query_sql_logs()
        latest: dict[str, dict[str, Any]] = {}
        for log in logs:
            key = str(log.get("job_key") or "")
            if not key:
                continue
            current = latest.get(key)
            if current is None or self._is_newer(log, current):
                latest[key] = log
        return sorted(latest.values(), key=lambda item: (str(item.get("domain") or ""), str(item.get("job_key") or "")))

    def _query_mig_logs(self) -> list[dict[str, Any]]:
        columns = self._available_columns("NEXT_MIG_LOG")
        if not {"STATUS"}.issubset(columns):
            return []
        table = self._qualify("NEXT_MIG_LOG")
        select_items = [
            self._select_expr(columns, "CREATED_AT"),
            self._select_expr(columns, "MAP_ID"),
            self._select_expr(columns, "STATUS"),
            self._select_expr(columns, "STEP_NAME"),
            self._select_expr(columns, "RETRY_COUNT"),
        ]
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {', '.join(select_items)} FROM {table} ORDER BY 1 DESC FETCH FIRST {self._max_log_rows()} ROWS ONLY")
            rows = cur.fetchall()
        return [
            {
                "domain": "DB_MIGRATION",
                "job_key": f"MIG:{self._to_text(row[1])}",
                "created_at": self._to_text(row[0]),
                "identifier": f"map_id={self._to_text(row[1]) or '-'}",
                "status": self._normalize_status(row[2]),
                "stage": self._to_text(row[3]),
                "retry": self._num(row[4]),
                "class": self._status_class(row[2]),
            }
            for row in rows
        ]

    def _query_sql_logs(self) -> list[dict[str, Any]]:
        columns = self._available_columns("NEXT_MIG_LOG")
        if not {"STATUS", "MIG_KIND"}.issubset(columns):
            return []
        table = self._qualify("NEXT_MIG_LOG")
        select_items = [
            self._select_expr(columns, "CREATED_AT"),
            self._select_expr(columns, "MAP_ID"),
            self._select_expr(columns, "MIG_KIND"),
            self._select_expr(columns, "LOG_TYPE"),
            self._select_expr(columns, "STATUS"),
            self._select_expr(columns, "STEP_NAME"),
            self._select_expr(columns, "RETRY_COUNT"),
        ]
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT {', '.join(select_items)}
                  FROM {table}
                 WHERE UPPER(TRIM(NVL(MIG_KIND, ''))) IN ('SQL_CONVERSION', 'SQL_TUNING', 'SQL_FORMATTING')
                 ORDER BY 1 DESC FETCH FIRST {self._max_log_rows()} ROWS ONLY
                """
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            domain = self._to_text(row[2]) or self._sql_domain(row[3], row[5])
            sql_id, space_nm = self._split_sql_map_id(row[1])
            out.append(
                {
                    "domain": domain,
                    "job_key": f"{domain}:{space_nm}:{sql_id}",
                    "created_at": self._to_text(row[0]),
                    "identifier": f"space_nm={space_nm or '-'}, sql_id={sql_id or '-'}",
                    "status": self._normalize_status(row[4]),
                    "stage": self._to_text(row[5]),
                    "retry": self._num(row[6]),
                    "class": self._status_class(row[4]),
                }
            )
        return out

    def _split_sql_map_id(self, value: Any) -> tuple[str, str]:
        text = self._to_text(value)
        if " / " not in text:
            return text, ""
        sql_id, space_nm = text.split(" / ", 1)
        return sql_id.strip(), space_nm.strip()

    def _final_row(self, domain: str, job_key: str, identifier: str, status: Any) -> dict[str, Any]:
        normalized = self._normalize_status(status)
        return {
            "domain": domain,
            "job_key": job_key,
            "identifier": identifier,
            "status": normalized,
            "class": self._status_class(normalized),
        }

    def _summarize(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        summary = {"total": len(rows), "success": 0, "fail": 0, "pending": 0, "skipped": 0, "unknown": 0}
        for row in rows:
            cls = str(row.get("class") or "unknown")
            summary[cls] = summary.get(cls, 0) + 1
        return summary

    def _status_class(self, status: Any) -> str:
        value = self._normalize_status(status)
        if not value:
            return "pending"
        if value in SUCCESS_STATUSES or value.startswith("PASS-") or value.startswith("SUCCESS-"):
            return "success"
        if value in {"FAIL", "FAILED"} or value.startswith("FAIL-"):
            return "fail"
        if value in SKIP_STATUSES or value.startswith("SKIP-"):
            return "skipped"
        return "unknown"

    def _render(self, summary: dict[str, int], info_rows: list[dict[str, Any]], log_rows: list[dict[str, Any]]) -> str:
        lines = [
            "## 최종 상태 감사",
            "",
            f"- 대상: {summary.get('total', 0)}건",
            f"- 성공: {summary.get('success', 0)}건",
            f"- 실패: {summary.get('fail', 0)}건",
            f"- 대기/미실행: {summary.get('pending', 0)}건",
            f"- 스킵: {summary.get('skipped', 0)}건",
            f"- 미분류: {summary.get('unknown', 0)}건",
            "",
            "### 실패 또는 미분류 대상",
        ]
        suspicious = [row for row in info_rows if row.get("class") in {"fail", "unknown"}]
        if not suspicious:
            lines.append("- 최종 INFO 상태 기준 실패 또는 미분류 대상이 없습니다.")
        else:
            for row in suspicious[:30]:
                lines.append(f"- {row.get('domain')} {row.get('identifier')}: {row.get('status') or 'NULL'} ({row.get('class')})")
        lines.extend(["", "### 최신 로그 샘플"])
        if not log_rows:
            lines.append("- 로그를 조회하지 못했거나 로그 테이블에 상태 컬럼이 없습니다.")
        else:
            for row in log_rows[:30]:
                lines.append(f"- {row.get('domain')} {row.get('identifier')}: {row.get('status') or 'NULL'} / {row.get('stage') or '-'} ({row.get('class')})")
        return "\n".join(lines)

    def _is_newer(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_retry = self._num(left.get("retry"))
        right_retry = self._num(right.get("retry"))
        if left_retry != right_retry:
            return left_retry > right_retry
        return str(left.get("created_at") or "") > str(right.get("created_at") or "")

    def _sql_domain(self, sql_kind: Any, stage_name: Any) -> str:
        text = f"{sql_kind or ''} {stage_name or ''}".upper()
        if "FORMAT" in text:
            return "SQL_FORMATTING"
        if "TUN" in text:
            return "SQL_TUNING"
        return "SQL_CONVERSION"

    def _available_columns(self, table_name: str) -> set[str]:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                if schema:
                    cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2", [schema, table])
                else:
                    cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [table])
                return {str(row[0]).upper() for row in cur.fetchall()}
        except Exception:
            return set()

    def _select_expr(self, columns: set[str], column: str) -> str:
        clean = self._clean_identifier(column)
        return clean if clean in columns else f"CAST(NULL AS VARCHAR2(4000)) AS {clean}"

    def _formatted_expr(self, columns: set[str]) -> str:
        if "FORMATTED_SQL" in columns:
            return "CASE WHEN FORMATTED_SQL IS NOT NULL THEN 1 ELSE 0 END AS FORMATTED_PRESENT"
        return "0 AS FORMATTED_PRESENT"

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

    def _db_config_from_inputs(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(getattr(self, "db_host", "") or payload_config.get("db_host") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or payload_config.get("db_port") or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or payload_config.get("db_service_name") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or payload_config.get("db_username") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)) or str(payload_config.get("db_password") or ""),
            "system_schema": str(getattr(self, "system_schema", "") or payload_config.get("system_schema") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Final status audit requires database settings: missing {', '.join(missing)}")

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        return f"{self._clean_identifier(schema)}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

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
        return parsed if isinstance(parsed, dict) else {}

    def _normalize_status(self, value: Any) -> str:
        return str(value or "").strip().upper()

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _max_log_rows(self) -> int:
        try:
            return max(1, min(5000, int(getattr(self, "max_log_rows", None) or 500)))
        except (TypeError, ValueError):
            return 500
