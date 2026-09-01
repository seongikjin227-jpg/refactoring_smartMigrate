from __future__ import annotations

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


class NewType04StatusChange(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "04 Status Change"
    description = "POC status/priority/targeting update branch."
    name = "NewType04StatusChange"
    icon = "SlidersHorizontal"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        # Execute the component and return a Langflow message.
        self._insert_log(0, "WORKFLOW", "04_STATUS_CHANGE", "INFO", "RUN", "START", "before run", 0, "")
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            target = payload.get("target") or {}
            answer = (
                "Status 변경 플로우로 분기되었습니다. "
                f"target={json.dumps(target, ensure_ascii=False, default=str)}. "
                "POC에서는 실제 DB update 없이 라우팅 결과만 반환합니다."
            )
            result = {**payload, "component": "04_statusChange", "answer_text": answer, "final": True}
            self.status = result
            __log_result = Message(text=answer)
            self._insert_log(0, "WORKFLOW", "04_STATUS_CHANGE", "INFO", "RUN", "END", "after run", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "04_STATUS_CHANGE", "ERROR", "RUN", "ERROR", f"error run: {exc}", 0, "")
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
