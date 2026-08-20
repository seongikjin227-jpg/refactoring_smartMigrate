from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType06GetPendingJobs(Component):
    display_name = "06 Get Pending Jobs"
    description = "Loads all runnable pending job candidates as routing context."
    name = "NewType06GetPendingJobs"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="get_pending_jobs")]

    def get_pending_jobs(self) -> Data:
        # Load pending jobs and attach them to the routing payload.
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("should_execute", True):
                payload.update({"component": "06_getPendingJobs", "next_node": "13_finalSummary"})
                return Data(data=payload)

            if not self._has_db_config():
                raise ValueError("DB connection settings are required for 06 Get Pending Jobs")
            jobs = self._load_from_db()
            summary = {
                "total": len(jobs.get("all_jobs", [])),
                "migration_total": len(jobs.get("migration_jobs", [])),
                "sql_conversion_total": len(jobs.get("sql_conversion_jobs", [])),
                "sql_tuning_total": len(jobs.get("sql_tuning_jobs", [])),
                "sql_formatting_total": len(jobs.get("sql_formatting_jobs", [])),
            }
            payload.update(
                {
                    "component": "06_getPendingJobs",
                    "pending_jobs": jobs,
                    "pending_summary": summary,
                    "next_node": "08_jobExecutionRouter",
                }
            )
            payload.setdefault("history", []).append(
                {
                    "step": "get_pending_jobs",
                    "message": (
                        f"total={summary['total']}, mig={summary['migration_total']}, "
                        f"sql_conversion={summary['sql_conversion_total']}"
                    ),
                }
            )
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "06_getPendingJobs", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _load_from_db(self) -> dict[str, list[dict[str, Any]]]:
        # Query only pending job identifiers needed for downstream routing.
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        with self._connect() as conn:
            cur = conn.cursor()
            migration_jobs = self._query_jobs(
                cur,
                f"""
                SELECT MAP_ID
                  FROM {mig_table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND STATUS IS NULL
                 ORDER BY MAP_ID ASC
                """,
                "MIG",
                ["map_id"],
            )
            sql_conversion_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID
                  FROM {sql_table}
                 WHERE STATUS_CONVERSION IS NULL
                 ORDER BY SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_CONVERSION",
                ["space_nm", "sql_id"],
            )
            sql_tuning_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID
                  FROM {sql_table}
                 WHERE STATUS_TUNING IS NULL
                   AND UPPER(TRIM(STATUS_CONVERSION)) = 'PASS-CONVERSION'
                 ORDER BY SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_TUNING",
                ["space_nm", "sql_id"],
            )
            sql_formatting_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                 ORDER BY SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_FORMATTING",
                ["space_nm", "sql_id"],
            )
        all_jobs = [*migration_jobs, *sql_conversion_jobs, *sql_tuning_jobs, *sql_formatting_jobs]
        return {
            "all_jobs": all_jobs,
            "job_lookup_jobs": all_jobs,
            "migration_jobs": migration_jobs,
            "sql_conversion_jobs": sql_conversion_jobs,
            "sql_jobs": sql_conversion_jobs,
            "sql_tuning_jobs": sql_tuning_jobs,
            "sql_formatting_jobs": sql_formatting_jobs,
        }

    def _query_jobs(self, cur: Any, sql: str, route: str, columns: list[str]) -> list[dict[str, Any]]:
        # Execute an identifier-only pending-job query for one route.
        cur.execute(sql)
        jobs: list[dict[str, Any]] = []
        for row in cur.fetchall():
            job = {
                "job_route": route,
                "job_type": "MIG" if route == "MIG" else "SQL",
            }
            for index, column in enumerate(columns):
                job[column] = self._json_value(row[index])
            jobs.append(job)
        return jobs

    @contextmanager
    def _connect(self):
        # Open and safely close an Oracle database connection.
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret_to_str(getattr(self, "db_password", None)),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _has_db_config(self) -> bool:
        # Check whether the minimum DB connection settings are present.
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        # Qualify a table name with the optional system schema.
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        # Validate and normalize an Oracle identifier.
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse a Langflow Data, dict, or JSON string payload.
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

    def _json_value(self, value: Any) -> Any:
        # Convert database values into JSON-safe values.
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    def _secret_to_str(self, value: Any) -> str:
        # Convert a Langflow secret value into a plain string.
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
