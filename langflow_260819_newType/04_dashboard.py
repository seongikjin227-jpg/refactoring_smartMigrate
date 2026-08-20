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


class NewType04Dashboard(Component):
    display_name = "04 Dashboard"
    description = "Dashboard/query branch that formats dashboard payload as a chat message."
    name = "NewType04Dashboard"
    icon = "Gauge"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        answer = self._build_answer(payload)
        result = {**payload, "component": "04_dashboard", "answer_text": answer, "final": True}
        self.status = result
        return Message(text=answer)

    def _build_answer(self, payload: dict[str, Any]) -> str:
        user_request = str(payload.get("user_request") or payload.get("original_request") or "").strip()
        route = str(payload.get("management_route") or payload.get("route") or "DASHBOARD").strip()
        dashboard_data = (
            payload.get("dashboard_data")
            or payload.get("dashboard_result")
            or payload.get("query_result")
            or payload.get("rows")
            or payload.get("summary")
        )

        lines = [
            "component=04_dashboard",
            "대시보드 조회 플로우로 분기되었습니다.",
        ]
        if user_request:
            lines.append(f"사용자 요청: {user_request}")
        lines.append(f"관리 라우트: {route}")

        if dashboard_data:
            lines.append("대시보드 조회 결과:")
            lines.append(json.dumps(dashboard_data, ensure_ascii=False, indent=2, default=str))
            lines.append("위 대시보드 조회 결과만 근거로 사용자에게 답변하세요.")
        else:
            lines.append("POC payload에 아직 dashboard_data/query_result가 없어 실제 조회 결과는 포함되지 않았습니다.")
            lines.append("실제 플로우에서는 Dashboard 조회 컴포넌트의 결과를 dashboard_data 또는 query_result로 전달해야 합니다.")
        return "\n".join(lines)

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
