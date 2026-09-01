from __future__ import annotations

import logging
import json
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


TUNING_SUCCESS_STATUSES = {"PASS", "PASS-TUNING"}
FORMATTED = "FORMATTED"
FAIL_FORMATTING = "FAIL-FORMATTING"


class NewType17CSqlFormattingOneJobPocExecutor(Component):

    display_name = "17C SQL Formatting One Job POC Executor"
    description = "Formats one SQL job and stores FORMATTED_SQL without changing conversion/tuning statuses."
    name = "NewType17CSqlFormattingOneJobPocExecutor"
    icon = "TextCursorInput"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "START", "before run_job", 0]})
        try:
            """Run one formatting job or pass through when tuning did not pass."""
            started = time.perf_counter()
            payload = self._parse_payload(getattr(self, "job_item", ""))
            if not self._should_run_formatting(payload):
                result = self._component_pass_through(payload, started, "17C skipped because job_name is migration.")
                self.status = result
                __log_result = Data(data=result)
                logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "END", "after run_job", 0]})
                return __log_result
            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            job: dict[str, Any] = {}
            try:
                job = self._load_sql_job(db_config, payload)
                merged = {**job, **payload}

                tuning_status = self._status(merged.get("tuning_status") or merged.get("status_tuning") or job.get("status_tuning"))
                if not self._is_tuning_pass(tuning_status):
                    result = self._pass_through(
                        payload=merged,
                        job=job,
                        started=started,
                        status=self._status(merged.get("status")) or tuning_status or "NOT-RUN",
                        message=f"SQL formatting passed through without DB update because tuning status is {tuning_status or 'NULL'}.",
                    )
                    self.status = result
                    __log_result = Data(data=result)
                    logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "END", "after run_job", 0]})
                    return __log_result

                self._increment_batch_count(db_config, str(job["row_id"]))
                result = self._run_formatting(merged, job, db_config, started)
            except Exception as exc:
                result = self._finish_failure(payload, job, started, str(exc))
            self.status = result
            __log_result = Data(data=result)
            logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "END", "after run_job", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error run_job: {exc}", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "ERROR", "RUN_JOB", "ERROR", f"error run_job: {exc}", 0]})
            raise

    def _should_run_formatting(self, payload: dict[str, Any]) -> bool:
        return self._job_name(payload) in {"conversion", "tuning", "formatting"}

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
            "component": "17C_sqlFormattingOneJobPocExecutor",
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
            "pass_through_component": "17C",
            "message": payload.get("message") or message,
            "next_node": "18D_fullWorkflowDashboard",
        }
        history = list(result.get("history") or [])
        history.append({"step": "17C_pass_through", "message": message})
        result["history"] = history
        return result

    def _run_formatting(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Generate and store FORMATTED_SQL for one row."""
        existing_tuned_sql = str(payload.get("tuned_to_sql") or job.get("tuned_to_sql") or "").strip()
        base_to_sql = str(payload.get("to_sql") or job.get("to_sql") or "").strip()
        source_sql = existing_tuned_sql or self._build_poc_tuned_sql(base_to_sql)
        if not source_sql:
            return self._finish_failure(payload, job, started, "TUNED_TO_SQL/TO_SQL is empty")

        # Future LLM section:
        # - Call sql_indent_format_prompt.json through SqlLlmService.generate_formatted_sql().
        # - Store only FORMATTED_SQL; do not update STATUS_CONVERSION or STATUS_TUNING.
        formatted_sql = self._format_sql(source_sql)
        update_values = {"FORMATTED_SQL": formatted_sql}
        if not existing_tuned_sql and source_sql:
            update_values["TUNED_TO_SQL"] = source_sql
        self._update_row(db_config, job["row_id"], update_values)
        self._insert_sql_log(db_config, job, "FORMATTED_SQL", formatted_sql, "SUCCESS", 1, "GENERATE_FORMATTED_SQL", elapsed_seconds=time.perf_counter() - started)
        return self._result(
            payload=payload,
            job=job,
            ok=True,
            status=FORMATTED,
            elapsed=time.perf_counter() - started,
            attempts=[{"attempt": 1, "stage": "GENERATE_FORMATTED_SQL", "status": FORMATTED}],
            message="SQL formatting completed.",
            extra={
                "formatting_status": FORMATTED,
                "formatted_sql": formatted_sql,
                "tuned_to_sql": source_sql,
                "poc_tuned_to_sql_updated": not bool(existing_tuned_sql),
                "next_node": self._dashboard_node(payload),
            },
        )

    def _finish_failure(self, payload: dict[str, Any], job: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        """Return a formatting failure without changing DB statuses."""
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=FAIL_FORMATTING,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message=message,
            extra={"formatting_status": FAIL_FORMATTING, "next_node": self._dashboard_node(payload)},
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
        """Return the same job without DB updates when formatting is not eligible."""
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message=message,
            extra={"formatting_skipped": True, "next_node": self._dashboard_node(payload)},
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
        if not extra.get("formatting_skipped"):
            stages["formatting"] = {"ok": ok, "status": status, "message": message, "attempts": attempts}
        return {
            **payload,
            **extra,
            "component": "17C_sqlFormattingOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_FORMATTING",
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
            "db_status_updated": bool(job.get("row_id")) and ok,
        }

    def _format_sql(self, sql_text: str) -> str:
        """Apply a deterministic POC SQL layout until the LLM formatter is connected."""
        text = re.sub(r"\s+", " ", str(sql_text or "").strip())
        keywords = [
            "SELECT",
            "FROM",
            "WHERE",
            "GROUP BY",
            "ORDER BY",
            "HAVING",
            "UNION ALL",
            "UNION",
            "INSERT INTO",
            "VALUES",
            "UPDATE",
            "SET",
            "DELETE FROM",
        ]
        for keyword in sorted(keywords, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(keyword)}\b", f"\n{keyword}", text, flags=re.I)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _build_poc_tuned_sql(self, to_sql: str) -> str:
        """Create a deterministic POC TUNED_TO_SQL when formatting starts from TO_SQL."""
        text = str(to_sql or "").strip()
        if not text:
            return ""
        return text

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
                raise ValueError("SQL formatting item requires row_id or space_nm+sql_id")
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
        """Increment NEXT_SQL_INFO.BATCH_CNT when a SQL formatting job starts."""
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

    def _is_tuning_pass(self, value: Any) -> bool:
        """Return True when tuning passed under current or legacy status names."""
        return self._status(value) in TUNING_SUCCESS_STATUSES

    def _dashboard_node(self, payload: dict[str, Any]) -> str:
        """Return the dashboard that owns the current chained flow."""
        if payload.get("full_workflow"):
            return "18D_fullWorkflowDashboard"
        route = str(payload.get("job_route") or "").upper()
        if route == "SQL_CONVERSION":
            return "12D_sqlConversionIterationDashboard"
        if route == "SQL_TUNING":
            return "15D_sqlTuningIterationDashboard"
        return "17D_sqlFormattingIterationDashboard"

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
            raise ValueError(f"17C SQL Formatting is not connected to database settings: missing {', '.join(missing)}")

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
