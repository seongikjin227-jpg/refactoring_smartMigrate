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
    description = "Loads pending MIG and SQL jobs from Oracle, or from mock_jobs_json for branch-only POC tests."
    name = "NewType06GetPendingJobs"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(
            name="mock_jobs_json",
            display_name="Mock Jobs JSON",
            required=False,
            info='Optional fallback. Example: {"migration_jobs":[{"map_id":1,"priority":1}],"sql_jobs":[]}',
        ),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="limit", display_name="Limit", value=5, required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="get_pending_jobs")]

    def get_pending_jobs(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("should_execute"):
                payload.update({"component": "06_getPendingJobs", "next_node": "13_finalSummary"})
                return Data(data=payload)

            jobs = self._load_from_db() if self._has_db_config() else self._load_mock_jobs()
            payload.update(
                {
                    "component": "06_getPendingJobs",
                    "pending_jobs": jobs,
                    "pending_summary": {
                        "migration_total": len(jobs.get("migration_jobs", [])),
                        "sql_total": len(jobs.get("sql_jobs", [])),
                    },
                    "next_node": "08_longJobRouter",
                }
            )
            payload.setdefault("history", []).append(
                {
                    "step": "get_pending_jobs",
                    "message": (
                        f"mig={len(jobs.get('migration_jobs', []))}, "
                        f"sql={len(jobs.get('sql_jobs', []))}"
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
        limit = max(1, int(getattr(self, "limit", None) or 5))
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, PRIORITY, PRIOR_MAP_ID, RETRY_COUNT, BATCH_CNT
                  FROM {mig_table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND STATUS IS NULL
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                 FETCH FIRST :1 ROWS ONLY
                """,
                [limit],
            )
            migration_jobs = [
                {
                    "job_type": "MIG",
                    "map_id": self._json_value(row[0]),
                    "map_type": self._json_value(row[1]),
                    "fr_table": self._json_value(row[2]),
                    "to_table": self._json_value(row[3]),
                    "priority": self._json_value(row[4]),
                    "prior_map_id": self._json_value(row[5]),
                    "retry_count": self._json_value(row[6]) or 0,
                    "batch_cnt": self._json_value(row[7]) or 0,
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                f"""
                SELECT ROWIDTOCHAR(ROWID), SPACE_NM, SQL_ID, TAG_KIND, TARGET_TABLE, PRIORITY, RETRY_COUNT
                  FROM {sql_table}
                 WHERE STATUS_CONVERSION IS NULL
                 ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, TO_CHAR(SPACE_NM), TO_CHAR(SQL_ID)
                 FETCH FIRST :1 ROWS ONLY
                """,
                [limit],
            )
            sql_jobs = [
                {
                    "job_type": "SQL",
                    "row_id": self._json_value(row[0]),
                    "space_nm": self._json_value(row[1]),
                    "sql_id": self._json_value(row[2]),
                    "tag_kind": self._json_value(row[3]),
                    "target_table": self._json_value(row[4]),
                    "priority": self._json_value(row[5]),
                    "retry_count": self._json_value(row[6]) or 0,
                }
                for row in cur.fetchall()
            ]
        return {"migration_jobs": migration_jobs, "sql_jobs": sql_jobs}

    def _load_mock_jobs(self) -> dict[str, list[dict[str, Any]]]:
        raw = getattr(self, "mock_jobs_json", "") or ""
        if not str(raw).strip():
            return {
                "migration_jobs": [
                    {"job_type": "MIG", "map_id": 101, "map_type": "MIG", "fr_table": "SRC_EMP", "to_table": "TGT_EMP", "priority": 1}
                ],
                "sql_jobs": [
                    {"job_type": "SQL", "row_id": "MOCK_ROWID_1", "space_nm": "demo", "sql_id": "selectEmp", "priority": 10}
                ],
            }
        parsed = self._parse_payload(raw)
        return {
            "migration_jobs": list(parsed.get("migration_jobs") or []),
            "sql_jobs": list(parsed.get("sql_jobs") or []),
        }

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
