from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType03IntentRouter(Component):
    display_name = "03 Intent Router"
    description = "Routes classified input to general chat, fast status/control, or long-running job flow."
    name = "NewType03IntentRouter"
    icon = "Route"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]

    outputs = [
        Output(display_name="General Chat Payload", name="general_chat_payload", method="route_general_chat"),
        Output(display_name="Fast Status Payload", name="fast_status_payload", method="route_fast_status"),
        Output(display_name="Long Job Payload", name="long_job_payload", method="route_long_job"),
        Output(display_name="Control Payload", name="control_payload", method="route_control"),
    ]

    def route_general_chat(self) -> Data:
        return Data(data=self._route("GENERAL_CHAT"))

    def route_fast_status(self) -> Data:
        return Data(data=self._route("FAST_STATUS"))

    def route_long_job(self) -> Data:
        return Data(data=self._route("LONG_RUNNING_JOB"))

    def route_control(self) -> Data:
        return Data(data=self._route("STOP_CONTROL"))

    def _route(self, target_route: str) -> dict[str, Any]:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            route = str(payload.get("route") or (payload.get("classification") or {}).get("route") or "GENERAL_CHAT").upper()
            active = route == target_route
            next_node = {
                "GENERAL_CHAT": "04_generalChatResponder",
                "FAST_STATUS": "05_fastStatusResponder",
                "LONG_RUNNING_JOB": "06_longTaskNotice",
                "STOP_CONTROL": "05_fastStatusResponder",
            }.get(route, "04_generalChatResponder")
            routed = {
                **payload,
                "component": "03_intentRouter",
                "router_target": target_route,
                "active": active,
                "next_node": next_node,
            }
            if active:
                routed.setdefault("history", []).append({"step": "intent_router", "message": f"route={route}"})
            self.status = routed
            return routed
        except Exception as exc:
            result = {"ok": False, "component": "03_intentRouter", "error": str(exc), "active": False}
            self.status = result
            return result

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
