from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class NewType02IntentRouter(Component):
    display_name = "02 Intent Conditional Router"
    description = "Route the 01 classifier payload while preserving its complete output."
    name = "NewType02IntentRouter"
    icon = "Route"

    inputs = [
        MessageTextInput(name="payload_json", display_name="Classifier Message JSON", required=True)
    ]

    outputs = [
        Output(display_name="General Chat", name="general_chat", method="general_chat_response", group_outputs=True),
        Output(display_name="Management", name="management", method="management_response", group_outputs=True),
        Output(display_name="Job Execution", name="job_execution", method="job_execution_response", group_outputs=True),
    ]

    def general_chat_response(self) -> Data:
        return self._route_output("GENERAL_CHAT", "general_chat")

    def management_response(self) -> Data:
        return self._route_output("MANAGEMENT", "management")

    def job_execution_response(self) -> Data:
        return self._route_output("JOB_EXECUTION", "job_execution")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        try:
            if not getattr(self, "_router_started", False):
                logging.getLogger("smartmigrate.workflow").info(
                    "02 Intent Router started",
                    extra={"workflow_log": [0, "WORKFLOW", "02_INTENT_ROUTER", "INFO", "ROUTE", "START", 0]},
                )
                self._router_started = True

            raw_classifier_output = self._raw_classifier_output(getattr(self, "payload_json", ""))
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            payload = self._ensure_source_message_session_id(
                payload, getattr(self, "payload_json", None)
            )

            # Log the unmodified 01 output once. MESSAGE holds the first 4,000
            # characters; GENERATE_SQL retains the full JSON CLOB.
            if not getattr(self, "_classifier_output_logged", False):
                logging.getLogger("smartmigrate.workflow").info(
                    raw_classifier_output,
                    extra={
                        "workflow_log": [
                            0, "WORKFLOW", "01_FINAL_OUTPUT", "INFO",
                            "CLASSIFIER_HANDOFF", "PASS", 0, raw_classifier_output,
                        ]
                    },
                )
                self._classifier_output_logged = True

            route = str(
                payload.get("route") or (payload.get("classification") or {}).get("route") or "GENERAL_CHAT"
            ).upper()
            if payload.get("clarification_required") or str(payload.get("confirmation") or "").upper() == "REJECTED":
                payload["classified_route"] = route
                route = "GENERAL_CHAT"

            next_node = {
                "GENERAL_CHAT": "03_llmResponse",
                "MANAGEMENT": "04_managementRouter",
                "JOB_EXECUTION": "06_getRemainingJobs",
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

        if text.startswith(chr(96) * 3):
            text = re.sub(r"^\x60{3}(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*\x60{3}$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.S)
        text = match.group(0) if match else text
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed

    @staticmethod
    def _raw_classifier_output(raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        if isinstance(raw, Data):
            return json.dumps(raw.data or {}, ensure_ascii=False, default=str)
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False, default=str)
        return str(raw or "")

    @staticmethod
    def _ensure_source_message_session_id(payload: dict[str, Any], raw: Any) -> dict[str, Any]:
        """Keep the session identifier beside files even if 01 omitted it."""
        source_message = payload.get("source_message")
        if not isinstance(source_message, dict):
            source_message = {}
            payload["source_message"] = source_message

        existing_session_id = str(source_message.get("session_id") or "").strip()
        if existing_session_id:
            return payload

        session_id = str(
            payload.get("session_id")
            or getattr(raw, "session_id", "")
            or ""
        ).strip()
        if not session_id:
            files = source_message.get("files") or payload.get("files") or []
            first_file = files[0] if isinstance(files, (list, tuple)) and files else files
            first_file = str(first_file or "").replace("\\", "/").lstrip("/")
            if "/" in first_file:
                session_id = first_file.split("/", 1)[0].strip()

        if session_id:
            source_message["session_id"] = session_id
        return payload
