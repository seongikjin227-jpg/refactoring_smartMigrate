from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType09ExecutionPlanSummary(Component):
    display_name = "09 Execution Plan Summary"
    description = "Builds a pre-execution summary before a job-target pipeline starts."
    name = "NewType09ExecutionPlanSummary"
    icon = "ListChecks"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [
        Output(display_name="Notice Message", name="notice", method="build_notice", types=["Message"]),
        Output(display_name="Payload", name="payload", method="build_payload"),
    ]

    def build_notice(self) -> Message:
        # Build the execution-plan notice message.
        payload = self._build()
        notice = Message(text=str(payload.get("execution_plan_message") or ""))
        self.status = {
            **payload,
            "component": "09_executionPlanSummary",
            "answer_text": notice.text,
            "pre_execution_notice": True,
        }
        return notice

    def build_payload(self) -> Data:
        # Build the execution-plan payload for the selected pipeline.
        payload = self._build()
        self.status = payload
        return Data(data=payload)

    def _build(self) -> dict[str, Any]:
        # Create and cache the execution-plan data structure.
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        route = str(payload.get("job_route") or "").upper()
        run_mode = str(payload.get("run_mode") or ("all_pending" if payload.get("run_all_pending") else "targeted"))
        jobs = self._jobs_for_route(payload, route)
        command = {
            "action": self._action_for_route(route),
            "run_mode": run_mode,
            "run_all_pending": run_mode == "all_pending",
            "target_filter": payload.get("target_filter") or {},
        }
        out = {
            **payload,
            "component": "09_executionPlanSummary",
            "run_mode": run_mode,
            "command_json": command,
            "planned_job_route": route,
            "planned_job_count": len(jobs),
            "planned_jobs": jobs,
            "execution_plan_message": self._message(route, run_mode, jobs),
            "execution_plan_prompt": self._llm_prompt(route, run_mode, jobs),
            "next_node": self._next_node(route),
        }
        out.setdefault("history", []).append(
            {"step": "execution_plan_summary", "message": f"route={route}, count={len(jobs)}"}
        )
        self._cached_payload = out
        return out

    def _jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
        # Select the planned jobs for the chosen execution route.
        selected_jobs = payload.get("selected_jobs")
        if isinstance(selected_jobs, list) and selected_jobs:
            return [dict(job) for job in selected_jobs if isinstance(job, dict)]
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
        # Map an execution route to its action name.
        return {
            "MIG": "run_migration_job",
            "SQL_CONVERSION": "run_sql_conversion_job",
            "SQL_TUNING": "run_sql_tuning_job",
            "SQL_FORMATTING": "run_sql_formatting_job",
        }.get(route, "run_pending_jobs")

    def _next_node(self, route: str) -> str:
        # Resolve the next component name for a route.
        return {
            "MIG": "10_migPipeline",
            "SQL_CONVERSION": "12_sqlConversionPipeline",
            "SQL_TUNING": "15_sqlTuningPipeline",
            "SQL_FORMATTING": "17_sqlFormattingPipeline",
        }.get(route, "13_finalSummary")

    def _message(self, route: str, run_mode: str, jobs: list[dict[str, Any]]) -> str:
        # Format the execution-plan notice text.
        label = {
            "MIG": "DB Migration",
            "SQL_CONVERSION": "SQL Conversion",
            "SQL_TUNING": "SQL Tuning",
            "SQL_FORMATTING": "SQL Formatting",
        }.get(route, route or "Unknown")
        mode_label = "전체 pending 작업" if run_mode == "all_pending" else "지정 작업"
        lines = [
            "component=09_executionPlanSummary",
            f"{label} {mode_label}을 시작합니다.",
            f"- 실행 예정 작업 수: {len(jobs)}",
        ]
        if jobs:
            lines.append("- 실행 예정 목록:")
            for job in jobs[:20]:
                lines.append(f"  - {self._job_label(job)}")
            if len(jobs) > 20:
                lines.append(f"  - ... and {len(jobs) - 20} more")
        return "\n".join(lines)

    def _llm_prompt(self, route: str, run_mode: str, jobs: list[dict[str, Any]]) -> str:
        # Build an optional LLM prompt for execution-plan messaging.
        return "\n".join(
            [
                "아래 실행 계획을 사용자에게 한국어로 짧고 명확하게 안내하세요.",
                "실제 실행 결과가 아니라 실행 전 계획임을 분명히 말하세요.",
                "사용자가 지정한 작업이면 지정 작업이라고 말하고, 전체 대기 작업이면 전체 pending 작업이라고 말하세요.",
                "불필요한 설명은 하지 말고 실행 도메인, 실행 모드, 실행 예정 건수, 작업 목록만 포함하세요.",
                "",
                "실행 계획:",
                json.dumps(
                    {
                        "job_route": route,
                        "run_mode": run_mode,
                        "planned_job_count": len(jobs),
                        "planned_jobs": [self._compact_job(job) for job in jobs[:50]],
                    },
                    ensure_ascii=False,
                    default=str,
                    indent=2,
                ),
            ]
        )

    def _compact_job(self, job: dict[str, Any]) -> dict[str, Any]:
        # Reduce a job dictionary to the fields needed for display.
        return {
            "job_route": job.get("job_route"),
            "job_type": job.get("job_type"),
            "map_id": job.get("map_id"),
            "space_nm": job.get("space_nm"),
            "sql_id": job.get("sql_id") or job.get("row_id"),
            "priority": job.get("priority"),
        }

    def _job_label(self, job: dict[str, Any]) -> str:
        # Return a compact display label for a job target.
        if str(job.get("job_route") or job.get("job_type") or "").upper() == "MIG" or job.get("map_id") is not None:
            return f"MIG map_id={job.get('map_id')}, priority={job.get('priority')}"
        return (
            f"SQL space_nm={job.get('space_nm') or '-'}, "
            f"sql_id={job.get('sql_id') or job.get('row_id') or '-'}, "
            f"priority={job.get('priority')}"
        )

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
