from __future__ import annotations

import json
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


MANAGEMENT_ROUTER_PROMPT = """당신은 SmartMigrate의 Management 라우터입니다. 반드시 JSON 객체만 반환하세요.

Management route:
- DASHBOARD: Dashboard, 상태/현황/건수/실패/잔여 작업/대기 작업 조회. DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 잔여 조회를 포함합니다.
- STATUS_CHANGE: status, priority, USE_YN, retry 상태, 포함/제외, 실행 대상 순서 변경.
- CORRECT_SQL_INPUT: 사용자가 수정 SQL을 입력하거나 Correct SQL 저장을 요청하는 경우.
- EXCEPTION: Management로 들어왔지만 위 세 가지 중 하나로 판단하기 어려운 경우.

주의:
- 실제 작업 실행 요청은 이 컴포넌트가 처리하지 않습니다. 그런 요청은 01 Request Classifier가 JOB_EXECUTION으로 보내야 합니다.
- "DB Migration 작업 남은거 있어?", "SQL Conversion 잔여 작업 조회", "SQL Tuning 잔여 보여줘", "Formatting 대기 작업 몇 건이야" 같은 읽기 요청은 DASHBOARD입니다.
- "남은거 있어?", "남은 작업 있어?", "잔여 작업 조회", "대기 작업 몇 건이야"는 DASHBOARD입니다.
- 애매하면 억지로 Dashboard나 Status Change로 보내지 말고 EXCEPTION을 반환하세요.

반환 JSON schema:
{
  "management_route": "DASHBOARD|STATUS_CHANGE|CORRECT_SQL_INPUT|EXCEPTION",
  "target": {},
  "correct_sql": "",
  "reason": "짧은 한국어 사유"
}
"""

EXCEPTION_MESSAGE = """Management 요청을 분류할 수 없습니다.
Dashboard 조회, Status/priority/USE_YN 변경, Correct SQL 입력 중 어떤 작업인지 다시 요청해주세요.
실행 요청이라면 "map_id=101 실행"처럼 실행할 식별자와 실행 의도를 함께 입력해주세요."""


class NewType04ManagementRouter(Component):
    display_name = "04 Management LLM Router"
    description = "Routes management requests to Dashboard, Status Change, Correct SQL Input, or Exception."
    name = "NewType04ManagementRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="", required=True),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=True),
        StrInput(name="llm_model", display_name="LLM Model", value="", required=True),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=1200, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=90, required=False),
    ]

    outputs = [
        Output(display_name="Dashboard", name="dashboard", method="dashboard_response", group_outputs=True),
        Output(display_name="Status Change", name="status_change", method="status_change_response", group_outputs=True),
        Output(display_name="Correct SQL Input", name="correct_sql_input", method="correct_sql_input_response", group_outputs=True),
        Output(display_name="Exception Message", name="exception", method="exception_response", group_outputs=True, types=["Message"]),
    ]

    def dashboard_response(self) -> Data:
        # Return the dashboard branch when management routing matches.
        return self._route_output("DASHBOARD", "dashboard")

    def status_change_response(self) -> Data:
        # Return the status-change branch when management routing matches.
        return self._route_output("STATUS_CHANGE", "status_change")

    def correct_sql_input_response(self) -> Data:
        # Return the Correct SQL branch when management routing matches.
        return self._route_output("CORRECT_SQL_INPUT", "correct_sql_input")

    def exception_response(self) -> Message:
        # Return a user-facing exception message for ambiguous management requests.
        routed = self._get_routed_payload()
        if routed.get("management_route") != "EXCEPTION":
            self.stop("exception")
            return Message(text="")
        self.status = {**routed, "selected_output": "exception", "answer_text": EXCEPTION_MESSAGE, "final": True}
        return Message(text=EXCEPTION_MESSAGE)

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        # Build a routed payload for the active output branch.
        try:
            routed = self._get_routed_payload()
            if routed.get("management_route") != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "04_managementRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _get_routed_payload(self) -> dict[str, Any]:
        # Compute and cache the routed payload for this component.
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision = self._normalize_decision(self._route_with_llm(payload))
        routed = {
            **payload,
            "component": "04_managementRouter",
            "management_route": decision["management_route"],
            "target": decision.get("target") or {},
            "correct_sql": decision.get("correct_sql") or "",
            "management_routing_reason": decision.get("reason") or "",
        }
        routed.setdefault("history", []).append(
            {"step": "management_route", "message": f"management_route={routed['management_route']}"}
        )
        self._cached_routed_payload = routed
        return routed

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Call the configured LLM to decide the route.
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not api_key:
            raise ValueError("llm_api_key is required for 04 Management Router")
        if not model:
            raise ValueError("llm_model is required for 04 Management Router")
        if not base_url:
            raise ValueError("llm_base_url is required for 04 Management Router")

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": MANAGEMENT_ROUTER_PROMPT},
                {"role": "user", "content": json.dumps({"user_request": payload.get("user_request") or ""}, ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 1200),
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
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"04 Management Router LLM HTTP {exc.code}: {detail[:1000]}") from exc
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        # Validate and normalize the management router decision.
        route = str(decision.get("management_route") or "").upper()
        if route not in {"DASHBOARD", "STATUS_CHANGE", "CORRECT_SQL_INPUT", "EXCEPTION"}:
            raise ValueError(f"Invalid management_route: {route}")
        target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
        return {
            "management_route": route,
            "target": target,
            "correct_sql": str(decision.get("correct_sql") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    def _next_node(self, route: str) -> str:
        # Resolve the next component name for a route.
        return {
            "DASHBOARD": "04_dashboard",
            "STATUS_CHANGE": "04_statusChange",
            "CORRECT_SQL_INPUT": "04_correctSqlInput",
            "EXCEPTION": "04_managementRouter",
        }.get(route, "04_dashboard")

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse a Langflow Data, dict, or JSON string payload.
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        # Parse a JSON object from raw LLM or text output.
        clean = str(text or "").strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
            clean = re.sub(r"\s*```$", "", clean)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        clean = match.group(0) if match else clean
        parsed = json.loads(clean)
        if not isinstance(parsed, dict):
            raise ValueError("LLM must return a JSON object")
        return parsed

    def _secret_to_str(self, value: Any) -> str:
        # Convert a Langflow secret value into a plain string.
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
