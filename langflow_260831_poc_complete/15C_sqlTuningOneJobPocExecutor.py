from __future__ import annotations

import json
import random
import re
import time
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


CONVERSION_SUCCESS_STATUSES = {"PASS", "PASS-CONVERSION"}
TUNING_PASS = "PASS-TUNING"
FAIL_TUNED = "FAIL-TUNED"
FAIL_TEST = "FAIL-TEST"
POC_TUNING_FAIL_PERCENT = 50


class NewType15CSqlTuningOneJobPocExecutor(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "15C SQL Tuning One Job POC Executor"
    description = "Runs one SQL Tuning POC job and can be chained after SQL Conversion."
    name = "NewType15CSqlTuningOneJobPocExecutor"
    icon = "WandSparkles"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        self._insert_log(0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "START", "before run_job", 0, "")
        try:
            """Run one tuning job or pass through when upstream conversion failed."""
            started = time.perf_counter()
            payload = self._parse_payload(getattr(self, "job_item", ""))
            self._payload_max_retry = payload.get("max_retry") if isinstance(payload, dict) else None
            if not self._should_run_tuning(payload):
                result = self._component_pass_through(payload, started, "15C skipped because job_name is not conversion or tuning.")
                self.status = result
                __log_result = Data(data=result)
                self._insert_log(0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "END", "after run_job", 0, "")
                return __log_result
            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            job: dict[str, Any] = {}
            try:
                job = self._load_sql_job(db_config, payload)
                merged = {**job, **payload}

                conversion_status = self._status(merged.get("conversion_status") or merged.get("status_conversion") or job.get("status_conversion"))
                if not self._is_conversion_pass(conversion_status):
                    result = self._pass_through(
                        payload=merged,
                        job=job,
                        started=started,
                        status=conversion_status or self._status(merged.get("status")) or "NOT-RUN",
                        message=f"SQL tuning passed through without DB update because conversion status is {conversion_status or 'NULL'}.",
                    )
                    self.status = result
                    __log_result = Data(data=result)
                    self._insert_log(0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "END", "after run_job", 0, "")
                    return __log_result

                self._increment_batch_count(db_config, str(job["row_id"]))
                result = self._run_tuning(merged, job, db_config, started)
            except Exception as exc:
                result = self._finish_failure(payload, job, db_config, started, FAIL_TUNED, str(exc))
            self.status = result
            __log_result = Data(data=result)
            self._insert_log(0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "END", "after run_job", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "15C_SQL_TUNE", "ERROR", "RUN_JOB", "ERROR", f"error run_job: {exc}", 0, "")
            raise

    def _should_run_tuning(self, payload: dict[str, Any]) -> bool:
        return self._job_name(payload) in {"conversion", "tuning"}

    def _job_name(self, payload: dict[str, Any]) -> str:
        value = str(payload.get("job_name") or "").strip().lower()
        if value:
            return value
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()
        return {
            "MIG": "migration",
            "SQL_CONVERSION": "conversion",
            "SQL_TUNING": "tuning",
            "SQL_FORMATTING": "formatting",
        }.get(route, "")

    def _component_pass_through(self, payload: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        result = {
            **payload,
            "component": "15C_sqlTuningOneJobPocExecutor",
            "ok": bool(payload.get("ok", True)),
            "status": payload.get("status") or "PASS-THROUGH",
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": int(payload.get("attempt_count") or 0),
            "attempts": list(payload.get("attempts") or []),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
            "stages": dict(payload.get("stages") or {}),
            "component_pass_through": True,
            "pass_through_component": "15C",
            "message": payload.get("message") or message,
            "next_node": "17C_sqlFormattingOneJobPocExecutor",
        }
        history = list(result.get("history") or [])
        history.append({"step": "15C_pass_through", "message": message})
        result["history"] = history
        return result

    def _run_tuning(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Apply DB-driven tuning branches for one NEXT_SQL_INFO row."""
        to_sql = str(payload.get("to_sql") or job.get("to_sql") or "").strip()
        if not to_sql:
            return self._finish_failure(payload, job, db_config, started, FAIL_TUNED, "TO_SQL is empty")

        tag_kind = str(payload.get("tag_kind") or job.get("tag_kind") or "").strip().upper()
        attempts: list[dict[str, Any]] = []

        tuned_sql = str(payload.get("tuned_to_sql") or job.get("tuned_to_sql") or "").strip()
        tuned_result = str(payload.get("tuned_result") or job.get("tuned_result") or "").strip()
        tuning_guides: list[dict[str, Any]] = list(payload.get("tuning_guides") or [])
        resume_stage = "APPLY_TUNING_RULES"
        last_status = FAIL_TUNED
        last_message = "SQL tuning failed."

        for attempt_no in range(1, self._max_retry() + 1):
            if resume_stage == "APPLY_TUNING_RULES":
                # Future LLM/RAG section:
                # - Retrieve SQL_TUNING SEARCH rules and store BLOCK_RAG_CONTENT.
                # - Call tune_tobe_sql().
                # - If no rule is found in the real path, persist FAIL-TUNED.
                if self._poc_stage_failed(job, attempt_no, "APPLY_TUNING_RULES"):
                    last_status = FAIL_TUNED
                    last_message = "[POC] tuning rule application failed"
                    attempts.append({"attempt": attempt_no, "stage": "APPLY_TUNING_RULES", "status": last_status, "reason": last_message})
                    self._insert_sql_log(db_config, job, "TUNED_TO_SQL", None, last_status, attempt_no, "APPLY_TUNING_RULES", last_message)
                    resume_stage = "APPLY_TUNING_RULES"
                    continue

                tuned_sql, tuned_result = self._build_poc_tuned_sql(to_sql)
                tuning_guides = self._poc_tuning_guides(tag_kind, tuned_result)
                self._insert_sql_log(db_config, job, "TUNED_TO_SQL", tuned_sql, "SUCCESS", attempt_no, "APPLY_TUNING_RULES")
                self._update_row(
                    db_config,
                    job["row_id"],
                    {
                        "TO_SQL": to_sql,
                        "TUNED_TO_SQL": tuned_sql,
                        "TUNED_RESULT": tuned_result,
                    },
                )
                attempts.append(
                    {
                        "attempt": attempt_no,
                        "stage": "APPLY_TUNING_RULES",
                        "status": TUNING_PASS,
                        "result": tuned_result,
                        "guide_ids": [guide["guide_id"] for guide in tuning_guides],
                    }
                )
                resume_stage = "VALIDATE_TUNED_SQL" if tag_kind == "SELECT" and tuned_sql.strip() != to_sql.strip() else "SKIP_TUNED_VALIDATION"
            elif tuned_sql:
                attempts.append({"attempt": attempt_no, "stage": "REUSE_TUNED_SQL", "status": TUNING_PASS, "reason": f"resume_from={resume_stage}"})

            if resume_stage == "VALIDATE_TUNED_SQL":
                # Future validation section:
                # - Generate tuned comparison TEST_SQL.
                # - Execute against the same BIND_SET.
                # - On FAIL-TEST retry, keep TUNED_TO_SQL and resume here.
                if self._poc_stage_failed(job, attempt_no, "VALIDATE_TUNED_SQL"):
                    last_status = FAIL_TEST
                    last_message = "[POC] tuned SQL validation failed"
                    attempts.append({"attempt": attempt_no, "stage": "VALIDATE_TUNED_SQL", "status": last_status, "reason": last_message})
                    self._insert_sql_log(db_config, job, "TUNED_TEST_SQL", tuned_sql, last_status, attempt_no, "VALIDATE_TUNED_SQL", last_message)
                    resume_stage = "VALIDATE_TUNED_SQL"
                    continue
                attempts.append({"attempt": attempt_no, "stage": "VALIDATE_TUNED_SQL", "status": TUNING_PASS})
                self._insert_sql_log(db_config, job, "TUNED_TEST_SQL", tuned_sql, "PASS", attempt_no, "VALIDATE_TUNED_SQL")
            else:
                reason = "NO_TUNING" if tuned_sql.strip() == to_sql.strip() else f"TAG_KIND:{tag_kind or 'UNKNOWN'}"
                attempts.append({"attempt": attempt_no, "stage": "SKIP_TUNED_VALIDATION", "status": TUNING_PASS, "reason": reason})

            final_log = f"FINAL SUCCESS stage=SQL_TUNING status={TUNING_PASS} job={job.get('space_nm')}.{job.get('sql_id')} result={tuned_result}"
            self._update_row(
                db_config,
                job["row_id"],
                {
                    "TO_SQL": to_sql,
                    "TUNED_TO_SQL": tuned_sql,
                    "TUNED_RESULT": tuned_result,
                    "STATUS_TUNING": TUNING_PASS,
                    "LOG": final_log,
                    "RETRY_COUNT": attempt_no - 1,
                },
            )
            return self._result(
                payload=payload,
                job=job,
                ok=True,
                status=TUNING_PASS,
                elapsed=time.perf_counter() - started,
                attempts=attempts,
                message="SQL tuning completed.",
                extra={
                    "status_tuning": TUNING_PASS,
                    "tuning_status": TUNING_PASS,
                    "tuned_to_sql": tuned_sql,
                    "tuned_result": tuned_result,
                    "tuning_guides": tuning_guides,
                    "tag_kind": tag_kind,
                    "next_node": "17C_sqlFormattingOneJobPocExecutor",
                },
            )

        return self._finish_failure(
            payload,
            job,
            db_config,
            started,
            last_status,
            last_message,
            attempts=attempts,
            partial_values={"TUNED_TO_SQL": tuned_sql, "TUNED_RESULT": tuned_result},
            tuning_guides=tuning_guides,
            mark_user_edited=True,
        )

    def _finish_failure(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
        status: str,
        message: str,
        attempts: list[dict[str, Any]] | None = None,
        partial_values: dict[str, Any] | None = None,
        tuning_guides: list[dict[str, Any]] | None = None,
        mark_user_edited: bool = False,
    ) -> dict[str, Any]:
        """Persist a SQL tuning failure using the source status values."""
        failure_attempts = attempts or [{"attempt": 1, "stage": self._failure_stage(status), "status": status, "reason": message}]
        if job.get("row_id"):
            tuned_result = str((partial_values or {}).get("TUNED_RESULT") or message)[:4000]
            update_values = {
                key: value
                for key, value in (partial_values or {}).items()
                if value not in (None, "")
            }
            update_values.update(
                {
                    "STATUS_TUNING": status,
                    "TUNED_RESULT": tuned_result,
                    "LOG": f"FINAL FAILURE stage=SQL_TUNING status={status} error={message}",
                    "RETRY_COUNT": self._configured_retry_limit(),
                }
            )
            if mark_user_edited:
                update_values["USER_EDITED"] = "Y"
            self._update_row(
                db_config,
                str(job["row_id"]),
                update_values,
            )
            self._insert_sql_log(
                db_config,
                job,
                "SQL_TUNING",
                (partial_values or {}).get("TUNED_TO_SQL"),
                status,
                len(failure_attempts) or 1,
                self._failure_stage(status),
                message,
                elapsed_seconds=time.perf_counter() - started,
            )
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=failure_attempts,
            message=message,
            extra={
                "status_tuning": status,
                "tuning_status": status,
                "tuned_to_sql": (partial_values or {}).get("TUNED_TO_SQL"),
                "tuned_result": (partial_values or {}).get("TUNED_RESULT") or message,
                "tuning_guides": list(tuning_guides or []),
                "next_node": self._dashboard_node(payload),
            },
        )

    def _pass_through(
        self,
        *,
        payload: dict[str, Any],
        job: dict[str, Any],
        started: float,
        status: str,
        message: str,
    ) -> dict[str, Any]:
        """Return the same job without DB updates when tuning is not eligible."""
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message=message,
            extra={"tuning_skipped": True, "next_node": self._dashboard_node(payload)},
        )

    def _result(
        self,
        *,
        payload: dict[str, Any],
        job: dict[str, Any],
        ok: bool,
        status: str,
        elapsed: float,
        attempts: list[dict[str, Any]],
        message: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the standard Loop result payload."""
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        completed = min(index, total)
        stages = dict(payload.get("stages") or {})
        if not extra.get("tuning_skipped"):
            stages["tuning"] = {
                "ok": ok,
                "status": status,
                "message": message,
                "attempts": attempts,
                "tuned_result": extra.get("tuned_result"),
                "tuning_guides": list(extra.get("tuning_guides") or []),
            }
        return {
            **payload,
            **extra,
            "component": "15C_sqlTuningOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_TUNING",
            "job_type": "SQL",
            "row_id": job.get("row_id") or payload.get("row_id"),
            "space_nm": job.get("space_nm") or payload.get("space_nm"),
            "sql_id": job.get("sql_id") or payload.get("sql_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "db_status_updated": bool(job.get("row_id")) and not extra.get("tuning_skipped"),
        }

    def _poc_tuning_guides(self, tag_kind: str, tuned_result: str) -> list[dict[str, Any]]:
        """Return visible POC tuning guide metadata until RAG retrieval is connected."""
        return [
            {
                "guide_id": "POC-SQL-TUNING-001",
                "category": "SQL_TUNING",
                "rule_type": "POC",
                "tag_kind": tag_kind or "UNKNOWN",
                "result": tuned_result,
                "guidance": (
                    "RAG/LLM tuning rule retrieval is not connected yet. "
                    "The POC records a deterministic tuned SQL comment so the "
                    "tuning validation and FAIL-TEST resume branches are visible."
                ),
            }
        ]

    def _build_poc_tuned_sql(self, to_sql: str) -> tuple[str, str]:
        """Create a deterministic tuned SQL placeholder until RAG/LLM tuning is connected."""
        return f"/* POC SQL_TUNING: guide applied before formatting */\n{to_sql.strip()}", "POC TUNING GUIDE APPLIED"

    def _poc_stage_failed(self, job: dict[str, Any], attempt: int, stage: str) -> bool:
        """Return a deterministic POC failure decision for one tuning stage."""
        probability = self._fail_probability(stage)
        if probability <= 0:
            return False
        seed = f"SQL_TUNING:{job.get('space_nm')}:{job.get('sql_id')}:{attempt}:{stage}"
        return random.Random(seed).random() < probability

    def _fail_probability(self, stage: str) -> float:
        """Return the fixed POC tuning failure probability for each retry attempt."""
        return (max(0, min(100, POC_TUNING_FAIL_PERCENT)) / 100) if stage in {"APPLY_TUNING_RULES", "VALIDATE_TUNED_SQL"} else 0.0

    def _max_retry(self) -> int:
        """Return bounded total attempts for the POC tuning loop."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(1, min(11, int(getattr(self, "_payload_max_retry") or 0) + 1))
        return max(1, min(11, int(getattr(self, "max_retry", None) or 2) + 1))

    def _configured_retry_limit(self) -> int:
        """Return the configured retry limit, not including the first attempt."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(0, min(10, int(getattr(self, "_payload_max_retry") or 0)))
        return max(0, min(10, int(getattr(self, "max_retry", None) or 2)))

    def _failure_stage(self, status: str) -> str:
        """Return the tuning stage represented by a failure status."""
        return "VALIDATE_TUNED_SQL" if status == FAIL_TEST else "APPLY_TUNING_RULES"

    def _load_sql_job(self, db_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Load one NEXT_SQL_INFO row by ROWID or by SPACE_NM + SQL_ID."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        aliases = [
            ("TAG_KIND", "tag_kind", "VARCHAR2(100)"),
            ("SPACE_NM", "space_nm", "VARCHAR2(4000)"),
            ("SQL_ID", "sql_id", "VARCHAR2(4000)"),
            ("TO_SQL", "to_sql", "CLOB"),
            ("TUNED_TO_SQL", "tuned_to_sql", "CLOB"),
            ("STATUS_CONVERSION", "status_conversion", "VARCHAR2(100)"),
            ("STATUS_TUNING", "status_tuning", "VARCHAR2(100)"),
            ("BIND_SQL", "bind_sql", "CLOB"),
            ("BIND_SET", "bind_set", "CLOB"),
            ("TEST_SQL", "test_sql", "CLOB"),
            ("TARGET_TABLE", "target_table", "VARCHAR2(4000)"),
            ("TUNED_RESULT", "tuned_result", "VARCHAR2(4000)"),
            ("FORMATTED_SQL", "formatted_sql", "CLOB"),
            ("PRIORITY", "priority", "NUMBER"),
            ("RETRY_COUNT", "retry_count", "NUMBER"),
        ]
        select_sql = ",\n               ".join(["ROWIDTOCHAR(ROWID) AS row_id", *[self._select_expr(columns, col, alias, data_type) for col, alias, data_type in aliases]])
        row_id = str(payload.get("row_id") or "").strip()
        if row_id:
            where_sql = "ROWID = CHARTOROWID(:rid)"
            params = {"rid": row_id}
        else:
            space_nm = str(payload.get("space_nm") or "").strip()
            sql_id = str(payload.get("sql_id") or "").strip()
            if not space_nm or not sql_id:
                raise ValueError("SQL tuning item requires row_id or space_nm+sql_id")
            where_sql = "TO_CHAR(SPACE_NM) = :space_nm AND TO_CHAR(SQL_ID) = :sql_id"
            params = {"space_nm": space_nm, "sql_id": sql_id}
        query = f"""
            SELECT {select_sql}
              FROM {table}
             WHERE {where_sql}
             ORDER BY UPD_TS NULLS FIRST
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_SQL_INFO row not found: space_nm={payload.get('space_nm')}, sql_id={payload.get('sql_id')}")
            keys = ["row_id", *[alias for _, alias, _ in aliases]]
            loaded = {key: self._lob_to_str(row[index]) for index, key in enumerate(keys)}
        return {**payload, **loaded}

    def _update_row(self, db_config: dict[str, Any], row_id: str, values: dict[str, Any]) -> None:
        """Update only columns that exist in NEXT_SQL_INFO."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"rid": row_id}
        for index, (column, value) in enumerate(values.items(), start=1):
            if column not in columns:
                continue
            name = f"p{index}"
            set_clauses.append(f"{column} = :{name}")
            params[name] = value
        if "UPD_TS" in columns:
            set_clauses.append("UPD_TS = CURRENT_TIMESTAMP")
        if not set_clauses:
            return
        query = f"""
            UPDATE {table}
               SET {", ".join(set_clauses)}
             WHERE ROWID = CHARTOROWID(:rid)
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()

    def _increment_batch_count(self, db_config: dict[str, Any], row_id: str) -> None:
        """Increment NEXT_SQL_INFO.BATCH_CNT when a SQL tuning job starts."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if "BATCH_CNT" not in columns:
            return
        set_clause = "BATCH_CNT = NVL(BATCH_CNT, 0) + 1"
        if "UPD_TS" in columns:
            set_clause += ", UPD_TS = CURRENT_TIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET {set_clause}
                 WHERE ROWID = CHARTOROWID(:1)
                """,
                [row_id],
            )
            conn.commit()

    def _insert_sql_log(
        self,
        db_config: dict[str, Any],
        job: dict[str, Any],
        sql_kind: str,
        sql_content: Any,
        status: str,
        attempt_no: int | None,
        stage_name: str,
        error_message: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        """Insert a best-effort NEXT_SQL_LOG row without truncating SQL_CONTENT."""
        try:
            table = self._qualify("NEXT_SQL_LOG", db_config.get("system_schema"))
            columns = self._table_columns(db_config, table)
            values: dict[str, Any] = {
                "CREATED_AT": "CURRENT_TIMESTAMP",
                "SPACE_NM": self._fit_text(job.get("space_nm"), 200),
                "SQL_ID": self._fit_text(job.get("sql_id"), 200),
                "SQL_INFO_ROWID": self._fit_text(job.get("row_id"), 30),
                "SQL_KIND": self._fit_text(sql_kind, 30),
                "SQL_CONTENT": None if sql_content is None else str(sql_content),
                "STATUS": self._fit_text(status, 20),
                "PROMPT_NAME": None,
                "MODEL_NAME": None,
                "BATCH_NO": None,
                "CYCLE_NO": None,
                "ELAPSED_SECONDS": round(float(elapsed_seconds), 3) if elapsed_seconds is not None else None,
                "ATTEMPT_NO": attempt_no,
                "STAGE_NAME": self._fit_text(stage_name, 100),
                "ERROR_MESSAGE": self._fit_text(error_message, 4000),
            }
            insert_columns: list[str] = []
            value_exprs: list[str] = []
            params: dict[str, Any] = {}
            for column, value in values.items():
                if column not in columns:
                    continue
                insert_columns.append(column)
                if column == "CREATED_AT":
                    value_exprs.append("CURRENT_TIMESTAMP")
                    continue
                bind_name = column.lower()
                value_exprs.append(f":{bind_name}")
                params[bind_name] = value
            if not insert_columns:
                return
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO {table} ({", ".join(insert_columns)})
                    VALUES ({", ".join(value_exprs)})
                    """,
                    params,
                )
                conn.commit()
        except Exception:
            return

    def _status(self, value: Any) -> str:
        """Normalize a status string for comparisons."""
        return str(value or "").strip().upper()

    def _is_conversion_pass(self, value: Any) -> bool:
        """Return True when conversion passed under current or legacy status names."""
        return self._status(value) in CONVERSION_SUCCESS_STATUSES

    def _dashboard_node(self, payload: dict[str, Any]) -> str:
        """Return the dashboard that owns the current chained flow."""
        if payload.get("full_workflow"):
            return "17C_sqlFormattingOneJobPocExecutor"
        route = str(payload.get("job_route") or "").upper()
        if route == "SQL_CONVERSION":
            return "12D_sqlConversionIterationDashboard"
        return "15D_sqlTuningIterationDashboard"

    def _select_expr(self, columns: set[str], column: str, alias: str, data_type: str) -> str:
        """Return a safe SELECT expression for optional NEXT_SQL_INFO columns."""
        if column in columns:
            return f"{column} AS {alias}"
        if data_type.upper() == "CLOB":
            return f"TO_CLOB(NULL) AS {alias}"
        return f"CAST(NULL AS {data_type}) AS {alias}"

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        """Return available upper-case column names for a table."""
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2"
            params = [owner, table_name]
        else:
            sql = "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1"
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper() for row in cur.fetchall()}

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        """Open and close an Oracle database connection."""
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extract Oracle connection settings from the Loop item."""
        item_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        """Fail early when the Loop item does not include database settings."""
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"15C SQL Tuning is not connected to database settings: missing {', '.join(missing)}")

    def _qualify(self, table_name: str, schema: Any) -> str:
        """Return a validated schema-qualified table name."""
        clean_table = self._clean_identifier(table_name)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    def _clean_identifier(self, value: str) -> str:
        """Validate and normalize an Oracle identifier."""
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        """Split an optional owner-qualified table identifier."""
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

    def _lob_to_str(self, value: Any) -> str:
        """Convert Oracle LOB and nullable values to strings."""
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _fit_text(self, value: Any, max_len: int) -> str | None:
        """Trim log metadata by UTF-8 bytes; SQL payloads are not passed here."""
        if value is None:
            return None
        text = str(value)
        encoded = text.encode("utf-8", errors="ignore")
        if len(encoded) <= max_len:
            return text
        return encoded[:max_len].decode("utf-8", errors="ignore")

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        """Parse a Langflow Data, Message, dict, or JSON string payload."""
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
            raise ValueError("job_item must be a JSON object")
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
