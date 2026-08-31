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


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")
ROUTE_LABELS = {
    "MIG": "DB Migration",
    "SQL_CONVERSION": "SQL Conversion",
    "SQL_TUNING": "SQL Tuning",
    "SQL_FORMATTING": "SQL Formatting",
}


class NewType18AFullWorkflowJobsToLoopTable(Component):
    display_name = "18A Full Workflow Jobs To Loop Table"
    description = "Builds one ordered Full Workflow queue: DB Migration, SQL Conversion, SQL Tuning, SQL Formatting."
    name = "NewType18AFullWorkflowJobsToLoopTable"
    icon = "ListOrdered"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    def build_jobs_table(self) -> DataFrame:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        db_config = self._db_config(payload)
        max_retry = max(0, int(getattr(self, "max_retry", None) or 2))
        grouped = self._group_jobs(payload, db_config)
        grouped["MIG"] = self._sort_migration_jobs(grouped["MIG"])

        total = sum(len(grouped[route]) for route in ROUTE_ORDER)
        rows: list[dict[str, Any]] = []
        global_index = 0
        route_totals = {route: len(grouped[route]) for route in ROUTE_ORDER}

        for phase_index, route in enumerate(ROUTE_ORDER, start=1):
            route_jobs = grouped[route]
            for route_index, job in enumerate(route_jobs, start=1):
                global_index += 1
                self._validate_job(route, job, route_index)
                rows.append(
                    {
                        **job,
                        "component": "18A_fullWorkflowJobsToLoopTable",
                        "job_route": route,
                        "planned_job_route": route,
                        "job_name": self._job_name(route),
                        "job_type": "MIG" if route == "MIG" else "SQL",
                        "route_label": ROUTE_LABELS[route],
                        "run_mode": payload.get("run_mode") or "all_pending",
                        "full_workflow": True,
                        "phase_index": phase_index,
                        "phase_count": len(ROUTE_ORDER),
                        "route_job_index": route_index,
                        "route_total_jobs": route_totals[route],
                        "job_index": global_index,
                        "total_jobs": total,
                        "completed_before": global_index - 1,
                        "max_retry": max_retry,
                        "db_config": db_config,
                        "history": list(payload.get("history") or []),
                        "workflow_plan_counts": dict(route_totals),
                    }
                )

        status = {
            **payload,
            "component": "18A_fullWorkflowJobsToLoopTable",
            "job_route": "FULL_WORKFLOW",
            "full_workflow": True,
            "loop_job_count": total,
            "workflow_plan_counts": route_totals,
            "planned_jobs": rows,
            "next_node": "18B_fullWorkflowLoop",
        }
        self.status = status
        return DataFrame(rows)

    def _group_jobs(self, payload: dict[str, Any], db_config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {route: [] for route in ROUTE_ORDER}
        explicit_jobs = payload.get("selected_jobs")
        if isinstance(explicit_jobs, list) and explicit_jobs:
            for job in explicit_jobs:
                if not isinstance(job, dict):
                    continue
                route = self._normalize_route(job.get("job_route") or job.get("planned_job_route"))
                if route in grouped:
                    grouped[route].append(dict(job))
            return grouped

        requested = payload.get("requested_jobs") if isinstance(payload.get("requested_jobs"), dict) else {}
        jobs = payload.get("remaining_jobs") or payload.get("pending_jobs") or requested or {}
        sources = {
            "MIG": jobs.get("migration_jobs") or [],
            "SQL_CONVERSION": jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [],
            "SQL_TUNING": jobs.get("sql_tuning_jobs") or [],
            "SQL_FORMATTING": jobs.get("sql_formatting_jobs") or [],
        }
        for route, route_jobs in sources.items():
            grouped[route] = [dict(job) for job in route_jobs if isinstance(job, dict)]
        if not any(grouped.values()) and str(payload.get("run_mode") or "").lower() == "all_pending":
            return self._load_all_pending_jobs(db_config)
        return grouped

    def _load_all_pending_jobs(self, db_config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        self._require_db_config(db_config)
        mig_table = self._qualify("NEXT_MIG_INFO", db_config)
        sql_table = self._qualify("NEXT_SQL_INFO", db_config)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            return {
                "MIG": self._query_jobs(
                    cur,
                    f"""
                    SELECT MAP_ID, PRIORITY, PRIOR_MAP_ID
                      FROM {mig_table}
                     WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                       AND (STATUS IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'))
                     ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                    """,
                    "MIG",
                    ["map_id", "priority", "prior_map_id"],
                ),
                "SQL_CONVERSION": self._query_jobs(
                    cur,
                    f"""
                    SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                      FROM {sql_table}
                     WHERE STATUS_CONVERSION IS NULL
                        OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%')
                     ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_CONVERSION",
                    ["space_nm", "sql_id", "priority"],
                ),
                "SQL_TUNING": self._query_jobs(
                    cur,
                    f"""
                    SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                      FROM {sql_table}
                     WHERE UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                       AND (STATUS_TUNING IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%'))
                     ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_TUNING",
                    ["space_nm", "sql_id", "priority"],
                ),
                "SQL_FORMATTING": self._query_jobs(
                    cur,
                    f"""
                    SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                      FROM {sql_table}
                     WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                       AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                     ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_FORMATTING",
                    ["space_nm", "sql_id", "priority"],
                ),
            }

    def _query_jobs(self, cur: Any, sql: str, route: str, columns: list[str]) -> list[dict[str, Any]]:
        cur.execute(sql)
        jobs: list[dict[str, Any]] = []
        for row in cur.fetchall():
            job = {"job_route": route, "job_type": "MIG" if route == "MIG" else "SQL"}
            for index, column in enumerate(columns):
                job[column] = self._json_value(row[index])
            jobs.append(job)
        return jobs

    def _validate_job(self, route: str, job: dict[str, Any], index: int) -> None:
        if route == "MIG":
            if str(job.get("map_id") or "").strip():
                return
            raise ValueError(f"18A MIG job row {index} requires map_id")
        if str(job.get("row_id") or "").strip():
            return
        if str(job.get("space_nm") or "").strip() and str(job.get("sql_id") or "").strip():
            return
        raise ValueError(f"18A {route} job row {index} requires row_id or space_nm+sql_id")

    def _sort_migration_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                raise ValueError(f"18A migration dependency cycle detected at map_id={map_id}")
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
            else:
                visit(map_id)
        return ordered

    def _normalize_route(self, value: Any) -> str:
        route = str(value or "").strip().upper()
        aliases = {
            "DB_MIGRATION": "MIG",
            "MIGRATION": "MIG",
            "SQL": "SQL_CONVERSION",
            "CONVERSION": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
        }
        return aliases.get(route, route)

    def _job_name(self, route: str) -> str:
        return {
            "MIG": "migration",
            "SQL_CONVERSION": "conversion",
            "SQL_TUNING": "tuning",
            "SQL_FORMATTING": "formatting",
        }.get(route, str(route or "").lower())

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
            raise ValueError(f"18A Full Workflow is not connected to database settings: missing {', '.join(missing)}")

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
