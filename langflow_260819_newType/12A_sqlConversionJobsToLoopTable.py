from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType12ASqlConversionJobsToLoopTable(Component):
    display_name = "12A SQL Conversion Jobs To Loop Table"
    description = "Converts selected SQL Conversion jobs into Loop rows."
    name = "NewType12ASqlConversionJobsToLoopTable"
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
        """Build one Loop row per SQL conversion job."""
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        jobs = self._sql_jobs(payload)
        total = len(jobs)
        db_config = self._db_config(payload)
        self._require_db_config(db_config)
        rows: list[dict[str, Any]] = []
        for index, job in enumerate(jobs, start=1):
            self._validate_sql_key(job, index)
            rows.append(
                {
                    **job,
                    "component": "12A_sqlConversionJobsToLoopTable",
                    "job_route": "SQL_CONVERSION",
                    "job_type": "SQL",
                    "run_mode": payload.get("run_mode") or "targeted",
                    "job_index": index,
                    "total_jobs": total,
                    "completed_before": index - 1,
                    "db_config": db_config,
                    "history": list(payload.get("history") or []),
                }
            )
        self.status = {**payload, "component": "12A_sqlConversionJobsToLoopTable", "loop_job_count": total, "next_node": "12B_sqlConversionLoop"}
        return DataFrame(rows)

    def _sql_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Return only SQL conversion jobs from the routed payload."""
        jobs = payload.get("selected_jobs") or payload.get("planned_jobs") or []
        out: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            route = str(job.get("job_route") or "SQL_CONVERSION").upper()
            if route == "SQL_CONVERSION":
                out.append(dict(job))
        return out

    def _validate_sql_key(self, job: dict[str, Any], index: int) -> None:
        """Require ROWID or the logical SQL key used by NEXT_SQL_INFO."""
        if str(job.get("row_id") or "").strip():
            return
        if str(job.get("space_nm") or "").strip() and str(job.get("sql_id") or "").strip():
            return
        raise ValueError(f"12A SQL Conversion job row {index} requires row_id or space_nm+sql_id")

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Collect DB connection settings for downstream Loop items."""
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
        """Fail early when SQL Conversion is not wired to database settings."""
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"12A SQL Conversion is not connected to database settings: missing {', '.join(missing)}")

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        """Parse a Langflow Data, dict, or JSON string payload."""
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

    def _secret_to_str(self, value: Any) -> str:
        """Convert a Langflow secret value into a plain string."""
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
