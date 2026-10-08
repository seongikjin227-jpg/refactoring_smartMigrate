from __future__ import annotations

import logging
import json
import re
import traceback
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType06GetRemainingJobs(Component):

    display_name = "06 Get Remaining Jobs"
    description = "Loads dashboard-like remaining counts plus exact target status when requested."
    name = "NewType06GetRemainingJobs"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="get_remaining_jobs")]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def get_remaining_jobs(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "START", 0]})
        payload: dict[str, Any] = {}
        try:
            try:
                payload = self._parse_payload(getattr(self, "payload_json", ""))
                self._log_detail("06_INPUT_PAYLOAD", "RECEIVE", "PASS", "06 received payload", payload)
                if not payload.get("should_execute", True) or payload.get("clarification_required", False):
                    payload.update({"component": "06_getRemainingJobs", "next_node": "chat_output", "final": True})
                    self._log_detail("06_FINAL_OUTPUT", "SKIP", "PASS", "06 skipped: execution intent is not confirmed", payload)
                    __log_result = Data(data=payload)
                    logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
                    return __log_result

                if not self._has_db_config():
                    raise ValueError("DB connection settings are required for 06 Get Remaining Jobs")

                # 02 LLM이 확정한 식별자를 검증한다. 원문을 다시 해석하지 않는다.
                targets = self._validated_targets(payload)
                self._log_detail("06_TARGET_FILTER", "VALIDATE_TARGETS", "PASS", "06 validated 02 targets", {
                    "effective_user_request": self._effective_user_request(payload),
                    "input_target_filter": payload.get("target_filter"),
                    "validated_target_filter": targets,
                    "has_exact_target": self._has_exact_target(targets),
                })
                self._validate_sql_target_identity(targets)
                with self._connect() as conn:
                    counts = self._load_counts(conn)
                    requested_jobs = self._empty_requested_jobs()
                    target_statuses = self._load_target_statuses(conn, targets)
                    if self._has_exact_target(targets):
                        requested_jobs = self._load_target_jobs(conn, targets)

                summary = {
                    "total": sum(counts[key] for key in ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")),
                    "migration_total": counts["MIG"],
                    "migration_incomplete_total": counts["MIG_INCOMPLETE"],
                    "sql_conversion_total": counts["SQL_CONVERSION"],
                    "sql_tuning_total": counts["SQL_TUNING"],
                    "sql_formatting_total": counts["SQL_FORMATTING"],
                }
                payload.update(
                    {
                        "component": "06_getRemainingJobs",
                        "effective_user_request": self._effective_user_request(payload),
                        "job_availability": summary,
                        "requested_jobs": requested_jobs,
                        "requested_target_status": target_statuses,
                        "remaining_summary": summary,
                        "pending_summary": summary,
                        "target_filter": targets,
                        "query_target_filter": targets,
                        "target_query_executed": self._has_exact_target(targets),
                        "job_detail_mode": "requested_jobs" if self._has_exact_target(targets) else "counts_only",
                        "next_node": "08_jobExecutionRouter",
                    }
                )
                payload.setdefault("history", []).append(
                    {
                        "step": "get_remaining_jobs",
                        "message": (
                            f"total={summary['total']}, mig={summary['migration_total']}, "
                            f"sql_conversion={summary['sql_conversion_total']}, detail={payload['job_detail_mode']}"
                        ),
                    }
                )
                self.status = payload
                self._log_detail("06_FINAL_OUTPUT", "SEND_08", "PASS", "06 payload sent to 08", payload)
                __log_result = Data(data=payload)
                logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
                return __log_result
            except Exception as exc:
                answer = (
                    "실행 대상의 현재 상태와 실행 가능 여부를 확인하는 중 문제가 발생했습니다. 조회 결과를 확인하지 못했으므로 작업이 없다고 판단할 수 없습니다."
                    "\n\nMigration은 MAP_ID, SQL 작업은 도메인과 SQL_SEQ 또는 SQL_ID + SPACE_NM을 함께 알려주세요. "
                    "전체 실행이라면 전체 workflow인지 도메인 전체인지 명시해 주세요."
                    "\n예: '마이그레이션 59번의 상태, USE_YN, RETRY_COUNT와 최근 로그를 보여줘', "
                    "'SQL Conversion 순번 42의 상태와 최근 로그를 보여줘'."
                    "\n\n대상 정보가 맞는데 문제가 반복되면 운영자에게 DB 연결·조회 권한·라우터 전달값을 확인해 달라고 요청해 주세요. "
                    "연결이 복구된 뒤 현재 상태를 먼저 확인하고 실행 여부를 결정해 주세요."
                )
                result = {**payload, "ok": False, "component": "06_getRemainingJobs", "error": str(exc),
                          "answer_text": answer, "exception_message": answer, "should_execute": False,
                          "next_node": "chat_output", "final": True}
                self._log_detail("06_GET_JOBS", "GET_REMAINING_JOBS", "ERROR", f"06 failed: {exc}", {
                    "error": str(exc), "traceback": traceback.format_exc(),
                })
                self.status = result
                __log_result = Data(data=result)
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error get_remaining_jobs: {exc}", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "ERROR", "GET_REMAINING_JOBS", "ERROR", 0]})
            raise

    def _log_detail(self, log_type: str, step: str, status: str, message: str, detail: Any) -> None:
        level = "ERROR" if status == "ERROR" else "INFO"
        logging.getLogger("smartmigrate.workflow").log(
            logging.ERROR if level == "ERROR" else logging.INFO,
            message,
            extra={"workflow_log": [0, "WORKFLOW", log_type, level, step, status, 0,
                                    json.dumps(detail, ensure_ascii=False, default=str)]},
        )

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_counts(self, conn: Any) -> dict[str, int]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        queries = {
            "MIG_INCOMPLETE": f"""
                SELECT COUNT(*)
                  FROM {mig_table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR UPPER(TRIM(STATUS)) <> 'PASS')
            """,
            "MIG": f"""
                SELECT COUNT(*)
                  FROM {mig_table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR UPPER(TRIM(NVL(STATUS, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
            """,
            "SQL_CONVERSION": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE (STATUS_CONVERSION IS NULL OR UPPER(TRIM(NVL(STATUS_CONVERSION, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
            """,
            "SQL_TUNING": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                   AND (STATUS_TUNING IS NULL OR UPPER(TRIM(NVL(STATUS_TUNING, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
            """,
            "SQL_FORMATTING": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
            """,
        }
        cur = conn.cursor()
        return {route: self._scalar_count(cur, sql) for route, sql in queries.items()}

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_target_jobs(self, conn: Any, targets: dict[str, list[Any]]) -> dict[str, list[dict[str, Any]]]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        cur = conn.cursor()
        migration_jobs: list[dict[str, Any]] = []
        sql_conversion_jobs: list[dict[str, Any]] = []
        sql_tuning_jobs: list[dict[str, Any]] = []
        sql_formatting_jobs: list[dict[str, Any]] = []

        map_ids = [item for item in (self._to_int(v) for v in targets.get("map_ids", [])) if item is not None]
        if map_ids:
            placeholders = ", ".join(f":{index + 1}" for index in range(len(map_ids)))
            migration_jobs = self._query_jobs(
                cur,
                f"""
                SELECT MAP_ID, PRIORITY, PRIOR_MAP_ID
                  FROM {mig_table}
                 WHERE MAP_ID IN ({placeholders})
                   AND UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR UPPER(TRIM(NVL(STATUS, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                """,
                map_ids,
                "MIG",
                ["map_id", "priority", "prior_map_id"],
            )

        sql_where, sql_params = self._sql_target_where(targets)
        if sql_where:
            sql_conversion_jobs = self._query_jobs(
                cur,
                f"""
                SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND (STATUS_CONVERSION IS NULL OR UPPER(TRIM(NVL(STATUS_CONVERSION, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_CONVERSION",
                ["sql_seq", "sql_id", "space_nm", "priority"],
            )
            sql_tuning_jobs = self._query_jobs(
                cur,
                f"""
                SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                   AND (STATUS_TUNING IS NULL OR UPPER(TRIM(NVL(STATUS_TUNING, ''))) LIKE 'FAIL-%')
                   AND NVL(RETRY_COUNT, 0) < 2
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_TUNING",
                ["sql_seq", "sql_id", "space_nm", "priority"],
            )
            sql_formatting_jobs = self._query_jobs(
                cur,
                f"""
                SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_FORMATTING",
                ["sql_seq", "sql_id", "space_nm", "priority"],
            )

        all_jobs = [*migration_jobs, *sql_conversion_jobs, *sql_tuning_jobs, *sql_formatting_jobs]
        return {
            "all_jobs": all_jobs,
            "job_lookup_jobs": all_jobs,
            "migration_jobs": migration_jobs,
            "sql_conversion_jobs": sql_conversion_jobs,
            "sql_jobs": sql_conversion_jobs,
            "sql_tuning_jobs": sql_tuning_jobs,
            "sql_formatting_jobs": sql_formatting_jobs,
        }

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_target_statuses(self, conn: Any, targets: dict[str, list[Any]]) -> dict[str, list[dict[str, Any]]]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        cur = conn.cursor()
        statuses = {"migration": [], "sql": []}

        map_ids = [item for item in (self._to_int(v) for v in targets.get("map_ids", [])) if item is not None]
        if map_ids:
            placeholders = ", ".join(f":{index + 1}" for index in range(len(map_ids)))
            statuses["migration"] = self._query_statuses(
                cur,
                f"""
                SELECT MAP_ID, STATUS, USER_EDITED, PRIOR_MAP_ID, USE_YN, PRIORITY, RETRY_COUNT
                  FROM {mig_table}
                 WHERE MAP_ID IN ({placeholders})
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                """,
                map_ids,
                ["map_id", "status", "user_edited", "prior_map_id", "use_yn", "priority", "retry_count"],
            )

        sql_where, sql_params = self._sql_target_where(targets)
        if sql_where:
            statuses["sql"] = self._query_statuses(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID,
                       STATUS_CONVERSION,
                       STATUS_TUNING,
                       USER_EDITED,
                       PRIORITY,
                       SQL_SEQ,
                       RETRY_COUNT,
                       NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) AS FORMATTED_SQL_LENGTH
                  FROM {sql_table}
                 WHERE {sql_where}
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                ["space_nm", "sql_id", "status_conversion", "status_tuning", "user_edited", "priority", "sql_seq", "retry_count", "formatted_sql_length"],
            )
        return statuses

    # cursor 결과를 후속 payload에서 쓰기 쉬운 dict row 목록으로 변환한다.
    def _query_jobs(self, cur: Any, sql: str, params: list[Any], route: str, columns: list[str]) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        jobs: list[dict[str, Any]] = []
        for row in cur.fetchall():
            job = {"job_route": route, "job_type": "MIG" if route == "MIG" else "SQL"}
            for index, column in enumerate(columns):
                job[column] = self._json_value(row[index])
            jobs.append(job)
        return jobs

    # cursor 결과를 후속 payload에서 쓰기 쉬운 dict row 목록으로 변환한다.
    def _query_statuses(self, cur: Any, sql: str, params: list[Any], columns: list[str]) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        rows: list[dict[str, Any]] = []
        for row in cur.fetchall():
            rows.append({column: self._json_value(row[index]) for index, column in enumerate(columns)})
        return rows

    # 전달된 SQL/조건으로 단일 COUNT 값을 조회한다.
    def _scalar_count(self, cur: Any, sql: str) -> int:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0

    # 특정 target 요청이 없을 때 사용할 빈 route별 job 목록을 만든다.
    def _empty_requested_jobs(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "all_jobs": [],
            "job_lookup_jobs": [],
            "migration_jobs": [],
            "sql_conversion_jobs": [],
            "sql_jobs": [],
            "sql_tuning_jobs": [],
            "sql_formatting_jobs": [],
        }


    def _validated_targets(self, payload: dict[str, Any]) -> dict[str, list[Any]]:
        targets = self._normalize_target_filter(payload.get("target_filter", {}))
        scope = str(payload.get("execution_scope") or "unknown").lower()
        domain = str(payload.get("requested_domain") or "UNKNOWN").upper()
        has_mig = bool(targets["map_ids"])
        has_sql = any(targets[key] for key in ("sql_seqs", "sql_ids", "space_nms"))
        if scope not in {"all", "domain", "targeted"}:
            raise ValueError("02 must provide execution_scope; request interpretation is required before DB lookup")
        if domain not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "FULL_WORKFLOW"}:
            raise ValueError("02 must provide requested_domain before DB lookup")
        if (scope == "all") != (domain == "FULL_WORKFLOW"):
            raise ValueError("FULL_WORKFLOW requires all scope; use domain/targeted for individual domains")
        if has_mig and has_sql:
            raise ValueError("Migration and SQL targets must be requested separately")
        if (has_mig or has_sql) and scope != "targeted":
            raise ValueError("Nonempty target_filter requires targeted scope")
        if scope == "targeted" and not (has_mig or has_sql):
            raise ValueError("Targeted request has no identifier from 02; it cannot run all pending jobs")
        if has_mig and domain != "MIG":
            raise ValueError("map_ids require MIG domain")
        if has_sql and domain not in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            raise ValueError("SQL identifiers require a SQL domain")
        if len(targets["sql_ids"]) > 1 and len(targets["space_nms"]) > 1:
            raise ValueError("Multiple SQL_ID/SPACE_NM pairs require explicit SQL_SEQ identifiers")
        self._validate_sql_target_identity(targets)
        return targets

    @staticmethod
    def _normalize_target_filter(raw: Any) -> dict[str, list[Any]]:
        if not isinstance(raw, dict):
            raise ValueError("target_filter must be an object.")
        targets: dict[str, list[Any]] = {}
        for key in ("map_ids", "sql_seqs", "sql_ids", "space_nms"):
            values = raw.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"target_filter.{key} must be an array.")
            normalized = []
            for value in values:
                if key in {"map_ids", "sql_seqs"}:
                    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
                        raise ValueError(f"target_filter.{key} must contain positive integers.")
                    value = int(value)
                elif not isinstance(value, str) or not value.strip():
                    raise ValueError(f"target_filter.{key} must contain nonempty strings.")
                else:
                    value = value.strip()
                if value not in normalized:
                    normalized.append(value)
            targets[key] = normalized
        return targets

    def _effective_user_request(self, payload: dict[str, Any]) -> str:
        """Return the original request preserved by 02 for diagnostics only."""
        return str(
            payload.get("resolved_user_request")
            or payload.get("user_request")
            or payload.get("original_request")
            or payload.get("input")
            or ""
        ).strip()

    # 사용자가 MAP_ID 또는 SPACE_NM/SQL_ID 같은 특정 작업을 지정했는지 확인한다.
    def _has_exact_target(self, targets: dict[str, list[Any]]) -> bool:
        return bool(targets.get("map_ids") or targets.get("sql_seqs") or targets.get("sql_ids") or targets.get("space_nms"))

    def _validate_sql_target_identity(self, targets: dict[str, list[Any]]) -> None:
        if targets.get("sql_seqs"):
            return
        has_sql_id = bool(targets.get("sql_ids"))
        has_space_nm = bool(targets.get("space_nms"))
        if has_sql_id != has_space_nm:
            raise ValueError("SQL target requires sql_seq or both sql_id and space_nm")

    # SPACE_NM/SQL_ID target 조건을 SQL WHERE 절과 bind 값으로 변환한다.
    def _sql_target_where(self, targets: dict[str, list[Any]]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        sql_seqs = [item for item in (self._to_int(v) for v in targets.get("sql_seqs", [])) if item is not None]
        sql_ids = [str(v).strip() for v in targets.get("sql_ids", []) if str(v).strip()]
        space_nms = [str(v).strip() for v in targets.get("space_nms", []) if str(v).strip()]
        if sql_seqs:
            placeholders = []
            for value in sql_seqs:
                params.append(value)
                placeholders.append(f":{len(params)}")
            clauses.append(f"SQL_SEQ IN ({', '.join(placeholders)})")
        if sql_ids:
            placeholders = []
            for value in sql_ids:
                params.append(value)
                placeholders.append(f":{len(params)}")
            clauses.append(f"TO_CHAR(SQL_ID) IN ({', '.join(placeholders)})")
        if space_nms:
            placeholders = []
            for value in space_nms:
                params.append(value)
                placeholders.append(f":{len(params)}")
            clauses.append(f"TO_CHAR(SPACE_NM) IN ({', '.join(placeholders)})")
        return " AND ".join(clauses), params


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

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
