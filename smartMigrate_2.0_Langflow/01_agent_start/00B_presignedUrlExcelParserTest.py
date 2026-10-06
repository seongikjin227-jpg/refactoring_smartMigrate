from __future__ import annotations

import asyncio
import json
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput, Output
from lfx.schema.message import Message


class NewType00BPresignedUrlExcelParserTest(Component):
    display_name = "00B Presigned URL Excel Parser Test"
    description = "Playground test: receive a downloadUrl in Chat Input and return parsed Excel JSON."
    name = "NewType00BPresignedUrlExcelParserTest"
    icon = "FileSpreadsheet"
    inputs = [MessageInput(name="input_message", display_name="Chat Input", required=True)]
    outputs = [Output(display_name="Parsed Excel", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        message = getattr(self, "input_message", None)
        if not isinstance(message, Message):
            raise TypeError("00B requires a Chat Input Message.")
        url = self._find_url(self._decode_text(message.text))
        if not url:
            raise ValueError('Send a URL directly or JSON such as {"downloadUrl":"http://..."}.')
        parsed = self._parse_excel(asyncio.run(self._download(url)))
        return Message(text=json.dumps({"download_url": url, "parsed_excel": parsed}, ensure_ascii=False, default=str),
                       sender="Machine", sender_name="00B Presigned URL Excel Parser Test",
                       session_id=message.session_id)

    @staticmethod
    def _decode_text(text: Any) -> Any:
        raw = str(text or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"downloadUrl": raw}

    @staticmethod
    def _find_url(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).replace("_", "").lower() in {"downloadurl", "presignedurl", "fileurl"} and isinstance(item, str):
                    return item.strip()
        return ""

    @staticmethod
    async def _download(url: str) -> bytes:
        import aiohttp
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("downloadUrl must be HTTP or HTTPS.")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url, allow_redirects=False) as response:
                response.raise_for_status()
                return await response.read()

    @staticmethod
    def _parse_excel(content: bytes) -> dict[str, Any]:
        import pandas as pd
        sheets = pd.read_excel(BytesIO(content), sheet_name=None, dtype=object, engine="openpyxl")
        return {"format": "xlsx", "sheets": [
            {"name": str(name), "columns": [str(column) for column in frame.columns],
             "rows": frame.where(frame.notna(), None).to_dict(orient="records")}
            for name, frame in sheets.items()
        ]}
