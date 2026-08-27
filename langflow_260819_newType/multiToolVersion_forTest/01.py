from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")
ROUTE_LABELS = {
    "MIG": "DB Migration",
    "SQL_CONVERSION": "SQL Conversion",
    "SQL_TUNING": "SQL Tuning",
    "SQL_FORMATTING": "SQL Formatting",
}
ROUTE_TOOL_NAMES = {
    "MIG": "poc_db_migration_command_tool",
    "SQL_CONVERSION": "poc_sql_conversion_command_tool",
    "SQL_TUNING": "poc_sql_tuning_command_tool",
    "SQL_FORMATTING": "poc_sql_formatting_command_tool",
}
ROUTE_ACTIONS = {
    "MIG": "run_migration_job",
    "SQL_CONVERSION": "run_sql_conversion_job",
    "SQL_TUNING": "run_sql_tuning_job",
    "SQL_FORMATTING": "run_sql_formatting_job",
}


class MultiToolPoc01FullWorkflowJobsToAgentMessage(Component):
    display_name = "01 Full Workflow Jobs To Agent Message"
    description = "Builds an ordered Full Workflow task queue for a tool-calling Agent POC."
    name = "MultiToolPoc01FullWorkflowJobsToAgentMessage"
    icon = "ListOrdered"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [
        Output(display_name="Agent Input", name="agent_input", method="build_agent_input", types=["Message"]),
    ]

    def build_agent_input(self) -> Message:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        db_config = self._db_config(payload)
        max_retry = max(0, int(getattr(self, "max_retry", None) or 2))
        grouped = self._group_jobs(payload)
        grouped["MIG"] = self._sort_migration_jobs(grouped["MIG"])

        route_totals = {route: len(grouped[route]) for route in ROUTE_ORDER}
        total = sum(route_totals.values())
        tasks: list[dict[str, Any]] = []
        global_index = 0

        for phase_index, route in enumerate(ROUTE_ORDER, start=1):
            for route_index, job in enumerate(grouped[route], start=1):
                global_index += 1
                self._validate_job(route, job, route_index)
                command_json = self._command_json(route, job, max_retry, db_config)
                tasks.append(
                    {
                        "task_id": self._task_id(global_index, route, job),
                        "sequence": global_index,
                        "total_tasks": total,
                        "phase_index": phase_index,
                        "phase_count": len(ROUTE_ORDER),
                        "route_index": route_index,
                        "route_total": route_totals[route],
                        "job_route": route,
                        "route_label": ROUTE_LABELS[route],
                        "tool_name": ROUTE_TOOL_NAMES[route],
                        "action": ROUTE_ACTIONS[route],
                        "command_json": command_json,
                        "job": dict(job),
                    }
                )

        agent_payload = {
            "component": "01_fullWorkflowJobsToAgentMessage",
            "intent": "FULL_WORKFLOW_TOOL_AGENT_POC",
            "run_mode": payload.get("run_mode") or "all_pending",
            "user_request": payload.get("user_request") or payload.get("original_request") or payload.get("input") or "",
            "route_order": list(ROUTE_ORDER),
            "workflow_plan_counts": route_totals,
            "total_tasks": total,
            "tool_call_contract": {
                "tool_mode_argument": "command_json",
                "command_json_type": "json_object_or_compact_json_text",
                "success_status": "PASS",
            },
            "tasks": tasks,
            "completion_rule": "Call each task's tool in ascending sequence. After all tool calls return PASS, answer with a compact Korean summary.",
            "next_node": "Agent",
        }
        self.status = agent_payload
        return Message(text=json.dumps(agent_payload, ensure_ascii=False, default=str, indent=2))

    def _group_jobs(self, payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {route: [] for route in ROUTE_ORDER}
        explicit_jobs = payload.get("selected_jobs")
        if isinstance(explicit_jobs, list) and explicit_jobs:
            for job in explicit_jobs:
                if not isinstance(job, dict):
                    continue
                route = self._normalize_route(job.get("job_route") or job.get("planned_job_route"))
                if route in grouped:
                    grouped[route].append(dict(job))
            return grouped

        jobs = payload.get("remaining_jobs") or payload.get("pending_jobs") or {}
        sources = {
            "MIG": jobs.get("migration_jobs") or [],
            "SQL_CONVERSION": jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [],
            "SQL_TUNING": jobs.get("sql_tuning_jobs") or [],
            "SQL_FORMATTING": jobs.get("sql_formatting_jobs") or [],
        }
        for route, route_jobs in sources.items():
            grouped[route] = [dict(job) for job in route_jobs if isinstance(job, dict)]
        return grouped

    def _command_json(self, route: str, job: dict[str, Any], max_retry: int, db_config: dict[str, Any]) -> dict[str, Any]:
        command: dict[str, Any] = {
            "action": ROUTE_ACTIONS[route],
            "job_route": route,
            "max_retry": max_retry,
            "db_config": db_config,
            "job": dict(job),
        }
        if route == "MIG":
            command["map_id"] = self._to_int(job.get("map_id"))
        else:
            for key in ("row_id", "space_nm", "sql_id"):
                if str(job.get(key) or "").strip():
                    command[key] = job.get(key)
        return command

    def _validate_job(self, route: str, job: dict[str, Any], index: int) -> None:
        if route == "MIG":
            if str(job.get("map_id") or "").strip():
                return
            raise ValueError(f"01 MIG task {index} requires map_id")
        if str(job.get("row_id") or "").strip():
            return
        if str(job.get("space_nm") or "").strip() and str(job.get("sql_id") or "").strip():
            return
        raise ValueError(f"01 {route} task {index} requires row_id or space_nm+sql_id")

    def _sort_migration_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                raise ValueError(f"01 migration dependency cycle detected at map_id={map_id}")
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
            else:
                visit(map_id)
        return ordered

    def _task_id(self, index: int, route: str, job: dict[str, Any]) -> str:
        if route == "MIG":
            identifier = str(job.get("map_id") or "unknown")
        else:
            identifier = str(job.get("row_id") or f"{job.get('space_nm', '')}:{job.get('sql_id', '')}" or "unknown")
        clean_identifier = re.sub(r"[^A-Za-z0-9_.:-]+", "_", identifier).strip("_") or "unknown"
        return f"{index:03d}-{route}-{clean_identifier}"

    def _normalize_route(self, value: Any) -> str:
        route = str(value or "").strip().upper()
        aliases = {
            "DB_MIGRATION": "MIG",
            "MIGRATION": "MIG",
            "SQL": "SQL_CONVERSION",
            "CONVERSION": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
        }
        return aliases.get(route, route)

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
