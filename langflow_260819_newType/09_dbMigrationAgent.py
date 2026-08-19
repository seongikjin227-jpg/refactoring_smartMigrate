from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


DB_MIGRATION_AGENT_PROMPT = """
당신은 SmartMigration DB Migration Agent입니다.

이미 상위 Chat/Classifier/Router에서 DB Migration 실행 요청으로 분기된 입력만 받습니다.
다른 agent로 다시 라우팅하지 않습니다.
직접 DB credential, LLM credential, schema 값을 사용자에게 묻거나 노출하지 않습니다.

실행 규칙:
1. 실제 실행은 반드시 MIG Pipeline의 run_migration_job만 사용합니다.
2. command_json action은 항상 run_migration_job입니다.
3. map_id가 있으면 해당 map_id만 실행합니다.
4. map_id가 없으면 pending DB Migration 전체 실행 의도로 간주합니다.
5. 새 구조에서는 여러 DB Migration 작업 실행 전에 사용자 재승인을 요구하지 않습니다.
6. 작업 대상 조건은 NEXT_MIG_INFO.USE_YN='Y' AND STATUS IS NULL입니다.
7. 실행 결과의 성공/실패는 MIG Pipeline 결과만 근거로 판단합니다.
8. 생성된 MIG_SQL은 INSERT 계열이어야 합니다.
9. 생성된 VERIFY_SQL은 SELECT 또는 WITH query여야 합니다.
10. DDL이나 mapping 정보에 없는 컬럼을 임의로 만들지 않습니다.

MIG Pipeline command_json schema:
{"action":"run_migration_job","map_id":101}

map_id가 없을 때:
{"action":"run_migration_job"}
""".strip()


class NewType09DbMigrationAgent(Component):
    display_name = "09 DB Migration Agent"
    description = "Holds DB Migration Agent instructions and builds command_json for 10 MIG Pipeline."
    name = "NewType09DbMigrationAgent"
    icon = "Bot"

    inputs = [
        DataInput(
            name="payload_json",
            display_name="Payload JSON",
            required=True,
            info="Payload from 08 Job Type Conditional Router MIG Job output.",
        ),
        MessageTextInput(
            name="agent_prompt",
            display_name="DB Migration Agent Prompt",
            value=DB_MIGRATION_AGENT_PROMPT,
            required=False,
        ),
        MessageTextInput(
            name="command_override_json",
            display_name="Command Override JSON",
            required=False,
            info='Optional. Example: {"action":"run_migration_job","map_id":101,"max_attempts":3}',
        ),
        StrInput(
            name="run_all_when_no_map_id",
            display_name="Run All When No MAP_ID",
            value="Y",
            required=False,
            info="Y means no map_id becomes run all pending DB Migration jobs in 10 MIG Pipeline.",
        ),
    ]

    outputs = [
        Output(display_name="Payload", name="payload", method="build_payload"),
        Output(display_name="Command JSON", name="command_json", method="build_command_json"),
    ]

    def build_payload(self) -> Data:
        try:
            payload = self._build()
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "09_dbMigrationAgent", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def build_command_json(self) -> Data:
        try:
            payload = self._build()
            command = payload.get("command_json") or {}
            self.status = command
            return Data(data=command)
        except Exception as exc:
            result = {"ok": False, "action": "run_migration_job", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _build(self) -> dict[str, Any]:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        command = self._parse_optional_json(getattr(self, "command_override_json", ""))
        if not command:
            command = {"action": "run_migration_job"}
        command["action"] = "run_migration_job"

        selected = payload.get("selected_job") or {}
        user_request = str(payload.get("user_request") or "")
        run_all_requested = self._as_bool(command.get("run_all_pending")) or self._is_run_all_request(user_request)
        if run_all_requested:
            command.pop("map_id", None)
            command["run_all_pending"] = True
        elif command.get("map_id") is None and selected.get("map_id") is not None:
            command["map_id"] = selected.get("map_id")

        if command.get("map_id") is None:
            command["run_all_pending"] = self._as_bool(getattr(self, "run_all_when_no_map_id", "Y"))

        out = {
            **payload,
            "component": "09_dbMigrationAgent",
            "agent_prompt": str(getattr(self, "agent_prompt", "") or DB_MIGRATION_AGENT_PROMPT),
            "command_json": command,
            "next_node": "10_migPipeline",
        }
        out.setdefault("history", []).append(
            {
                "step": "db_migration_agent",
                "message": "command_json=" + json.dumps(command, ensure_ascii=False, default=str),
            }
        )
        return out

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_optional_json(raw)

    def _parse_optional_json(self, raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
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
            raise ValueError("JSON input must be an object")
        return parsed

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    def _is_run_all_request(self, text: str) -> bool:
        return bool(re.search(r"(전체|모든|전부|끝까지|all|every|전체\s*진행|전체\s*실행)", str(text or ""), flags=re.I))
