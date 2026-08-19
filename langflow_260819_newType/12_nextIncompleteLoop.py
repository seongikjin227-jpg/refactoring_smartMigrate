from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType12NextIncompleteLoop(Component):
    display_name = "12 Next Incomplete Loop"
    description = "Decides whether a Langflow edge should loop back to 07 Get Pending Jobs or finish."
    name = "NewType12NextIncompleteLoop"
    icon = "RefreshCw"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        IntInput(name="max_poc_cycles", display_name="Max POC Cycles", value=1, required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="decide_loop")]

    def decide_loop(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            cycle_no = int(payload.get("cycle_no") or 0) + 1
            max_cycles = max(1, int(getattr(self, "max_poc_cycles", None) or 1))
            should_loop = bool(payload.get("job_result")) and cycle_no < max_cycles
            payload.update(
                {
                    "component": "12_nextIncompleteLoop",
                    "cycle_no": cycle_no,
                    "max_poc_cycles": max_cycles,
                    "should_loop": should_loop,
                    "next_node": "07_getPendingJobs" if should_loop else "13_finalSummary",
                }
            )
            payload.setdefault("history", []).append({"step": "loop_decision", "message": f"should_loop={should_loop}"})
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "12_nextIncompleteLoop", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
