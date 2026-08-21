from __future__ import annotations

import json
import re
import time
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
        StrInput(name="migration_log_sequence", display_name="Migration Log Sequence", value="MIGRATION_LOG_SEQ", required=False),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    def build_jobs_table(self) -> DataFrame:
        # Build a Loop-compatible DataFrame where each row is one MIG job.
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        jobs = self._mig_jobs(payload)
        run_id = str(payload.get("run_id") or f"MIG-POC-{int(time.time())}")
        total = len(jobs)
        db_config = self._db_config()
        rows: list[Data] = []
        for index, job in enumerate(jobs, start=1):
            row = {
                **job,
                "component": "10A_migJobsToLoopTable",
                "job_route": "MIG",
                "job_type": "MIG",
                "run_id": run_id,
                "run_mode": payload.get("run_mode") or "targeted",
                "job_index": index,
                "total_jobs": total,
                "completed_before": index - 1,
                "db_config": db_config,
                "history": list(payload.get("history") or []),
            }
            rows.append(Data(data=row))
        status = {
            **payload,
            "component": "10A_migJobsToLoopTable",
            "run_id": run_id,
            "loop_job_count": total,
            "next_node": "10B_migLoop",
        }
        self.status = status
        return DataFrame(rows)

    def _mig_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        jobs = payload.get("selected_jobs") or payload.get("planned_jobs") or []
        out = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("job_route") or job.get("job_type") or "MIG").upper() == "MIG":
                out.append(dict(job))
        return out

    def _db_config(self) -> dict[str, Any]:
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
            "migration_log_sequence": str(getattr(self, "migration_log_sequence", "") or "MIGRATION_LOG_SEQ").strip(),
        }

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

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
