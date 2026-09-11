from __future__ import annotations

import json
import logging
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


ROUTE_LABELS = {
    "MIG": "DB Migration",
    "SQL_CONVERSION": "SQL Conversion",
    "SQL_TUNING": "SQL Tuning",
    "SQL_FORMATTING": "SQL Formatting",
}

TERMINAL_STATUSES = {
    "PASS",
    "SUCCESS",
    "PASS-CONVERSION",
    "PASS-TUNING",
    "FORMATTED",
    "FAIL",
    "FAIL-TOBE",
    "FAIL-BIND",
    "FAIL-TEST",
    "FAIL-TUNED",
    "FAIL-FORMATTING",
    "FAIL-TRUNCATE",
    "FAIL-INSERT",
    "ERROR",
    "SKIP",
    "SKIPPED",
    "END",
}


class NewType04CurrentProgress(Component):

    display_name = "04 Current Progress"
    description = "Shows currently running SmartMigrate jobs and the five most recent migration logs."
    name = "NewType04CurrentProgress"
    icon = "Activity"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]

    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def run(self) -> Message:
        logging.getLogger("smartmigrate.workflow").info("before run", extra={"workflow_log": [0, "WORKFLOW", "04_CURRENT_PROGRESS", "INFO", "RUN", "START", 0]})
        try:
            try:
                payload = self._parse_payload(getattr(self, "payload_json", ""))
                progress = self._query_progress()
                answer = self._build_answer(progress)
                self.status = {
                    **payload,
                    "component": "04_currentProgress",
                    "current_progress": progress,
                    "answer_text": answer,
                    "final": True,
                }
                result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").info("after run", extra={"workflow_log": [0, "WORKFLOW", "04_CURRENT_PROGRESS", "INFO", "RUN", "END", 0]})
                return result
            except Exception as exc:
                answer = f"[Current Progress]\nProgress lookup failed.\nError: {exc}"
                self.status = {"ok": False, "component": "04_currentProgress", "error": str(exc), "answer_text": answer}
                logging.getLogger("smartmigrate.workflow").error("error run", extra={"workflow_log": [0, "WORKFLOW", "04_CURRENT_PROGRESS", "ERROR", "RUN", "ERROR", 0]})
                return Message(text=answer)
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error run: {exc}", extra={"workflow_log": [0, "WORKFLOW", "04_CURRENT_PROGRESS", "ERROR", "RUN", "ERROR", 0]})
            raise

    # 조회 조건을 조립해 DB에서 요청된 정보를 가져온다.
    def _query_progress(self) -> dict[str, Any]:
        if not self._has_db_config():
            raise ValueError("DB connection settings are required for 04 Current Progress")
        with self._connect() as conn:
            remaining = self._load_remaining_counts(conn)
            running_jobs = self._load_running_status_jobs(conn)
            recent_logs = self._load_recent_logs(conn)
        active_jobs = self._merge_active_jobs(running_jobs)
        recent_log_age_seconds = self._recent_log_age_seconds(recent_logs)
        return {
            "ok": True,
            "active_count": len(active_jobs),
            "active_jobs": active_jobs,
            "remaining_summary": remaining,
            "recent_logs": recent_logs,
            "recent_log_age_seconds": recent_log_age_seconds,
            "recent_activity_possible": bool(not active_jobs and recent_log_age_seconds is not None and recent_log_age_seconds <= 600),
        }

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_remaining_counts(self, conn: Any) -> dict[str, int]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        cur = conn.cursor()
        counts = {"MIG": 0, "SQL_CONVERSION": 0, "SQL_TUNING": 0, "SQL_FORMATTING": 0}

        counts["MIG"] = self._count(
            cur,
            f"""
            SELECT COUNT(*)
              FROM {mig_table}
             WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
               AND (STATUS IS NULL OR ({self._user_edited_expr()} AND UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'))
            """,
        )
        counts["SQL_CONVERSION"] = self._count(
            cur,
            f"""
            SELECT COUNT(*)
              FROM {sql_table}
             WHERE STATUS_CONVERSION IS NULL
                OR ({self._user_edited_expr()} AND UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%')
            """,
        )
        counts["SQL_TUNING"] = self._count(
            cur,
            f"""
            SELECT COUNT(*)
              FROM {sql_table}
             WHERE UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
               AND (STATUS_TUNING IS NULL OR ({self._user_edited_expr()} AND UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%'))
            """,
        )
        counts["SQL_FORMATTING"] = self._count(
            cur,
            f"""
            SELECT COUNT(*)
              FROM {sql_table}
             WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
               AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
            """,
        )
        counts["TOTAL"] = sum(counts.values())
        return counts

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_running_status_jobs(self, conn: Any) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        jobs.extend(self._load_running_migration_jobs(conn))
        jobs.extend(self._load_running_sql_jobs(conn, "SQL_CONVERSION", "STATUS_CONVERSION"))
        jobs.extend(self._load_running_sql_jobs(conn, "SQL_TUNING", "STATUS_TUNING"))
        return jobs

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_running_migration_jobs(self, conn: Any) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_MIG_INFO")
        select_sql = ", ".join(
            [
                "TO_CHAR(MAP_ID) AS JOB_ID",
                "TO_CHAR(FR_TABLE) AS FR_TABLE",
                "TO_CHAR(TO_TABLE) AS TO_TABLE",
                "TO_CHAR(PRIORITY) AS PRIORITY",
                "TO_CHAR(STATUS) AS STATUS_VALUE",
            ]
        )
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {select_sql}
              FROM {table}
             WHERE UPPER(TRIM(NVL(STATUS, ''))) LIKE 'RUNNING%'
             ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
            """
        )
        rows = self._rows(cur)
        return [
            {
                "route": "MIG",
                "label": ROUTE_LABELS["MIG"],
                "job_id": row.get("job_id"),
                "status": row.get("status_value") or "RUNNING",
                "stage": "STATUS",
                "source": "STATUS_RUNNING",
                "detail": self._join_detail([row.get("fr_table"), row.get("to_table")]),
                "last_log_age_seconds": None,
                "message": "",
            }
            for row in rows
        ]

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_running_sql_jobs(self, conn: Any, route: str, status_column: str) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_SQL_INFO")
        select_sql = ", ".join(
            [
                "TO_CHAR(SPACE_NM) AS SPACE_NM",
                "TO_CHAR(SQL_ID) AS SQL_ID",
                "TO_CHAR(PRIORITY) AS PRIORITY",
                f"TO_CHAR({status_column}) AS STATUS_VALUE",
            ]
        )
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {select_sql}
              FROM {table}
             WHERE UPPER(TRIM(NVL({status_column}, ''))) LIKE 'RUNNING%'
             ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
            """
        )
        rows = self._rows(cur)
        return [
            {
                "route": route,
                "label": ROUTE_LABELS[route],
                "job_id": self._join_detail([row.get("sql_id"), row.get("space_nm")]),
                "space_nm": row.get("space_nm"),
                "sql_id": row.get("sql_id"),
                "status": row.get("status_value") or "RUNNING",
                "stage": status_column,
                "source": "STATUS_RUNNING",
                "detail": self._join_detail([row.get("space_nm"), row.get("sql_id")]),
                "last_log_age_seconds": None,
                "message": "",
            }
            for row in rows
        ]

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_recent_logs(self, conn: Any) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_MIG_LOG")
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT CREATED_AT,
                   MIG_KIND,
                   STEP_NAME,
                   MAP_ID,
                   AGE_SECONDS
              FROM (
                    SELECT TO_CHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT,
                           TO_CHAR(MIG_KIND) AS MIG_KIND,
                           TO_CHAR(STEP_NAME) AS STEP_NAME,
                           TO_CHAR(MAP_ID) AS MAP_ID,
                           ROUND((SYSDATE - CAST(CREATED_AT AS DATE)) * 86400) AS AGE_SECONDS
                     FROM {table}
                    WHERE UPPER(TRIM(NVL(MIG_KIND, ''))) <> 'WORKFLOW'
                     ORDER BY LOG_ID DESC
                   )
             WHERE ROWNUM <= 5
            """
        )
        return self._rows(cur)

    # 여러 출처의 값이나 목록을 중복 없이 하나로 합친다.
    def _merge_active_jobs(self, running_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for job in running_jobs:
            key = (str(job.get("route") or ""), str(job.get("job_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(job)
        return result

    # 후속 단계나 사용자 응답에 필요한 구조화된 결과를 조립한다.
    def _build_answer(self, progress: dict[str, Any]) -> str:
        active_jobs = list(progress.get("active_jobs") or [])
        remaining = dict(progress.get("remaining_summary") or {})
        recent_logs = list(progress.get("recent_logs") or [])
        lines = ["# Current Progress"]
        lines.append("")
        if active_jobs:
            lines.append(f"진행 중인 작업: {len(active_jobs)}건")
            lines.append("")
            lines.append("| Stage | Job | Status |")
            lines.append("|---|---|---|")
            for job in active_jobs[:20]:
                lines.append(
                    "| "
                    f"{job.get('label') or job.get('route') or '-'} | "
                    f"{self._cell(job.get('job_id') or job.get('detail') or '-')} | "
                    f"{self._cell(job.get('status') or '-')} |"
                )
        else:
            if progress.get("recent_activity_possible"):
                lines.append("최근 10분 내에 진행된 작업이 있으니 프로세스가 진행 중일 가능성이 높습니다. 현재 Running 중인 작업은 없습니다.")
            else:
                lines.append("진행 중인 작업이 없습니다.")

        lines.append("")
        lines.append("## Remaining")
        lines.append("| Stage | Remaining |")
        lines.append("|---|---:|")
        for route in ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"):
            lines.append(f"| {ROUTE_LABELS[route]} | {int(remaining.get(route) or 0)} |")
        lines.append(f"| Total | {int(remaining.get('TOTAL') or 0)} |")

        lines.extend(["", "## 최근 로그입니다."])
        if recent_logs:
            lines.append("| Created At | Mig Kind | Step Name | Map ID |")
            lines.append("|---|---|---|---|")
            for log in recent_logs:
                lines.append(
                    "| "
                    f"{self._cell(log.get('created_at'))} | "
                    f"{self._cell(log.get('mig_kind'))} | "
                    f"{self._cell(log.get('step_name'))} | "
                    f"{self._cell(log.get('map_id'))} |"
                )
        else:
            lines.append("최근 로그가 없습니다.")
        return "\n".join(lines)

    # 최근 workflow 로그 시각과 현재 시각 차이를 초 단위로 계산한다.
    def _recent_log_age_seconds(self, recent_logs: list[dict[str, Any]]) -> int | None:
        if not recent_logs:
            return None
        value = self._to_int((recent_logs[0] or {}).get("age_seconds"))
        if value is None:
            return None
        return abs(value)

    # 전달된 SQL/조건으로 단일 COUNT 값을 조회한다.
    def _count(self, cur: Any, sql: str) -> int:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0

    # cursor 결과를 후속 payload에서 쓰기 쉬운 dict row 목록으로 변환한다.
    def _rows(self, cur: Any) -> list[dict[str, Any]]:
        columns = [str(item[0]).lower() for item in cur.description]
        result = []
        for row in cur.fetchall():
            result.append({columns[index]: self._json_value(row[index]) for index in range(len(columns))})
        return result

    # USER_EDITED=Y인 실패 row를 재실행 대상으로 판단하는 SQL 조건을 만든다.
    def _user_edited_expr(self) -> str:
        return "UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y'"

    # NEXT_MIG_LOG.MIG_KIND 값을 화면 표시용 route 이름으로 변환한다.
    def _route_from_mig_kind(self, value: Any) -> str:
        text = self._norm(value)
        if text == "DB_MIGRATION":
            return "MIG"
        if text in ROUTE_LABELS:
            return text
        if "TUNING" in text:
            return "SQL_TUNING"
        if "FORMATTING" in text:
            return "SQL_FORMATTING"
        if "CONVERSION" in text:
            return "SQL_CONVERSION"
        return text or "UNKNOWN"

    # 상태나 값이 특정 조건에 해당하는지 boolean으로 판단한다.
    def _is_terminal_status(self, status: str) -> bool:
        text = self._norm(status)
        return text in TERMINAL_STATUSES or text.startswith("FAIL-") or text.startswith("PASS-")

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
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

    # 필수 DB 접속 값이 없으면 DB 작업 전에 명확히 실패시킨다.
    def _has_db_config(self) -> bool:
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        return f"{schema}.{table}"

    # 동적 SQL identifier에 안전한 Oracle 문자만 허용한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
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

    # payload나 로그에 넣을 값을 JSON 직렬화 가능한 형태로 정리한다.
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    # 상태 비교가 흔들리지 않도록 문자열을 대문자 기준으로 정규화한다.
    def _norm(self, value: Any) -> str:
        return str(value or "").strip().upper()

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _to_int(self, value: Any) -> int | None:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    # 여러 상세 값을 빈 값 없이 결합해 표시용 문자열을 만든다.
    def _join_detail(self, values: list[Any]) -> str:
        return " / ".join(str(value).strip() for value in values if str(value or "").strip())

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _short(self, value: Any, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _age(self, seconds: Any) -> str:
        value = self._to_int(seconds)
        if value is None:
            return "-"
        if value < 60:
            return f"{value}s"
        return f"{round(value / 60, 1)}m"

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _cell(self, value: Any) -> str:
        text = str(value or "").replace("|", "\\|").replace("\n", " ").strip()
        return text or "-"
