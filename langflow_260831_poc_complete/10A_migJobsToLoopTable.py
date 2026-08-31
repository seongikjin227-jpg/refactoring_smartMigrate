from __future__ import annotations

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


class NewType10AMigJobsToLoopTable(Component):
    display_name = "10A MIG Jobs To Loop Table"
    description = "Converts selected MIG jobs into Loop input rows for one-job-at-a-time POC execution."
    name = "NewType10AMigJobsToLoopTable"
    icon = "Table"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    def build_jobs_table(self) -> DataFrame:
        # Build a Loop-compatible DataFrame where each row is one MIG job.
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        db_config = self._db_config(payload)
        self._require_db_config(db_config)
        jobs = self._sort_by_dependency(self._mig_jobs(payload, db_config))
        total = len(jobs)
        rows: list[dict[str, Any]] = []
        for index, job in enumerate(jobs, start=1):
            if job.get("map_id") is None or str(job.get("map_id")).strip() == "":
                raise ValueError(f"10A MIG job row {index} requires map_id")
            row = {
                **job,
                "component": "10A_migJobsToLoopTable",
                "job_route": "MIG",
                "job_type": "MIG",
                "run_mode": payload.get("run_mode") or "targeted",
                "job_index": index,
                "total_jobs": total,
                "completed_before": index - 1,
                "db_config": db_config,
                "history": list(payload.get("history") or []),
            }
            rows.append(row)
        status = {
            **payload,
            "component": "10A_migJobsToLoopTable",
            "loop_job_count": total,
            "next_node": "10B_migLoop",
        }
        self.status = status
        return DataFrame(rows)

    def _mig_jobs(self, payload: dict[str, Any], db_config: dict[str, Any]) -> list[dict[str, Any]]:
        requested = payload.get("requested_jobs") if isinstance(payload.get("requested_jobs"), dict) else {}
        jobs = payload.get("selected_jobs") or requested.get("migration_jobs") or payload.get("planned_jobs") or []
        if self._should_load_all_pending(payload, jobs):
            return self._load_all_pending_jobs(db_config)
        out = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("job_route") or job.get("job_type") or "MIG").upper() == "MIG":
                out.append(dict(job))
        return out

    def _should_load_all_pending(self, payload: dict[str, Any], jobs: Any) -> bool:
        if str(payload.get("run_mode") or "").lower() != "all_pending":
            return False
        return not isinstance(jobs, list) or not jobs

    def _load_all_pending_jobs(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_MIG_INFO", db_config)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_ID, PRIORITY, PRIOR_MAP_ID
                  FROM {table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'))
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                """
            )
            jobs: list[dict[str, Any]] = []
            for row in cur.fetchall():
                jobs.append(
                    {
                        "job_route": "MIG",
                        "job_type": "MIG",
                        "map_id": self._json_value(row[0]),
                        "priority": self._json_value(row[1]),
                        "prior_map_id": self._json_value(row[2]),
                    }
                )
            return jobs

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(payload_config.get("db_host") or getattr(self, "db_host", "") or "").strip(),
            "db_port": int(payload_config.get("db_port") or getattr(self, "db_port", None) or 1521),
            "db_service_name": str(payload_config.get("db_service_name") or getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(payload_config.get("db_username") or getattr(self, "db_username", "") or "").strip(),
            "db_password": str(payload_config.get("db_password") or "") or self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(payload_config.get("system_schema") or getattr(self, "system_schema", "") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"10A MIG is not connected to database settings: missing {', '.join(missing)}")

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

    def _qualify(self, table_name: str, db_config: dict[str, Any]) -> str:
        table = self._clean_identifier(table_name)
        schema = str(db_config.get("system_schema") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

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

    def _sort_by_dependency(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Order jobs so an included PRIOR_MAP_ID runs before its dependent job.
        indexed = [(index, job) for index, job in enumerate(jobs)]
        by_map_id = {self._to_int(job.get("map_id")): (index, job) for index, job in indexed if self._to_int(job.get("map_id")) is not None}
        visited: set[int] = set()
        visiting: set[int] = set()
        ordered: list[dict[str, Any]] = []

        def base_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            index, job = item
            priority = self._to_int(job.get("priority"))
            map_id = self._to_int(job.get("map_id"))
            return (priority if priority is not None else 999999999, map_id if map_id is not None else 999999999, index)

        def visit(map_id: int) -> None:
            if map_id in visited:
                return
            if map_id in visiting:
                raise ValueError(f"10A MIG dependency cycle detected at map_id={map_id}")
            item = by_map_id.get(map_id)
            if item is None:
                return
            visiting.add(map_id)
            prior = self._to_int(item[1].get("prior_map_id"))
            if prior is not None and prior > 0 and prior in by_map_id:
                visit(prior)
            visiting.remove(map_id)
            visited.add(map_id)
            ordered.append(dict(item[1]))

        for _, job in sorted(indexed, key=base_key):
            map_id = self._to_int(job.get("map_id"))
            if map_id is None:
                ordered.append(dict(job))
                continue
            visit(map_id)
        return ordered

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
