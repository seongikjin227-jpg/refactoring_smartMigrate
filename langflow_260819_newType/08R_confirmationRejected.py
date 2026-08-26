import re
from importlib import import_module
from typing import Any

from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


def _load_component_base():
    for module_name in (
        "langflow.custom.custom_component.base_component",
        "langflow.custom.custom_component.component",
        "lfx.custom.custom_component.component",
        "lfx.custom",
    ):
        try:
            module = import_module(module_name)
            component = getattr(module, "Component", None)
            if component is not None:
                return component
        except Exception:
            continue
    raise ImportError("Could not import Langflow Component base class")


Component = _load_component_base()


class NewType08RConfirmationRejected(Component):
    display_name = "08R Confirmation Rejected"
    description = "Builds a cancellation message when Human Input Reject is selected."
    name = "NewType08RConfirmationRejected"
    icon = "ShieldX"

    inputs = [
        MessageTextInput(name="reject_message", display_name="Reject Message", required=True),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_message(self) -> Message:
        message_text = self._message_text(getattr(self, "reject_message", ""))
        confirmation_id = self._extract_confirmation_id(message_text)

        text = "\n".join(
            [
                "작업 실행이 취소되었습니다.",
                f"confirmation_id={confirmation_id}" if confirmation_id else "",
                "승인되지 않았으므로 DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 작업은 시작하지 않았습니다.",
            ]
        ).strip()
        self.status = {
            "component": "08R_confirmationRejected",
            "confirmation_id": confirmation_id,
            "confirmation_status": "REJECTED",
            "final": True,
        }
        return Message(text=text)

    def _extract_confirmation_id(self, text: str) -> str:
        match = re.search(r"\bconfirmation_id\s*=\s*([A-Za-z0-9_.:-]+)", text or "")
        return match.group(1).strip() if match else ""

    def _message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        return str(raw or "")
