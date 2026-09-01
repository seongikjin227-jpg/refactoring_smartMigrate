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


class NewType12SqlConversionPipeline(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "12 SQL Conversion Pipeline"
    description = "POC pipeline that sequentially returns random test results for selected SQL Conversion jobs."
    name = "NewType12SqlConversionPipeline"
    icon = "FileCode"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="run_pipeline")]

    def run_pipeline(self) -> Data:
        # Run the POC pipeline and return test execution results.
        self._insert_log(0, "WORKFLOW", "12_PIPELINE", "INFO", "RUN_PIPELINE", "START", "before run_pipeline", 0, "")
        try:
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
                "message": f"Processed {len(processed)} SQL Conversion test job(s).",
            }
            out = {**payload, "component": "12_sqlConversionPipeline", "pipeline_status": result["status"], "job_result": result, "next_node": "13_finalSummary"}
            self.status = out
            __log_result = Data(data=out)
            self._insert_log(0, "WORKFLOW", "12_PIPELINE", "INFO", "RUN_PIPELINE", "END", "after run_pipeline", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "12_PIPELINE", "ERROR", "RUN_PIPELINE", "ERROR", f"error run_pipeline: {exc}", 0, "")
            raise

    def _execution_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        # Read selected or planned jobs from the payload.
        return [dict(job) for job in (payload.get("selected_jobs") or payload.get("planned_jobs") or []) if isinstance(job, dict)]

    def _mock_result(self, job: dict[str, Any], index: int) -> dict[str, Any]:
        # Create a deterministic-looking POC result for one job.
        sql_id = job.get("sql_id") or job.get("row_id") or f"SQL-{index}"
        space_nm = job.get("space_nm") or "-"
        rng = random.Random(f"SQL_CONVERSION:{space_nm}:{sql_id}:{index}")
        ok = rng.choice([True, False])
        status = "SUCCESS-TEST" if ok else "FAIL-TEST"
        return {
            "job_type": "SQL_CONVERSION",
            "job": job,
            "ok": ok,
            "status": status,
            "message": f"space_nm={space_nm}, sql_id={sql_id} {status} 입니다.",
            "log": f"[POC][SQL_CONVERSION][{index}] space_nm={space_nm}, sql_id={sql_id}, status={status}, trace_id=TEST-{rng.randint(1000, 9999)}",
        }

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse a Langflow Data, dict, or JSON string payload.
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

    def _insert_log(
        self,
        map_id,
        mig_kind,
        log_type,
        log_level,
        step_name,
        status,
        message,
        retry_count,
        generated_sql="",
    ):
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO SFAADM.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    SFAADM.MIGRATION_LOG_SEQ.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8, CURRENT_TIMESTAMP
                )
                """,
                [
                    map_id,
                    str(mig_kind or "")[:100],
                    str(log_type or "")[:20],
                    str(log_level or "")[:20],
                    str(step_name or "")[:50],
                    str(status or "")[:20],
                    str(message or "")[:4000],
                    retry_count,
                ],
            )
            conn.commit()
        except Exception as exc:
            self.status = f"NEXT_MIG_LOG insert failed: {exc}"
        finally:
            if conn is not None:
                conn.close()
