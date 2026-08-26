from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.message import Message


DEFAULT_STATE_DIR = ".smartmigrate_confirmation_state"


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
        StrInput(name="state_dir", display_name="State Directory", value=DEFAULT_STATE_DIR, required=False, advanced=True),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_message(self) -> Message:
        message_text = self._message_text(getattr(self, "reject_message", ""))
        confirmation_id = self._extract_confirmation_id(message_text)
        if confirmation_id:
            self._mark_rejected(confirmation_id)

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

    def _mark_rejected(self, confirmation_id: str) -> None:
        path = self._record_path(confirmation_id)
        if not path.exists():
            return
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        record["status"] = "REJECTED"
        record["rejected_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    def _extract_confirmation_id(self, text: str) -> str:
        match = re.search(r"\bconfirmation_id\s*=\s*([A-Za-z0-9_.:-]+)", text or "")
        return match.group(1).strip() if match else ""

    def _record_path(self, confirmation_id: str) -> Path:
        state_dir = Path(str(getattr(self, "state_dir", None) or DEFAULT_STATE_DIR))
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", confirmation_id)
        return state_dir / f"{safe_id}.json"

    def _message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        return str(raw or "")
