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


class NewType08JobTypeRouter(Component):
    display_name = "08 Job Type Conditional Router"
    description = "Conditional router for selected job type. Inactive branches are stopped with self.stop()."
    name = "NewType08JobTypeRouter"
    icon = "Route"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]

    outputs = [
        Output(display_name="MIG Job", name="mig_job", method="mig_response", group_outputs=True),
        Output(display_name="SQL Job", name="sql_job", method="sql_response", group_outputs=True),
        Output(display_name="No Job", name="no_job", method="no_job_response", group_outputs=True),
    ]

    def mig_response(self) -> Data:
        return self._route_output("MIG", "mig_job")

    def sql_response(self) -> Data:
        return self._route_output("SQL", "sql_job")

    def no_job_response(self) -> Data:
        return self._route_output("NO_JOB", "no_job")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            selected = payload.get("selected_job") or {}
            job_type = str(selected.get("job_type") or "").upper() if selected else "NO_JOB"
            job_route = job_type if job_type in {"MIG", "SQL"} else "NO_JOB"
            if job_route != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {
                **payload,
                "component": "08_jobTypeRouter",
                "job_route": job_route,
                "selected_output": output_name,
                "next_node": self._next_node(job_route),
            }
            routed.setdefault("history", []).append({"step": "job_type_route", "message": f"job_route={job_route}"})
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "08_jobTypeRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _next_node(self, route: str) -> str:
        if route == "MIG":
            return "09_dbMigrationAgent"
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
