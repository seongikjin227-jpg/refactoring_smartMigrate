from __future__ import annotations

import json
import random
import re
import time
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


FAILURE_STAGES = [
    ("TRUNCATE", "FAIL-TRUNCATE"),
    ("GENERATE_SQL", "FAIL-INSERT"),
    ("INSERT", "FAIL-INSERT"),
    ("VERIFY", "FAIL-TEST"),
]


class NewType10CMigOneJobPocExecutor(Component):
    display_name = "10C MIG One Job POC Executor"
    description = "Runs one DB Migration POC job with real DB status/log updates and internal retry."
    name = "NewType10CMigOneJobPocExecutor"
    icon = "DatabaseZap"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=3, required=False),
        StrInput(name="random_seed", display_name="Random Seed", value="", required=False),
        BoolInput(name="dry_run", display_name="Dry Run", value=False, required=False),
        StrInput(name="db_host", display_name="DB Host Override", required=False),
        IntInput(name="db_port", display_name="DB Port Override", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name Override", required=False),
        StrInput(name="db_username", display_name="DB Username Override", required=False),
        SecretStrInput(name="db_password", display_name="DB Password Override", required=False),
        StrInput(name="system_schema", display_name="System Schema Override", required=False),
        StrInput(name="migration_log_sequence", display_name="Migration Log Sequence Override", value="", required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job")]

    def run_job(self) -> Data:
        started = time.perf_counter()
        job = self._parse_payload(getattr(self, "job_item", ""))
        map_id = self._to_int(job.get("map_id"))
        if map_id is None:
            raise ValueError("MIG job item requires map_id")
        max_retry = max(0, int(getattr(self, "max_retry", None) or 3))
        dry_run = bool(getattr(self, "dry_run", False))
        db_config = self._db_config(job)
        attempts: list[dict[str, Any]] = []

        try:
            dep_status = self._dependency_status(db_config, map_id, job.get("prior_map_id"), dry_run=dry_run)
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

            if not dry_run:
                self._mark_running(db_config, map_id)

            final_status = "FAIL-TEST"
            final_ok = False
            retry_count = 0
            message = ""
            for attempt in range(1, max_retry + 2):
                attempt_result = self._poc_attempt(job, attempt)
                retry_count = attempt - 1
                attempts.append(attempt_result)
                final_status = attempt_result["status"]
                final_ok = bool(attempt_result["ok"])
                message = str(attempt_result["message"])

                if final_ok:
                    elapsed = int(time.perf_counter() - started)
                    if not dry_run:
                        self._update_job(db_config, map_id, "PASS", elapsed, retry_count)
                        self._insert_log(db_config, map_id, "POC_FINAL", "INFO", "VERIFY", "PASS", message, retry_count)
                    break

                if retry_count < max_retry:
                    if not dry_run:
                        self._update_job(db_config, map_id, final_status, 0, retry_count + 1)
                        self._insert_log(
                            db_config,
                            map_id,
                            "POC_RETRY",
                            "WARN",
                            attempt_result["stage"],
                            final_status,
                            message,
                            retry_count + 1,
                        )
                    continue

                elapsed = int(time.perf_counter() - started)
                if not dry_run:
                    self._update_job(db_config, map_id, final_status, elapsed, retry_count)
                    self._insert_log(
                        db_config,
                        map_id,
                        "POC_FINAL",
                        "ERROR",
                        attempt_result["stage"],
                        final_status,
                        message,
                        retry_count,
                    )
                break

            elapsed = int(time.perf_counter() - started)
            result = self._result(job, ok=final_ok, status="PASS" if final_ok else final_status, elapsed=elapsed, attempts=attempts)
            result.update(
                {
                    "retry_count": retry_count,
                    "message": message,
                    "dry_run": dry_run,
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

    def _poc_attempt(self, job: dict[str, Any], attempt: int) -> dict[str, Any]:
        map_id = job.get("map_id")
        run_id = job.get("run_id") or "MIG-POC"
        seed_prefix = str(getattr(self, "random_seed", "") or "").strip()
        seed = f"{seed_prefix}:{run_id}:MIG:{map_id}:{attempt}"
        rng = random.Random(seed)
        fail_probability = max(0.05, 0.65 - ((attempt - 1) * 0.25))
        failed = rng.random() < fail_probability
        if failed:
            stage, status = rng.choice(FAILURE_STAGES)
            return {
                "attempt": attempt,
                "ok": False,
                "stage": stage,
                "status": status,
                "message": f"[POC] map_id={map_id} attempt={attempt} failed at {stage}",
                "trace_id": f"TEST-{rng.randint(1000, 9999)}",
            }
        return {
            "attempt": attempt,
            "ok": True,
            "stage": "VERIFY",
            "status": "PASS",
            "message": f"[POC] map_id={map_id} attempt={attempt} passed",
            "trace_id": f"TEST-{rng.randint(1000, 9999)}",
        }

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

    def _dependency_status(self, db_config: dict[str, Any], map_id: int, prior_map_id: Any, *, dry_run: bool) -> str:
        prior = self._to_int(prior_map_id)
        if prior is None or prior <= 0:
            return "READY"
        if dry_run:
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
    ) -> None:
        table = self._qualify("NEXT_MIG_LOG", db_config.get("system_schema"))
        sequence = self._qualify(str(db_config.get("migration_log_sequence") or "MIGRATION_LOG_SEQ"), db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        ts_columns = [column for column in ("CREATED_AT", "UPD_TS") if column in columns]
        generate_sql_column = ", GENERATE_SQL" if "GENERATE_SQL" in columns else ""
        generate_sql_value = ", :9" if "GENERATE_SQL" in columns else ""
        ts_column_sql = "".join(f", {column}" for column in ts_columns)
        ts_value_sql = "".join(", CURRENT_TIMESTAMP" for _ in ts_columns)
        params = [map_id, "DB_MIG_POC", log_type, log_level, step_name, status, str(message)[:4000], retry_count]
        if "GENERATE_SQL" in columns:
            params.append("")
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
        override_password = self._secret_to_str(getattr(self, "db_password", None))
        return {
            "db_host": str(getattr(self, "db_host", "") or item_config.get("db_host") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or item_config.get("db_port") or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or item_config.get("db_service_name") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or item_config.get("db_username") or "").strip(),
            "db_password": override_password or str(item_config.get("db_password") or ""),
            "system_schema": str(getattr(self, "system_schema", "") or item_config.get("system_schema") or "").strip(),
            "migration_log_sequence": str(
                getattr(self, "migration_log_sequence", "") or item_config.get("migration_log_sequence") or "MIGRATION_LOG_SEQ"
            ).strip(),
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

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
