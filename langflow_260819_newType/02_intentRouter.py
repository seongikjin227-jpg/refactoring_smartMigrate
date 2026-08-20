from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class NewType02IntentRouter(Component):
    display_name = "02 Intent Conditional Router"
    description = "Conditional router for classified intent. Inactive branches are stopped with self.stop()."
    name = "NewType02IntentRouter"
    icon = "Route"

    inputs = [MessageTextInput(name="payload_json", display_name="Classifier Message JSON", required=True)]

    outputs = [
        Output(display_name="General Chat", name="general_chat", method="general_chat_response", group_outputs=True),
        Output(display_name="Management", name="management", method="management_response", group_outputs=True),
        Output(display_name="Job Execution", name="job_execution", method="job_execution_response", group_outputs=True),
    ]

    def general_chat_response(self) -> Data:
        # Return the general chat branch when the route matches.
        return self._route_output("GENERAL_CHAT", "general_chat")

    def management_response(self) -> Data:
        # Return the management branch when the route matches.
        return self._route_output("MANAGEMENT", "management")

    def job_execution_response(self) -> Data:
        # Return the job execution branch when the route matches.
        return self._route_output("JOB_EXECUTION", "job_execution")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        # Build a routed payload for the active output branch.
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            route = str(payload.get("route") or (payload.get("classification") or {}).get("route") or "GENERAL_CHAT").upper()
            next_node = {
                "GENERAL_CHAT": "03_llmResponse",
                "MANAGEMENT": "04_managementRouter",
                "JOB_EXECUTION": "06_getPendingJobs",
            }.get(route, "03_llmResponse")
            if route != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {
                **payload,
                "component": "02_intentRouter",
                "route": route,
                "selected_output": output_name,
                "next_node": next_node,
            }
            routed.setdefault("history", []).append({"step": "intent_router", "message": f"route={route}"})
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "02_intentRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse classifier output from Langflow Data, Message, dict, or JSON text.
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, Message):
            text = str(raw.text or "").strip()
        elif hasattr(raw, "text"):
            text = str(raw.text or "").strip()
        elif hasattr(raw, "data") and isinstance(raw.data, dict):
            return dict(raw.data or {})
        else:
            text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.S)
        text = match.group(0) if match else text
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
