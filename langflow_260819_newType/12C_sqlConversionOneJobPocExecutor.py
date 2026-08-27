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


CONVERSION_PASS = "PASS-CONVERSION"
FAIL_TOBE = "FAIL-TOBE"
FAIL_BIND = "FAIL-BIND"
FAIL_TEST = "FAIL-TEST"
SQL_LENGTH_SHORT_MAX = 5000
POC_FAIL_PERCENT = 50


class NewType12CSqlConversionOneJobPocExecutor(Component):
    display_name = "12C SQL Conversion One Job POC Executor"
    description = "Runs one SQL Conversion POC job with DB status updates and downstream tuning eligibility."
    name = "NewType12CSqlConversionOneJobPocExecutor"
    icon = "FileCode"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
    ]

    outputs = [
        Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"]),
    ]

    def run_job(self) -> Data:
        """Run one SQL conversion job and return a payload for 15C."""
        started = time.perf_counter()
        payload = self._parse_payload(getattr(self, "job_item", ""))
        self._payload_max_retry = payload.get("max_retry") if isinstance(payload, dict) else None
        if not self._should_run_conversion(payload):
            result = self._pass_through(payload, started, "12C skipped because job_name is not conversion.")
            self.status = result
            return Data(data=result)
        db_config = self._db_config(payload)
        self._require_db_config(db_config)
        job: dict[str, Any] = {}
        try:
            job = self._load_sql_job(db_config, payload)
            self._increment_batch_count(db_config, str(job["row_id"]))
            result = self._run_conversion(payload, job, db_config, started)
        except Exception as exc:
            result = self._finish_failure(payload, job, db_config, started, FAIL_TOBE, str(exc))
        self.status = result
        return Data(data=result)

    def _should_run_conversion(self, payload: dict[str, Any]) -> bool:
        return self._job_name(payload) == "conversion"

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

    def _pass_through(self, payload: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        result = {
            **payload,
            "component": "12C_sqlConversionOneJobPocExecutor",
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
            "pass_through_component": "12C",
            "message": payload.get("message") or message,
            "next_node": "15C_sqlTuningOneJobPocExecutor",
        }
        history = list(result.get("history") or [])
        history.append({"step": "12C_pass_through", "message": message})
        result["history"] = history
        return result

    def _run_conversion(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Apply DB-driven conversion branches for one NEXT_SQL_INFO row."""
        source_sql = self._source_sql(job)
        if not source_sql.strip():
            return self._finish_failure(payload, job, db_config, started, FAIL_TOBE, "FR_SQL/EDIT_FR_SQL is empty")
        target_table = str(job.get("target_table") or "").strip()
        if not target_table:
            return self._finish_failure(
                payload,
                job,
                db_config,
                started,
                FAIL_TOBE,
                "TARGET_TABLE is empty. Cannot retrieve mapping rules for SQL conversion.",
            )

        tag_kind = str(job.get("tag_kind") or "").strip().upper()
        attempts: list[dict[str, Any]] = []
        source_for_conversion, tuned_fr_sql, sql_length = self._prepare_conversion_source(job, db_config, source_sql)
        max_retry = self._max_retry()
        last_status = FAIL_TOBE
        last_message = "SQL conversion failed."
        to_sql = str(job.get("to_sql") or "").strip()
        bind_sql = str(job.get("bind_sql") or "").strip()
        bind_set = str(job.get("bind_set") or "") or None
        test_sql = str(job.get("test_sql") or "").strip()
        resume_stage = self._initial_resume_stage(job, tag_kind, to_sql, bind_sql)

        for attempt_no in range(1, max_retry + 1):
            if resume_stage == "GENERATE_TOBE_SQL":
                if self._poc_stage_failed(job, attempt_no, "GENERATE_TOBE_SQL"):
                    last_status = FAIL_TOBE
                    last_message = "[POC] TO_SQL generation failed"
                    attempts.append({"attempt": attempt_no, "stage": "GENERATE_TOBE_SQL", "status": last_status, "reason": last_message})
                    self._insert_sql_log(db_config, job, "TOBE_SQL", None, last_status, attempt_no, "GENERATE_TOBE_SQL", last_message)
                    resume_stage = "GENERATE_TOBE_SQL"
                    continue

                to_sql = self._build_poc_to_sql(job, source_for_conversion)
                attempts.append({"attempt": attempt_no, "stage": "GENERATE_TOBE_SQL", "status": CONVERSION_PASS, "sql_length": len(to_sql)})
                self._insert_sql_log(db_config, job, "TOBE_SQL", to_sql, "SUCCESS", attempt_no, "GENERATE_TOBE_SQL")
                generated_values = {"TO_SQL": to_sql}
                if tuned_fr_sql is not None:
                    generated_values["TUNED_FR_SQL"] = tuned_fr_sql
                self._update_row(db_config, job["row_id"], generated_values)
                resume_stage = "GENERATE_BIND_SQL" if tag_kind == "SELECT" else "SKIP_TEST_FOR_NON_SELECT"
            elif to_sql:
                attempts.append({"attempt": attempt_no, "stage": "REUSE_TOBE_SQL", "status": CONVERSION_PASS, "reason": f"resume_from={resume_stage}"})

            if tag_kind == "SELECT":
                if resume_stage == "GENERATE_BIND_SQL":
                    if self._poc_stage_failed(job, attempt_no, "GENERATE_BIND_SQL"):
                        last_status = FAIL_BIND
                        last_message = "[POC] bind SQL generation failed"
                        attempts.append({"attempt": attempt_no, "stage": "GENERATE_BIND_SQL", "status": last_status, "reason": last_message})
                        self._insert_sql_log(db_config, job, "BIND_SQL", None, last_status, attempt_no, "GENERATE_BIND_SQL", last_message)
                        resume_stage = "GENERATE_BIND_SQL"
                        continue

                    bind_status, bind_sql, bind_set = self._build_poc_bind_payload(job, source_for_conversion, to_sql)
                    attempts.append({"attempt": attempt_no, "stage": "GENERATE_BIND_SQL", "status": bind_status})
                    if bind_status != CONVERSION_PASS:
                        last_status = FAIL_BIND
                        last_message = "Bind SQL generation failed"
                        resume_stage = "GENERATE_BIND_SQL"
                        self._insert_sql_log(db_config, job, "BIND_SQL", bind_sql, last_status, attempt_no, "GENERATE_BIND_SQL", last_message)
                        continue
                    self._insert_sql_log(db_config, job, "BIND_SQL", bind_sql, "SUCCESS", attempt_no, "GENERATE_BIND_SQL")
                    self._update_row(db_config, job["row_id"], {"BIND_SQL": bind_sql, "BIND_SET": bind_set})
                    resume_stage = "GENERATE_TEST_SQL"
                elif resume_stage == "GENERATE_TEST_SQL":
                    attempts.append({"attempt": attempt_no, "stage": "REUSE_BIND_SQL", "status": CONVERSION_PASS, "reason": "resume_from=GENERATE_TEST_SQL"})

                test_sql = self._build_poc_test_sql(job)
                attempts.append({"attempt": attempt_no, "stage": "GENERATE_TEST_SQL", "status": CONVERSION_PASS})
                self._insert_sql_log(db_config, job, "TEST_SQL", test_sql, "SUCCESS", attempt_no, "GENERATE_TEST_SQL")
                self._update_row(db_config, job["row_id"], {"TEST_SQL": test_sql})

                if resume_stage == "GENERATE_TEST_SQL" and self._poc_stage_failed(job, attempt_no, "VALIDATE_TEST_SQL"):
                    last_status = FAIL_TEST
                    last_message = "[POC] test SQL validation failed"
                    attempts.append({"attempt": attempt_no, "stage": "VALIDATE_TEST_SQL", "status": last_status, "reason": last_message})
                    self._insert_sql_log(db_config, job, "TEST_SQL", test_sql, last_status, attempt_no, "VALIDATE_TEST_SQL", last_message)
                    resume_stage = "GENERATE_TEST_SQL"
                    continue
                attempts.append({"attempt": attempt_no, "stage": "VALIDATE_TEST_SQL", "status": CONVERSION_PASS})
                self._insert_sql_log(db_config, job, "TEST_SQL", test_sql, "PASS", attempt_no, "VALIDATE_TEST_SQL")
            else:
                bind_sql = ""
                bind_set = None
                test_sql = ""
                attempts.append({"attempt": attempt_no, "stage": "SKIP_TEST_FOR_NON_SELECT", "status": CONVERSION_PASS, "tag_kind": tag_kind or "UNKNOWN"})

            final_log = (
                f"FINAL SUCCESS stage=SQL_CONVERSION status={CONVERSION_PASS} "
                f"job={job.get('space_nm')}.{job.get('sql_id')} reason=TAG_KIND:{tag_kind or 'UNKNOWN'}"
            )
            success_values = {
                "TO_SQL": to_sql,
                "BIND_SQL": bind_sql,
                "BIND_SET": bind_set,
                "TEST_SQL": test_sql,
                "STATUS_CONVERSION": CONVERSION_PASS,
                "LOG": final_log,
                "RETRY_COUNT": attempt_no - 1,
            }
            if tuned_fr_sql is not None:
                success_values["TUNED_FR_SQL"] = tuned_fr_sql
            self._update_row(
                db_config,
                job["row_id"],
                success_values,
            )
            return self._result(
                payload=payload,
                job=job,
                ok=True,
                status=CONVERSION_PASS,
                elapsed=time.perf_counter() - started,
                attempts=attempts,
                message="SQL conversion completed. Continuing to tuning.",
                extra={
                    "status_conversion": CONVERSION_PASS,
                    "conversion_status": CONVERSION_PASS,
                    "to_sql": to_sql,
                    "bind_sql": bind_sql,
                    "bind_set": bind_set,
                    "test_sql": test_sql,
                    "tuned_fr_sql": tuned_fr_sql,
                    "sql_length": sql_length,
                    "tag_kind": tag_kind,
                    "next_node": "15C_sqlTuningOneJobPocExecutor",
                },
            )

        return self._finish_failure(
            payload,
            job,
            db_config,
            started,
            last_status,
            last_message,
            attempts,
            partial_values={
                "TO_SQL": to_sql,
                "BIND_SQL": bind_sql,
                "BIND_SET": bind_set,
                "TEST_SQL": test_sql,
                "TUNED_FR_SQL": tuned_fr_sql,
            },
        )

    def _prepare_conversion_source(
        self,
        job: dict[str, Any],
        db_config: dict[str, Any],
        source_sql: str,
    ) -> tuple[str, str | None, str]:
        """Choose the SQL that feeds TO_SQL generation and store LONG-SQL pretuning output."""
        saved_tuned_fr_sql = str(job.get("tuned_fr_sql") or "").strip()
        if saved_tuned_fr_sql:
            return saved_tuned_fr_sql, saved_tuned_fr_sql, self._sql_length_kind(source_sql)

        sql_length = self._sql_length_kind(source_sql)
        if sql_length != "LONG":
            return source_sql, None, sql_length

        # Future LLM/RAG section:
        # - Load SQL_TUNING GENERAL/SEARCH rules.
        # - Generate bind-friendly AS-IS SQL through bind_tuned_sql_prompt.json.
        # - Store the result in NEXT_SQL_INFO.TUNED_FR_SQL and use it for TO_SQL generation.
        tuned_fr_sql = self._build_poc_tuned_fr_sql(source_sql)
        self._update_row(db_config, job["row_id"], {"TUNED_FR_SQL": tuned_fr_sql})
        return tuned_fr_sql, tuned_fr_sql, sql_length

    def _build_poc_to_sql(self, job: dict[str, Any], source_sql: str) -> str:
        """Create a deterministic TO_SQL placeholder until the LLM node is connected."""
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("to_sql") or "").strip():
            return str(job.get("to_sql") or "")

        # Future LLM/RAG section:
        # - Load mapping rules and SQL_CONVERSION RAG examples.
        # - Generate target-dialect SQL from source_sql.
        # - On generation failure, persist FAIL-TOBE.
        return f"/* POC SQL_CONVERSION: TO_SQL generation node pending */\n{source_sql.strip()}"

    def _build_poc_bind_payload(self, job: dict[str, Any], source_sql: str, to_sql: str) -> tuple[str, str, str | None]:
        """Build bind SQL metadata for SELECT validation."""
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("bind_sql") or "").strip():
            bind_sql = str(job.get("bind_sql") or "")
            bind_set = str(job.get("bind_set") or "") or None
            return CONVERSION_PASS, bind_sql, bind_set

        bind_names = self._bind_names(f"{source_sql}\n{to_sql}")
        if not bind_names:
            return CONVERSION_PASS, "", None

        # Future LLM/RAG section:
        # - Generate BIND_SQL.
        # - Execute BIND_SQL and build up to three bind sets.
        columns = ", ".join(f"NULL AS {name}" for name in bind_names)
        bind_sql = f"SELECT {columns} FROM DUAL"
        bind_set = json.dumps([{name: None for name in bind_names}], ensure_ascii=False)
        return CONVERSION_PASS, bind_sql, bind_set

    def _build_poc_test_sql(self, job: dict[str, Any]) -> str:
        """Build a placeholder comparison SQL for SELECT validation."""
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("test_sql") or "").strip():
            return str(job.get("test_sql") or "")

        # Future LLM/RAG section:
        # - Generate TEST_SQL.
        # - Execute TEST_SQL and compare AS-IS/TO-BE row counts.
        # - On validation failure, persist FAIL-TEST and retry.
        return "SELECT 'POC' AS CHECK_NAME, 1 AS FROM_COUNT, 1 AS TO_COUNT FROM DUAL"

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
    ) -> dict[str, Any]:
        """Persist a SQL conversion failure using the source status values."""
        failure_attempts = attempts or [{"attempt": 1, "stage": self._failure_stage(status), "status": status, "reason": message}]
        if job.get("row_id"):
            update_values = {
                key: value
                for key, value in (partial_values or {}).items()
                if value not in (None, "")
            }
            update_values.update(
                {
                    "STATUS_CONVERSION": status,
                    "LOG": f"FINAL FAILURE stage=SQL_CONVERSION status={status} error={message}",
                    "RETRY_COUNT": self._configured_retry_limit(),
                }
            )
            self._update_row(
                db_config,
                str(job["row_id"]),
                update_values,
            )
            self._insert_sql_log(
                db_config,
                job,
                "SQL_CONVERSION",
                (partial_values or {}).get("TO_SQL"),
                status,
                self._retry_count(failure_attempts) + 1,
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
                "status_conversion": status,
                "conversion_status": status,
                "to_sql": (partial_values or {}).get("TO_SQL"),
                "bind_sql": (partial_values or {}).get("BIND_SQL"),
                "bind_set": (partial_values or {}).get("BIND_SET"),
                "test_sql": (partial_values or {}).get("TEST_SQL"),
                "next_node": "15C_sqlTuningOneJobPocExecutor" if payload.get("full_workflow") else "12D_sqlConversionIterationDashboard",
            },
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
        stages["conversion"] = {"ok": ok, "status": status, "message": message, "attempts": attempts}
        return {
            **payload,
            **extra,
            "component": "12C_sqlConversionOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_CONVERSION",
            "job_type": "SQL",
            "row_id": job.get("row_id") or payload.get("row_id"),
            "space_nm": job.get("space_nm") or payload.get("space_nm"),
            "sql_id": job.get("sql_id") or payload.get("sql_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "retry_count": self._retry_count(attempts),
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "db_status_updated": bool(job.get("row_id")),
        }

    def _failure_stage(self, status: str) -> str:
        """Return the conversion stage represented by a failure status."""
        if status == FAIL_BIND:
            return "GENERATE_BIND_SQL"
        if status == FAIL_TEST:
            return "VALIDATE_TEST_SQL"
        return "GENERATE_TOBE_SQL"

    def _initial_resume_stage(self, job: dict[str, Any], tag_kind: str, to_sql: str, bind_sql: str) -> str:
        """Resume user-corrected failed SQL rows from the next useful stage."""
        if str(job.get("user_edited") or "").strip().upper() != "Y":
            return "GENERATE_TOBE_SQL"
        if not str(to_sql or "").strip():
            return "GENERATE_TOBE_SQL"

        status = str(job.get("status_conversion") or "").strip().upper()
        if str(tag_kind or "").strip().upper() != "SELECT":
            return "SKIP_TEST_FOR_NON_SELECT"
        if status == FAIL_TEST and str(bind_sql or "").strip():
            return "GENERATE_TEST_SQL"
        if status in {FAIL_TOBE, FAIL_BIND, FAIL_TEST} or status.startswith("FAIL-"):
            return "GENERATE_BIND_SQL"
        return "GENERATE_TOBE_SQL"

    def _retry_count(self, attempts: list[dict[str, Any]]) -> int:
        """Return retries from attempt history."""
        max_attempt = 1
        for attempt in attempts:
            try:
                max_attempt = max(max_attempt, int(attempt.get("attempt") or 1))
            except (TypeError, ValueError):
                continue
        return max(max_attempt - 1, 0)

    def _load_sql_job(self, db_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Load one NEXT_SQL_INFO row by ROWID or by SPACE_NM + SQL_ID."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        aliases = [
            ("TAG_KIND", "tag_kind", "VARCHAR2(100)"),
            ("SPACE_NM", "space_nm", "VARCHAR2(4000)"),
            ("SQL_ID", "sql_id", "VARCHAR2(4000)"),
            ("FR_SQL", "fr_sql", "CLOB"),
            ("TARGET_TABLE", "target_table", "VARCHAR2(4000)"),
            ("EDIT_FR_SQL", "edit_fr_sql", "CLOB"),
            ("TO_SQL", "to_sql", "CLOB"),
            ("TUNED_TO_SQL", "tuned_to_sql", "CLOB"),
            ("STATUS_TUNING", "status_tuning", "VARCHAR2(100)"),
            ("BIND_SQL", "bind_sql", "CLOB"),
            ("BIND_SET", "bind_set", "CLOB"),
            ("TEST_SQL", "test_sql", "CLOB"),
            ("STATUS_CONVERSION", "status_conversion", "VARCHAR2(100)"),
            ("LOG", "log", "VARCHAR2(4000)"),
            ("TUNED_FR_SQL", "tuned_fr_sql", "CLOB"),
            ("USER_EDITED", "user_edited", "VARCHAR2(1)"),
            ("FORMATTED_SQL", "formatted_sql", "CLOB"),
            ("TUNED_RESULT", "tuned_result", "VARCHAR2(4000)"),
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
                raise ValueError("SQL job item requires row_id or space_nm+sql_id")
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
        """Increment NEXT_SQL_INFO.BATCH_CNT when a SQL conversion job starts."""
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

    def _source_sql(self, job: dict[str, Any]) -> str:
        """Return EDIT_FR_SQL first, otherwise FR_SQL."""
        edited = str(job.get("edit_fr_sql") or "").strip()
        return edited if edited else str(job.get("fr_sql") or "")

    def _sql_length_kind(self, sql_text: str) -> str:
        """Classify runtime SQL length using the as-is 5000 character threshold."""
        return "LONG" if len(str(sql_text or "")) > SQL_LENGTH_SHORT_MAX else "SHORT"

    def _build_poc_tuned_fr_sql(self, source_sql: str) -> str:
        """Create a placeholder TUNED_FR_SQL for the LONG SQL branch."""
        return f"/* POC SQL_TUNING pretune before conversion; RAG node pending */\n{source_sql.strip()}"

    def _bind_names(self, sql_text: str) -> list[str]:
        """Extract Oracle bind parameter names."""
        names = []
        for match in re.findall(r":([A-Za-z][A-Za-z0-9_]*)", sql_text or ""):
            if match not in names:
                names.append(match)
        return names

    def _poc_stage_failed(self, job: dict[str, Any], attempt: int, stage: str) -> bool:
        """Return a deterministic POC failure decision for one conversion stage."""
        probability = self._fail_probability(attempt, stage)
        if probability <= 0:
            return False
        seed = f"SQL_CONVERSION:{job.get('space_nm')}:{job.get('sql_id')}:{attempt}:{stage}"
        return random.Random(seed).random() < probability

    def _fail_probability(self, attempt: int, stage: str) -> float:
        """Return the fixed POC failure probability for each retry attempt."""
        base = max(0, min(100, POC_FAIL_PERCENT)) / 100
        return base if stage in {"GENERATE_TOBE_SQL", "GENERATE_BIND_SQL", "VALIDATE_TEST_SQL"} else 0.0

    def _max_retry(self) -> int:
        """Return bounded total attempts for the POC conversion loop."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(1, min(11, int(getattr(self, "_payload_max_retry") or 0) + 1))
        return max(1, min(11, int(getattr(self, "max_retry", None) or 2) + 1))

    def _configured_retry_limit(self) -> int:
        """Return the configured retry limit, not including the first attempt."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(0, min(10, int(getattr(self, "_payload_max_retry") or 0)))
        return max(0, min(10, int(getattr(self, "max_retry", None) or 2)))

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
            raise ValueError(f"12C SQL Conversion is not connected to database settings: missing {', '.join(missing)}")

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
