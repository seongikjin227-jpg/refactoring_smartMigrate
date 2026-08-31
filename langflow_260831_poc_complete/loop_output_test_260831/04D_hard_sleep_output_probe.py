from __future__ import annotations

import time
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import FloatInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


GENERIC_MESSAGE = "\ucd9c\ub825 \uc785\ub2c8\ub2e4."


class LoopOutputTest04DHardSleepOutputProbe(Component):
    display_name = "Loop Output Test 04D Hard Sleep Output Probe"
    description = "Receives Chat Output.message_response, sleeps, and returns a generic loop feedback payload."
    name = "LoopOutputTest04DHardSleepOutputProbe"
    icon = "TimerReset"

    inputs = [
        MessageTextInput(name="chat_output_message", display_name="Chat Output Message", required=True),
        FloatInput(name="sleep_seconds", display_name="Sleep Seconds", value=5.0, required=False),
    ]

    outputs = [
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_loop_result(self) -> Data:
        sleep_seconds = self._sleep_seconds()
        chat_text = self._message_text(getattr(self, "chat_output_message", ""))
        started = time.perf_counter()

        self.status = {
            "component": "LoopOutputTest04DHardSleepOutputProbe",
            "status": "SLEEPING_AFTER_CHAT_OUTPUT",
            "message": GENERIC_MESSAGE,
            "sleep_seconds": sleep_seconds,
            "chat_output_seen": bool(chat_text),
        }
        self.log(GENERIC_MESSAGE, name="sleep-after-chat-output")

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        elapsed = time.perf_counter() - started
        payload = {
            "component": "LoopOutputTest04DHardSleepOutputProbe",
            "status": "PASS",
            "ok": True,
            "message": GENERIC_MESSAGE,
            "answer_text": GENERIC_MESSAGE,
            "sleep_probe_position": "after_chat_output_message_response",
            "sleep_probe_seconds": sleep_seconds,
            "sleep_probe_elapsed_seconds": round(elapsed, 3),
            "sleep_probe_chat_output_seen": bool(chat_text),
        }
        self.status = payload
        return Data(data=payload)

    def _message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "").strip()
        return str(raw or "").strip()

    def _sleep_seconds(self) -> float:
        try:
            return max(0.0, min(300.0, float(getattr(self, "sleep_seconds", 5.0) or 0.0)))
        except (TypeError, ValueError):
            return 5.0
