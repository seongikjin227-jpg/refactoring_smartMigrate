from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType09ExecutionPlanSummary(Component):
    display_name = "09 Execution Plan Summary"
    description = "Builds a pre-execution summary before an all-pending pipeline starts."
    name = "NewType09ExecutionPlanSummary"
    icon = "ListChecks"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [
        Output(display_name="Notice", name="notice", method="build_notice"),
        Output(display_name="Payload", name="payload", method="build_payload"),
    ]

    def build_notice(self) -> Data:
        payload = self._build()
        notice = {
            **payload,
            "component": "09_executionPlanSummary",
            "answer_text": payload.get("execution_plan_message"),
            "pre_execution_notice": True,
        }
        self.status = notice
        return Data(data=notice)

    def build_payload(self) -> Data:
        payload = self._build()
        self.status = payload
        return Data(data=payload)

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        route = str(payload.get("job_route") or "").upper()
        jobs = self._jobs_for_route(payload, route)
        command = {"action": self._action_for_route(route), "run_all_pending": True}
        out = {
            **payload,
            "component": "09_executionPlanSummary",
            "run_mode": "all_pending",
            "command_json": command,
            "planned_job_route": route,
            "planned_job_count": len(jobs),
            "planned_jobs": jobs,
            "execution_plan_message": self._message(route, jobs),
            "next_node": self._next_node(route),
        }
        out.setdefault("history", []).append(
            {"step": "execution_plan_summary", "message": f"route={route}, count={len(jobs)}"}
        )
        self._cached_payload = out
        return out

    def _jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
        jobs = payload.get("pending_jobs") or {}
        if route == "MIG":
            return list(jobs.get("migration_jobs") or [])
        if route == "SQL_CONVERSION":
            return list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [])
        if route == "SQL_TUNING":
            return list(jobs.get("sql_tuning_jobs") or [])
        if route == "SQL_FORMATTING":
            return list(jobs.get("sql_formatting_jobs") or [])
        return []

    def _action_for_route(self, route: str) -> str:
        return {
            "MIG": "run_migration_job",
            "SQL_CONVERSION": "run_sql_conversion_job",
            "SQL_TUNING": "run_sql_tuning_job",
            "SQL_FORMATTING": "run_sql_formatting_job",
        }.get(route, "run_pending_jobs")

    def _next_node(self, route: str) -> str:
        return {
            "MIG": "10_migPipeline",
            "SQL_CONVERSION": "12_sqlConversionPipeline",
            "SQL_TUNING": "15_sqlTuningPipeline",
            "SQL_FORMATTING": "17_sqlFormattingPipeline",
        }.get(route, "13_finalSummary")

    def _message(self, route: str, jobs: list[dict[str, Any]]) -> str:
        label = {
            "MIG": "DB Migration",
            "SQL_CONVERSION": "SQL Conversion",
            "SQL_TUNING": "SQL Tuning",
            "SQL_FORMATTING": "SQL Formatting",
        }.get(route, route or "Unknown")
        lines = [f"{label} 전체 작업을 시작합니다.", f"- 실행 예정 작업 수: {len(jobs)}"]
        if jobs:
            lines.append("- 실행 예정 목록:")
            for job in jobs[:20]:
                lines.append(f"  - {self._job_label(job)}")
            if len(jobs) > 20:
                lines.append(f"  - ... and {len(jobs) - 20} more")
        return "\n".join(lines)

    def _job_label(self, job: dict[str, Any]) -> str:
        if str(job.get("job_route") or job.get("job_type") or "").upper() == "MIG" or job.get("map_id") is not None:
            return f"MIG map_id={job.get('map_id')}, priority={job.get('priority')}"
        return (
            f"SQL space_nm={job.get('space_nm') or '-'}, "
            f"sql_id={job.get('sql_id') or job.get('row_id') or '-'}, "
            f"priority={job.get('priority')}"
        )

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
