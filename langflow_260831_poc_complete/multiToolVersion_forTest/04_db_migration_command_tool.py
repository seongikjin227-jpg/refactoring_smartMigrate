from __future__ import annotations

import logging
import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(
        logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO,
        str(message or ""),
        extra={
            "workflow_log": {
                "map_id": 0,
                "mig_kind": "WORKFLOW",
                "log_type": "MULTI_TOOL_04_DB_MIGRATION_COMMAND_TOOL",
                "log_level": str(log_level or "INFO").upper(),
                "step_name": str(step_name or "")[:50],
                "status": str(status or "")[:20],
                "message": str(message or "")[:4000],
                "retry_count": 0,
            }
        },
    )

class PocDbMigrationCommandTool(Component):
    display_name = "POC DB Migration Command Tool"
    description = "POC tool for DB Migration. It returns PASS when the expected command is called."
    name = "poc_db_migration_command_tool"
    icon = "Database"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info='Expected JSON: {"action":"run_migration_job","map_id":101}',
        ),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        _workflow_log("RUN_COMMAND", "START", "before run_command")
        try:
            command = self._parse_command()
            action = str(command.get("action") or "").strip()
            if action != "run_migration_job":
                result = {"ok": False, "status": "FAIL", "tool": self.name, "error": f"Unsupported action: {action}"}
            elif command.get("map_id") is None or str(command.get("map_id")).strip() == "":
                result = {"ok": False, "status": "FAIL", "tool": self.name, "error": "map_id is required"}
            else:
                result = {
                    "ok": True,
                    "status": "PASS",
                    "tool": self.name,
                    "action": action,
                    "job_route": "MIG",
                    "map_id": command.get("map_id"),
                    "poc": True,
                    "message": "PASS: DB Migration POC tool was called. No real migration was executed.",
                    "command_json": command,
                }
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "status": "FAIL", "tool": self.name, "error": str(exc)}
            self.status = result
            return Data(data=result)

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
