from __future__ import annotations

import re
from typing import Any

from lfx.custom import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class NewType08RConfirmationRejected(Component):
    display_name = "08R Confirmation Rejected"
    description = "Builds a cancellation message when Human Input Reject is selected."
    name = "NewType08RConfirmationRejected"
    icon = "ShieldX"

    inputs = [
        HandleInput(
            name="reject_payload",
            display_name="Reject Payload",
            input_types=["Data", "Message"],
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_message(self) -> Message:
        payload = self._payload(getattr(self, "reject_payload", None))
        confirmation_id = str(payload.get("confirmation_id") or "").strip()

        text = "\n".join(
            [
                "작업 실행이 취소되었습니다.",
                f"confirmation_id={confirmation_id}" if confirmation_id else "",
                "승인되지 않았으므로 DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 작업을 시작하지 않았습니다.",
            ]
        ).strip()
        self.status = {
            "component": "08R_confirmationRejected",
            "confirmation_id": confirmation_id,
            "confirmation_status": "REJECTED",
            "final": True,
        }
        return Message(text=text)

    def _payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            return {"message": str(raw.text or ""), "confirmation_id": self._extract_confirmation_id(raw.text or "")}
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "")
        return {"message": text, "confirmation_id": self._extract_confirmation_id(text)}

    def _extract_confirmation_id(self, text: str) -> str:
        match = re.search(r"\bconfirmation_id\s*=\s*([A-Za-z0-9_.:-]+)", text or "")
        return match.group(1).strip() if match else ""
