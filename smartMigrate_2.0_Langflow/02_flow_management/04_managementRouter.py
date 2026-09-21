from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


MANAGEMENT_ROUTER_PROMPT = """당신은 SmartMigrate 04 관리 요청 라우터입니다.
반드시 Markdown 없이 JSON 객체 1개만 반환하세요.

선택 가능한 route:
- DASHBOARD: 전체/도메인 dashboard, 집계 현황, 성공/실패/자동 실행 대상 건수 요약 요청
- CURRENT_PROGRESS: 현재 실행 중인 작업, running 상태, 지금 돌고 있는지 확인하는 단순 요청
- MANAGEMENT_AGENT: 조회/분석/잔여 작업 목록/상태 변경/SQL 저장 또는 비우기/RAG Guide 조회·추가·수정·비활성화 요청
- EXCEPTION: 필수 정보가 없거나 모호해서 route를 고를 수 없는 경우

라우팅 규칙:
- "대시보드", "전체 현황", "집계", "건수 요약"은 DASHBOARD입니다.
- "지금 돌고 있어?", "현재 실행 중?", "running 있어?"처럼 현재 실행 여부만 묻는 요청은 CURRENT_PROGRESS입니다.
- "남은 작업", "잔여 작업", "작업 리스트", "대상 목록"은 MANAGEMENT_AGENT입니다.
- 특정 map_id/sql_id/space_nm의 상태, 결과, 로그, 실패 원인 조회는 MANAGEMENT_AGENT입니다.
- DB row 변경 요청은 MANAGEMENT_AGENT입니다.
- USER_EDITED, USE_YN, priority, 상태 초기화, SQL 저장, SQL 비우기 요청은 MANAGEMENT_AGENT입니다.
- RAG Guide 조회/추가/수정/비활성화 요청은 MANAGEMENT_AGENT입니다.
- "비슷한 AS-IS SQL", "유사 SQL", "유사한 실패 SQL", "Fail-* 재시도 후보"처럼 AS-IS SQL 벡터 검색 또는 그 결과의 재시도 요청은 MANAGEMENT_AGENT입니다.
- 유사 SQL 검색 결과로 FAIL-* 상태를 NULL로 바꾸는 요청도 MANAGEMENT_AGENT입니다. 검색만 요청한 경우에는 상태를 변경하지 않습니다.
- VectorDB, Milvus, 벡터DB, vector upload, vector sync 요청도 MANAGEMENT_AGENT입니다. Agent가 연결된 Sync Milvus Vector DB Tool을 직접 호출합니다.
- RAG Guide를 추가/수정/비활성화한 직후라도 VectorDB 동기화를 자동으로 이어서 실행하지 않습니다. 사용자가 별도로 요청한 경우에만 Agent가 sync_all Tool command를 호출합니다.

필수 정보 누락 규칙:
- route 자체를 판단할 수 없으면 EXCEPTION으로 보내고 exception_message에 필요한 정보를 한국어로 적으세요.
- MANAGEMENT_AGENT가 세부 파라미터 누락을 직접 물어볼 수 있으므로, route가 명확하면 EXCEPTION으로 보내지 마세요.

JSON schema:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|MANAGEMENT_AGENT|EXCEPTION","exception_message":"","reason":""}"""


EXCEPTION_MESSAGE = "Management 요청을 처리할 수 없습니다. 어떤 관리 작업인지 다시 알려주세요."


