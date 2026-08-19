from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data


class NewType01IntentParser(Component):
    display_name = "01 Intent Parser"
    description = "POC intent parser without LLM. Converts a user request into a routing payload."
    name = "NewType01IntentParser"
    icon = "MessagesSquare"

    inputs = [
        MessageTextInput(
            name="user_request",
            display_name="User Request",
            required=True,
            info="Raw chat input. Example: '대기 작업 실행해줘', 'status', 'stop'.",
        ),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="parse_intent")]

    def parse_intent(self) -> Data:
        try:
            text = str(getattr(self, "user_request", "") or "").strip()
            intent = self._classify(text)
            payload = {
                "ok": True,
                "component": "01_intentParser",
                "user_request": text,
                "intent": intent,
                "is_execution_command": intent["action"] == "RUN_PENDING_JOBS",
                "next_node": "02_llmClassifier",
                "history": [self._event("parsed", f"intent={intent['action']}")],
            }
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "01_intentParser", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _classify(self, text: str) -> dict[str, Any]:
        value = text.lower()
        if re.search(r"(stop|중지|멈춰|정지)", value):
            return {"action": "STOP", "confidence": 0.95, "reason": "stop keyword"}
        if re.search(r"(status|상태|현황|요약)", value):
            return {"action": "STATUS", "confidence": 0.9, "reason": "status keyword"}
        if re.search(r"(실행|run|start|처리|배치|pending|대기|job|작업)", value):
            return {"action": "RUN_PENDING_JOBS", "confidence": 0.9, "reason": "execution keyword"}
        return {"action": "CHAT_ONLY", "confidence": 0.5, "reason": "no execution keyword"}

    def _event(self, step: str, message: str) -> dict[str, str]:
        return {"step": step, "message": message}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
