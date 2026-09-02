from __future__ import annotations

import logging
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


class NewType10DMigIterationDashboard(Component):

    display_name = "10D MIG Iteration Dashboard"
    description = "Formats one completed MIG POC iteration for Chat Output and loop feedback."
    name = "NewType10DMigIterationDashboard"
    icon = "Gauge"

    inputs = [DataInput(name="job_result", display_name="Job Result", required=True)]
    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        logging.getLogger("smartmigrate.workflow").info("before build_message", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "INFO", "BUILD_MESSAGE", "START", 0]})
        try:
            payload = self._build()
            self.status = payload
            __log_result = Message(text=str(payload.get("answer_text") or ""))
            logging.getLogger("smartmigrate.workflow").info("after build_message", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "INFO", "BUILD_MESSAGE", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error build_message: {exc}", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "ERROR", "BUILD_MESSAGE", "ERROR", 0]})
            raise

    def build_loop_result(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before build_loop_result", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "INFO", "BUILD_LOOP_RESULT", "START", 0]})
        try:
            payload = self._build()
            self.status = payload
            __log_result = Data(data=payload.get("loop_result") or payload)
            logging.getLogger("smartmigrate.workflow").info("after build_loop_result", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "INFO", "BUILD_LOOP_RESULT", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error build_loop_result: {exc}", extra={"workflow_log": [0, "WORKFLOW", "10D_MIG_DASH", "ERROR", "BUILD_LOOP_RESULT", "ERROR", 0]})
            raise

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        result = self._parse_payload(getattr(self, "job_result", ""))
        answer = self._answer(result)
        loop_result = {
            "job_type": "MIG",
            "map_id": result.get("map_id"),
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "retry_count": result.get("retry_count", 0),
            "attempt_count": result.get("attempt_count", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "job_index": result.get("job_index", 1),
            "total_jobs": result.get("total_jobs", 1),
            "completed_count": result.get("completed_count", result.get("job_index", 1)),
            "remaining_count": result.get("remaining_count", 0),
            "message": result.get("message") or "",
            "attempts": result.get("attempts") or [],
        }
        payload = {
            **result,
            "component": "10D_migIterationDashboard",
            "answer_text": answer,
            "loop_result": loop_result,
            "final": False,
        }
        self._cached_payload = payload
        return payload

    def _answer(self, result: dict[str, Any]) -> str:
        index = int(result.get("job_index") or 1)
        total = int(result.get("total_jobs") or 1)
        completed = int(result.get("completed_count") or index)
        remaining = max(int(result.get("remaining_count") or (total - completed)), 0)
        attempts = list(result.get("attempts") or [])
        progress_rate = (completed / total * 100) if total else 0.0
        not_runnable = bool(result.get("not_runnable"))
        skipped = bool(result.get("skipped"))
        current_success = 1 if result.get("ok") and not not_runnable and not skipped else 0
        current_failure = 0 if result.get("ok") or not_runnable or skipped else 1

        lines = [
            "## MIG Progress",
            "",
            f"- Current job: map_id={result.get('map_id')}",
            f"- Progress: {completed}/{total} jobs, {progress_rate:.1f}%",
            self._bar(completed, total),
            f"- Current status: {result.get('status')}",
            f"- retry: {result.get('retry_count', 0)}",
            f"- elapsed: {result.get('elapsed_seconds', 0)} seconds",
            "",
            "| Metric | Count |",
            "|---|---:|",
            f"| Completed | {completed} |",
            f"| Current success | {current_success} |",
            f"| Current failure | {current_failure} |",
            f"| Remaining | {remaining} |",
        ]
        if attempts:
            lines.extend(["", "Recent logs:"])
            for attempt in attempts[-5:]:
                stage = attempt.get("failed_stage") or "VERIFY"
                lines.append(f"- attempt {attempt.get('attempt')}: {attempt.get('status')} ({stage})")
                for step in list(attempt.get("steps") or [])[-4:]:
                    lines.append(f"  - {step.get('stage')}: {step.get('status')}")
        message = str(result.get("message") or "").strip()
        if message:
            lines.extend(["", f"Message: {message}"])
        if completed >= total:
            lines.extend(
                [
                    "",
                    "## MIG Request Summary",
                    "",
                    f"- Target jobs: {total}",
                    f"- Completed: {completed}/{total}",
                    f"- Last job status: {result.get('status')}",
                    "",
                    "Requested MIG loop is complete.",
                ]
            )
        return "\n".join(lines)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'#' * filled}{'-' * (width - filled)} `{percent:.1f}%`"

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
            raise ValueError("job_result must be a JSON object")
        return parsed
