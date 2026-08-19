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


class NewType12SqlConversionPipeline(Component):
    display_name = "12 SQL Conversion Pipeline"
    description = "Placeholder all-pending SQL Conversion pipeline."
    name = "NewType12SqlConversionPipeline"
    icon = "FileCode"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="run_pipeline")]

    def run_pipeline(self) -> Data:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        jobs = list(((payload.get("pending_jobs") or {}).get("sql_conversion_jobs")) or [])
        processed = [{"job_type": "SQL_CONVERSION", "job": job, "ok": True, "status": "PLACEHOLDER"} for job in jobs]
        result = {
            "ok": True,
            "status": "PLACEHOLDER",
            "run_mode": "all_pending",
            "processed_jobs": processed,
            "completed_jobs": [],
            "failed_jobs": [],
            "message": "SQL Conversion pipeline placeholder. Real implementation is pending.",
        }
        out = {
            **payload,
            "component": "12_sqlConversionPipeline",
            "pipeline_status": "PLACEHOLDER",
            "job_result": result,
            "next_node": "13_finalSummary",
        }
        self.status = out
        return Data(data=out)

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
