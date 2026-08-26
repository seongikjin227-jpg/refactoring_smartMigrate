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


class NewType18DFullWorkflowDashboard(Component):
    display_name = "18D Full Workflow Dashboard"
    description = "Formats Full Workflow iteration progress or the final aggregated summary."
    name = "NewType18DFullWorkflowDashboard"
    icon = "ClipboardCheck"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        payload = self._build()
        self.status = payload
        return Message(text=str(payload.get("answer_text") or ""))

    def build_loop_result(self) -> Data:
        payload = self._build()
        self.status = payload
        return Data(data=payload.get("loop_result") or payload)

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        raw_payload = self._parse_payload(getattr(self, "payload_json", ""))
        if raw_payload.get("loop_done"):
            payload = self._final_payload(raw_payload)
        else:
            payload = self._iteration_payload(raw_payload)
        self._cached_payload = payload
        return payload

    def _iteration_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        route = str(result.get("planned_job_route") or result.get("job_route") or "").upper()
        loop_result = {
            **result,
            "component": "18D_fullWorkflowDashboard",
            "job_route": route,
            "planned_job_route": route,
            "route_success": self._is_success(route, result),
            "route_skipped": self._is_skipped(result),
        }
        payload = {
            **result,
            "component": "18D_fullWorkflowDashboard",
            "answer_text": self._iteration_message_v2(result, route),
            "loop_result": loop_result,
            "final": False,
        }
        return payload

    def _final_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        results = [self._data_dict(item) for item in payload.get("aggregated_results") or []]
        summary = payload.get("workflow_summary") or self._summary(results, payload.get("workflow_plan_counts") or {})
        answer = self._final_message(summary, results, payload)
        return {
            **payload,
            "component": "18D_fullWorkflowDashboard",
            "answer_text": answer,
            "loop_result": {**payload, "workflow_summary": summary},
            "workflow_summary": summary,
            "final": True,
        }

    def _iteration_message_v2(self, result: dict[str, Any], route: str) -> str:
        label = ROUTE_LABELS.get(route, route or "Unknown")
        index = int(result.get("job_index") or 1)
        total = int(result.get("total_jobs") or 1)
        phase_index = int(result.get("phase_index") or 0)
        phase_count = int(result.get("phase_count") or len(ROUTE_ORDER))
        route_index = int(result.get("route_job_index") or index)
        route_total = int(result.get("route_total_jobs") or total)
        lines = [
            "## Overall Progress",
            "",
            f"- 전체 진행률: {index}/{total}건, {self._pct(index, total)}",
            self._bar(index, total),
            f"- 단계 진행률: {route_index}/{route_total}건, {self._pct(route_index, route_total)}",
            self._bar(route_index, route_total),
            f"- 현재 단계: {phase_index}/{phase_count} {label}",
            f"- 현재 작업: {self._job_label(result)}",
            f"- 현재 상태: {result.get('status')}",
            f"- 재시도: {self._retry_count(result)}",
        ]
        stages = result.get("stages") or {}
        if stages:
            lines.extend(["", "| 단계 | 상태 | 메시지 |", "|---|---|---|"])
            for stage in ("conversion", "tuning", "formatting"):
                item = stages.get(stage) or {}
                lines.append(f"| {self._stage_label(stage)} | {self._cell(item.get('status', '-'))} | {self._cell(item.get('message', '-'))} |")
        message = str(result.get("message") or "").strip()
        if message:
            lines.extend(["", f"메시지: {message}"])
        return "\n".join(lines)

    def _iteration_message(self, result: dict[str, Any], route: str) -> str:
        label = ROUTE_LABELS.get(route, route or "Unknown")
        index = int(result.get("job_index") or 1)
        total = int(result.get("total_jobs") or 1)
        phase_index = int(result.get("phase_index") or 0)
        phase_count = int(result.get("phase_count") or len(ROUTE_ORDER))
        route_index = int(result.get("route_job_index") or index)
        route_total = int(result.get("route_total_jobs") or total)
        lines = [
            "## Overall Progress",
            "",
            f"- 전체 진행률: {index}/{total}건, {self._pct(index, total)}",
            self._bar(index, total),
            f"- 현재 단계: {phase_index}/{phase_count} {label}",
            f"- 단계 진행률: {route_index}/{route_total}건, {self._pct(route_index, route_total)}",
            f"- 현재 작업: {self._job_label(result)}",
            f"- 현재 상태: {result.get('status')}",
            f"- 재시도: {self._retry_count(result)}",
        ]
        stages = result.get("stages") or {}
        if stages:
            lines.extend(["", "| 단계 | 상태 | 메시지 |", "|---|---|---|"])
            for stage in ("conversion", "tuning", "formatting"):
                item = stages.get(stage) or {}
                lines.append(f"| {self._stage_label(stage)} | {self._cell(item.get('status', '-'))} | {self._cell(item.get('message', '-'))} |")
        message = str(result.get("message") or "").strip()
        if message:
            lines.extend(["", f"메시지: {message}"])
        return "\n".join(lines)

    def _final_message(self, summary: dict[str, Any], results: list[dict[str, Any]], payload: dict[str, Any]) -> str:
        total = sum(int((summary.get(route) or {}).get("planned") or 0) for route in ROUTE_ORDER)
        completed = sum(int((summary.get(route) or {}).get("completed") or 0) for route in ROUTE_ORDER)
        skipped = sum(int((summary.get(route) or {}).get("skipped") or 0) for route in ROUTE_ORDER)
        handled = min(total, completed + skipped)
        stage_activity = self._stage_activity(results)
        lines = [
            "# Overall Progress",
            "",
            f"- 전체 진행률: {handled}/{total}건 처리, {self._pct(handled, total)}",
            self._bar(handled, total),
        ]
        if payload.get("workflow_aborted"):
            lines.extend(["", f"- 중단 사유: {payload.get('abort_reason') or '선행 단계 실패로 후속 작업을 생략했습니다.'}"])
            skipped_counts = dict(payload.get("skipped_plan_counts") or {})
            skipped_total = sum(self._num(value) for value in skipped_counts.values())
            if skipped_total:
                lines.extend(["", f"- 생략된 후속 작업: {skipped_total}건", "", "| 생략 기능 | 건수 |", "|---|---:|"])
                for route in ROUTE_ORDER:
                    count = self._num(skipped_counts.get(route))
                    if count:
                        lines.append(f"| {ROUTE_LABELS[route]} | {count} |")
        lines.extend(["", "## 전체 작업 요약", "", "| 기능 | 예정 | 완료 | PASS | FAIL | SKIP |", "|---|---:|---:|---:|---:|---:|"])
        for route in ROUTE_ORDER:
            item = summary.get(route) or {}
            lines.append(
                "| "
                f"{ROUTE_LABELS[route]} | "
                f"{self._num(item.get('planned'))} | "
                f"{self._num(item.get('completed'))} | "
                f"{self._num(item.get('pass'))} | "
                f"{self._num(item.get('fail'))} | "
                f"{self._num(item.get('skipped'))} |"
            )
        lines.extend(["", "## 실행 단계 현황", "", "| 단계 | 실행 | PASS | FAIL | SKIP |", "|---|---:|---:|---:|---:|"])
        for stage in ("db_migration", "conversion", "tuning", "formatting"):
            item = stage_activity.get(stage) or {}
            lines.append(
                "| "
                f"{self._stage_label(stage)} | "
                f"{self._num(item.get('executed'))} | "
                f"{self._num(item.get('pass'))} | "
                f"{self._num(item.get('fail'))} | "
                f"{self._num(item.get('skipped'))} |"
            )
        if results:
            lines.extend(["", "## 최근 작업 결과"])
            for result in results[-10:]:
                lines.append(f"- {self._job_label(result)}: {result.get('status')}")
        lines.extend(["", "전체 작업 루프가 완료되었습니다."])
        return "\n".join(lines)

    def _stage_activity(self, results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        activity = {
            "db_migration": {"executed": 0, "pass": 0, "fail": 0, "skipped": 0},
            "conversion": {"executed": 0, "pass": 0, "fail": 0, "skipped": 0},
            "tuning": {"executed": 0, "pass": 0, "fail": 0, "skipped": 0},
            "formatting": {"executed": 0, "pass": 0, "fail": 0, "skipped": 0},
        }
        for result in results:
            route = str(result.get("planned_job_route") or result.get("job_route") or "").upper()
            if route == "MIG":
                self._add_activity(activity["db_migration"], bool(result.get("ok")), self._is_skipped(result))
            stages = result.get("stages") or {}
            for stage_name in ("conversion", "tuning", "formatting"):
                stage = stages.get(stage_name) or {}
                if not stage:
                    continue
                self._add_activity(activity[stage_name], bool(stage.get("ok")), self._is_stage_skipped(stage, result, stage_name))
        return activity

    def _add_activity(self, bucket: dict[str, int], ok: bool, skipped: bool) -> None:
        bucket["executed"] += 1
        if skipped:
            bucket["skipped"] += 1
        elif ok:
            bucket["pass"] += 1
        else:
            bucket["fail"] += 1

    def _is_stage_skipped(self, stage: dict[str, Any], result: dict[str, Any], stage_name: str) -> bool:
        if stage.get("skipped"):
            return True
        if stage_name == "tuning" and result.get("tuning_skipped"):
            return True
        if stage_name == "formatting" and result.get("formatting_skipped"):
            return True
        return False

    def _summary(self, results: list[dict[str, Any]], plan_counts: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, dict[str, int]] = {
            route: {"planned": self._num(plan_counts.get(route)), "completed": 0, "pass": 0, "fail": 0, "skipped": 0}
            for route in ROUTE_ORDER
        }
        for result in results:
            route = str(result.get("planned_job_route") or result.get("job_route") or "").upper()
            if route not in summary:
                continue
            summary[route]["completed"] += 1
            if self._is_failure_status(result.get("status")):
                summary[route]["fail"] += 1
            elif self._is_success(route, result):
                summary[route]["pass"] += 1
            elif self._is_skipped(result):
                summary[route]["skipped"] += 1
            else:
                summary[route]["fail"] += 1
        return summary

    def _is_success(self, route: str, result: dict[str, Any]) -> bool:
        stages = result.get("stages") or {}
        status = str(result.get("status") or "").upper()
        if route == "MIG":
            return bool(result.get("ok")) and status == "PASS"
        if route == "SQL_CONVERSION":
            stage = stages.get("conversion") or {}
            return bool(stage.get("ok")) or status in {"PASS", "PASS-CONVERSION", "PASS-TUNING", "FORMATTED"}
        if route == "SQL_TUNING":
            stage = stages.get("tuning") or {}
            return bool(stage.get("ok")) or status in {"PASS", "PASS-TUNING", "FORMATTED"}
        if route == "SQL_FORMATTING":
            stage = stages.get("formatting") or {}
            return bool(stage.get("ok")) or status == "FORMATTED"
        return bool(result.get("ok"))

    def _is_failure_status(self, status: Any) -> bool:
        value = str(status or "").strip().upper()
        return value == "FAIL" or value.startswith("FAIL-")

    def _is_skipped(self, result: dict[str, Any]) -> bool:
        return bool(result.get("workflow_blocked") or result.get("not_runnable") or result.get("skipped") or result.get("tuning_skipped") or result.get("formatting_skipped"))

    def _job_label(self, result: dict[str, Any]) -> str:
        route = str(result.get("planned_job_route") or result.get("job_route") or "").upper()
        label = self._job_type_label(result, route)
        map_id = self._first_value(result, "map_id", "MAP_ID")
        space_nm = self._first_value(result, "space_nm", "SPACE_NM", "spaceName")
        sql_id = self._first_value(result, "sql_id", "SQL_ID", "sqlId")
        if route == "MIG" or str(result.get("job_name") or "").strip().lower() == "migration":
            return f"{label} map_id={map_id or '-'}"
        parts = [
            f"space_nm={space_nm or '-'}",
            f"sql_id={sql_id or '-'}",
        ]
        row_id = str(self._first_value(result, "row_id", "ROW_ID", "rowid", "ROWID") or "").strip()
        if row_id:
            parts.append(f"row_id={row_id}")
        return f"{label} " + ", ".join(parts)

    def _first_value(self, data: dict[str, Any], *keys: str) -> Any:
        candidates = [data]
        for nested_key in ("payload", "job", "job_item", "input", "source", "original", "loop_result"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for key in keys:
            for candidate in candidates:
                value = candidate.get(key)
                if not self._is_blank_value(value):
                    return value
        return None

    def _is_blank_value(self, value: Any) -> bool:
        if value is None:
            return True
        text = str(value).strip()
        return text == "" or text.lower() in {"nan", "none", "null", "nat"}

    def _job_type_label(self, result: dict[str, Any], route: str) -> str:
        job_name = str(result.get("job_name") or "").strip().lower()
        if job_name == "migration":
            return "DB Migration"
        if job_name == "conversion":
            return "SQL Conversion"
        if job_name == "tuning":
            return "SQL Tuning"
        if job_name == "formatting":
            return "SQL Formatting"
        return ROUTE_LABELS.get(route, route or "Unknown")

    def _stage_label(self, stage: str) -> str:
        return {
            "db_migration": "DB Migration",
            "conversion": "SQL Conversion",
            "tuning": "SQL Tuning",
            "formatting": "SQL Formatting",
        }.get(str(stage or ""), str(stage or "-"))

    def _retry_count(self, result: dict[str, Any]) -> int:
        max_attempt = 1
        attempts = list(result.get("attempts") or [])
        for stage in (result.get("stages") or {}).values():
            attempts.extend(stage.get("attempts") or [])
        for attempt in attempts:
            try:
                max_attempt = max(max_attempt, int(attempt.get("attempt") or 1))
            except (TypeError, ValueError):
                continue
        return max(max_attempt - 1, 0)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'■' * filled}{'□' * (width - filled)} `{percent:.1f}%`"

    def _pct(self, value: int, total: int) -> str:
        return f"{(value / total * 100):.1f}%" if total else "-"

    def _cell(self, value: Any) -> str:
        return str(value or "-").replace("|", "/")

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _data_dict(self, item: Any) -> dict[str, Any]:
        if isinstance(item, Data):
            return dict(item.data or {})
        if isinstance(item, Message):
            parsed = self._parse_json_text(item.text)
            if parsed is not None:
                return parsed
            return {"text": item.text}
        if isinstance(item, dict):
            return dict(item)
        return {"value": item}

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        parsed = self._parse_json_text(text)
        if parsed is None:
            raise ValueError("payload_json must be a JSON object")
        return parsed

    def _parse_json_text(self, text: Any) -> dict[str, Any] | None:
        value = str(text or "").strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
            value = re.sub(r"\s*```$", "", value)
        try:
            parsed = json.loads(value) if value else {}
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
