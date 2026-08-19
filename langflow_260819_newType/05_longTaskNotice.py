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


class NewType05LongTaskNotice(Component):
    display_name = "05 Long Task Notice"
    description = "Adds a user-facing notice before routing to pending-job lookup for long-running work."
    name = "NewType05LongTaskNotice"
    icon = "Clock"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="add_notice")]

    def add_notice(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            notice = (
                "이 요청은 DB migration 또는 SQL conversion 작업 실행으로 분류되었습니다. "
                "실제 실행은 오래 걸릴 수 있으므로 먼저 pending job을 확인하고, 선택된 작업만 처리합니다."
            )
            payload.update(
                {
                    "component": "05_longTaskNotice",
                    "long_task_notice": notice,
                    "should_execute": True,
                    "next_node": "06_getPendingJobs",
                }
            )
            payload.setdefault("history", []).append({"step": "long_task_notice", "message": "long running task notice added"})
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "05_longTaskNotice", "error": str(exc)}
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
