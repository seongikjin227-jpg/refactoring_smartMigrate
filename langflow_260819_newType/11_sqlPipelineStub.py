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


class NewType11SqlPipelineStub(Component):
    display_name = "11 SQL Pipeline Stub"
    description = "Branch-only POC for SQL conversion execution. It does not call LLM or execute SQL."
    name = "NewType11SqlPipelineStub"
    icon = "FileCode"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="run_stub")]

    def run_stub(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            if not payload.get("active") or str(payload.get("route") or "").upper() != "SQL":
                payload.update({"component": "11_sqlPipelineStub", "pipeline_status": "SKIPPED", "next_node": "13_finalSummary"})
                return Data(data=payload)
            job = payload.get("selected_job") or {}
            result = {
                "job_type": "SQL",
                "row_id": job.get("row_id"),
                "space_nm": job.get("space_nm"),
                "sql_id": job.get("sql_id"),
                "status": "POC_PASS",
                "executed_steps": ["load_sql_job", "branch_check", "stub_sql_conversion_pipeline"],
                "message": "SQL conversion branch selected. Real LLM/SQL work is intentionally skipped.",
            }
            payload.update(
                {
                    "component": "11_sqlPipelineStub",
                    "pipeline_status": "POC_PASS",
                    "job_result": result,
                    "next_node": "12_nextIncompleteLoop",
                }
            )
            payload.setdefault("history", []).append({"step": "sql_stub", "message": f"sql_id={job.get('sql_id')} POC_PASS"})
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "11_sqlPipelineStub", "error": str(exc)}
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
