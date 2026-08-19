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


class NewType04Dashboard(Component):
    display_name = "04 Dashboard"
    description = "POC dashboard/status query branch."
    name = "NewType04Dashboard"
    icon = "Gauge"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result", name="result", method="run")]

    def run(self) -> Data:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        answer = "Dashboard 조회 플로우로 분기되었습니다. POC에서는 실제 DB 조회 없이 라우팅 결과만 반환합니다."
        result = {**payload, "component": "04_dashboard", "answer_text": answer, "final": True}
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