class NewType04ManagementRouter(Component):
    display_name = "04 Management LLM Router"
    description = "Routes management requests to dashboard, current progress, management agent, or exception."
    name = "NewType04ManagementRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="", required=True),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=True),
        StrInput(name="llm_model", display_name="LLM Model", value="", required=True),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=800, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=90, required=False),
    ]
    outputs = [
        Output(display_name="Dashboard", name="dashboard", method="dashboard_response", group_outputs=True),
        Output(display_name="Current Progress", name="current_progress", method="current_progress_response", group_outputs=True),
        Output(display_name="Management Agent", name="management_agent", method="management_agent_response", group_outputs=True),
        Output(display_name="Exception Message", name="exception", method="exception_response", group_outputs=True, types=["Message"]),
    ]

    # group output은 하나만 실제 payload를 내보내고, 나머지 output은 stop 처리한다.
    # 이 규칙으로 Langflow graph에서 의도하지 않은 관리 branch의 동시 실행을 막는다.
    def dashboard_response(self) -> Data:
        return self._route_output("DASHBOARD", "dashboard")

    def current_progress_response(self) -> Data:
        return self._route_output("CURRENT_PROGRESS", "current_progress")

    def management_agent_response(self) -> Data:
        return self._route_output("MANAGEMENT_AGENT", "management_agent")

    # LLM이 route를 정할 수 없을 때만 사용자에게 보낼 최종 Message branch를 연다.
    def exception_response(self) -> Message:
        routed = self._get_routed_payload()
        if routed.get("management_route") != "EXCEPTION":
            self.stop("exception")
            return Message(text="")
        answer = str(routed.get("exception_message") or EXCEPTION_MESSAGE)
        self.status = {**routed, "selected_output": "exception", "answer_text": answer, "final": True}
        return Message(text=answer)

    # 선택된 route와 일치하는 Data output만 활성화하고, 다음 컴포넌트 이름을 payload에 명시한다.
    def _route_output(self, expected_route: str, output_name: str) -> Data:
        routed = self._get_routed_payload()
        if routed.get("management_route") != expected_route:
            self.stop(output_name)
            return Data(data={})
        routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
        self.status = routed
        return Data(data=routed)

    # 여러 group output이 같은 실행에서 호출되어도 LLM을 한 번만 호출하도록 route 결과를 캐시한다.
    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached
        logging.getLogger("smartmigrate.workflow").info(
            "04 Management Router started",
            extra={"workflow_log": [0, "WORKFLOW", "04_MGMT_ROUTER", "INFO", "ROUTE", "START", 0]},
        )
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision = self._normalize_decision(self._route_with_llm(payload))
        routed = {
            **payload,
            "component": "04_managementRouter",
            "effective_user_request": self._effective_user_request(payload),
            "management_route": decision["management_route"],
            "exception_message": decision.get("exception_message", ""),
            "management_routing_reason": decision.get("reason", ""),
        }
        routed.setdefault("history", []).append({"step": "management_route", "message": f"management_route={routed['management_route']}"})
        self._cached_routed_payload = routed
        return routed

    # 자연어 요청은 여기서만 LLM에 전달한다. 이후 분기에서는 검증된 route 값만 사용한다.
    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not all((api_key, model, base_url)):
            raise ValueError("llm_base_url, llm_api_key, and llm_model are required for 04 Management Router")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": MANAGEMENT_ROUTER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_request": self._effective_user_request(payload),
                            "original_user_request": payload.get("user_request") or "",
                            "is_follow_up": bool(payload.get("is_follow_up", False)),
                            "confirmation": payload.get("confirmation") or "NOT_REQUIRED",
                            "target_filter": payload.get("target_filter") or {},
                            "clarification_required": bool(payload.get("clarification_required", False)),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 800),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 90)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            raise ValueError(f"04 Management Router LLM HTTP {exc.code}: {exc.read().decode('utf-8', errors='ignore')[:1000]}") from exc
        return self._parse_json_object((((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip())

    def _effective_user_request(self, payload: dict[str, Any]) -> str:
        """Use the canonical request produced by the history-aware 01 classifier."""
        return str(
            payload.get("resolved_user_request")
            or payload.get("user_request")
            or payload.get("original_request")
            or payload.get("input")
            or ""
        ).strip()

    # LLM 응답을 허용된 route 집합으로 제한해, 임의의 component name으로 이어지는 것을 차단한다.
    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("management_route") or "").upper()
        allowed = {"DASHBOARD", "CURRENT_PROGRESS", "MANAGEMENT_AGENT", "EXCEPTION"}
        if route not in allowed:
            raise ValueError(f"Invalid management_route: {route}")
        return {
            "management_route": route,
            "exception_message": str(decision.get("exception_message") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    # route는 논리 이름이고 next_node는 실제 Langflow 컴포넌트 이름이다.
    def _next_node(self, route: str) -> str:
        return {
            "DASHBOARD": "04_dashboard",
            "CURRENT_PROGRESS": "04_currentProgress",
            "MANAGEMENT_AGENT": "04_managementAgent",
            "EXCEPTION": "04_managementRouter",
        }.get(route, "04_managementAgent")

    # Data/dict/JSON text 형태의 상위 payload를 동일한 dict 계약으로 정규화한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        parsed = json.loads(match.group(0) if match else clean)
        if not isinstance(parsed, dict):
            raise ValueError("LLM must return a JSON object")
        return parsed

    def _secret_to_str(self, value: Any) -> str:
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")
