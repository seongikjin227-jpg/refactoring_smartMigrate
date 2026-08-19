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


class NewType04GeneralChatResponder(Component):
    display_name = "04 General Chat Responder"
    description = "Fast POC response for general conversation. Replace with an LLM answer node in production."
    name = "NewType04GeneralChatResponder"
    icon = "MessageCircle"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result", name="result", method="respond")]

    def respond(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("active"):
                payload.update({"component": "04_generalChatResponder", "response_status": "SKIPPED"})
                return Data(data=payload)
            answer = (
                "일반 대화로 분류했습니다. POC에서는 LLM 호출 없이 즉시 응답합니다. "
                "운영 구조에서는 이 지점에 Chat LLM 노드를 연결하면 됩니다."
            )
            result = {**payload, "component": "04_generalChatResponder", "answer_text": answer, "final": True}
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_generalChatResponder", "error": str(exc)}
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
