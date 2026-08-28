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


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")
ROUTE_LABELS = {
    "MIG": "DB Migration",
    "SQL_CONVERSION": "SQL Conversion",
    "SQL_TUNING": "SQL Tuning",
    "SQL_FORMATTING": "SQL Formatting",
}
ROUTE_COLORS = {
    "MIG": "🟩",
    "SQL_CONVERSION": "🟦",
    "SQL_TUNING": "🟨",
    "SQL_FORMATTING": "🟪",
}
EMPTY_SQUARE = "⬜"


class NewType20AFullWorkflowHitlPrompt(Component):
    display_name = "20A Full Workflow HITL Prompt"
    description = "Builds a readable per-job Full Workflow approval prompt and passes the original job item through."
    name = "NewType20AFullWorkflowHitlPrompt"
    icon = "ClipboardCheck"

    inputs = [DataInput(name="payload_json", display_name="Job Item", required=True)]

    outputs = [
        Output(display_name="Prompt Message", name="prompt_message", method="build_prompt_message", types=["Message"]),
        Output(display_name="Job Item", name="job_item", method="build_job_item", types=["Data"]),
    ]

    def build_prompt_message(self) -> Message:
        payload = self._build()
        self.status = payload
        return Message(text=str(payload.get("prompt_text") or ""))

    def build_job_item(self) -> Data:
        payload = self._build()
        self.status = payload
        return Data(data=payload.get("job_item") or {})

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        job = self._parse_payload(getattr(self, "payload_json", ""))
        route = self._route(job)
        label = ROUTE_LABELS.get(route, route or "Unknown")
        total = self._positive_int(job.get("total_jobs"), 1)
        job_index = self._positive_int(job.get("job_index"), 1)
        completed_before = self._bounded_int(job.get("completed_before"), default=max(0, job_index - 1), minimum=0, maximum=total)
        phase_index = self._positive_int(job.get("phase_index"), 0)
        phase_count = self._positive_int(job.get("phase_count"), len(ROUTE_ORDER))
        route_index = self._positive_int(job.get("route_job_index"), job_index)
        route_total = self._positive_int(job.get("route_total_jobs"), total)
        plan_counts = self._plan_counts(job)

        prompt_text = self._prompt_text(
            job=job,
            route=route,
            label=label,
            total=total,
            job_index=job_index,
            completed_before=completed_before,
            phase_index=phase_index,
            phase_count=phase_count,
            route_index=route_index,
            route_total=route_total,
            plan_counts=plan_counts,
        )

        job_item = {
            **job,
            "hitl_prompt_component": "20A_fullWorkflowHitlPrompt",
            "hitl_prompt_ready": True,
            "hitl_prompt_text": prompt_text,
            "hitl_completed_before": completed_before,
            "hitl_progress_percent_before": self._pct_number(completed_before, total),
            "next_node": "20B_fullWorkflowHumanInput",
        }
        payload = {
            "component": "20A_fullWorkflowHitlPrompt",
            "prompt_text": prompt_text,
            "job_item": job_item,
            "job_route": route,
            "job_label": label,
            "job_identifier": self._job_identifier(job, route),
            "completed_before": completed_before,
            "total_jobs": total,
            "final": False,
        }
        self._cached_payload = payload
        return payload

    def _prompt_text(
        self,
        job: dict[str, Any],
        route: str,
        label: str,
        total: int,
        job_index: int,
        completed_before: int,
        phase_index: int,
        phase_count: int,
        route_index: int,
        route_total: int,
        plan_counts: dict[str, int],
    ) -> str:
        percent = self._pct(completed_before, total)
        progress_bar = self._colored_bar(completed_before, total, route)
        job_identifier = self._job_identifier(job, route)
        lines = [
            "## Full Workflow 작업 승인",
            "",
            f"현재 진행상황: {completed_before}/{total}건 완료 ({percent})",
            progress_bar,
            "",
            "| 구분 | 값 |",
            "|---|---|",
            f"| 현재 단계 | {phase_index}/{phase_count} {label} |",
            f"| 단계 내 순서 | {route_index}/{route_total} |",
            f"| 전체 작업 순서 | {job_index}/{total} |",
            f"| 진행할 Job | {label} {job_identifier} |",
            "",
            "### 전체 계획",
            "",
            "| 기능 | 예정 건수 |",
            "|---|---:|",
        ]
        for item_route in ROUTE_ORDER:
            lines.append(f"| {ROUTE_LABELS[item_route]} | {plan_counts.get(item_route, 0)} |")
        lines.extend(
            [
                "",
                f"이번에 진행할 Job은 {label} {job_identifier} 입니다.",
                "진행하시겠습니까?",
                "",
                "30초 동안 응답이 없으면 Fallback으로 자동 승인됩니다.",
            ]
        )
        return "\n".join(lines)

    def _colored_bar(self, completed: int, total: int, current_route: str) -> str:
        if total <= 0:
            return ""
        width = min(total, 40)
        filled = round(completed / total * width)
        color = ROUTE_COLORS.get(current_route, "🟫")
        return f"{color * filled}{EMPTY_SQUARE * (width - filled)}"

    def _plan_counts(self, job: dict[str, Any]) -> dict[str, int]:
        raw = job.get("workflow_plan_counts")
        counts = {route: 0 for route in ROUTE_ORDER}
        if isinstance(raw, dict):
            for route in ROUTE_ORDER:
                counts[route] = self._non_negative_int(raw.get(route), 0)
        if sum(counts.values()) <= 0:
            route = self._route(job)
            if route in counts:
                counts[route] = self._positive_int(job.get("total_jobs"), 1)
        return counts

    def _job_identifier(self, job: dict[str, Any], route: str) -> str:
        map_id = self._first_value(job, "map_id", "MAP_ID")
        space_nm = self._first_value(job, "space_nm", "SPACE_NM", "spaceName")
        sql_id = self._first_value(job, "sql_id", "SQL_ID", "sqlId")
        row_id = self._first_value(job, "row_id", "ROW_ID", "ROWID")
        if route == "MIG":
            return f"map_id={map_id or '-'}"
        parts = []
        if row_id:
            parts.append(f"row_id={row_id}")
        parts.extend([f"space_nm={space_nm or '-'}", f"sql_id={sql_id or '-'}"])
        return ", ".join(parts)

    def _first_value(self, data: dict[str, Any], *keys: str) -> Any:
        candidates = [data]
        for nested_key in ("payload", "job", "job_item", "input", "source", "original", "loop_result"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for key in keys:
            for candidate in candidates:
                value = candidate.get(key)
                if not self._is_blank(value):
                    return value
        return None

    def _route(self, payload: dict[str, Any]) -> str:
        return str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
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

    def _pct(self, value: int, total: int) -> str:
        return f"{self._pct_number(value, total):.1f}%" if total else "-"

    def _pct_number(self, value: int, total: int) -> float:
        return float(value) / float(total) * 100.0 if total else 0.0

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _non_negative_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else default
        except (TypeError, ValueError):
            return default

    def _bounded_int(self, value: Any, default: int, minimum: int, maximum: int) -> int:
        parsed = self._non_negative_int(value, default)
        return max(minimum, min(parsed, maximum))

    def _is_blank(self, value: Any) -> bool:
        if value is None:
            return True
        text = str(value).strip()
        return text == "" or text.lower() in {"nan", "none", "null", "nat"}
