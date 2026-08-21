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

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType10CMigOneJobPocExecutor(Component):
    display_name = "10C MIG One Job POC Executor"
    description = "Runs one DB Migration POC job with real DB status/log updates and internal retry."
    name = "NewType10CMigOneJobPocExecutor"
    icon = "DatabaseZap"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=3, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job")]

    def run_job(self) -> Data:
        started = time.perf_counter()
        job = self._parse_payload(getattr(self, "job_item", ""))
        map_id = self._to_int(job.get("map_id"))
        if map_id is None:
            raise ValueError("MIG job item requires map_id")
        max_retry = max(0, int(getattr(self, "max_retry", None) or 3))
        db_config = self._db_config(job)
        attempts: list[dict[str, Any]] = []

        try:
            dep_status = self._dependency_status(db_config, map_id, job.get("prior_map_id"))
            if dep_status != "READY":
                elapsed = int(time.perf_counter() - started)
                result = self._result(job, ok=False, status="WAITING", elapsed=elapsed, attempts=attempts)
                result.update(
                    {
                        "error_type": "DEPENDENCY_WAIT",
                        "message": f"prior_map_id={job.get('prior_map_id')} status={dep_status}",
                    }
                )
                self.status = result
                return Data(data=result)

            self._mark_running(db_config, map_id)

            final_status = "FAIL-TEST"
            final_ok = False
            retry_count = 0
            message = ""
            for attempt in range(1, max_retry + 2):
                attempt_result = self._run_pipeline_attempt(job, map_id, attempt)
                retry_count = attempt - 1
                attempts.append(attempt_result)
                final_status = attempt_result["status"]
                final_ok = bool(attempt_result["ok"])
                message = str(attempt_result["message"])

                if final_ok:
                    elapsed = int(time.perf_counter() - started)
                    self._update_job(db_config, map_id, "PASS", elapsed, retry_count)
                    self._insert_log(
                        db_config,
                        map_id,
                        "POC_FINAL",
                        "INFO",
                        "VERIFY",
                        "PASS",
                        message,
                        retry_count,
                        attempt_result.get("migration_sql", ""),
                    )
                    break

                if retry_count < max_retry:
                    self._update_job(db_config, map_id, final_status, 0, retry_count + 1)
                    self._insert_log(
                        db_config,
                        map_id,
                        "POC_RETRY",
                        "WARN",
                        attempt_result["failed_stage"],
                        final_status,
                        message,
                        retry_count + 1,
                        attempt_result.get("migration_sql", ""),
                    )
                    continue

                elapsed = int(time.perf_counter() - started)
                self._update_job(db_config, map_id, final_status, elapsed, retry_count)
                self._insert_log(
                    db_config,
                    map_id,
                    "POC_FINAL",
                    "ERROR",
                    attempt_result["failed_stage"],
                    final_status,
                    message,
                    retry_count,
                    attempt_result.get("migration_sql", ""),
                )
                break

            elapsed = int(time.perf_counter() - started)
            result = self._result(job, ok=final_ok, status="PASS" if final_ok else final_status, elapsed=elapsed, attempts=attempts)
            result.update(
                {
                    "retry_count": retry_count,
                    "message": message,
                    "next_node": "10D_migIterationDashboard",
                }
            )
            self.status = result
            return Data(data=result)
        except Exception as exc:
            elapsed = int(time.perf_counter() - started)
            result = self._result(job, ok=False, status="ERROR", elapsed=elapsed, attempts=attempts)
            result.update({"error": str(exc), "message": f"POC executor error: {exc}"})
            self.status = result
            return Data(data=result)

    def _run_pipeline_attempt(
        self,
        job: dict[str, Any],
        map_id: int,
        attempt: int,
    ) -> dict[str, Any]:
        # Dependency and DDL loading are real. Generation/execution/verification are POC stubs.
        steps: list[dict[str, Any]] = []
        context: dict[str, Any] = {"job": job, "map_id": map_id, "attempt": attempt}

        for node in (
            self._node_fetch_ddl,
            self._node_generate_sql,
            self._node_execute_sql,
            self._node_verify,
        ):
            step = node(context)
            steps.append(step)
            context.update(step.get("outputs") or {})
            if step.get("status") != "PASS":
                return {
                    "attempt": attempt,
                    "ok": False,
                    "failed_stage": step["stage"],
                    "status": step["status"],
                    "message": step["message"],
                    "migration_sql": context.get("migration_sql", ""),
                    "verification_sql": context.get("verification_sql", ""),
                    "steps": steps,
                }

        return {
            "attempt": attempt,
            "ok": True,
            "failed_stage": "",
            "status": "PASS",
            "message": f"[POC] map_id={map_id} attempt={attempt} migration pipeline passed",
            "migration_sql": context.get("migration_sql", ""),
            "verification_sql": context.get("verification_sql", ""),
            "steps": steps,
        }

    def _node_fetch_ddl(self, context: dict[str, Any]) -> dict[str, Any]:
        map_id = self._to_int(context.get("map_id"))
        db_config = self._db_config(context["job"])
        metadata = self._load_mig_metadata(db_config, map_id)
        return {
            "stage": "FETCH_DDL",
            "status": "PASS",
            "message": "[REAL] migration mapping and DDL metadata loaded",
            "outputs": {
                **metadata,
            },
        }

    def _node_generate_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        job = context["job"]
        rng = self._rng(context, "GENERATE_SQL")
        if rng.random() < self._fail_probability(context["attempt"], "GENERATE_SQL"):
            return {
                "stage": "GENERATE_SQL",
                "status": "FAIL-INSERT",
                "message": "[POC] migration SQL generation failed",
                "outputs": {"migration_sql": "", "verification_sql": ""},
            }
        map_id = job.get("map_id")
        target_table = context.get("to_table") or job.get("to_table") or "POC_TARGET"
        source_table = context.get("fr_table") or job.get("fr_table") or "POC_SOURCE"
        mapped_columns = self._mapped_columns(context.get("mapping_details") or [])
        if mapped_columns:
            to_cols = ", ".join(item["to_col"] for item in mapped_columns)
            fr_cols = ", ".join(item["fr_col"] for item in mapped_columns)
            migration_sql = f"INSERT INTO {target_table} ({to_cols}) SELECT {fr_cols} FROM {source_table} /* POC map_id={map_id} */"
        else:
            migration_sql = f"INSERT INTO {target_table} SELECT * FROM {source_table} /* POC map_id={map_id} */"
        verification_sql = f"SELECT 0 AS DIFF_TOT FROM DUAL /* POC verify map_id={map_id} */"
        return {
            "stage": "GENERATE_SQL",
            "status": "PASS",
            "message": "[POC] migration and verification SQL generated",
            "outputs": {"migration_sql": migration_sql, "verification_sql": verification_sql},
        }

    def _node_execute_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        rng = self._rng(context, "EXECUTE_SQL")
        if rng.random() < self._fail_probability(context["attempt"], "EXECUTE_SQL"):
            return {
                "stage": "EXECUTE_SQL",
                "status": "FAIL-INSERT",
                "message": "[POC] migration SQL execution failed",
                "outputs": {"affected_rows": 0},
            }
        return {
            "stage": "EXECUTE_SQL",
            "status": "PASS",
            "message": "[POC] migration SQL executed",
            "outputs": {"affected_rows": self._rng(context, "ROWS").randint(1, 500)},
        }

    def _node_verify(self, context: dict[str, Any]) -> dict[str, Any]:
        rng = self._rng(context, "VERIFY")
        if rng.random() < self._fail_probability(context["attempt"], "VERIFY"):
            return {
                "stage": "VERIFY",
                "status": "FAIL-TEST",
                "message": "[POC] verification SQL returned differences",
                "outputs": {"diff_count": rng.randint(1, 20)},
            }
        return {
            "stage": "VERIFY",
            "status": "PASS",
            "message": "[POC] verification SQL passed",
            "outputs": {"diff_count": 0},
        }

    def _rng(self, context: dict[str, Any], node_name: str) -> random.Random:
        job = context["job"]
        seed = f"MIG:{job.get('map_id')}:{job.get('job_index')}:{context.get('attempt')}:{node_name}"
        return random.Random(seed)

    def _fail_probability(self, attempt: int, node_name: str) -> float:
        base = {
            "GENERATE_SQL": 0.25,
            "EXECUTE_SQL": 0.35,
            "VERIFY": 0.30,
        }.get(node_name, 0.0)
        return max(0.05, base - ((attempt - 1) * 0.15))

    def _result(self, job: dict[str, Any], *, ok: bool, status: str, elapsed: int, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        total = int(job.get("total_jobs") or 1)
        index = int(job.get("job_index") or 1)
        return {
            **job,
            "component": "10C_migOneJobPocExecutor",
            "job_type": "MIG",
            "map_id": job.get("map_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": elapsed,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
        }

    def _dependency_status(self, db_config: dict[str, Any], map_id: int, prior_map_id: Any) -> str:
        prior = self._to_int(prior_map_id)
        if prior is None or prior <= 0:
            return "READY"
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT STATUS FROM {table} WHERE MAP_ID = :1", [prior])
            row = cur.fetchone()
        if not row:
            return "PENDING"
        status = str(row[0] or "").strip().upper()
        return "READY" if status == "PASS" else (status or "PENDING")

    def _mark_running(self, db_config: dict[str, Any], map_id: int) -> None:
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = :1,
                       BATCH_CNT = NVL(BATCH_CNT, 0) + 1,
                       UPD_TS = CURRENT_TIMESTAMP
                 WHERE MAP_ID = :2
                """,
                ["RUNNING", map_id],
            )
            conn.commit()

    def _update_job(self, db_config: dict[str, Any], map_id: int, status: str, elapsed: int, retry_count: int) -> None:
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = :1,
                       ELAPSED_SECONDS = :2,
                       RETRY_COUNT = :3,
                       UPD_TS = CURRENT_TIMESTAMP
                 WHERE MAP_ID = :4
                """,
                [status, elapsed, retry_count, map_id],
            )
            conn.commit()

    def _insert_log(
        self,
        db_config: dict[str, Any],
        map_id: int,
        log_type: str,
        log_level: str,
        step_name: str,
        status: str,
        message: str,
        retry_count: int,
        generated_sql: str = "",
    ) -> None:
        table = self._qualify("NEXT_MIG_LOG", db_config.get("system_schema"))
        sequence = self._qualify("MIGRATION_LOG_SEQ", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        ts_columns = [column for column in ("CREATED_AT", "UPD_TS") if column in columns]
        generate_sql_column = ", GENERATE_SQL" if "GENERATE_SQL" in columns else ""
        generate_sql_value = ", :9" if "GENERATE_SQL" in columns else ""
        ts_column_sql = "".join(f", {column}" for column in ts_columns)
        ts_value_sql = "".join(", CURRENT_TIMESTAMP" for _ in ts_columns)
        params = [map_id, "DB_MIG_POC", log_type, log_level, step_name, status, str(message)[:4000], retry_count]
        if "GENERATE_SQL" in columns:
            params.append(str(generated_sql or "")[:4000])
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT{generate_sql_column}{ts_column_sql}
                ) VALUES ({sequence}.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8{generate_sql_value}{ts_value_sql})
                """,
                params,
            )
            conn.commit()

    def _load_mig_metadata(self, db_config: dict[str, Any], map_id: int | None) -> dict[str, Any]:
        if map_id is None:
            raise ValueError("FETCH_DDL requires map_id")
        info_table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        detail_table = self._qualify("NEXT_MIG_INFO_DTL", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_TYPE,
                       FR_TABLE,
                       TO_TABLE,
                       TRUNC_YN,
                       CONDITION,
                       MIG_SQL,
                       VERIFY_SQL,
                       USER_EDITED
                  FROM {info_table}
                 WHERE MAP_ID = :1
                """,
                [map_id],
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_MIG_INFO row not found: map_id={map_id}")
            cur.execute(
                f"""
                SELECT MAP_DTL,
                       FR_COL,
                       TO_COL
                  FROM {detail_table}
                 WHERE MAP_ID = :1
                 ORDER BY MAP_DTL
                """,
                [map_id],
            )
            detail_rows = cur.fetchall()

        map_type = self._lob_to_str(row[0]) or "TABLE"
        fr_table = self._lob_to_str(row[1])
        to_table = self._lob_to_str(row[2])
        details = [
            {
                "map_dtl": item[0],
                "fr_col": self._lob_to_str(item[1]),
                "to_col": self._lob_to_str(item[2]),
            }
            for item in detail_rows
        ]
        return {
            "map_type": map_type,
            "fr_table": fr_table,
            "to_table": to_table,
            "trunc_yn": self._lob_to_str(row[3]),
            "condition": self._lob_to_str(row[4]),
            "saved_migration_sql": self._lob_to_str(row[5]),
            "saved_verification_sql": self._lob_to_str(row[6]),
            "user_edited": self._lob_to_str(row[7]),
            "mapping_details": details,
            "source_ddl": self._fetch_table_columns(db_config, fr_table) if self._looks_like_table(fr_table) else [],
            "target_ddl": self._fetch_table_columns(db_config, to_table) if self._looks_like_table(to_table) else [],
        }

    def _fetch_table_columns(self, db_config: dict[str, Any], table: str) -> list[dict[str, Any]]:
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, NULLABLE
                  FROM ALL_TAB_COLUMNS
                 WHERE OWNER = :1
                   AND TABLE_NAME = :2
                 ORDER BY COLUMN_ID
            """
            params = [owner, table_name]
        else:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, NULLABLE
                  FROM USER_TAB_COLUMNS
                 WHERE TABLE_NAME = :1
                 ORDER BY COLUMN_ID
            """
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [
                {
                    "column_name": self._lob_to_str(row[0]),
                    "data_type": self._lob_to_str(row[1]),
                    "data_length": row[2],
                    "data_precision": row[3],
                    "data_scale": row[4],
                    "nullable": self._lob_to_str(row[5]),
                }
                for row in cur.fetchall()
            ]

    def _mapped_columns(self, details: list[dict[str, Any]]) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for item in details:
            fr_col = str(item.get("fr_col") or "").strip()
            to_col = str(item.get("to_col") or "").strip()
            if fr_col and to_col:
                columns.append({"fr_col": fr_col, "to_col": to_col})
        return columns

    def _looks_like_table(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if re.search(r"\bSELECT\b|\bWITH\b|\s", text, flags=re.I):
            return False
        parts = text.split(".")
        return all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", part.strip()) for part in parts)

    def _lob_to_str(self, value: Any) -> str:
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
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

    def _db_config(self, job: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(job.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
        }

    def _qualify(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip().upper()
        if "." in value:
            return value
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if clean_schema:
            clean_schema = self._clean_identifier(clean_schema)
            return f"{clean_schema}.{clean_table}"
        return clean_table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

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
            raise ValueError("job_item must be a JSON object")
        return parsed

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
