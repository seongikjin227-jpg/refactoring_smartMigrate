from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04FileAttachmentTrace(Component):
    display_name = "04 File Attachment Trace"
    description = "Reads the uploaded file referenced by 04 Router and outputs the same attachment text Langflow Agent sends to an LLM."
    name = "NewType04FileAttachmentTrace"
    icon = "FileSearch"

    inputs = [
        DataInput(
            name="payload_json",
            display_name="04 Router Payload",
            required=True,
            info="Connect 04 Router's File Attachment Trace output.",
        )
    ]
    outputs = [Output(display_name="Attachment Context", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        payload = self._payload(getattr(self, "payload_json", None))
        file_reference = str(payload.get("attachment_file_reference") or "").strip()
        relative_path = self._session_scoped_path(file_reference)

        # Message.get_file_content_dicts is the same Langflow method used by
        # Message.to_lc_message in the built-in Agent component.
        source_message = Message(text="", sender="User", files=[relative_path])
        content_dicts = source_message.get_file_content_dicts()
        attachment_context = self._render(content_dicts)
        if not attachment_context:
            raise ValueError(f"No readable text was produced for attachment: {relative_path}")

        logging.getLogger("smartmigrate.workflow").info(
            attachment_context,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "04_FILE_ATTACHMENT_TRACE",
                    "INFO",
                    "READ_ATTACHMENT",
                    "PASS",
                    0,
                    attachment_context,
                ]
            },
        )
        self.status = {
            "ok": True,
            "file_reference": file_reference,
            "resolved_relative_path": relative_path,
            "content_block_count": len(content_dicts),
        }
        return Message(
            text=attachment_context,
            sender="Machine",
            sender_name="04 File Attachment Trace",
            session_id=getattr(getattr(self, "graph", None), "session_id", None),
        )

    @staticmethod
    def _payload(raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        data = getattr(raw, "data", None)
        return dict(data) if isinstance(data, dict) else {}

    def _session_scoped_path(self, file_reference: str) -> str:
        if not file_reference:
            raise ValueError("attachment_file_reference is missing from the 04 Router payload.")

        normalized = file_reference.replace("\\", "/").lstrip("/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or ":" in normalized:
            raise ValueError("attachment_file_reference must be a safe relative Langflow upload path.")

        if len(path.parts) > 1:
            return str(path)

        session_id = str(getattr(getattr(self, "graph", None), "session_id", "") or "").strip()
        if not session_id:
            raise ValueError("Graph session_id is required to resolve an attachment filename.")
        return str(PurePosixPath(session_id) / path.name)

    @staticmethod
    def _render(content_dicts: list[dict[str, Any]]) -> str:
        rendered: list[str] = []
        for item in content_dicts:
            if item.get("type") == "text":
                rendered.append(str(item.get("text") or ""))
            else:
                rendered.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n\n".join(part for part in rendered if part)
