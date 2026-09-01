from __future__ import annotations

import logging
import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04CorrectSqlInput(Component):

    display_name = "04 Correct SQL Input"
    description = "POC corrected SQL input branch. Production should set USER_EDITED='Y' and store SQL."
    name = "NewType04CorrectSqlInput"
    icon = "FilePenLine"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        # Execute the component and return a Langflow message.
        logging.getLogger("smartmigrate.workflow").info(
            "before run",
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "04_CORRECT_SQL",
                    "log_level": "INFO",
                    "step_name": "RUN",
                    "status": "START",
                    "message": "before run",
                    "retry_count": 0,
                }
            },
        )
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            correct_sql = payload.get("correct_sql") or ""
            answer = (
                "Correct SQL 입력 플로우로 분기되었습니다. "
                "운영 구현에서는 USER_EDITED='Y'로 변경하고 입력 SQL을 대상 컬럼에 저장해야 합니다. "
                f"sql_length={len(correct_sql)}"
            )
            result = {**payload, "component": "04_correctSqlInput", "answer_text": answer, "final": True}
            self.status = result
            __log_result = Message(text=answer)
            logging.getLogger("smartmigrate.workflow").info(
                "after run",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "04_CORRECT_SQL",
                        "log_level": "INFO",
                        "step_name": "RUN",
                        "status": "END",
                        "message": "after run",
                        "retry_count": 0,
                    }
                },
            )
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"error run: {exc}",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "04_CORRECT_SQL",
                        "log_level": "ERROR",
                        "step_name": "RUN",
                        "status": "ERROR",
                        "message": f"error run: {exc}",
                        "retry_count": 0,
                    }
                },
            )
            raise

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
