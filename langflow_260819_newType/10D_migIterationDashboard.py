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
            "requested_success_count": result.get("requested_success_count", 0),
            "requested_failed_count": result.get("requested_failed_count", 0),
            "requested_waiting_count": result.get("requested_waiting_count", 0),
            "requested_processed_count": result.get("requested_processed_count", result.get("completed_count", result.get("job_index", 1))),
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
        success_so_far = int(result.get("requested_success_count") or 0)
        if success_so_far <= 0 and (result.get("ok") is True or str(result.get("status") or "").upper() == "PASS"):
            success_so_far = 1
        progress_rate = (completed / total * 100) if total else 0.0
        advancement_rate = (success_so_far / total * 100) if total else 0.0
        success_count = 1 if result.get("ok") else 0
        fail_count = 0 if result.get("ok") else 1
        lines = [
            "## MIG 진행 현황",
            "",
            f"- 실행 작업: map_id={result.get('map_id')}",
            f"- 진행률: {completed}/{total}건, {progress_rate:.1f}%",
            self._bar(completed, total),
            f"- 진척률: {success_so_far}/{total}건 PASS, {advancement_rate:.1f}%",
            self._bar(success_so_far, total),
            f"- 현재 결과: {result.get('status')}",
            f"- retry: {result.get('retry_count', 0)}",
            f"- 소요시간: {result.get('elapsed_seconds', 0)}초",
            "",
            "| 구분 | 건수 |",
            "|---|---:|",
            f"| 완료 | {completed} |",
            f"| 현재 성공 | {success_count} |",
            f"| 현재 실패 | {fail_count} |",
            f"| 잔여 | {remaining} |",
        ]
        if attempts:
            lines.extend(["", "최근 로그:"])
            for attempt in attempts[-5:]:
                stage = attempt.get("failed_stage") or "VERIFY"
                lines.append(
                    f"- attempt {attempt.get('attempt')}: {attempt.get('status')} "
                    f"({stage})"
                )
                for step in list(attempt.get("steps") or [])[-4:]:
                    lines.append(f"  - {step.get('stage')}: {step.get('status')}")
        message = str(result.get("message") or "").strip()
        if message:
            lines.extend(["", f"메시지: {message}"])
        if completed >= total:
            failed_so_far = int(result.get("requested_failed_count") or 0)
            waiting_so_far = int(result.get("requested_waiting_count") or 0)
            lines.extend(
                [
                    "",
                    "## MIG 요청 작업 최종 요약",
                    "",
                    f"- 작업 대상: {total}건",
                    f"- 진행률: {completed}/{total}건, {progress_rate:.1f}%",
                    self._bar(completed, total),
                    f"- 진척률: {success_so_far}/{total}건 PASS, {advancement_rate:.1f}%",
                    self._bar(success_so_far, total),
                    f"- 성공: {success_so_far}건",
                    f"- 실패: {failed_so_far}건",
                    f"- 대기: {waiting_so_far}건",
                ]
            )
        return "\n".join(lines)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'🟩' * filled}{'⬜' * (width - filled)} `{percent:.1f}%`"

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
