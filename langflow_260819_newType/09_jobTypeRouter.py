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


class NewType09JobTypeRouter(Component):
    display_name = "09 Job Type Router"
    description = "Routes the selected job to MIG, SQL, or final summary. No LLM is used."
    name = "NewType09JobTypeRouter"
    icon = "Route"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]

    outputs = [
        Output(display_name="MIG Payload", name="mig_payload", method="route_mig"),
        Output(display_name="SQL Payload", name="sql_payload", method="route_sql"),
        Output(display_name="No Job Payload", name="no_job_payload", method="route_no_job"),
    ]

    def route_mig(self) -> Data:
        return Data(data=self._route("MIG"))

    def route_sql(self) -> Data:
        return Data(data=self._route("SQL"))

    def route_no_job(self) -> Data:
        return Data(data=self._route("NO_JOB"))

    def _route(self, output_route: str) -> dict[str, Any]:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            selected = payload.get("selected_job") or {}
            job_type = str(selected.get("job_type") or "").upper() if selected else "NO_JOB"
            route = job_type if job_type in {"MIG", "SQL"} else "NO_JOB"
            active = route == output_route
            routed = {
                **payload,
                "component": "09_jobTypeRouter",
                "route": route,
                "active": active,
                "next_node": self._next_node(route),
            }
            if active:
                routed.setdefault("history", []).append({"step": "job_type_route", "message": f"route={route}"})
            self.status = routed
            return routed
        except Exception as exc:
            result = {"ok": False, "component": "09_jobTypeRouter", "error": str(exc), "active": False}
            self.status = result
            return result

    def _next_node(self, route: str) -> str:
        if route == "MIG":
            return "10_migPipelineStub"
        if route == "SQL":
            return "11_sqlPipelineStub"
        return "13_finalSummary"

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
