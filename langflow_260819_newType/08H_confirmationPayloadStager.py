from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.message import Message


PAYLOAD_BEGIN = "SMARTMIGRATE_PAYLOAD_B64_BEGIN"
PAYLOAD_END = "SMARTMIGRATE_PAYLOAD_B64_END"


def _load_component_base():
    for module_name in (
        "langflow.custom.custom_component.base_component",
        "langflow.custom.custom_component.component",
        "lfx.custom.custom_component.component",
        "lfx.custom",
    ):
        try:
            module = import_module(module_name)
            component = getattr(module, "Component", None)
            if component is not None:
                return component
        except Exception:
            continue
    raise ImportError("Could not import Langflow Component base class")


Component = _load_component_base()


class NewType08HConfirmationPayloadStager(Component):
    display_name = "08H Confirmation Message Builder"
    description = "Builds one Message for Human Input. The execution payload is embedded in the message."
    name = "NewType08HConfirmationPayloadStager"
    icon = "ShieldQuestion"

    inputs = [
        HandleInput(
            name="payload_json",
            display_name="Execution Payload",
            input_types=["Data", "Message"],
        ),
    ]

    outputs = [
        Output(display_name="Human Input Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_message(self) -> Message:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        confirmation_id = self._confirmation_id(payload)
        staged_payload = {
            **payload,
            "confirmation_id": confirmation_id,
            "confirmation_required": True,
            "confirmation_status": "PENDING",
            "confirmation_created_at": datetime.now(timezone.utc).isoformat(),
        }
        staged_payload.setdefault("history", []).append(
            {
                "step": "human_input_confirmation_message_built",
                "message": f"confirmation_id={confirmation_id}",
            }
        )

        message = self._message_text(confirmation_id, self._plan_text(payload), staged_payload)
        self.status = {
            "component": "08H_confirmationPayloadStager",
            "confirmation_id": confirmation_id,
            "status": "PENDING",
            "message_only_payload": True,
        }
        return Message(text=message)

    def _message_text(self, confirmation_id: str, plan_text: str, payload: dict[str, Any]) -> str:
        payload_text = base64.b64encode(
            json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        hidden_payload = f"<!--\n{PAYLOAD_BEGIN}\n{payload_text}\n{PAYLOAD_END}\n-->"
        return "\n".join(
            [
                "요청하신 작업 계획입니다.",
                "",
                plan_text.strip(),
                "",
                f"confirmation_id={confirmation_id}",
                "",
                "진행 여부를 선택해주세요.",
                "",
                hidden_payload,
            ]
        )

    def _plan_text(self, payload: dict[str, Any]) -> str:
        route = str(payload.get("job_route") or payload.get("planned_job_route") or "UNKNOWN").upper()
        run_mode = str(payload.get("run_mode") or "all_pending")
        jobs = self._jobs_for_route(payload, route)
        counts = self._plan_counts(payload, route, jobs)
        total_count = sum(counts.values()) if counts else len(jobs)
        mode_label = "전체 잔여 작업" if run_mode == "all_pending" else "지정 작업"

        lines = [
            f"작업 유형: {self._route_label(route)}",
            f"실행 모드: {mode_label}",
            f"실행 예정 건수: {total_count}",
        ]
        if route == "FULL_WORKFLOW":
            lines.extend(
                [
                    "",
                    "| 기능 | 실행 예정 |",
                    "|---|---:|",
                    f"| DB Migration | {counts.get('MIG', 0)} |",
                    f"| SQL Conversion | {counts.get('SQL_CONVERSION', 0)} |",
                    f"| SQL Tuning | {counts.get('SQL_TUNING', 0)} |",
                    f"| SQL Formatting | {counts.get('SQL_FORMATTING', 0)} |",
                ]
            )
        if jobs:
            lines.extend(["", "실행 예정 목록:"])
            for job in jobs[:20]:
                lines.append(f"- {self._job_label(job)}")
            if len(jobs) > 20:
                lines.append(f"- ... 외 {len(jobs) - 20}건")
        return "\n".join(lines)

    def _jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
        selected_jobs = payload.get("selected_jobs")
        if isinstance(selected_jobs, list) and selected_jobs:
            return [dict(job) for job in selected_jobs if isinstance(job, dict)]

        jobs = payload.get("remaining_jobs") or payload.get("pending_jobs") or {}
        if route == "MIG":
            return list(jobs.get("migration_jobs") or [])
        if route == "SQL_CONVERSION":
            return list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [])
        if route == "SQL_TUNING":
            return list(jobs.get("sql_tuning_jobs") or [])
        if route == "SQL_FORMATTING":
            return list(jobs.get("sql_formatting_jobs") or [])
        if route == "FULL_WORKFLOW":
            return [
                *list(jobs.get("migration_jobs") or []),
                *list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or []),
                *list(jobs.get("sql_tuning_jobs") or []),
                *list(jobs.get("sql_formatting_jobs") or []),
            ]
        return []

    def _plan_counts(self, payload: dict[str, Any], route: str, jobs: list[dict[str, Any]]) -> dict[str, int]:
        if route != "FULL_WORKFLOW":
            return {route: len(jobs)} if route else {}
        pending = payload.get("remaining_jobs") or payload.get("pending_jobs") or {}
        if pending:
            return {
                "MIG": len(pending.get("migration_jobs") or []),
                "SQL_CONVERSION": len(pending.get("sql_conversion_jobs") or pending.get("sql_jobs") or []),
                "SQL_TUNING": len(pending.get("sql_tuning_jobs") or []),
                "SQL_FORMATTING": len(pending.get("sql_formatting_jobs") or []),
            }
        counts = {"MIG": 0, "SQL_CONVERSION": 0, "SQL_TUNING": 0, "SQL_FORMATTING": 0}
        for job in jobs:
            job_route = str(job.get("job_route") or job.get("planned_job_route") or "").upper()
            if job_route in counts:
                counts[job_route] += 1
        return counts

    def _route_label(self, route: str) -> str:
        return {
            "MIG": "DB Migration",
            "SQL_CONVERSION": "SQL Conversion",
            "SQL_TUNING": "SQL Tuning",
            "SQL_FORMATTING": "SQL Formatting",
            "FULL_WORKFLOW": "Full Workflow",
        }.get(route, route or "Unknown")

    def _job_label(self, job: dict[str, Any]) -> str:
        route = str(job.get("job_route") or job.get("planned_job_route") or job.get("job_type") or "").upper()
        if route == "MIG" or job.get("map_id") is not None:
            return f"DB Migration map_id={job.get('map_id')}"
        return f"SQL space_nm={job.get('space_nm') or '-'}, sql_id={job.get('sql_id') or '-'}"

    def _confirmation_id(self, payload: dict[str, Any]) -> str:
        existing = str(payload.get("confirmation_id") or "").strip()
        if existing:
            return existing
        seed = json.dumps(
            {
                "job_route": payload.get("job_route"),
                "run_mode": payload.get("run_mode"),
                "target_filter": payload.get("target_filter"),
                "selected_jobs": payload.get("selected_jobs"),
                "user_request": payload.get("user_request") or payload.get("original_request"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"CONF-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{digest}"

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
