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


class NewType13FinalSummary(Component):

    display_name = "13 Final Summary"
    description = "Builds the final chat-output-ready summary for the POC flow."
    name = "NewType13FinalSummary"
    icon = "MessageCircle"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="summarize", types=["Message"])]

    def summarize(self) -> Message:
        # Create the final user-facing flow summary message.
        logging.getLogger("smartmigrate.workflow").info("before summarize", extra={"workflow_log": [0, "WORKFLOW", "13_FINAL_SUMMARY", "INFO", "SUMMARIZE", "START", "before summarize", 0]})
        try:
            try:
                payload = self._parse_payload(getattr(self, "payload_json", ""))
                answer = self._answer_text(payload)
                result = {**payload, "component": "13_finalSummary", "answer_text": answer, "final": True}
                self.status = result
                __log_result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").info("after summarize", extra={"workflow_log": [0, "WORKFLOW", "13_FINAL_SUMMARY", "INFO", "SUMMARIZE", "END", "after summarize", 0]})
                return __log_result
            except Exception as exc:
                result = {"ok": False, "component": "13_finalSummary", "error": str(exc), "answer_text": f"POC flow failed: {exc}"}
                self.status = result
                __log_result = Message(text=result["answer_text"])
                logging.getLogger("smartmigrate.workflow").error("error summarize", extra={"workflow_log": [0, "WORKFLOW", "13_FINAL_SUMMARY", "ERROR", "SUMMARIZE", "ERROR", "error summarize", 0]})
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after summarize", extra={"workflow_log": [0, "WORKFLOW", "13_FINAL_SUMMARY", "INFO", "SUMMARIZE", "END", "after summarize", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error summarize: {exc}", extra={"workflow_log": [0, "WORKFLOW", "13_FINAL_SUMMARY", "ERROR", "SUMMARIZE", "ERROR", f"error summarize: {exc}", 0]})
            raise

    def _answer_text(self, payload: dict[str, Any]) -> str:
        # Select the appropriate final summary text for a payload.
        if not payload.get("ok", True):
            return f"실패: {payload.get('error') or 'unknown error'}"

        job_result = payload.get("job_result") or {}
        if job_result:
            return self._pipeline_summary(payload, job_result)

        route = payload.get("job_route") or payload.get("route")
        if route == "PREREQUISITE_BLOCKED":
            return self._blocked_summary(payload)
        if route == "NO_RUNNABLE_JOB":
            summary = payload.get("remaining_summary") or payload.get("pending_summary") or {}
            reason = payload.get("routing_reason") or "실행 가능한 잔여 작업이 없습니다."
            return (
                f"{reason} 현재 대기 작업: "
                f"MIG={summary.get('migration_total', 0)}, "
                f"SQL_CONVERSION={summary.get('sql_conversion_total', summary.get('sql_total', 0))}, "
                f"SQL_TUNING={summary.get('sql_tuning_total', 0)}, "
                f"SQL_FORMATTING={summary.get('sql_formatting_total', 0)}"
            )
        return f"POC 라우팅 완료: route={route}, next={payload.get('next_node')}"

    def _blocked_summary(self, payload: dict[str, Any]) -> str:
        # Format a prerequisite-blocked or no-target summary.
        blocker = payload.get("blocker_route") or "선행"
        requested = self._requested_label_from_reason(payload.get("routing_reason") or "")
        summary = payload.get("remaining_summary") or payload.get("pending_summary") or {}
        if blocker == "TARGET_NOT_RUNNABLE":
            blocked_jobs = list(payload.get("blocked_jobs") or [])
            lines = [
                "요청한 잔여 작업은 현재 실행 가능한 상태가 아닙니다.",
                "Management의 Status Change에서 USE_YN/status/priority를 먼저 조정한 뒤 다시 실행해주세요.",
            ]
            if blocked_jobs:
                lines.append("- blocked target:")
                for job in blocked_jobs[:10]:
                    lines.append(
                        f"  - {self._job_label(job)} "
                        f"use_yn={job.get('use_yn') or '-'} "
                        f"status={job.get('status') if job.get('status') is not None else 'NULL'}"
                    )
            return "\n".join(lines)
        if blocker == "MIG":
            return (
                "DB Migration 작업이 아직 남아 있습니다. "
                f"{requested} 작업을 진행하기 전에 DB Migration 전체 작업을 먼저 진행해주세요. "
                f"현재 MIG 대기 작업 수: {summary.get('migration_total', 0)}"
            )
        return (
            f"{blocker} 선행 작업이 아직 남아 있습니다. "
            f"{requested} 작업을 진행하기 전에 선행 작업을 먼저 진행해주세요."
        )

    def _requested_label_from_reason(self, reason: str) -> str:
        # Infer a requested stage label from a routing reason.
        match = re.search(r"(SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|MIG)", reason or "")
        return match.group(1) if match else "요청한"

    def _pipeline_summary(self, payload: dict[str, Any], job_result: dict[str, Any]) -> str:
        # Format the final summary for a completed pipeline run.
        processed = list(job_result.get("processed_jobs") or job_result.get("results") or [])
        completed = list(job_result.get("completed_jobs") or [])
        failed = list(job_result.get("failed_jobs") or [])
        route = payload.get("job_route") or "MIG"
        run_mode = payload.get("run_mode") or job_result.get("run_mode") or "targeted"
        mode_label = "전체 잔여 작업" if run_mode == "all_pending" else "지정 작업"
        status = job_result.get("status") or payload.get("pipeline_status")
        lines = [
            f"{route} {mode_label} 실행 완료",
            f"- status: {status}",
            f"- processed: {len(processed)}",
            f"- success: {len(completed)}",
            f"- failed: {len(failed)}",
        ]
        if processed:
            lines.append("- job list:")
            for item in processed[:30]:
                lines.append(f"  - {self._job_label(item)}: {item.get('status') or '-'}")
                if item.get("log"):
                    lines.append(f"    log: {item.get('log')}")
            if len(processed) > 30:
                lines.append(f"  - ... and {len(processed) - 30} more")
        if failed:
            lines.append("- failed detail:")
            for item in failed[:10]:
                message = str(item.get("message") or item.get("error") or "").strip()
                lines.append(f"  - {self._job_label(item)}: {message or item.get('status') or 'failed'}")
        return "\n".join(lines)

    def _job_label(self, item: dict[str, Any]) -> str:
        # Return a compact display label for a job target.
        job = item.get("job") if isinstance(item.get("job"), dict) else item
        job_type = str(item.get("job_type") or job.get("job_type") or "").upper()
        if job_type == "MIG" or item.get("map_id") is not None or job.get("map_id") is not None:
            return f"MIG map_id={item.get('map_id') or job.get('map_id')}"
        return f"SQL space_nm={job.get('space_nm') or '-'} sql_id={job.get('sql_id') or job.get('row_id') or '-'}"

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
