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


class NewType12DSqlConversionIterationDashboard(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "12D SQL Conversion Iteration Dashboard"
    description = "Formats one SQL Conversion loop result for Chat Output and loop feedback."
    name = "NewType12DSqlConversionIterationDashboard"
    icon = "Gauge"

    inputs = [DataInput(name="job_result", display_name="Job Result", required=True)]
    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "INFO", "BUILD_MESSAGE", "START", "before build_message", 0, "")
        try:
            """Return a chat-friendly iteration message."""
            payload = self._build()
            self.status = payload
            __log_result = Message(text=str(payload.get("answer_text") or ""))
            self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "INFO", "BUILD_MESSAGE", "END", "after build_message", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "ERROR", "BUILD_MESSAGE", "ERROR", f"error build_message: {exc}", 0, "")
            raise

    def build_loop_result(self) -> Data:
        self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "INFO", "BUILD_LOOP_RESULT", "START", "before build_loop_result", 0, "")
        try:
            """Return the loop aggregation payload."""
            payload = self._build()
            self.status = payload
            __log_result = Data(data=payload.get("loop_result") or payload)
            self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "INFO", "BUILD_LOOP_RESULT", "END", "after build_loop_result", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "12D_SQL_DASH", "ERROR", "BUILD_LOOP_RESULT", "ERROR", f"error build_loop_result: {exc}", 0, "")
            raise

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        result = self._parse_payload(getattr(self, "job_result", ""))
        answer = self._answer(result)
        loop_result = {
            "job_type": "SQL_CONVERSION",
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
        payload = {**result, "component": "12D_sqlConversionIterationDashboard", "answer_text": answer, "loop_result": loop_result, "final": False}
        self._cached_payload = payload
        return payload

    def _answer(self, result: dict[str, Any]) -> str:
        """Build Markdown for one SQL conversion/tuning/formatting iteration."""
        index = int(result.get("job_index") or 1)
        total = int(result.get("total_jobs") or 1)
        completed = int(result.get("completed_count") or index)
        progress_rate = (completed / total * 100) if total else 0.0
        lines = [
            "## SQL Conversion Progress",
            "",
            f"- Current job: space_nm={result.get('space_nm')}, sql_id={result.get('sql_id')}",
            f"- Progress: {completed}/{total} jobs, {progress_rate:.1f}%",
            self._bar(completed, total),
            f"- Current status: {result.get('status')}",
            f"- retry: {self._retry_count(result)}",
            "",
            "| Stage | Status | Message |",
            "|---|---|---|",
        ]
        stages = result.get("stages") or {}
        for stage in ("conversion", "tuning", "formatting"):
            item = stages.get(stage) or {}
            lines.append(f"| {stage} | {self._cell(item.get('status', ''))} | {self._cell(item.get('message', ''))} |")
        if result.get("message"):
            lines.extend(["", f"Message: {result.get('message')}"])
        attempt_lines = self._attempt_lines(stages)
        if attempt_lines:
            lines.extend(["", "Attempt history:", "| Stage | Attempt | Step | Status | Detail |", "|---|---:|---|---|---|"])
            lines.extend(attempt_lines)
        if completed >= total:
            lines.extend(["", "Requested SQL Conversion loop is complete."])
        return "\n".join(lines)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        """Return a simple Markdown progress bar."""
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'#' * filled}{'-' * (width - filled)} `{percent:.1f}%`"

    def _cell(self, value: Any) -> str:
        """Escape pipe characters for Markdown table cells."""
        return str(value or "").replace("|", "/")

    def _retry_count(self, result: dict[str, Any]) -> int:
        """Return the number of retry attempts represented in stage history."""
        max_attempt = 1
        for stage in (result.get("stages") or {}).values():
            for attempt in stage.get("attempts") or []:
                try:
                    max_attempt = max(max_attempt, int(attempt.get("attempt") or 1))
                except (TypeError, ValueError):
                    continue
        return max(max_attempt - 1, 0)

    def _attempt_lines(self, stages: dict[str, Any]) -> list[str]:
        """Format stage attempt history for Markdown output."""
        lines: list[str] = []
        for stage_name in ("conversion", "tuning", "formatting"):
            stage = stages.get(stage_name) or {}
            for attempt in stage.get("attempts") or []:
                detail = self._attempt_detail(attempt)
                lines.append(
                    "| "
                    f"{stage_name} | "
                    f"{attempt.get('attempt', '')} | "
                    f"{self._cell(attempt.get('stage'))} | "
                    f"{self._cell(attempt.get('status'))} | "
                    f"{self._cell(detail)} |"
                )
        return lines

    def _attempt_detail(self, attempt: dict[str, Any]) -> str:
        """Build a compact detail string for one attempt row."""
        details = []
        for key in ("reason", "result", "tag_kind", "sql_length"):
            value = attempt.get(key)
            if value not in (None, ""):
                details.append(f"{key}={value}")
        return ", ".join(details)

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

    def _insert_log(
        self,
        map_id,
        mig_kind,
        log_type,
        log_level,
        step_name,
        status,
        message,
        retry_count,
        generated_sql="",
    ):
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO SFAADM.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    SFAADM.MIGRATION_LOG_SEQ.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8, CURRENT_TIMESTAMP
                )
                """,
                [
                    map_id,
                    str(mig_kind or "")[:100],
                    str(log_type or "")[:20],
                    str(log_level or "")[:20],
                    str(step_name or "")[:50],
                    str(status or "")[:20],
                    str(message or "")[:4000],
                    retry_count,
                ],
            )
            conn.commit()
        except Exception as exc:
            self.status = f"NEXT_MIG_LOG insert failed: {exc}"
        finally:
            if conn is not None:
                conn.close()
