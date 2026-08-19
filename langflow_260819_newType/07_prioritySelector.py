from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType07PrioritySelector(Component):
    display_name = "07 Priority Selector"
    description = "Selects one job from pending jobs. Default strategy keeps DB migration before SQL conversion."
    name = "NewType07PrioritySelector"
    icon = "ListOrdered"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(
            name="selection_strategy",
            display_name="Selection Strategy",
            value="MIG_FIRST",
            required=False,
            info="MIG_FIRST or LOWEST_PRIORITY_ACROSS_TYPES.",
        ),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="select_job")]

    def select_job(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            jobs = payload.get("pending_jobs") or {}
            selected = self._select(jobs)
            payload.update(
                {
                    "component": "07_prioritySelector",
                    "selected_job": selected,
                    "has_selected_job": bool(selected),
                    "next_node": "08_jobTypeRouter" if selected else "13_finalSummary",
                }
            )
            payload.setdefault("history", []).append(
                {"step": "priority_select", "message": f"selected={self._job_label(selected)}"}
            )
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "07_prioritySelector", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _select(self, jobs: dict[str, Any]) -> dict[str, Any] | None:
        mig_jobs = [dict(j, job_type="MIG") for j in (jobs.get("migration_jobs") or [])]
        sql_jobs = [dict(j, job_type="SQL") for j in (jobs.get("sql_jobs") or [])]
        strategy = str(getattr(self, "selection_strategy", "") or "MIG_FIRST").strip().upper()
        if strategy == "LOWEST_PRIORITY_ACROSS_TYPES":
            all_jobs = mig_jobs + sql_jobs
            return min(all_jobs, key=self._priority_key) if all_jobs else None
        if mig_jobs:
            return min(mig_jobs, key=self._priority_key)
        if sql_jobs:
            return min(sql_jobs, key=self._priority_key)
        return None

    def _priority_key(self, job: dict[str, Any]) -> tuple[int, str]:
        try:
            priority = int(job.get("priority") if job.get("priority") is not None else 999999999)
        except Exception:
            priority = 999999999
        return priority, self._job_label(job)

    def _job_label(self, job: dict[str, Any] | None) -> str:
        if not job:
            return "none"
        if str(job.get("job_type") or "").upper() == "MIG":
            return f"MIG:{job.get('map_id')}"
        return f"SQL:{job.get('space_nm') or '-'}:{job.get('sql_id') or job.get('row_id')}"

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
