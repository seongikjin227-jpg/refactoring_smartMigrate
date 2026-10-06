from __future__ import annotations

import json
import logging
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class NewType04FileCommandTool(Component):
    display_name = "04 File Command Tool"
    description = "Logs the Management Agent attachment input exactly as received. It does not read, chunk, or modify files."
    name = "NewType04FileCommandTool"
    icon = "FileSearch"

    inputs = [
        MessageTextInput(
            name="input_data",
            display_name="Attachment Input Data",
            required=True,
            tool_mode=True,
            info=(
                "Call with a JSON object containing action=log_attachment_input and "
                "the full attachment/session/request data received by the Management Agent."
            ),
        )
    ]
    outputs = [
        Output(display_name="Tool Result", name="tool_result", method="run_tool", types=["Data"]),
    ]

    def run_tool(self) -> Data:
        raw_input = self._raw_input(getattr(self, "input_data", ""))
        payload = self._parse_payload(raw_input)
        action = str(payload.get("action") or "log_attachment_input").strip().lower()

        if action != "log_attachment_input":
            raise ValueError("Only action=log_attachment_input is supported.")

        logging.getLogger("smartmigrate.workflow").info(
            raw_input,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "04_FILE_COMMAND_TOOL",
                    "INFO",
                    "LOG_AGENT_INPUT",
                    "PASS",
                    0,
                    raw_input,
                ]
            },
        )

        result = {
            "ok": True,
            "component": "04_fileCommandTool",
            "action": action,
            "logged_chars": len(raw_input),
            "final": True,
        }
        self.status = result
        return Data(data=result)

    @staticmethod
    def _raw_input(value: Any) -> str:
        if isinstance(value, Message):
            return str(value.text or "")
        if isinstance(value, Data):
            return json.dumps(value.data or {}, ensure_ascii=False, default=str)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value or "")

    @staticmethod
    def _parse_payload(raw_input: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            raise ValueError("input_data must be one JSON object.") from exc
        if not isinstance(payload, dict):
            raise ValueError("input_data must be one JSON object.")
        return payload
