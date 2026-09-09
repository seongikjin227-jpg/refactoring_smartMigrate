from __future__ import annotations

import logging
import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


# =============================================================================
# 02 Intent Conditional Router
# =============================================================================
# 01 Request Classifier가 만든 JSON payload를 받아 Langflow graph의 세 갈래 중
# 정확히 하나만 통과시키는 조건부 라우터다.
#
# route 계약:
# - GENERAL_CHAT: 일반 대화 응답 프롬프트(03)로 이동
# - MANAGEMENT: 대시보드/진행 조회/상태 초기화/교정 입력/Job QA(04)로 이동
# - JOB_EXECUTION: 잔여 작업 조회 후 실제 batch 실행 라우터(06 -> 08)로 이동
#
# 중요한 운영 규칙:
# - 선택되지 않은 output은 반드시 self.stop(output_name)으로 중지한다.
# - 이 컴포넌트는 route만 결정하며, SQL 생성/DB update/실패 분석을 하지 않는다.
# - payload 원문은 보존하고 component, selected_output, next_node 같은 추적용
#   metadata만 덧붙인다.
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

    def general_chat_response(self) -> Data:
        return self._route_output("GENERAL_CHAT", "general_chat")

    def management_response(self) -> Data:
        return self._route_output("MANAGEMENT", "management")

    def job_execution_response(self) -> Data:
        return self._route_output("JOB_EXECUTION", "job_execution")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        # 활성 output branch에 전달할 routed payload를 만든다.
        #
        # Langflow group output은 각 output method가 개별적으로 호출될 수 있다.
        # 그래서 expected_route와 실제 route가 다르면 해당 output을 stop 처리해야
        # 뒤쪽 컴포넌트가 잘못 실행되지 않는다.
        try:
            if not getattr(self, "_router_started", False):
                logging.getLogger("smartmigrate.workflow").info("02 Intent Router started", extra={"workflow_log": [0, "WORKFLOW", "02_INTENT_ROUTER", "INFO", "ROUTE", "START", 0]})
                self._router_started = True
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            route = str(payload.get("route") or (payload.get("classification") or {}).get("route") or "GENERAL_CHAT").upper()
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

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # 01 classifier 결과는 Langflow 연결 방식에 따라 Data, Message, dict,
        # JSON 문자열 중 하나로 들어올 수 있다. 라우터 뒤쪽 컴포넌트가 동일한
        # 구조를 기대하므로 여기서 dict로 통일한다.
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
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.S)
        text = match.group(0) if match else text
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
