from __future__ import annotations

import logging
import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType17ASqlFormattingJobsToLoopTable(Component):

    display_name = "17A SQL Formatting Jobs To Loop Table"
    description = "Converts selected SQL Formatting jobs into Loop rows."
    name = "NewType17ASqlFormattingJobsToLoopTable"
    icon = "Table"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def build_jobs_table(self) -> DataFrame:
        logging.getLogger("smartmigrate.workflow").info("before build_jobs_table", extra={"workflow_log": [0, "WORKFLOW", "17A_SQL_JOBS", "INFO", "BUILD_JOBS_TABLE", "START", 0]})
        try:
            """Build one Loop row per SQL formatting job."""
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            jobs = self._sql_jobs(payload, db_config)
            total = len(jobs)
            rows: list[dict[str, Any]] = []
            for index, job in enumerate(jobs, start=1):
                self._validate_sql_key(job, index)
                rows.append(
                    {
                        **job,
                        "component": "17A_sqlFormattingJobsToLoopTable",
                        "job_route": "SQL_FORMATTING",
                        "job_type": "SQL",
                        "run_mode": payload.get("run_mode") or "targeted",
                        "job_index": index,
                        "total_jobs": total,
                        "completed_before": index - 1,
                        "db_config": db_config,
                        "history": list(payload.get("history") or []),
                    }
                )
            self.status = {**payload, "component": "17A_sqlFormattingJobsToLoopTable", "loop_job_count": total, "next_node": "17B_sqlFormattingLoop"}
            __log_result = DataFrame(rows)
            logging.getLogger("smartmigrate.workflow").info("after build_jobs_table", extra={"workflow_log": [0, "WORKFLOW", "17A_SQL_JOBS", "INFO", "BUILD_JOBS_TABLE", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error build_jobs_table: {exc}", extra={"workflow_log": [0, "WORKFLOW", "17A_SQL_JOBS", "ERROR", "BUILD_JOBS_TABLE", "ERROR", 0]})
            raise

    # payload 또는 DB에서 현재 SQL 단계 loop에 넣을 NEXT_SQL_INFO job 목록을 만든다.
    def _sql_jobs(self, payload: dict[str, Any], db_config: dict[str, Any]) -> list[dict[str, Any]]:
        """Return only SQL formatting jobs from the routed payload."""
        requested = payload.get("requested_jobs") if isinstance(payload.get("requested_jobs"), dict) else {}
        jobs = payload.get("selected_jobs") or requested.get("sql_formatting_jobs") or payload.get("planned_jobs") or []
        if self._should_load_all_pending(payload, jobs):
            return self._load_all_pending_jobs(db_config)
        out: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            route = str(job.get("job_route") or "SQL_FORMATTING").upper()
            if route == "SQL_FORMATTING":
                out.append(dict(job))
        return out

    # 현재 payload 상태에서 이 단계가 실행되어야 하는지 판단한다.
    def _should_load_all_pending(self, payload: dict[str, Any], jobs: Any) -> bool:
        if str(payload.get("run_mode") or "").lower() != "all_pending":
            return False
        return not isinstance(jobs, list) or not jobs

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_all_pending_jobs(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_SQL_INFO", db_config)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                  FROM {table}
                 WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                 ORDER BY PRIORITY ASC NULLS LAST, UPD_TS ASC NULLS FIRST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """
            )
            return [
                {
                    "job_route": "SQL_FORMATTING",
                    "job_type": "SQL",
                    "space_nm": self._json_value(row[0]),
                    "sql_id": self._json_value(row[1]),
                    "priority": self._json_value(row[2]),
                }
                for row in cur.fetchall()
            ]

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_sql_key(self, job: dict[str, Any], index: int) -> None:
        """Require the logical SQL key used by NEXT_SQL_INFO."""
        if str(job.get("space_nm") or "").strip() and str(job.get("sql_id") or "").strip():
            return
        raise ValueError(f"17A SQL Formatting job row {index} requires space_nm+sql_id")

    # payload와 Langflow 입력에서 Oracle 접속 및 schema 설정을 모은다.
    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Collect DB connection settings for downstream Loop items."""
        payload_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(payload_config.get("db_host") or getattr(self, "db_host", "") or "").strip(),
            "db_port": int(payload_config.get("db_port") or getattr(self, "db_port", None) or 1521),
            "db_service_name": str(payload_config.get("db_service_name") or getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(payload_config.get("db_username") or getattr(self, "db_username", "") or "").strip(),
            "db_password": str(payload_config.get("db_password") or "") or self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(payload_config.get("system_schema") or getattr(self, "system_schema", "") or "").strip(),
        }

    # 필수 DB 접속 값이 없으면 DB 작업 전에 명확히 실패시킨다.
    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        """Fail early when SQL Formatting is not wired to database settings."""
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"17A SQL Formatting is not connected to database settings: missing {', '.join(missing)}")

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
    def _connect(self, db_config: dict[str, Any]):
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(db_config.get("db_username") or "").strip(),
            password=str(db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str, db_config: dict[str, Any]) -> str:
        table = self._clean_identifier(table_name)
        schema = str(db_config.get("system_schema") or "").strip().upper()
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
        """Parse a Langflow Data, dict, or JSON string payload."""
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

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        """Convert a Langflow secret value into a plain string."""
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    # payload나 로그에 넣을 값을 JSON 직렬화 가능한 형태로 정리한다.
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)
