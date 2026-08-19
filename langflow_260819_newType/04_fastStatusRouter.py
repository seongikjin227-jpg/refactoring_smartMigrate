from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


FAST_STATUS_ROUTER_PROMPT = """You are a SmartMigrate fast-status router. Return JSON only.

FAST_STATUS has exactly three routes:
- DASHBOARD: read-only dashboard/status/failure/pending summary requests.
- STATUS_CHANGE: change DB control values such as status, priority, USE_YN, retry state, or make one job the next execution target.
- CORRECT_SQL_INPUT: user provides corrected SQL or asks to save user-edited SQL. This should set USER_EDITED='Y' and store the provided SQL in the proper SQL column.

Return JSON only:
{
  "fast_route": "DASHBOARD|STATUS_CHANGE|CORRECT_SQL_INPUT",
  "db_action": "READ|UPDATE_STATUS|UPDATE_CORRECT_SQL",
  "target": {},
  "correct_sql": "",
  "reason": "short reason"
}

Rules:
- If the user asks for status, count, failures, pending jobs, dashboard, or progress, use DASHBOARD.
- If the user asks to run one job, run a specific map_id/sql_id, change priority, change status, include/exclude a job, or make a job the next target, use STATUS_CHANGE. Do not execute the job directly.
- If the user provides SQL text or says corrected SQL/user edited SQL/save this SQL, use CORRECT_SQL_INPUT.
"""


class NewType04FastStatusRouter(Component):
    display_name = "04 Fast Status LLM Router"
    description = "Routes fast status requests to Dashboard, Status Change, or Correct SQL Input."
    name = "NewType04FastStatusRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(name="routing_prompt", display_name="Routing Prompt", value=FAST_STATUS_ROUTER_PROMPT, required=False),
        StrInput(name="router_mode", display_name="Router Mode", value="LLM", required=False),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="https://api.openai.com/v1", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="gpt-4.1-mini", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=400, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=30, required=False),
    ]

    outputs = [
        Output(display_name="Dashboard", name="dashboard", method="dashboard_response", group_outputs=True),
        Output(display_name="Status Change", name="status_change", method="status_change_response", group_outputs=True),
        Output(display_name="Correct SQL Input", name="correct_sql_input", method="correct_sql_input_response", group_outputs=True),
    ]

    def dashboard_response(self) -> Data:
        return self._route_output("DASHBOARD", "dashboard")

    def status_change_response(self) -> Data:
        return self._route_output("STATUS_CHANGE", "status_change")

    def correct_sql_input_response(self) -> Data:
        return self._route_output("CORRECT_SQL_INPUT", "correct_sql_input")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        try:
            routed = self._get_routed_payload()
            if routed.get("fast_route") != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "04_fastStatusRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision = self._route_with_llm(payload) if self._use_llm() else self._route_with_rules(payload)
        decision = self._normalize_decision(decision, payload)
        routed = {
            **payload,
            "component": "04_fastStatusRouter",
            "fast_route": decision["fast_route"],
            "db_action": decision["db_action"],
            "target": decision.get("target") or {},
            "correct_sql": decision.get("correct_sql") or "",
            "fast_routing_reason": decision.get("reason") or "",
        }
        routed.setdefault("history", []).append(
            {"step": "fast_status_route", "message": f"fast_route={routed['fast_route']}, db_action={routed['db_action']}"}
        )
        self._cached_routed_payload = routed
        return routed

    def _use_llm(self) -> bool:
        return str(getattr(self, "router_mode", "LLM") or "LLM").strip().upper() == "LLM"

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not api_key or not model or not base_url:
            return self._route_with_rules(payload)
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": str(getattr(self, "routing_prompt", "") or FAST_STATUS_ROUTER_PROMPT)},
                {"role": "user", "content": json.dumps({"user_request": payload.get("user_request") or ""}, ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 400),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 30)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return self._route_with_rules(payload)
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    def _route_with_rules(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("user_request") or "")
        lowered = text.lower()
        target = self._extract_target(text)
        if self._looks_like_correct_sql(text):
            return {
                "fast_route": "CORRECT_SQL_INPUT",
                "db_action": "UPDATE_CORRECT_SQL",
                "target": target,
                "correct_sql": self._extract_sql(text),
                "reason": "correct SQL input",
            }
        if re.search(r"(단건|단일|한\s*건|하나만|특정|map_id|mapid|sql_id|sqlid|row_id|rowid|priority|우선순위|status|상태|use_yn|제외|포함|대상|next|single|one\s+job)", lowered, flags=re.I):
            return {"fast_route": "STATUS_CHANGE", "db_action": "UPDATE_STATUS", "target": target, "correct_sql": "", "reason": "status or targeting change"}
        return {"fast_route": "DASHBOARD", "db_action": "READ", "target": target, "correct_sql": "", "reason": "dashboard/status query"}

    def _normalize_decision(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("fast_route") or "").upper()
        if route not in {"DASHBOARD", "STATUS_CHANGE", "CORRECT_SQL_INPUT"}:
            route = self._route_with_rules(payload)["fast_route"]
        db_action = str(decision.get("db_action") or "").upper()
        defaults = {
            "DASHBOARD": "READ",
            "STATUS_CHANGE": "UPDATE_STATUS",
            "CORRECT_SQL_INPUT": "UPDATE_CORRECT_SQL",
        }
        return {
            "fast_route": route,
            "db_action": db_action if db_action else defaults[route],
            "target": decision.get("target") if isinstance(decision.get("target"), dict) else {},
            "correct_sql": str(decision.get("correct_sql") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    def _next_node(self, route: str) -> str:
        return {
            "DASHBOARD": "04_dashboard",
            "STATUS_CHANGE": "04_statusChange",
            "CORRECT_SQL_INPUT": "04_correctSqlInput",
        }.get(route, "04_dashboard")

    def _looks_like_correct_sql(self, text: str) -> bool:
        return bool(
            re.search(r"(correct\s*sql|user\s*edited|수정\s*sql|정정\s*sql|sql\s*입력|이\s*sql|저장)", text, flags=re.I)
            or re.search(r"\b(select|insert|update|delete|merge|with)\b", text, flags=re.I)
        )

    def _extract_sql(self, text: str) -> str:
        fenced = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, flags=re.I | re.S)
        if fenced:
            return fenced.group(1).strip()
        sql = re.search(r"\b(select|insert|update|delete|merge|with)\b.*", text, flags=re.I | re.S)
        return sql.group(0).strip() if sql else ""

    def _extract_target(self, text: str) -> dict[str, Any]:
        target: dict[str, Any] = {}
        patterns = {
            "map_id": r"map[_\s-]*id\s*[=:]?\s*(\d+)",
            "sql_id": r"sql[_\s-]*id\s*[=:]?\s*([A-Za-z0-9_.:-]+)",
            "row_id": r"row[_\s-]*id\s*[=:]?\s*([A-Za-z0-9_.:-]+)",
            "priority": r"priority\s*[=:]?\s*(\d+)|우선순위\s*(\d+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text, flags=re.I)
            if not match:
                continue
            value = next((group for group in match.groups() if group), None)
            if value is not None:
                target[key] = int(value) if key in {"map_id", "priority"} and str(value).isdigit() else value
        return target

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
            clean = re.sub(r"\s*```$", "", clean)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        clean = match.group(0) if match else clean
        parsed = json.loads(clean) if clean else {}
        if not isinstance(parsed, dict):
            raise ValueError("Expected a JSON object")
        return parsed

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
