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


class NewType16SqlFormattingAgent(Component):
    display_name = "16 SQL Formatting Agent"
    description = "Placeholder agent that builds an all-pending SQL Formatting payload."
    name = "NewType16SqlFormattingAgent"
    icon = "Bot"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(name="agent_prompt", display_name="SQL Formatting Agent Prompt", required=False),
    ]
    outputs = [Output(display_name="Payload", name="payload", method="build_payload")]

    def build_payload(self) -> Data:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        command = {"action": "run_sql_formatting_job", "run_all_pending": True}
        out = {
            **payload,
            "component": "16_sqlFormattingAgent",
            "command_json": command,
            "run_mode": "all_pending",
            "next_node": "17_sqlFormattingPipeline",
        }
        self.status = out
        return Data(data=out)

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
