from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data

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
    outputs = [Output(display_name="Result", name="result", method="summarize")]

    def summarize(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            answer = self._answer_text(payload)
            result = {
                **payload,
                "component": "13_finalSummary",
                "answer_text": answer,
                "final": True,
            }
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "13_finalSummary", "error": str(exc), "answer_text": f"POC flow failed: {exc}"}
            self.status = result
            return Data(data=result)

    def _answer_text(self, payload: dict[str, Any]) -> str:
        if not payload.get("ok", True):
            return f"실패: {payload.get('error') or 'unknown error'}"
        intent = (payload.get("intent") or {}).get("action")
        if intent and intent != "RUN_PENDING_JOBS":
            return f"작업 실행 흐름이 아닙니다. intent={intent}, next={payload.get('next_node')}"
        selected = payload.get("selected_job") or {}
        result = payload.get("job_result") or {}
        if not selected:
            summary = payload.get("pending_summary") or {}
            return f"대기 작업이 없습니다. MIG={summary.get('migration_total', 0)}, SQL={summary.get('sql_total', 0)}"
        return (
            f"POC 완료: route={payload.get('route')}, status={result.get('status') or payload.get('pipeline_status')}. "
            f"selected_job={self._job_label(selected)}, loop={payload.get('cycle_no', 0)}/{payload.get('max_poc_cycles', 1)}"
        )

    def _job_label(self, job: dict[str, Any]) -> str:
        if str(job.get("job_type") or "").upper() == "MIG":
            return f"MIG map_id={job.get('map_id')}"
        return f"SQL space_nm={job.get('space_nm') or '-'} sql_id={job.get('sql_id') or job.get('row_id')}"

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
