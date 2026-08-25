from __future__ import annotations

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


CONVERSION_SUCCESS_STATUSES = {"PASS", "PASS-CONVERSION"}
TUNING_PASS = "PASS-TUNING"
FAIL_TUNED = "FAIL-TUNED"
FAIL_TEST = "FAIL-TEST"


class NewType15CSqlTuningOneJobPocExecutor(Component):
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
        """Run one tuning job or pass through when upstream conversion failed."""
        started = time.perf_counter()
        payload = self._parse_payload(getattr(self, "job_item", ""))
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
                return Data(data=result)

            result = self._run_tuning(merged, job, db_config, started)
        except Exception as exc:
            result = self._finish_failure(payload, job, db_config, started, FAIL_TUNED, str(exc))
        self.status = result
        return Data(data=result)

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

        # Future LLM/RAG section:
        # - Retrieve SQL_TUNING SEARCH rules and store BLOCK_RAG_CONTENT.
        # - Call tune_tobe_sql().
        # - If no rule is found in the real path, persist FAIL-TUNED.
        #
        # Current POC keeps TO_SQL unchanged so conversion-passed rows can continue
        # through the same Langflow request into formatting.
        tuned_sql = to_sql
        tuned_result = "NO TUNING"
        tuning_guides = self._poc_tuning_guides(tag_kind, tuned_result)
        attempts.append(
            {
                "attempt": 1,
                "stage": "APPLY_TUNING_RULES",
                "status": TUNING_PASS,
                "result": tuned_result,
                "guide_ids": [guide["guide_id"] for guide in tuning_guides],
            }
        )

        if tag_kind == "SELECT" and tuned_sql.strip() != to_sql.strip():
            # Future validation section:
            # - Generate tuned comparison TEST_SQL.
            # - Execute against the same BIND_SET.
            # - Retry tuning until max_retry is exhausted, then persist FAIL-TEST.
            attempts.append({"attempt": 1, "stage": "VALIDATE_TUNED_SQL", "status": TUNING_PASS})
        else:
            reason = "NO_TUNING" if tuned_sql.strip() == to_sql.strip() else f"TAG_KIND:{tag_kind or 'UNKNOWN'}"
            attempts.append({"attempt": 1, "stage": "SKIP_TUNED_VALIDATION", "status": TUNING_PASS, "reason": reason})

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
                "RETRY_COUNT": 0,
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

    def _finish_failure(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
        status: str,
        message: str,
    ) -> dict[str, Any]:
        """Persist a SQL tuning failure using the source status values."""
        failure_attempts = [{"attempt": 1, "stage": "APPLY_TUNING_RULES", "status": status, "reason": message}]
        if job.get("row_id"):
            self._update_row(
                db_config,
                str(job["row_id"]),
                {
                    "STATUS_TUNING": status,
                    "TUNED_RESULT": message[:4000],
                    "LOG": f"FINAL FAIL stage=SQL_TUNING status={status} error={message}",
                    "RETRY_COUNT": int(getattr(self, "max_retry", None) or 2),
                },
            )
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=failure_attempts,
            message=message,
            extra={"status_tuning": status, "tuning_status": status, "next_node": self._dashboard_node(payload)},
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
                    "The POC keeps TO_SQL unchanged, records TUNED_RESULT='NO TUNING', "
                    "and continues to formatting when conversion passed."
                ),
            }
        ]

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

    def _status(self, value: Any) -> str:
        """Normalize a status string for comparisons."""
        return str(value or "").strip().upper()

    def _is_conversion_pass(self, value: Any) -> bool:
        """Return True when conversion passed under current or legacy status names."""
        return self._status(value) in CONVERSION_SUCCESS_STATUSES

    def _dashboard_node(self, payload: dict[str, Any]) -> str:
        """Return the dashboard that owns the current chained flow."""
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
