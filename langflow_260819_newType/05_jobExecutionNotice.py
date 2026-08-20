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


class NewType05JobExecutionNotice(Component):
    display_name = "05 Job Execution Notice"
    description = "Adds a user-facing notice before loading job-target context."
    name = "NewType05JobExecutionNotice"
    icon = "Clock"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="add_notice")]

    def add_notice(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            notice = (
                "이 요청은 작업 대상 실행으로 분류되었습니다. "
                "먼저 pending job과 사용자가 지정한 map_id/sql_id/space_nm을 확인한 뒤 실행 대상을 정리합니다."
            )
            payload.update(
                {
                    "component": "05_jobExecutionNotice",
                    "job_execution_notice": notice,
                    "should_execute": True,
                    "next_node": "06_getPendingJobs",
                }
            )
            payload.setdefault("history", []).append({"step": "job_execution_notice", "message": "job execution notice added"})
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "05_jobExecutionNotice", "error": str(exc)}
            self.status = result
            return Data(data=result)

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
