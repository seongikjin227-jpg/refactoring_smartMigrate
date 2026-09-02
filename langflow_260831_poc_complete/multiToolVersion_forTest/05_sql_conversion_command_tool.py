from __future__ import annotations

import logging
import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "MULTI_TOOL_05_SQL_CONVERSION_COMMAND_TOOL", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], 0]})

class PocSqlConversionCommandTool(Component):
    display_name = "POC SQL Conversion Command Tool"
    description = "POC tool for SQL Conversion. It returns PASS when the expected command is called."
    name = "poc_sql_conversion_command_tool"
    icon = "FileCode"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info='Expected JSON: {"action":"run_sql_conversion_job","space_nm":"SFA","sql_id":"selectUser"}',
        ),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        _workflow_log("RUN_COMMAND", "START", "before run_command")
        try:
            command = self._parse_command()
            action = str(command.get("action") or "").strip()
            if action != "run_sql_conversion_job":
                result = {"ok": False, "status": "FAIL", "tool": self.name, "error": f"Unsupported action: {action}"}
            elif not self._has_sql_key(command):
                result = {"ok": False, "status": "FAIL", "tool": self.name, "error": "row_id or space_nm+sql_id is required"}
            else:
                result = {
                    "ok": True,
                    "status": "PASS",
                    "tool": self.name,
                    "action": action,
                    "job_route": "SQL_CONVERSION",
                    "row_id": command.get("row_id"),
                    "space_nm": command.get("space_nm"),
                    "sql_id": command.get("sql_id"),
                    "poc": True,
                    "message": "PASS: SQL Conversion POC tool was called. No real SQL conversion was executed.",
                    "command_json": command,
                }
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "status": "FAIL", "tool": self.name, "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _has_sql_key(self, command: dict[str, Any]) -> bool:
        if str(command.get("row_id") or "").strip():
            return True
        return bool(str(command.get("space_nm") or "").strip() and str(command.get("sql_id") or "").strip())

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if isinstance(raw, Data):
            raw = raw.data
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed
