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


class NewType10EMigFinalDashboard(Component):
    display_name = "10E MIG Final Dashboard"
    description = "Builds the final MIG loop dashboard after all iterations complete."
    name = "NewType10EMigFinalDashboard"
    icon = "ClipboardCheck"

    inputs = [DataInput(name="loop_result", display_name="Loop Result", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="build_result", types=["Message"])]

    def build_result(self) -> Message:
        payload = self._parse_payload(getattr(self, "loop_result", ""))
        answer = self._answer(payload)
        result = {**payload, "component": "10E_migFinalDashboard", "answer_text": answer, "final": True}
        self.status = result
        return Message(text=answer)

    def _answer(self, payload: dict[str, Any]) -> str:
        processed = list(payload.get("processed_jobs") or [])
        total = int(payload.get("total_jobs") or len(processed) or 0)
        success = int(payload.get("success_count") or 0)
        failed = int(payload.get("failed_count") or 0)
        waiting = int(payload.get("waiting_count") or 0)
        completed = len(processed)
        progress_rate = (completed / total * 100) if total else 0.0
        advancement_rate = (success / total * 100) if total else 0.0
        lines = [
            "## MIG 전체 진행 결과",
            "",
            f"- 전체 작업: {total}건",
            f"- 진행률: {completed}/{total}건, {progress_rate:.1f}%",
            self._bar(completed, total),
            f"- 진척률: {success}/{total}건 PASS, {advancement_rate:.1f}%",
            self._bar(success, total),
            f"- 성공: {success}건",
            f"- 실패: {failed}건",
            f"- 대기: {waiting}건",
            f"- 상태: {payload.get('pipeline_status') or '-'}",
        ]
        if processed:
            lines.extend(["", "| map_id | status | retry | elapsed |", "|---:|---|---:|---:|"])
            for item in processed[:50]:
                lines.append(
                    f"| {item.get('map_id')} | {item.get('status') or '-'} | "
                    f"{item.get('retry_count', 0)} | {item.get('elapsed_seconds', 0)}초 |"
                )
            if len(processed) > 50:
                lines.append(f"| ... | {len(processed) - 50}건 추가 | - | - |")
        failed_jobs = [item for item in processed if item.get("ok") is False]
        if failed_jobs:
            lines.extend(["", "### 실패 작업"])
            for item in failed_jobs[:20]:
                lines.append(f"- map_id={item.get('map_id')}: {item.get('status')} / {item.get('message') or '-'}")
        return "\n".join(lines)

    def _bar(self, value: int, total: int, width: int = 20) -> str:
        clamped = max(0, min(value, total))
        filled = round(clamped / total * width) if total > 0 else 0
        percent = (clamped / total * 100) if total > 0 else 0.0
        return f"{'🟩' * filled}{'⬜' * (width - filled)} `{percent:.1f}%`"

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
            raise ValueError("loop_result must be a JSON object")
        return parsed
