from __future__ import annotations

import logging
import json
import re
import time
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import FloatInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "LOOP_TEST_04A_SLEEP_INTERVAL_OUTPUT_PROBE", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], str(message or "")[:4000], 0]})

class LoopOutputTest04ASleepIntervalOutputProbe(Component):
    display_name = "Loop Output Test 04A Sleep Interval Output Probe"
    description = "Sleeps before returning each loop message/result to test whether Chat Output flushes during long loops."
    name = "LoopOutputTest04ASleepIntervalOutputProbe"
    icon = "Timer"

    inputs = [
        DataInput(name="message_payload", display_name="18D Message", required=False),
        DataInput(name="loop_result_input", display_name="18D Loop Result", required=True),
        FloatInput(name="sleep_seconds", display_name="Sleep Seconds", value=1.0, required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        payload = self._build()
        self.status = payload
        return Message(text=payload["answer_text"])

    def build_loop_result(self) -> Data:
        payload = self._build()
        self.status = payload
        return Data(data=payload["loop_result"])

    def _build(self) -> dict[str, Any]:
        _workflow_log("_BUILD", "START", "before _build")
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        message_payload = self._parse_any(getattr(self, "message_payload", ""))
        loop_result = self._parse_any(getattr(self, "loop_result_input", ""))
        sleep_seconds = self._sleep_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        text = self._message_text(message_payload) or self._message_text(loop_result)
        if not text:
            text = json.dumps(loop_result, ensure_ascii=False, default=str, indent=2)

        output = {
            **message_payload,
            "component": "LoopOutputTest04ASleepIntervalOutputProbe",
            "answer_text": text,
            "sleep_seconds": sleep_seconds,
            "loop_result": {
                **loop_result,
                "sleep_probe_seconds": sleep_seconds,
                "sleep_probe_component": "LoopOutputTest04ASleepIntervalOutputProbe",
            },
        }
        self._cached_payload = output
        _workflow_log("_BUILD", "END", "after _build")
        return output

    def _message_text(self, payload: dict[str, Any]) -> str:
        for key in ("answer_text", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

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
        return {"text": text}

    def _sleep_seconds(self) -> float:
        try:
            return max(0.0, min(300.0, float(getattr(self, "sleep_seconds", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0
