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


CONVERSION_PASS = "PASS-CONVERSION"
TUNING_READY = "READY"
FAIL_TOBE = "FAIL-TOBE"
FAIL_BIND = "FAIL-BIND"
FAIL_TEST = "FAIL-TEST"
SQL_LENGTH_SHORT_MAX = 5000


class NewType12CSqlConversionOneJobPocExecutor(Component):
    display_name = "12C SQL Conversion One Job POC Executor"
    description = "Runs one SQL Conversion POC job with DB status updates and downstream tuning eligibility."
    name = "NewType12CSqlConversionOneJobPocExecutor"
    icon = "FileCode"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=3, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        """Run one SQL conversion job and return a payload for 15C."""
        started = time.perf_counter()
        payload = self._parse_payload(getattr(self, "job_item", ""))
        db_config = self._db_config(payload)
        job: dict[str, Any] = {}
        try:
            job = self._load_sql_job(db_config, payload)
            result = self._run_conversion(payload, job, db_config, started)
        except Exception as exc:
            result = self._finish_failure(payload, job, db_config, started, FAIL_TOBE, str(exc))
        self.status = result
        return Data(data=result)

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

        tag_kind = str(job.get("tag_kind") or "").strip().upper()
        attempts: list[dict[str, Any]] = []
        source_for_conversion, tuned_fr_sql, sql_length = self._prepare_conversion_source(job, db_config, source_sql)
        to_sql = self._build_poc_to_sql(job, source_for_conversion)
        attempts.append({"attempt": 1, "stage": "GENERATE_TOBE_SQL", "status": CONVERSION_PASS, "sql_length": len(to_sql)})

        if tag_kind == "SELECT":
            bind_status, bind_sql, bind_set = self._build_poc_bind_payload(job, source_for_conversion, to_sql)
            attempts.append({"attempt": 1, "stage": "GENERATE_BIND_SQL", "status": bind_status})
            if bind_status != CONVERSION_PASS:
                return self._finish_failure(payload, job, db_config, started, FAIL_BIND, "Bind SQL generation failed", attempts)
            test_sql = self._build_poc_test_sql(job)
            attempts.append({"attempt": 1, "stage": "GENERATE_TEST_SQL", "status": CONVERSION_PASS})
        else:
            bind_sql = ""
            bind_set = None
            test_sql = ""
            attempts.append({"attempt": 1, "stage": "SKIP_TEST_FOR_NON_SELECT", "status": CONVERSION_PASS, "tag_kind": tag_kind or "UNKNOWN"})

        final_log = (
            f"FINAL SUCCESS stage=SQL_CONVERSION status={CONVERSION_PASS} "
            f"job={job.get('space_nm')}.{job.get('sql_id')} reason=TAG_KIND:{tag_kind or 'UNKNOWN'}"
        )
        self._update_row(
            db_config,
            job["row_id"],
            {
                "TO_SQL": to_sql,
                "BIND_SQL": bind_sql,
                "BIND_SET": bind_set,
                "TEST_SQL": test_sql,
                "STATUS_CONVERSION": CONVERSION_PASS,
                "STATUS_TUNING": TUNING_READY,
                "TUNED_FR_SQL": tuned_fr_sql,
                "LOG": final_log,
                "RETRY_COUNT": 0,
            },
        )
        return self._result(
            payload=payload,
            job=job,
            ok=True,
            status=CONVERSION_PASS,
            elapsed=time.perf_counter() - started,
            attempts=attempts,
            message="SQL conversion completed. Tuning is READY.",
            extra={
                "status_conversion": CONVERSION_PASS,
                "conversion_status": CONVERSION_PASS,
                "status_tuning": TUNING_READY,
                "tuning_status": TUNING_READY,
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
    ) -> dict[str, Any]:
        """Persist a SQL conversion failure using the source status values."""
        if job.get("row_id"):
            self._update_row(
                db_config,
                str(job["row_id"]),
                {
                    "STATUS_CONVERSION": status,
                    "LOG": f"FINAL FAIL stage=SQL_CONVERSION status={status} error={message}",
                    "RETRY_COUNT": int(getattr(self, "max_retry", None) or 3),
                },
            )
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=attempts or [],
            message=message,
            extra={
                "status_conversion": status,
                "conversion_status": status,
                "next_node": "12D_sqlConversionIterationDashboard",
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
        stages["conversion"] = {"ok": ok, "status": status, "message": message}
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
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "db_status_updated": bool(job.get("row_id")),
        }

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
