from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class NewType04FileCommandTool(Component):
    display_name = "04 File Command Tool"
    description = "Logs attachment input and loads/parses an Excel workbook from a Presigned URL on demand."
    name = "NewType04FileCommandTool"
    icon = "FileSearch"

    inputs = [MessageTextInput(name="input_data", display_name="Attachment Input Data", required=True, tool_mode=True)]
    outputs = [Output(display_name="Tool Result", name="tool_result", method="run_tool", types=["Data"])]

    def run_tool(self) -> Data:
        raw_input = self._raw_input(getattr(self, "input_data", ""))
        self._log("CALL_RECEIVED", "START", raw_input)
        payload = self._parse_payload(raw_input)
        action = str(payload.get("action") or "log_attachment_input").strip().lower()
        if action == "log_attachment_input":
            return self._result(action, raw_input)
        if action not in {"parse_mapping_workbook", "parse_uploaded_excel"}:
            raise ValueError("Supported actions: log_attachment_input, parse_mapping_workbook.")

        url = self._find_url(payload)
        if not url:
            raise ValueError("parse_mapping_workbook requires presigned_url or download_url.")
        content = asyncio.run(self._download(url))
        parsed = self._parse_excel(content)
        result = self._result(action, raw_input)
        result.update({"presigned_url": url, "byte_count": len(content), "parsed_excel": parsed})
        self._log("PARSED_EXCEL", "PASS", json.dumps(result, ensure_ascii=False, default=str))
        self.status = result
        return Data(data=result)

    def _result(self, action: str, raw_input: str) -> dict[str, Any]:
        result = {"ok": True, "component": "04_fileCommandTool", "action": action,
                  "logged_chars": len(raw_input), "logged_only": action == "log_attachment_input",
                  "mapping_registered": False, "database_executed": False, "final": True}
        self.status = result
        return result

    @staticmethod
    async def _download(url: str) -> bytes:
        import aiohttp
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Presigned URL must be an HTTP or HTTPS URL.")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                content = await response.read()
                if not content.startswith(b"PK"):
                    preview = content[:120].decode("utf-8", errors="replace").replace("\n", " ")
                    raise ValueError(
                        f"Downloaded response is not an XLSX ZIP file. "
                        f"content_type={response.headers.get('Content-Type')!r}, preview={preview!r}"
                    )
                return content

    @staticmethod
    def _parse_excel(content: bytes) -> dict[str, Any]:
        import pandas as pd
        try:
            sheets = pd.read_excel(BytesIO(content), sheet_name=None, dtype=object, engine="openpyxl")
        except ImportError as exc:
            raise RuntimeError("xlsx parsing requires openpyxl on the Langflow server.") from exc
        return {"format": "xlsx", "sheets": [
            {"name": str(name), "columns": [str(column) for column in frame.columns],
             "rows": frame.where(frame.notna(), None).to_dict(orient="records")}
            for name, frame in sheets.items()
        ]}

    def _log(self, step: str, status: str, payload: str) -> None:
        logging.getLogger("smartmigrate.workflow").info(
            payload, extra={"workflow_log": [0, "WORKFLOW", "04_FILE_COMMAND_TOOL", "INFO", step, status, 0, payload]}
        )

    @staticmethod
    def _find_url(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                if normalized in {"downloadurl", "presignedurl", "fileurl"} and isinstance(item, str):
                    return item.strip()
            for item in value.values():
                found = NewType04FileCommandTool._find_url(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = NewType04FileCommandTool._find_url(item)
                if found:
                    return found
        return ""

    @staticmethod
    def _raw_input(value: Any) -> str:
        if isinstance(value, Message):
            return str(value.text or "")
        if isinstance(value, Data):
            return json.dumps(value.data or {}, ensure_ascii=False, default=str)
        return json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict) else str(value or "")

    @staticmethod
    def _parse_payload(raw_input: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            raise ValueError("input_data must be one JSON object.") from exc
        if not isinstance(payload, dict):
            raise ValueError("input_data must be one JSON object.")
        return payload
