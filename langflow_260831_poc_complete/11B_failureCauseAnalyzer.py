from __future__ import annotations

import logging
import json
import os
import re
import urllib.error
import urllib.request
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


class NewType11BFailureCauseAnalyzer(Component):

    display_name = "11B Failure Cause Analyzer"
    description = "Collects current failed jobs after final INFO-state reconciliation and asks an LLM for root-cause analysis."
    name = "NewType11BFailureCauseAnalyzer"
    icon = "SearchX"

    inputs = [
        DataInput(name="loop_done", display_name="Loop Done", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        StrInput(name="llm_base_url", display_name="LLM Base URL", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="GLM-5.1", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=3000, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=120, required=False),
        IntInput(name="max_failures", display_name="Max Failure Logs", value=50, required=False),
    ]

    outputs = [Output(display_name="Failure Analysis", name="analysis", method="build_analysis", types=["Message"])]

    SUCCESS_STATUSES = {"PASS", "SUCCESS", "PASS-CONVERSION", "PASS-TUNING", "SUCCESS-TEST", "FORMATTED"}
    SKIP_STATUSES = {"PASS-THROUGH", "SKIP", "SKIPPED", "PREREQUISITE_REQUIRED"}

    def build_analysis(self) -> Message:
        logging.getLogger("smartmigrate.workflow").info("before build_analysis", extra={"workflow_log": [0, "WORKFLOW", "11B_FAIL_CAUSE", "INFO", "BUILD_ANALYSIS", "START", 0]})
        try:
            try:
                payload = self._parse_payload(getattr(self, "loop_done", ""))
                self._db_config = self._db_config_from_inputs(payload)
                self._require_db_config(self._db_config)

                evidence = self._collect_failure_evidence(payload)
                if not evidence["failures"] and not evidence["skipped_due_to_prior_fail"]:
                    answer = self._no_failure_answer(evidence)
                else:
                    answer = self._call_llm(evidence)

                self.status = {
                    **payload,
                    "component": "11B_failureCauseAnalyzer",
                    "failure_evidence": evidence,
                    "answer_text": answer,
                    "final": True,
                }
                __log_result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").info("after build_analysis", extra={"workflow_log": [0, "WORKFLOW", "11B_FAIL_CAUSE", "INFO", "BUILD_ANALYSIS", "END", 0]})
                return __log_result
            except Exception as exc:
                answer = f"## Fail 원인 분석\n\nFail 원인 분석 생성에 실패했습니다.\n\nError: {exc}"
                self.status = {"ok": False, "component": "11B_failureCauseAnalyzer", "error": str(exc), "answer_text": answer}
                __log_result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").error("error build_analysis", extra={"workflow_log": [0, "WORKFLOW", "11B_FAIL_CAUSE", "ERROR", "BUILD_ANALYSIS", "ERROR", 0]})
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after build_analysis", extra={"workflow_log": [0, "WORKFLOW", "11B_FAIL_CAUSE", "INFO", "BUILD_ANALYSIS", "END", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error build_analysis: {exc}", extra={"workflow_log": [0, "WORKFLOW", "11B_FAIL_CAUSE", "ERROR", "BUILD_ANALYSIS", "ERROR", 0]})
            raise

    def _collect_failure_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        mig_started_at = self._latest_mig_start_at()
        sql_started_at = self._latest_sql_start_at()
        final_statuses = self._collect_final_statuses()
        mig_failures = self._latest_per_job(self._query_mig_logs(mig_started_at, fail_only=True), "retry_no")
        sql_failures = self._latest_per_job(self._query_sql_logs(sql_started_at, fail_only=True), "retry_no")
        current_failures = self._filter_by_current_final_status(mig_failures + sql_failures, final_statuses)
        skipped = self._latest_per_job(
            self._query_mig_logs(mig_started_at, skip_only=True) + self._query_sql_logs(sql_started_at, skip_only=True),
            "retry_no",
        )
        current_skipped = self._filter_by_current_final_status(skipped, final_statuses)
        max_failures = self._positive_int(getattr(self, "max_failures", None), 50)
        failures = [self._llm_failure_item(item) for item in current_failures[:max_failures]]
        return {
            "user_request": payload.get("user_request") or payload.get("original_request") or "",
            "workflow_summary": payload.get("workflow_summary") or {},
            "workflow_plan_counts": payload.get("workflow_plan_counts") or {},
            "workflow_aborted": bool(payload.get("workflow_aborted")),
            "abort_reason": payload.get("abort_reason") or "",
            "final_status_summary": self._final_status_summary(final_statuses),
            "failures": failures,
            "skipped_due_to_prior_fail": [self._llm_failure_item(item) for item in current_skipped],
        }

    def _latest_mig_start_at(self) -> Any:
        table = self._qualify("NEXT_MIG_LOG")
        column_types = self._available_column_types("NEXT_MIG_LOG")
        columns = set(column_types)
        if not {"CREATED_AT", "LOG_TYPE", "STEP_NAME"}.issubset(columns):
            return None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAX(CREATED_AT)
                  FROM {table}
                 WHERE UPPER(TRIM(NVL(LOG_TYPE, ''))) = 'LOG_RUNTIME_START'
                   AND UPPER(TRIM(NVL(STEP_NAME, ''))) = 'RUN'
                """
            )
            row = cur.fetchone()
        return row[0] if row else None

    def _latest_sql_start_at(self) -> Any:
        return self._latest_mig_start_at()

    def _query_mig_logs(self, started_at: Any, *, fail_only: bool = False, skip_only: bool = False) -> list[dict[str, Any]]:
        if started_at is None:
            return []
        column_types = self._available_column_types("NEXT_MIG_LOG")
        columns = set(column_types)
        required = {"CREATED_AT", "STATUS"}
        if not required.issubset(columns):
            return []
        table = self._qualify("NEXT_MIG_LOG")
        conditions = ["1 = 1"]
        params: dict[str, Any] = {}
        if started_at is not None:
            conditions.append("CREATED_AT >= :started_at")
            params["started_at"] = started_at
        if fail_only:
            conditions.append(self._failure_status_condition("STATUS"))
        if skip_only:
            conditions.append("UPPER(TRIM(NVL(STATUS, ''))) LIKE 'SKIP-%'")
        select_sql = f"""
            SELECT
                {self._select_expr(columns, "CREATED_AT")},
                {self._select_expr(columns, "MAP_ID")},
                {self._select_expr(columns, "STATUS")},
                {self._select_expr(columns, "LOG_TYPE")},
                {self._select_expr(columns, "LOG_LEVEL")},
                {self._select_expr(columns, "STEP_NAME")},
                {self._text_preview_expr(column_types, "MESSAGE", 100)},
                {self._select_expr(columns, "RETRY_COUNT")}
              FROM {table}
             WHERE {" AND ".join(conditions)}
             ORDER BY CREATED_AT DESC
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(select_sql, params)
            logs: list[dict[str, Any]] = []
            for row in cur.fetchall():
                logs.append(
                    {
                        "domain": "DB_MIGRATION",
                        "job_key": f"MIG:{self._to_text(row[1])}",
                        "created_at": self._to_text(row[0]),
                        "map_id": self._json_value(row[1]),
                        "status": self._to_text(row[2]),
                        "log_type": self._to_text(row[3]),
                        "log_level": self._to_text(row[4]),
                        "stage_name": self._to_text(row[5]),
                        "message": self._to_text(row[6]),
                        "retry_no": self._num(row[7]),
                        "sql_snippet": "",
                    }
                )
        return logs

    def _llm_failure_item(self, item: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "domain": item.get("domain") or "",
            "status": item.get("status") or "",
            "stage": item.get("stage_name") or "",
            "retry": self._num(item.get("retry_no")),
            "message": item.get("message") or "",
            "final_status": item.get("final_status") or "",
            "final_status_class": item.get("final_status_class") or "",
        }
        if item.get("map_id") not in (None, ""):
            out["map_id"] = item.get("map_id")
        if item.get("space_nm") or item.get("sql_id"):
            out["space_nm"] = item.get("space_nm") or ""
            out["sql_id"] = item.get("sql_id") or ""
        return out

    def _query_sql_logs(self, started_at: Any, *, fail_only: bool = False, skip_only: bool = False) -> list[dict[str, Any]]:
        if started_at is None:
            return []
        column_types = self._available_column_types("NEXT_MIG_LOG")
        columns = set(column_types)
        required = {"CREATED_AT", "STATUS", "MIG_KIND"}
        if not required.issubset(columns):
            return []
        table = self._qualify("NEXT_MIG_LOG")
        conditions = ["UPPER(TRIM(NVL(MIG_KIND, ''))) IN ('SQL_CONVERSION', 'SQL_TUNING', 'SQL_FORMATTING')"]
        params: dict[str, Any] = {}
        if started_at is not None:
            conditions.append("CREATED_AT >= :started_at")
            params["started_at"] = started_at
        if fail_only:
            conditions.append(self._failure_status_condition("STATUS"))
        if skip_only:
            conditions.append("UPPER(TRIM(NVL(STATUS, ''))) LIKE 'SKIP-%'")
        select_sql = f"""
            SELECT
                {self._select_expr(columns, "CREATED_AT")},
                {self._select_expr(columns, "MAP_ID")},
                {self._select_expr(columns, "MIG_KIND")},
                {self._select_expr(columns, "LOG_TYPE")},
                {self._select_expr(columns, "STATUS")},
                {self._select_expr(columns, "LOG_LEVEL")},
                {self._select_expr(columns, "RETRY_COUNT")},
                {self._select_expr(columns, "STEP_NAME")},
                {self._text_preview_expr(column_types, "MESSAGE", 100)}
              FROM {table}
             WHERE {" AND ".join(conditions)}
             ORDER BY CREATED_AT DESC
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(select_sql, params)
            logs: list[dict[str, Any]] = []
            for row in cur.fetchall():
                domain = self._to_text(row[2]) or self._sql_domain(row[3], row[7])
                map_value = self._to_text(row[1])
                sql_id, space_nm = self._split_sql_map_id(map_value)
                logs.append(
                    {
                        "domain": domain,
                        "job_key": f"{domain}:{space_nm}:{sql_id}",
                        "created_at": self._to_text(row[0]),
                        "space_nm": space_nm,
                        "sql_id": sql_id,
                        "row_id": "",
                        "sql_kind": self._to_text(row[3]),
                        "status": self._to_text(row[4]),
                        "model_name": "",
                        "retry_no": self._num(row[6]),
                        "stage_name": self._to_text(row[7]),
                        "message": self._to_text(row[8]),
                        "sql_snippet": "",
                    }
                )
        return logs

    def _split_sql_map_id(self, value: Any) -> tuple[str, str]:
        text = self._to_text(value)
        if " / " not in text:
            return text, ""
        sql_id, space_nm = text.split(" / ", 1)
        return sql_id.strip(), space_nm.strip()

    def _latest_per_job(self, logs: list[dict[str, Any]], retry_field: str) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for log in logs:
            key = str(log.get("job_key") or "")
            if not key:
                continue
            current = selected.get(key)
            if current is None or self._is_newer_retry(log, current, retry_field):
                selected[key] = log
        return sorted(selected.values(), key=lambda item: (str(item.get("domain") or ""), str(item.get("job_key") or "")))

    def _is_newer_retry(self, left: dict[str, Any], right: dict[str, Any], retry_field: str) -> bool:
        left_retry = self._num(left.get(retry_field))
        right_retry = self._num(right.get(retry_field))
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

    def _collect_final_statuses(self) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        statuses.update(self._query_mig_final_statuses())
        statuses.update(self._query_sql_final_statuses())
        return statuses

    def _query_mig_final_statuses(self) -> dict[str, dict[str, Any]]:
        column_types = self._available_column_types("NEXT_MIG_INFO")
        columns = set(column_types)
        if "STATUS" not in columns:
            return {}
        table = self._qualify("NEXT_MIG_INFO")
        map_expr = "MAP_ID" if "MAP_ID" in columns else "CAST(NULL AS VARCHAR2(100)) AS MAP_ID"
        use_expr = "USE_YN" if "USE_YN" in columns else "'Y'"
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT {map_expr}, STATUS
                  FROM {table}
                 WHERE UPPER(TRIM(NVL({use_expr}, 'Y'))) = 'Y'
                """
            )
            rows = cur.fetchall()
        statuses: dict[str, dict[str, Any]] = {}
        for row in rows:
            map_id = self._to_text(row[0])
            status = self._status(row[1])
            statuses[f"MIG:{map_id}"] = {
                "domain": "DB_MIGRATION",
                "job_key": f"MIG:{map_id}",
                "status": status,
                "class": self._status_class(status),
            }
        return statuses

    def _query_sql_final_statuses(self) -> dict[str, dict[str, Any]]:
        column_types = self._available_column_types("NEXT_SQL_INFO")
        columns = set(column_types)
        if not columns:
            return {}
        table = self._qualify("NEXT_SQL_INFO")
        select_sql = ", ".join(
            [
                self._select_expr(columns, "SPACE_NM"),
                self._select_expr(columns, "SQL_ID"),
                self._select_expr(columns, "STATUS_CONVERSION"),
                self._select_expr(columns, "STATUS_TUNING"),
                self._formatted_status_expr(columns),
            ]
        )
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {select_sql} FROM {table}")
            rows = cur.fetchall()
        statuses: dict[str, dict[str, Any]] = {}
        for row in rows:
            space_nm = self._to_text(row[0])
            sql_id = self._to_text(row[1])
            base_key = f"{space_nm}:{sql_id}"
            for domain, status in (
                ("SQL_CONVERSION", row[2]),
                ("SQL_TUNING", row[3]),
                ("SQL_FORMATTING", row[4]),
            ):
                normalized = self._status(status)
                job_key = f"{domain}:{base_key}"
                statuses[job_key] = {
                    "domain": domain,
                    "job_key": job_key,
                    "space_nm": space_nm,
                    "sql_id": sql_id,
                    "status": normalized,
                    "class": self._status_class(normalized),
                }
        return statuses

    def _filter_by_current_final_status(
        self,
        logs: list[dict[str, Any]],
        final_statuses: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for log in logs:
            current = final_statuses.get(str(log.get("job_key") or ""))
            if current and current.get("class") == "success":
                continue
            enriched = dict(log)
            if current:
                enriched["final_status"] = current.get("status") or ""
                enriched["final_status_class"] = current.get("class") or ""
            else:
                enriched["final_status"] = ""
                enriched["final_status_class"] = "unknown"
            filtered.append(enriched)
        return filtered

    def _final_status_summary(self, final_statuses: dict[str, dict[str, Any]]) -> dict[str, int]:
        summary = {"total": len(final_statuses), "success": 0, "fail": 0, "pending": 0, "skipped": 0, "unknown": 0}
        for item in final_statuses.values():
            cls = str(item.get("class") or "unknown")
            summary[cls] = summary.get(cls, 0) + 1
        return summary

    def _formatted_status_expr(self, columns: set[str]) -> str:
        if "FORMATTED_SQL" in columns:
            return "CASE WHEN FORMATTED_SQL IS NOT NULL THEN 'FORMATTED' ELSE NULL END AS STATUS_FORMATTING"
        return "CAST(NULL AS VARCHAR2(20)) AS STATUS_FORMATTING"

    def _failure_status_condition(self, column: str) -> str:
        clean = self._clean_identifier(column)
        normalized = f"UPPER(TRIM(NVL({clean}, '')))"
        return f"({normalized} IN ('FAIL', 'FAILED') OR {normalized} LIKE 'FAIL-%')"

    def _status_class(self, status: Any) -> str:
        value = self._status(status)
        if not value:
            return "pending"
        if value in self.SUCCESS_STATUSES or value.startswith("PASS-") or value.startswith("SUCCESS-"):
            return "success"
        if value in {"FAIL", "FAILED"} or value.startswith("FAIL-"):
            return "fail"
        if value in self.SKIP_STATUSES or value.startswith("SKIP-"):
            return "skipped"
        return "unknown"

    def _call_llm(self, evidence: dict[str, Any]) -> str:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip() or os.getenv("LLM_API_KEY") or os.getenv("OPEN_API_KEY") or ""
        model = str(getattr(self, "llm_model", "") or os.getenv("LLM_MODEL") or "GLM-5.1").strip()
        base_url = str(getattr(self, "llm_base_url", "") or os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
        if not api_key:
            return self._fallback_answer(evidence, "LLM API key is missing.")
        if not model:
            return self._fallback_answer(evidence, "LLM model is missing.")
        if not base_url:
            return self._fallback_answer(evidence, "LLM base URL is missing.")

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": FAILURE_ANALYSIS_PROMPT},
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False, default=str)},
            ],
            "temperature": 0,
            "max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None), 3000),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._positive_int(getattr(self, "llm_timeout_seconds", None), 120)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            return self._fallback_answer(evidence, f"LLM HTTP {exc.code}: {detail[:1000]}")
        except Exception as exc:
            return self._fallback_answer(evidence, f"LLM call failed: {exc}")
        answer = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return answer or self._fallback_answer(evidence, "LLM returned an empty response.")

    def _no_failure_answer(self, evidence: dict[str, Any]) -> str:
        return self._render_no_failure_answer(evidence)
        summary = evidence.get("final_status_summary") or {}
        return (
            "## Fail ?먯씤 遺꾩꽍\n\n"
            "Current final INFO status has no failed job after reconciling prior retry logs.\n\n"
            f"- Workflow aborted: {evidence.get('workflow_aborted')}\n"
            f"- Final status total: {summary.get('total', 0)}\n"
            f"- Final success: {summary.get('success', 0)}\n"
            f"- Final pending: {summary.get('pending', 0)}\n"
            f"- Final skipped: {summary.get('skipped', 0)}\n"
            f"- Final unknown: {summary.get('unknown', 0)}"
        )
        return (
            "## Fail 원인 분석\n\n"
            "최신 WORKFLOW_START 이후 `FAIL-*` 로그가 없습니다.\n\n"
            f"- Workflow aborted: {evidence.get('workflow_aborted')}"
        )

    def _fallback_answer(self, evidence: dict[str, Any], reason: str) -> str:
        lines = ["## Fail 원인 분석", "", f"LLM 분석을 생성하지 못했습니다: {reason}", ""]
        lines.append(f"- FAIL 작업 수: {len(evidence.get('failures') or [])}")
        lines.append(f"- 선행 실패로 인한 미실행 수: {len(evidence.get('skipped_due_to_prior_fail') or [])}")
        if evidence.get("abort_reason"):
            lines.append(f"- 중단 사유: {evidence.get('abort_reason')}")
        if evidence.get("final_status_summary"):
            lines.append(f"- Final status summary: {evidence.get('final_status_summary')}")
        for item in evidence.get("failures") or []:
            ident = item.get("map_id") or f"{item.get('space_nm')}.{item.get('sql_id')}"
            lines.append(f"- {item.get('domain')} {ident}: {item.get('status')} / {item.get('stage')} / {item.get('message')}")
        return "\n".join(lines)

    def _render_no_failure_answer(self, evidence: dict[str, Any]) -> str:
        summary = evidence.get("final_status_summary") or {}
        return (
            "## Failure Analysis\n\n"
            "Current final INFO status has no failed job after reconciling prior retry logs.\n\n"
            f"- Workflow aborted: {evidence.get('workflow_aborted')}\n"
            f"- Final status total: {summary.get('total', 0)}\n"
            f"- Final success: {summary.get('success', 0)}\n"
            f"- Final pending: {summary.get('pending', 0)}\n"
            f"- Final skipped: {summary.get('skipped', 0)}\n"
            f"- Final unknown: {summary.get('unknown', 0)}"
        )

    def _available_columns(self, table_name: str) -> set[str]:
        return set(self._available_column_types(table_name))

    def _available_column_types(self, table_name: str) -> dict[str, str]:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2", [schema, table])
            else:
                cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [table])
            rows = cur.fetchall()
        return {str(row[0]).upper(): str(row[1]).upper() for row in rows}

    def _select_expr(self, columns: set[str], column: str) -> str:
        clean = self._clean_identifier(column)
        return clean if clean in columns else f"CAST(NULL AS VARCHAR2(4000)) AS {clean}"

    def _text_preview_expr(self, column_types: dict[str, str], column: str, length: int) -> str:
        clean = self._clean_identifier(column)
        if clean not in column_types:
            return f"CAST(NULL AS VARCHAR2({max(1, int(length))})) AS {clean}"
        if column_types.get(clean) in {"CLOB", "NCLOB"}:
            return f"DBMS_LOB.SUBSTR({clean}, {max(1, int(length))}, 1) AS {clean}"
        return f"SUBSTR({clean}, 1, {max(1, int(length))}) AS {clean}"

    @contextmanager
    def _connect(self):
        import oracledb

        oracledb.defaults.fetch_lobs = False
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
        _ = payload
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"11B Failure Cause Analyzer is not connected to database settings: missing {', '.join(missing)}")

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
        if not isinstance(parsed, dict):
            raise ValueError("loop_done must be a JSON object")
        return parsed

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _status(self, value: Any) -> str:
        return self._to_text(value).strip().upper()

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

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value or 0)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _clip(self, value: str, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated]"



FAILURE_ANALYSIS_PROMPT = """
You are the SmartMigrate workflow failure analyst.

Input is a compact JSON object collected after one workflow loop.
The code already filtered logs by the latest WORKFLOW_START marker, selected statuses matching FAIL-*, deduplicated retries, and removed low-value fields.
SKIP-* records are not root causes; report them separately as jobs skipped because a prior phase failed.

Write the answer in Korean Markdown.
Keep it practical and evidence-based.

Required structure:
## Fail 원인 분석

### 전체 요약
- Summarize planned/completed/pass/fail/skipped counts when available.
- Mention whether the workflow was aborted and why.

### 실패 작업
- Group by domain: DB Migration, SQL Conversion, SQL Tuning, SQL Formatting.
- For each failed job, include identifier, status, stage, retry/attempt number, and the most likely cause.

### 선행 실패로 인한 미실행
- Summarize SKIP-* records separately.
- Do not treat them as independent root causes.

### 다음 조치
- Give concrete next steps for retry, SQL correction, metadata correction, or data check.
- Separate random/temporary-looking failures from deterministic SQL/schema/data failures when the evidence supports it.
""".strip()
