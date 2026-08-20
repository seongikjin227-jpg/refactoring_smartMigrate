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


class NewType04StatusChange(Component):
    display_name = "04 Status Change"
    description = "POC status/priority/targeting update branch."
    name = "NewType04StatusChange"
    icon = "SlidersHorizontal"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        target = payload.get("target") or {}
        answer = (
            "Status 변경 플로우로 분기되었습니다. "
            f"target={json.dumps(target, ensure_ascii=False, default=str)}. "
            "POC에서는 실제 DB update 없이 라우팅 결과만 반환합니다."
        )
        result = {**payload, "component": "04_statusChange", "answer_text": answer, "final": True}
        self.status = result
        return Message(text=answer)

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
