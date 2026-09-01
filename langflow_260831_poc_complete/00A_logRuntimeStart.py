from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


class SmartMigrateMemoryHandler(logging.Handler):

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.handler_marker = HANDLER_MARKER
        self.run_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        self.records: list[dict] = []
        self.persisted = False

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, "workflow_log", None)
        if isinstance(event, (list, tuple)):
            event = {
                "map_id": event[0] if len(event) > 0 else 0,
                "mig_kind": event[1] if len(event) > 1 else "WORKFLOW",
                "log_type": event[2] if len(event) > 2 else "PY_LOG",
                "log_level": event[3] if len(event) > 3 else "noLevelName",
                "step_name": event[4] if len(event) > 4 else "LOGGING",
                "status": event[5] if len(event) > 5 else "noStatus",
                "message": event[6] if len(event) > 6 else "noMessage",
                "retry_count": event[7] if len(event) > 7 else 0,
            }
        if not isinstance(event, dict):
            event = {
                "map_id": 0,
                "mig_kind": "WORKFLOW",
                "log_type": "PY_LOG",
                "log_level": "noLevelName",
                "step_name": "LOGGING",
                "status": "noStatus",
                "message": "noMessage",
                "retry_count": 0,
            }
        created_at = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")
        self.records.append(
            {
                "RUN_ID": self.run_id,
                "SEQ": len(self.records) + 1,
                "CREATED_AT": created_at,
                "MAP_ID": int(event.get("map_id") or 0),
                "MIG_KIND": str(event.get("mig_kind") or "WORKFLOW")[:100],
                "LOG_TYPE": str(event.get("log_type") or "")[:20],
                "LOG_LEVEL": str(event.get("log_level") or "noLevelName")[:20],
                "STEP_NAME": str(event.get("step_name") or "")[:50],
                "STATUS": str(event.get("status") or "noStatus")[:20],
                "MESSAGE": str(event.get("message") or "noMessage")[:4000],
                "RETRY_COUNT": int(event.get("retry_count") or 0),
            }
        )


class NewType00ALogRuntimeStart(Component):
    display_name = "00A Log Runtime Start"
    description = "Reset the workflow logging handler, register a new in-memory handler, and pass the chat input through."
    name = "NewType00ALogRuntimeStart"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text", required=False),
    ]

    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        text = str(getattr(self, "input_text", "") or "")
        logger = logging.getLogger(LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = SmartMigrateMemoryHandler()
        logger.addHandler(handler)
        logger.info(f"workflow start input_len={len(text)}", extra={"workflow_log": [0, "WORKFLOW", "LOG_RUNTIME_START", "INFO", "RUN", "START", f"workflow start input_len={len(text)}", 0]})
        return Message(text=text)
