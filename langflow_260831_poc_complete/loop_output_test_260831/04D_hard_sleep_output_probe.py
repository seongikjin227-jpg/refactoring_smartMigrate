from __future__ import annotations

import time

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema.message import Message


class LoopOutputTest04DHardSleepOutputProbe(Component):
    display_name = "Loop Output Test 04D Hard Sleep Output Probe"
    description = "Uses fixed sleeps and returns once, to separate input-setting issues from streaming behavior."
    name = "LoopOutputTest04DHardSleepOutputProbe"
    icon = "TimerReset"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text", required=False),
        IntInput(name="probe_steps", display_name="Probe Steps", value=5, required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
    ]

    def build_message(self) -> Message:
        steps = self._steps()
        interval_seconds = 5
        started = time.perf_counter()
        lines = [
            "## Hard Sleep Probe",
            "",
            f"- configured_interval_seconds: {interval_seconds}",
            f"- configured_steps: {steps}",
            f"- input_text: {getattr(self, 'input_text', '') or '-'}",
            "",
        ]
        for index in range(1, steps + 1):
            message = f"hard-sleep-probe step {index}/{steps}"
            self.status = {
                "component": "LoopOutputTest04DHardSleepOutputProbe",
                "current_step": index,
                "total_steps": steps,
                "interval_seconds": interval_seconds,
                "message": message,
            }
            self.log(message, name=f"hard-sleep-{index}")
            lines.append(f"- {message}")
            if index < steps:
                time.sleep(interval_seconds)
        elapsed = time.perf_counter() - started
        lines.extend(["", f"- elapsed_seconds: {elapsed:.1f}"])
        return Message(text="\n".join(lines))

    def _steps(self) -> int:
        try:
            return max(1, min(20, int(getattr(self, "probe_steps", None) or 5)))
        except (TypeError, ValueError):
            return 5
