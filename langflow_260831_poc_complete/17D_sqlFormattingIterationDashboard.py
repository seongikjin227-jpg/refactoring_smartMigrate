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


class NewType17DSqlFormattingIterationDashboard(Component):

    display_name = "17D SQL Formatting Iteration Dashboard"
    description = "Formats one SQL Formatting loop result for Chat Output and loop feedback."
    name = "NewType17DSqlFormattingIterationDashboard"
    icon = "Gauge"

    inputs = [DataInput(name="job_result", display_name="Job Result", required=True)]
    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        logging.getLogger("smartmigrate.workflow").info(
            "before build_message",
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "17D_SQL_DASH",
                    "log_level": "INFO",
                    "step_name": "BUILD_MESSAGE",
                    "status": "START",
                    "message": "before build_message",
                    "retry_count": 0,
                }
            },
        )
        try:
            """Return a chat-friendly iteration message."""
            payload = self._build()
            self.status = payload
            __log_result = Message(text=str(payload.get("answer_text") or ""))
            logging.getLogger("smartmigrate.workflow").info(
                "after build_message",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "17D_SQL_DASH",
                        "log_level": "INFO",
                        "step_name": "BUILD_MESSAGE",
                        "status": "END",
                        "message": "after build_message",
                        "retry_count": 0,
                    }
                },
            )
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"error build_message: {exc}",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "17D_SQL_DASH",
                        "log_level": "ERROR",
                        "step_name": "BUILD_MESSAGE",
                        "status": "ERROR",
                        "message": f"error build_message: {exc}",
                        "retry_count": 0,
                    }
                },
            )
            raise

    def build_loop_result(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "before build_loop_result",
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "17D_SQL_DASH",
                    "log_level": "INFO",
                    "step_name": "BUILD_LOOP_RESULT",
                    "status": "START",
                    "message": "before build_loop_result",
                    "retry_count": 0,
                }
            },
        )
        try:
            """Return the loop aggregation payload."""
            payload = self._build()
            self.status = payload
            __log_result = Data(data=payload.get("loop_result") or payload)
            logging.getLogger("smartmigrate.workflow").info(
                "after build_loop_result",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "17D_SQL_DASH",
                        "log_level": "INFO",
                        "step_name": "BUILD_LOOP_RESULT",
                        "status": "END",
                        "message": "after build_loop_result",
                        "retry_count": 0,
                    }
                },
            )
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"error build_loop_result: {exc}",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "17D_SQL_DASH",
                        "log_level": "ERROR",
                        "step_name": "BUILD_LOOP_RESULT",
                        "status": "ERROR",
                        "message": f"error build_loop_result: {exc}",
                        "retry_count": 0,
                    }
                },
            )
            raise

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        result = self._parse_payload(getattr(self, "job_result", ""))
        answer = self._answer(result)
        loop_result = {
            "job_type": "SQL_FORMATTING",
            "space_nm": result.get("space_nm"),
            "sql_id": result.get("sql_id"),
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "job_index": result.get("job_index", 1),
            "total_jobs": result.get("total_jobs", 1),
            "completed_count": result.get("completed_count", result.get("job_index", 1)),
            "remaining_count": result.get("remaining_count", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "stages": result.get("stages") or {},
            "message": result.get("message") or "",
        }
        payload = {**result, "component": "17D_sqlFormattingIterationDashboard", "answer_text": answer, "loop_result": loop_result, "final": False}
        self._cached_payload = payload
        return payload

    def _answer(self, result: dict[str, Any]) -> str:
        """Build Markdown for one SQL formatting iteration."""
        index = int(result.get("job_index") or 1)
        total = int(result.get("total_jobs") or 1)
        completed = int(result.get("completed_count") or index)
        progress_rate = (completed / total * 100) if total else 0.0
        lines = [
            "## SQL Formatting Progress",
            "",
            f"- Current job: space_nm={result.get('space_nm')}, sql_id={result.get('sql_id')}",
            f"- Progress: {completed}/{total} jobs, {progress_rate:.1f}%",
            self._bar(completed, total),
            f"- Current status: {result.get('status')}",
            "",
            "| Stage | Status | Message |",
            "|---|---|---|",
        ]
        stages = result.get("stages") or {}
        for stage in ("conversion", "tuning", "formatting"):
            item = stages.get(stage) or {}
            lines.append(f"| {stage} | {item.get('status', '-')} | {self._cell(item.get('message', '-'))} |")
        if result.get("message"):
            lines.extend(["", f"Message: {result.get('message')}"])
        if completed >= total:
            lines.extend(["", "Requested SQL Formatting loop is complete."])
        return "\n".join(lines)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        """Return a simple Markdown progress bar."""
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'#' * filled}{'-' * (width - filled)} `{percent:.1f}%`"

    def _cell(self, value: Any) -> str:
        """Escape pipe characters for Markdown table cells."""
        return str(value or "-").replace("|", "/")

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        """Parse a Langflow Data, Message, dict, or JSON string payload."""
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
