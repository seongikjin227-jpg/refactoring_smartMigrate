from __future__ import annotations

import json
import re
import time
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import FloatInput, IntInput, MessageTextInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class LoopOutputTest04DInFlowStreamProbe(Component):
    display_name = "Loop Output Test 04D In Flow Stream Probe"
    description = "Emits component log/status updates during one execution to test whether an existing platform streaming reader can see them."
    name = "LoopOutputTest04DInFlowStreamProbe"
    icon = "Activity"

    inputs = [
        DataInput(name="payload", display_name="Payload", required=False),
        IntInput(name="probe_steps", display_name="Probe Steps", value=5, required=False),
        FloatInput(name="interval_seconds", display_name="Interval Seconds", value=1.0, required=False),
        StrInput(name="probe_label", display_name="Probe Label", value="stream-probe", required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Data", name="data", method="build_data", types=["Data"]),
    ]

    def build_message(self) -> Message:
        result = self._build()
        self.status = result
        return Message(text=result["answer_text"])

    def build_data(self) -> Data:
        result = self._build()
        self.status = result
        return Data(data=result)

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        payload = self._parse_any(getattr(self, "payload", ""))
        steps = self._probe_steps()
        interval = self._interval_seconds()
        label = str(getattr(self, "probe_label", "") or "stream-probe").strip()
        events: list[dict[str, Any]] = []

        for index in range(1, steps + 1):
            event = {
                "label": label,
                "step": index,
                "total": steps,
                "message": f"{label} step {index}/{steps}",
            }
            events.append(event)
            self.status = {
                "component": "LoopOutputTest04DInFlowStreamProbe",
                "stream_probe_running": True,
                "current_step": index,
                "total_steps": steps,
                "message": event["message"],
            }
            self.log(event["message"], name=f"{label}-{index}")
            if interval > 0 and index < steps:
                time.sleep(interval)

        answer = "\n".join(f"- {item['message']}" for item in events)
        result = {
            **payload,
            "component": "LoopOutputTest04DInFlowStreamProbe",
            "ok": True,
            "answer_text": answer,
            "stream_probe_events": events,
            "stream_probe_steps": steps,
            "stream_probe_interval_seconds": interval,
        }
        self._cached_payload = result
        return result

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

    def _probe_steps(self) -> int:
        try:
            return max(1, min(100, int(getattr(self, "probe_steps", None) or 5)))
        except (TypeError, ValueError):
            return 5

    def _interval_seconds(self) -> float:
        try:
            return max(0.0, min(300.0, float(getattr(self, "interval_seconds", None) or 1.0)))
        except (TypeError, ValueError):
            return 1.0
