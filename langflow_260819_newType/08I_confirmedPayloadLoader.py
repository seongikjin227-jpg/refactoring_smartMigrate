from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


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
        DataInput(name="payload_json", display_name="Execution Payload", required=True),
        MessageTextInput(name="approve_message", display_name="Approve Message", required=False),
        MessageTextInput(name="fallback_message", display_name="Fallback Message", required=False),
    ]

    outputs = [
        Output(display_name="Execution Payload", name="execution_payload", method="load_payload", types=["Data"]),
    ]

    def load_payload(self) -> Data:
        message_text, decision = self._selected_confirmation_message()
        if not message_text:
            self._stop_output("execution_payload")
            self.status = {
                "component": "08I_confirmedPayloadLoader",
                "confirmation_status": "WAITING_FOR_HUMAN_INPUT",
            }
            return Data(data={})

        payload = self._parse_payload(getattr(self, "payload_json", ""))
        confirmation_id = self._extract_confirmation_id(message_text)
        if not confirmation_id:
            confirmation_id = str(payload.get("confirmation_id") or "").strip()
        if not confirmation_id:
            raise ValueError("confirmation_id was not found in approve/fallback message or payload_json")

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

        self.status = {
            "component": "08I_confirmedPayloadLoader",
            "confirmation_id": confirmation_id,
            "confirmation_status": decision,
            "next_node": payload.get("next_node"),
        }
        return Data(data=payload)

    def _selected_confirmation_message(self) -> tuple[str, str]:
        approve_text = self._message_text(getattr(self, "approve_message", ""))
        fallback_text = self._message_text(getattr(self, "fallback_message", ""))
        if self._extract_confirmation_id(approve_text):
            return approve_text, "APPROVED"
        if self._extract_confirmation_id(fallback_text):
            return fallback_text, "APPROVED_BY_TIMEOUT"
        if approve_text.strip():
            return approve_text, "APPROVED"
        if fallback_text.strip():
            return fallback_text, "APPROVED_BY_TIMEOUT"
        return "", "WAITING_FOR_HUMAN_INPUT"

    def _extract_confirmation_id(self, text: str) -> str:
        match = re.search(r"\bconfirmation_id\s*=\s*([A-Za-z0-9_.:-]+)", text or "")
        return match.group(1).strip() if match else ""

    def _message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        return str(raw or "")

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

    def _stop_output(self, output_name: str) -> None:
        stop = getattr(self, "stop", None)
        if not callable(stop):
            return
        try:
            stop(output_name)
        except TypeError:
            try:
                stop(output_name=output_name)
            except Exception:
                pass
        except Exception:
            pass
