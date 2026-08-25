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


class NewType06GetRemainingJobs(Component):
    display_name = "06 Get Remaining Jobs"
    description = "Loads remaining runnable jobs and minimal routing metadata."
    name = "NewType06GetRemainingJobs"
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

    outputs = [Output(display_name="Payload", name="payload", method="get_remaining_jobs")]

    def get_remaining_jobs(self) -> Data:
        # Load remaining jobs and attach them to the routing payload.
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("should_execute", True):
                payload.update({"component": "06_getRemainingJobs", "next_node": "chat_output", "final": True})
                return Data(data=payload)

            if not self._has_db_config():
                raise ValueError("DB connection settings are required for 06 Get Remaining Jobs")
            jobs = self._load_from_db()
            db_config = self._db_config()
            summary = {
                "total": len(jobs.get("all_jobs", [])),
                "migration_total": len(jobs.get("migration_jobs", [])),
                "sql_conversion_total": len(jobs.get("sql_conversion_jobs", [])),
                "sql_tuning_total": len(jobs.get("sql_tuning_jobs", [])),
                "sql_formatting_total": len(jobs.get("sql_formatting_jobs", [])),
            }
            payload.update(
                {
                    "component": "06_getRemainingJobs",
                    "remaining_jobs": jobs,
                    "remaining_summary": summary,
                    "pending_jobs": jobs,
                    "pending_summary": summary,
                    "db_config": db_config,
                    "next_node": "08_jobExecutionRouter",
                }
            )
            payload.setdefault("history", []).append(
                {
                    "step": "get_remaining_jobs",
                    "message": (
                        f"total={summary['total']}, mig={summary['migration_total']}, "
                        f"sql_conversion={summary['sql_conversion_total']}"
                    ),
                }
            )
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "06_getRemainingJobs", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _load_from_db(self) -> dict[str, list[dict[str, Any]]]:
        # Query only remaining job identifiers and lightweight routing metadata.
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        with self._connect() as conn:
            cur = conn.cursor()
            migration_jobs = self._query_jobs(
                cur,
                f"""
                SELECT M.MAP_ID,
                       M.PRIORITY,
                       M.PRIOR_MAP_ID
                  FROM {mig_table} M
                  LEFT JOIN {mig_table} P ON P.MAP_ID = M.PRIOR_MAP_ID
                 WHERE UPPER(TRIM(NVL(M.USE_YN, 'N'))) = 'Y'
                   AND M.STATUS IS NULL
                 ORDER BY
                       CASE
                           WHEN M.PRIOR_MAP_ID IS NULL OR M.PRIOR_MAP_ID <= 0 THEN 0
                           WHEN P.STATUS IS NOT NULL THEN 1
                           ELSE 2
                       END ASC,
                       M.PRIORITY ASC NULLS LAST,
                       M.MAP_ID ASC
                """,
                "MIG",
                ["map_id", "priority", "prior_map_id"],
            )
            sql_conversion_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID,
                       PRIORITY
                  FROM {sql_table}
                 WHERE STATUS_CONVERSION IS NULL
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_CONVERSION",
                ["space_nm", "sql_id", "priority"],
            )
            sql_tuning_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID,
                       PRIORITY
                  FROM {sql_table}
                 WHERE STATUS_TUNING IS NULL
                   AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_TUNING",
                ["space_nm", "sql_id", "priority"],
            )
            sql_formatting_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID,
                       PRIORITY
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                "SQL_FORMATTING",
                ["space_nm", "sql_id", "priority"],
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

    def _db_config(self) -> dict[str, Any]:
        # Carry the same DB settings used by 06 into downstream route/loop payloads.
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

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
