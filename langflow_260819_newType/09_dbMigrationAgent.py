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


DB_MIGRATION_AGENT_PROMPT = """
당신은 SmartMigration DB Migration Agent입니다.

상위 Chat/Classifier/Router에서 이미 DB Migration 장시간 실행 요청으로 분기된 입력만 받습니다.
다른 Agent로 다시 라우팅하지 않습니다.

실행 규칙:
1. Long Job의 DB Migration은 단건 실행을 지원하지 않습니다.
2. 항상 전체 pending DB Migration 작업을 실행하도록 command_json을 생성합니다.
3. 단건 실행이 필요하면 Long Job이 아니라 Fast Status 플로우에서 해당 작업의 status/priority를 DB에서 조정한 뒤 전체 실행 흐름에 태웁니다.
4. 작업 대상 조건은 NEXT_MIG_INFO.USE_YN='Y' AND STATUS IS NULL 입니다.
5. 여러 DB Migration 작업 실행 전에 사용자 재승인을 요구하지 않습니다.
6. 실행 결과는 MIG Pipeline 결과만 근거로 판단합니다.

MIG Pipeline command_json schema:
{"action":"run_migration_job","run_all_pending":true}
""".strip()


class NewType09DbMigrationAgent(Component):
    display_name = "09 DB Migration Agent"
    description = "Builds the all-pending DB Migration payload for 10 MIG Pipeline."
    name = "NewType09DbMigrationAgent"
    icon = "Bot"

    inputs = [
        DataInput(
            name="payload_json",
            display_name="Payload JSON",
            required=True,
            info="Payload from 08 Long Job LLM Router MIG Job output.",
        ),
        MessageTextInput(
            name="agent_prompt",
            display_name="DB Migration Agent Prompt",
            value=DB_MIGRATION_AGENT_PROMPT,
            required=False,
        ),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="build_payload")]

    def build_payload(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            command = {"action": "run_migration_job", "run_all_pending": True}
            out = {
                **payload,
                "component": "09_dbMigrationAgent",
                "agent_prompt": str(getattr(self, "agent_prompt", "") or DB_MIGRATION_AGENT_PROMPT),
                "command_json": command,
                "run_mode": "all_pending",
                "selected_job": {},
                "next_node": "10_migPipeline",
            }
            out.setdefault("history", []).append(
                {
                    "step": "db_migration_agent",
                    "message": "command_json=" + json.dumps(command, ensure_ascii=False, default=str),
                }
            )
            self.status = out
            return Data(data=out)
        except Exception as exc:
            result = {"ok": False, "component": "09_dbMigrationAgent", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
