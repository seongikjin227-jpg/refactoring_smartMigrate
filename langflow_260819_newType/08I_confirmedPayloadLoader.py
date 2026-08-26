from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data
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


class NewType08IConfirmedPayloadLoader(Component):
    display_name = "08I Confirmed Payload Loader"
    description = "Loads the staged execution payload only after Human Input Approve or Fallback."
    name = "NewType08IConfirmedPayloadLoader"
    icon = "ShieldCheck"

    inputs = [
        MessageTextInput(name="approval_message", display_name="Approval Message", required=True),
        StrInput(name="state_dir", display_name="State Directory", value=DEFAULT_STATE_DIR, required=False, advanced=True),
        StrInput(name="decision", display_name="Decision", value="APPROVED", required=False, advanced=True),
    ]

    outputs = [
        Output(display_name="Execution Payload", name="execution_payload", method="load_payload", types=["Data"]),
    ]

    def load_payload(self) -> Data:
        message_text = self._message_text(getattr(self, "approval_message", ""))
        confirmation_id = self._extract_confirmation_id(message_text)
        if not confirmation_id:
            raise ValueError("confirmation_id was not found in the Human Input approval message")

        path = self._record_path(confirmation_id)
        if not path.exists():
            raise FileNotFoundError(f"confirmation payload not found: {path}")

        record = json.loads(path.read_text(encoding="utf-8"))
        payload = dict(record.get("payload") or {})
        decision = str(getattr(self, "decision", None) or "APPROVED").upper()
        now = datetime.now(timezone.utc).isoformat()

        payload.update(
            {
                "confirmation_id": confirmation_id,
                "confirmation_required": True,
                "confirmation_status": decision,
                "confirmed_at": now,
                "component": "08I_confirmedPayloadLoader",
            }
        )
        payload.setdefault("history", []).append(
            {
                "step": "human_input_confirmation",
                "message": f"confirmation_id={confirmation_id}, status={decision}",
            }
        )

        record["status"] = decision
        record["confirmed_at"] = now
        path.write_text(json.dumps(record, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

        self.status = {
            "component": "08I_confirmedPayloadLoader",
            "confirmation_id": confirmation_id,
            "confirmation_status": decision,
            "next_node": payload.get("next_node"),
        }
        return Data(data=payload)

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
