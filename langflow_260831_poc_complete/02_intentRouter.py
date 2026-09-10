from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


# =============================================================================
# 02 Intent Conditional Router
# =============================================================================
# 01 Request Classifier의 route 결정을 Langflow graph의 실제 output branch로
# 연결하는 얇은 조건 라우터다.
#
# 이 컴포넌트는 의도를 새로 판단하지 않는다. 앞 단계가 만든 payload를 유지한 채
# 선택된 branch만 열고, 추적용 metadata만 덧붙인다.
# =============================================================================
class NewType02IntentRouter(Component):

    display_name = "02 Intent Conditional Router"
    description = "Conditional router for classified intent. Inactive branches are stopped with self.stop()."
    name = "NewType02IntentRouter"
    icon = "Route"

    inputs = [MessageTextInput(name="payload_json", display_name="Classifier Message JSON", required=True)]

    outputs = [
        Output(display_name="General Chat", name="general_chat", method="general_chat_response", group_outputs=True),
        Output(display_name="Management", name="management", method="management_response", group_outputs=True),
        Output(display_name="Job Execution", name="job_execution", method="job_execution_response", group_outputs=True),
    ]

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def general_chat_response(self) -> Data:
        return self._route_output("GENERAL_CHAT", "general_chat")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def management_response(self) -> Data:
        return self._route_output("MANAGEMENT", "management")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def job_execution_response(self) -> Data:
        return self._route_output("JOB_EXECUTION", "job_execution")

    # 예상 route와 실제 route를 비교해 해당 output 실행 여부를 결정한다.
    def _route_output(self, expected_route: str, output_name: str) -> Data:
        try:
            if not getattr(self, "_router_started", False):
                logging.getLogger("smartmigrate.workflow").info(
                    "02 Intent Router started",
                    extra={"workflow_log": [0, "WORKFLOW", "02_INTENT_ROUTER", "INFO", "ROUTE", "START", 0]},
                )
                self._router_started = True

            # Langflow group output은 각 output method를 따로 호출하므로,
            # 모든 branch가 같은 payload를 파싱할 수 있게 여기서 표준 dict로 맞춘다.
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            route = str(payload.get("route") or (payload.get("classification") or {}).get("route") or "GENERAL_CHAT").upper()

            # route 값은 뒤쪽 graph 구성과 사람이 보는 status에서 같이 쓰인다.
            # route가 추가되면 output 정의와 next_node 매핑도 함께 늘려야 한다.
            next_node = {
                "GENERAL_CHAT": "03_llmResponse",
                "MANAGEMENT": "04_managementRouter",
                "JOB_EXECUTION": "06_getRemainingJobs",
            }.get(route, "03_llmResponse")

            if route != expected_route:
                self.stop(output_name)
                return Data(data={})

            routed = {
                **payload,
                "component": "02_intentRouter",
                "route": route,
                "selected_output": output_name,
                "next_node": next_node,
            }
            routed.setdefault("history", []).append({"step": "intent_router", "message": f"route={route}"})
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "02_intentRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Langflow 연결 방식에 따라 Data, Message, dict, JSON 문자열이 모두 들어올 수 있다.
        # 이후 단계는 dict만 받는다고 가정하므로 이 경계에서 입력 형태를 통일한다.
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, Message):
            text = str(raw.text or "").strip()
        elif hasattr(raw, "text"):
            text = str(raw.text or "").strip()
        elif hasattr(raw, "data") and isinstance(raw.data, dict):
            return dict(raw.data or {})
        else:
            text = str(raw or "").strip()

        # LLM이나 이전 노드가 ```json fenced block으로 넘겨도 JSON 본문만 뽑아낸다.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.S)
        text = match.group(0) if match else text

        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
