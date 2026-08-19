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
        MessageTextInput(
            name="mock_jobs_json",
            display_name="Mock Jobs JSON",
            required=False,
            info='Optional fallback. Example: {"migration_jobs":[{"map_id":1}],"sql_conversion_jobs":[]}',
        ),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="limit", display_name="Candidate Limit", value=200, required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="get_pending_jobs")]

    def get_pending_jobs(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("should_execute", True):
                payload.update({"component": "06_getPendingJobs", "next_node": "13_finalSummary"})
                return Data(data=payload)

            jobs = self._load_from_db() if self._has_db_config() else self._load_mock_jobs()
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
                    "next_node": "08_longJobRouter",
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
        limit = max(1, int(getattr(self, "limit", None) or 200))
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT *
                  FROM (
                        SELECT 'MIG' AS JOB_ROUTE,
                               'MIG' AS JOB_TYPE,
                               MAP_ID,
                               NULL AS ROW_ID,
                               NULL AS SPACE_NM,
                               NULL AS SQL_ID,
                               TO_CHAR(FR_TABLE) AS SOURCE_NAME,
                               TO_CHAR(TO_TABLE) AS TARGET_NAME,
                               PRIORITY,
                               RETRY_COUNT,
                               0 AS SORT_GROUP
                          FROM {mig_table}
                         WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                           AND STATUS IS NULL
                        UNION ALL
                        SELECT 'SQL_CONVERSION' AS JOB_ROUTE,
                               'SQL' AS JOB_TYPE,
                               NULL AS MAP_ID,
                               ROWIDTOCHAR(ROWID) AS ROW_ID,
                               TO_CHAR(SPACE_NM) AS SPACE_NM,
                               TO_CHAR(SQL_ID) AS SQL_ID,
                               TO_CHAR(TAG_KIND) AS SOURCE_NAME,
                               TO_CHAR(TARGET_TABLE) AS TARGET_NAME,
                               PRIORITY,
                               RETRY_COUNT,
                               1 AS SORT_GROUP
                          FROM {sql_table}
                         WHERE STATUS_CONVERSION IS NULL
                       )
                 ORDER BY SORT_GROUP ASC, PRIORITY ASC NULLS LAST, MAP_ID ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                 FETCH FIRST {limit} ROWS ONLY
                """
            )
            all_jobs = [self._row_to_job(row) for row in cur.fetchall()]
        return self._group_jobs(all_jobs)

    def _row_to_job(self, row: Any) -> dict[str, Any]:
        return {
            "job_route": self._json_value(row[0]),
            "job_type": self._json_value(row[1]),
            "map_id": self._json_value(row[2]),
            "row_id": self._json_value(row[3]),
            "space_nm": self._json_value(row[4]),
            "sql_id": self._json_value(row[5]),
            "source_name": self._json_value(row[6]),
            "target_name": self._json_value(row[7]),
            "priority": self._json_value(row[8]),
            "retry_count": self._json_value(row[9]) or 0,
        }

    def _load_mock_jobs(self) -> dict[str, list[dict[str, Any]]]:
        raw = getattr(self, "mock_jobs_json", "") or ""
        if not str(raw).strip():
            return self._group_jobs(
                [
                    {"job_route": "MIG", "job_type": "MIG", "map_id": 101, "source_name": "SRC_EMP", "target_name": "TGT_EMP", "priority": 1},
                    {"job_route": "SQL_CONVERSION", "job_type": "SQL", "row_id": "MOCK_ROWID_1", "space_nm": "demo", "sql_id": "selectEmp", "priority": 10},
                ]
            )
        parsed = self._parse_payload(raw)
        all_jobs = list(parsed.get("all_jobs") or [])
        if not all_jobs:
            all_jobs.extend(parsed.get("migration_jobs") or [])
            all_jobs.extend(parsed.get("sql_conversion_jobs") or parsed.get("sql_jobs") or [])
            all_jobs.extend(parsed.get("sql_tuning_jobs") or [])
            all_jobs.extend(parsed.get("sql_formatting_jobs") or [])
        return self._group_jobs(all_jobs)

    def _group_jobs(self, all_jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        normalized = [self._normalize_job(job) for job in all_jobs]
        migration_jobs = [job for job in normalized if job.get("job_route") == "MIG"]
        sql_conversion_jobs = [job for job in normalized if job.get("job_route") in {"SQL_CONVERSION", "SQL"}]
        sql_tuning_jobs = [job for job in normalized if job.get("job_route") == "SQL_TUNING"]
        sql_formatting_jobs = [job for job in normalized if job.get("job_route") == "SQL_FORMATTING"]
        return {
            "all_jobs": normalized,
            "migration_jobs": migration_jobs,
            "sql_conversion_jobs": sql_conversion_jobs,
            "sql_jobs": sql_conversion_jobs,
            "sql_tuning_jobs": sql_tuning_jobs,
            "sql_formatting_jobs": sql_formatting_jobs,
        }

    def _normalize_job(self, job: dict[str, Any]) -> dict[str, Any]:
        out = dict(job or {})
        route = str(out.get("job_route") or out.get("job_type") or "").upper()
        if route == "SQL":
            route = "SQL_CONVERSION"
        if not route and out.get("map_id") is not None:
            route = "MIG"
        if not route and (out.get("sql_id") is not None or out.get("row_id") is not None):
            route = "SQL_CONVERSION"
        out["job_route"] = route or "UNKNOWN"
        out["job_type"] = out.get("job_type") or ("MIG" if out["job_route"] == "MIG" else "SQL")
        return out

    @contextmanager
    def _connect(self):
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
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
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
