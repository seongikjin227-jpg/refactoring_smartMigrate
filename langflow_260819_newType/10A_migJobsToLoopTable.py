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
        jobs = self._sort_by_dependency(self._mig_jobs(payload))
        total = len(jobs)
        db_config = self._db_config(payload)
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

    def _mig_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        jobs = payload.get("selected_jobs") or payload.get("planned_jobs") or []
        out = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("job_route") or job.get("job_type") or "MIG").upper() == "MIG":
                out.append(dict(job))
        return out

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

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
