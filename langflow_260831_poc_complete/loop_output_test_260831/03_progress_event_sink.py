from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, MessageTextInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class LoopOutputTest03ProgressEventSink(Component):
    display_name = "Loop Output Test 03 Progress Event Sink"
    description = "Writes one progress event per loop iteration to JSONL for service-side polling tests."
    name = "LoopOutputTest03ProgressEventSink"
    icon = "FileClock"

    inputs = [
        DataInput(name="loop_result_input", display_name="Loop Result", required=True),
        StrInput(name="run_key", display_name="Run Key Override", required=False),
        StrInput(name="output_file", display_name="Output JSONL File", value="progress_events.jsonl", required=False),
        BoolInput(name="include_full_payload", display_name="Include Full Payload", value=False, required=False),
    ]

    outputs = [
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
        Output(display_name="Status Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_loop_result(self) -> Data:
        result = self._build()
        self.status = result
        return Data(data=result["loop_result"])

    def build_message(self) -> Message:
        result = self._build()
        self.status = result
        return Message(text=result["answer_text"])

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached
        payload = self._parse_any(getattr(self, "loop_result_input", ""))
        run_key = self._run_key(payload)
        event = self._event(run_key, payload)
        output_path = self._output_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        result = {
            "component": "LoopOutputTest03ProgressEventSink",
            "ok": True,
            "answer_text": f"progress event written: run_key={run_key}, status={event.get('status')}, file={output_path}",
            "event": event,
            "output_file": str(output_path),
            "loop_result": {**payload, "progress_run_key": run_key, "progress_event_file": str(output_path)},
        }
        self._cached_payload = result
        return result

    def _event(self, run_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "ts": time.time(),
            "run_key": run_key,
            "final": bool(payload.get("final") or payload.get("loop_done")),
            "job_route": payload.get("planned_job_route") or payload.get("job_route"),
            "job_index": payload.get("job_index"),
            "total_jobs": payload.get("total_jobs"),
            "status": payload.get("status"),
            "ok": payload.get("ok"),
            "message": payload.get("message") or payload.get("answer_text") or "",
            "map_id": payload.get("map_id"),
            "space_nm": payload.get("space_nm"),
            "sql_id": payload.get("sql_id"),
        }
        if bool(getattr(self, "include_full_payload", False)):
            event["payload"] = payload
        return event

    def _output_path(self) -> Path:
        configured = str(getattr(self, "output_file", "") or "progress_events.jsonl").strip()
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parent / path

    def _run_key(self, payload: dict[str, Any]) -> str:
        override = str(getattr(self, "run_key", "") or "").strip()
        if override:
            return self._safe_key(override)
        for key in ("workflow_run_id", "run_id", "batch_id", "session_id", "chat_id"):
            value = payload.get(key)
            if str(value or "").strip():
                return self._safe_key(value)
        return "default"

    def _parse_any(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"message": text}

    def _safe_key(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)
        return text[:120] or "default"
