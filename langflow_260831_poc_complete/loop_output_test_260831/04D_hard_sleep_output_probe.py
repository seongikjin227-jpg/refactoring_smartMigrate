from __future__ import annotations

import logging
import time
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import FloatInput, MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


GENERIC_MESSAGE = "\ucd9c\ub825 \uc785\ub2c8\ub2e4."



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "LOOP_TEST_04D_HARD_SLEEP_OUTPUT_PROBE", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], str(message or "")[:4000], 0]})

class LoopOutputTest04DHardSleepOutputProbe(Component):
    display_name = "Loop Output Test 04D Hard Sleep Output Probe"
    description = "Receives Data, sleeps, and returns the same payload as loop feedback."
    name = "LoopOutputTest04DHardSleepOutputProbe"
    icon = "TimerReset"

    inputs = [
        DataInput(name="input_data", display_name="Input Data", required=True),
        FloatInput(name="sleep_seconds", display_name="Sleep Seconds", value=10.0, required=False),
    ]

    outputs = [
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_loop_result(self) -> Data:
        _workflow_log("BUILD_LOOP_RESULT", "START", "before build_loop_result")
        sleep_seconds = self._sleep_seconds()
        input_payload = self._data_dict(getattr(self, "input_data", None))
        started = time.perf_counter()

        time.sleep(sleep_seconds)

        elapsed = time.perf_counter() - started
        payload = {
            **input_payload,
            "component": "LoopOutputTest04DHardSleepOutputProbe",
            "sleep_probe_position": "after_input_data",
            "sleep_probe_seconds": sleep_seconds,
            "sleep_probe_elapsed_seconds": round(elapsed, 3),
            "message": input_payload.get("message") or GENERIC_MESSAGE,
            "answer_text": input_payload.get("answer_text") or GENERIC_MESSAGE,
        }
        self.status = payload
        _workflow_log("BUILD_LOOP_RESULT", "END", "after build_loop_result")
        return Data(data=payload)

    def _data_dict(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return {"value": raw}

    def _sleep_seconds(self) -> float:
        return float(getattr(self, "sleep_seconds", 10.0) or 10.0)
