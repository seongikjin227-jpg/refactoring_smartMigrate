from __future__ import annotations

import json
import random
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType15SqlTuningPipeline(Component):
    display_name = "15 SQL Tuning Pipeline"
    description = "POC pipeline that sequentially returns random test results for selected SQL Tuning jobs."
    name = "NewType15SqlTuningPipeline"
    icon = "Activity"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="run_pipeline")]

    def run_pipeline(self) -> Data:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        processed = [self._mock_result(job, index) for index, job in enumerate(self._execution_jobs(payload), start=1)]
        completed = [item for item in processed if item["ok"]]
        failed = [item for item in processed if not item["ok"]]
        result = {
            "ok": not failed,
            "status": "DONE_WITH_TEST_FAILURES" if failed else "DONE",
            "run_mode": payload.get("run_mode") or "targeted",
            "processed_jobs": processed,
            "completed_jobs": completed,
            "failed_jobs": failed,
            "message": f"Processed {len(processed)} SQL Tuning test job(s).",
        }
        out = {**payload, "component": "15_sqlTuningPipeline", "pipeline_status": result["status"], "job_result": result, "next_node": "13_finalSummary"}
        self.status = out
        return Data(data=out)

    def _execution_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(job) for job in (payload.get("selected_jobs") or payload.get("planned_jobs") or []) if isinstance(job, dict)]

    def _mock_result(self, job: dict[str, Any], index: int) -> dict[str, Any]:
        sql_id = job.get("sql_id") or job.get("row_id") or f"SQL-{index}"
        space_nm = job.get("space_nm") or "-"
        rng = random.Random(f"SQL_TUNING:{space_nm}:{sql_id}:{index}")
        ok = rng.choice([True, False])
        status = "SUCCESS-TEST" if ok else "FAIL-TEST"
        return {
            "job_type": "SQL_TUNING",
            "job": job,
            "ok": ok,
            "status": status,
            "message": f"space_nm={space_nm}, sql_id={sql_id} {status} 입니다.",
            "log": f"[POC][SQL_TUNING][{index}] space_nm={space_nm}, sql_id={sql_id}, status={status}, trace_id=TEST-{rng.randint(1000, 9999)}",
        }

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
