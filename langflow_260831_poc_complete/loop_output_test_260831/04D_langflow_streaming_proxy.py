from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse


LANGFLOW_SERVER_URL = os.getenv("LANGFLOW_SERVER_URL", "").rstrip("/")
LANGFLOW_FLOW_ID = os.getenv("LANGFLOW_FLOW_ID", "")
LANGFLOW_API_KEY = os.getenv("LANGFLOW_API_KEY", "")

app = FastAPI(title="SmartMigrate Langflow Streaming Proxy")


@app.post("/smartmigrate/run-stream")
async def run_stream(request_body: dict[str, Any]) -> StreamingResponse:
    if not LANGFLOW_SERVER_URL or not LANGFLOW_FLOW_ID:
        raise HTTPException(status_code=500, detail="LANGFLOW_SERVER_URL and LANGFLOW_FLOW_ID are required")

    return StreamingResponse(
        _stream_langflow(request_body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_langflow(request_body: dict[str, Any]) -> AsyncIterator[str]:
    url = f"{LANGFLOW_SERVER_URL}/api/v1/run/{LANGFLOW_FLOW_ID}?stream=true"
    headers = {
        "accept": "text/event-stream, application/json",
        "Content-Type": "application/json",
    }
    if LANGFLOW_API_KEY:
        headers["x-api-key"] = LANGFLOW_API_KEY

    payload = {
        "input_value": request_body.get("input_value") or request_body.get("message") or "",
        "input_type": request_body.get("input_type") or "chat",
        "output_type": request_body.get("output_type") or "chat",
        "output_component": request_body.get("output_component"),
        "session_id": request_body.get("session_id"),
        "tweaks": request_body.get("tweaks") or {},
    }

    yield _sse("proxy_start", {"status": "started"})
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                detail = await response.aread()
                yield _sse("proxy_error", {"status_code": response.status_code, "detail": detail.decode("utf-8", errors="ignore")})
                return
            async for line in response.aiter_lines():
                if not line:
                    continue
                yield _normalize_stream_line(line)
    yield _sse("proxy_end", {"status": "ended"})


def _normalize_stream_line(line: str) -> str:
    text = line.strip()
    if text.startswith("data:") or text.startswith("event:"):
        return text + "\n\n"
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {"text": text}
    event_name = parsed.get("event") or parsed.get("type") or "langflow_event" if isinstance(parsed, dict) else "langflow_event"
    return _sse(str(event_name), parsed)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
