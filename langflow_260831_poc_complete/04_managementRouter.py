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


MANAGEMENT_ROUTER_PROMPT = """You are the SmartMigrate management router. Return exactly one JSON object, no Markdown.

Routes: DASHBOARD (read-only summary), CURRENT_PROGRESS (active/running work), STATUS_CHANGE (reset), CORRECT_SQL_INPUT (save user-supplied SQL), EXCEPTION (missing or ambiguous data).

STATUS_CHANGE reset means the status becomes NULL and RETRY_COUNT becomes 0. It never deletes SQL.
For STATUS_CHANGE and CORRECT_SQL_INPUT extract target.work_type: DB_MIGRATION, SQL_CONVERSION, SQL_TUNING, or SQL_FORMATTING.
- DB_MIGRATION requires target.map_id.
- SQL_* requires BOTH target.sql_id and target.space_nm.
- Correct SQL requires target.sql_column. DB_MIGRATION permits MIG_SQL or VERIFY_SQL. SQL_* permits TO_SQL, BIND_SQL, TEST_SQL, TUNED_TO_SQL, or FORMATTED_SQL.
- Put the exact user-provided SQL in correct_sql. Never invent SQL, identifiers, or a column.
- If any required value is absent, set management_route=EXCEPTION and write a specific Korean exception_message. Examples: "DB Migration Correct SQL 입력을 위해 MAP_ID를 알려주셔야 합니다.", "Status Change(Reset)를 위해 SQL_ID와 SPACE_NM을 모두 알려주셔야 합니다."

JSON schema:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|STATUS_CHANGE|CORRECT_SQL_INPUT|EXCEPTION","target":{"work_type":"","map_id":"","sql_id":"","space_nm":"","sql_column":""},"correct_sql":"","exception_message":"","reason":""}"""

EXCEPTION_MESSAGE = "Management 요청을 처리할 수 없습니다. 작업 종류와 필요한 식별자를 다시 알려주세요."


class NewType04ManagementRouter(Component):
    display_name = "04 Management LLM Router"
    description = "Routes management requests and extracts validated DB update parameters."
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
        Output(display_name="Current Progress", name="current_progress", method="current_progress_response", group_outputs=True),
        Output(display_name="Status Change", name="status_change", method="status_change_response", group_outputs=True),
        Output(display_name="Correct SQL Input", name="correct_sql_input", method="correct_sql_input_response", group_outputs=True),
        Output(display_name="Exception Message", name="exception", method="exception_response", group_outputs=True, types=["Message"]),
    ]

    def dashboard_response(self) -> Data:
        return self._route_output("DASHBOARD", "dashboard")

    def current_progress_response(self) -> Data:
        return self._route_output("CURRENT_PROGRESS", "current_progress")

    def status_change_response(self) -> Data:
        return self._route_output("STATUS_CHANGE", "status_change")

    def correct_sql_input_response(self) -> Data:
        return self._route_output("CORRECT_SQL_INPUT", "correct_sql_input")

    def exception_response(self) -> Message:
        routed = self._get_routed_payload()
        if routed.get("management_route") != "EXCEPTION":
            self.stop("exception")
            return Message(text="")
        answer = str(routed.get("exception_message") or EXCEPTION_MESSAGE)
        self.status = {**routed, "selected_output": "exception", "answer_text": answer, "final": True}
        return Message(text=answer)

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        routed = self._get_routed_payload()
        if routed.get("management_route") != expected_route:
            self.stop(output_name)
            return Data(data={})
        routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
        self.status = routed
        return Data(data=routed)

    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached
        logging.getLogger("smartmigrate.workflow").info("04 Management Router started", extra={"workflow_log": [0, "WORKFLOW", "04_MGMT_ROUTER", "INFO", "ROUTE", "START", 0]})
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            decision = self._validate_management_request(self._normalize_decision(self._route_with_llm(payload)))
        except Exception:
            raise
        routed = {**payload, "component": "04_managementRouter", "management_route": decision["management_route"], "target": decision["target"], "correct_sql": decision["correct_sql"], "exception_message": decision["exception_message"], "management_routing_reason": decision["reason"]}
        routed.setdefault("history", []).append({"step": "management_route", "message": f"management_route={routed['management_route']}"})
        self._cached_routed_payload = routed
        return routed

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not all((api_key, model, base_url)):
            raise ValueError("llm_base_url, llm_api_key, and llm_model are required for 04 Management Router")
        body = {"model": model, "messages": [{"role": "system", "content": MANAGEMENT_ROUTER_PROMPT}, {"role": "user", "content": json.dumps({"user_request": payload.get("user_request") or ""}, ensure_ascii=False)}], "temperature": 0, "max_tokens": int(getattr(self, "llm_max_tokens", None) or 1200)}
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 90)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            raise ValueError(f"04 Management Router LLM HTTP {exc.code}: {exc.read().decode('utf-8', errors='ignore')[:1000]}") from exc
        return self._parse_json_object((((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip())

    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("management_route") or "").upper()
        if route not in {"DASHBOARD", "CURRENT_PROGRESS", "STATUS_CHANGE", "CORRECT_SQL_INPUT", "EXCEPTION"}:
            raise ValueError(f"Invalid management_route: {route}")
        return {"management_route": route, "target": dict(decision.get("target") or {}), "correct_sql": str(decision.get("correct_sql") or ""), "exception_message": str(decision.get("exception_message") or ""), "reason": str(decision.get("reason") or "")}

    def _validate_management_request(self, decision: dict[str, Any]) -> dict[str, Any]:
        route, target = decision["management_route"], dict(decision["target"])
        if route not in {"STATUS_CHANGE", "CORRECT_SQL_INPUT"}:
            return decision
        work_type = str(target.get("work_type") or "").strip().upper()
        if work_type not in {"DB_MIGRATION", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            return self._exception(decision, "Status Change 또는 Correct SQL 입력을 위해 작업 종류(DB Migration, SQL Conversion, SQL Tuning, SQL Formatting)를 알려주셔야 합니다.")
        target["work_type"] = work_type
        if route == "STATUS_CHANGE" and work_type == "SQL_FORMATTING":
            return self._exception(decision, "SQL Formatting은 SQL을 삭제하지 않는 Reset 대상 상태 컬럼이 없으므로 Status Change(Reset)를 지원하지 않습니다.")
        if work_type == "DB_MIGRATION" and not str(target.get("map_id") or "").strip():
            return self._exception(decision, f"{self._operation_label(route, work_type)}을 위해 MAP_ID를 알려주셔야 합니다.")
        if work_type != "DB_MIGRATION" and (not str(target.get("sql_id") or "").strip() or not str(target.get("space_nm") or "").strip()):
            return self._exception(decision, f"{self._operation_label(route, work_type)}을 위해 SQL_ID와 SPACE_NM을 모두 알려주셔야 합니다.")
        if route == "CORRECT_SQL_INPUT":
            column = str(target.get("sql_column") or "").strip().upper()
            allowed = {"MIG_SQL", "VERIFY_SQL"} if work_type == "DB_MIGRATION" else {"TO_SQL", "BIND_SQL", "TEST_SQL", "TUNED_TO_SQL", "FORMATTED_SQL"}
            if column not in allowed:
                return self._exception(decision, "Correct SQL 입력을 위해 저장할 SQL 컬럼을 정확히 알려주셔야 합니다.")
            if not str(decision["correct_sql"]).strip():
                return self._exception(decision, "Correct SQL 입력을 위해 저장할 SQL 본문을 알려주셔야 합니다.")
            target["sql_column"] = column
        return {**decision, "target": target}

    def _exception(self, decision: dict[str, Any], message: str) -> dict[str, Any]:
        return {**decision, "management_route": "EXCEPTION", "exception_message": message}

    def _operation_label(self, route: str, work_type: str) -> str:
        if route == "CORRECT_SQL_INPUT":
            return "DB Migration Correct SQL 입력" if work_type == "DB_MIGRATION" else "Correct SQL 입력"
        return "Status Change(Reset)"

    def _next_node(self, route: str) -> str:
        return {"DASHBOARD": "04_dashboard", "CURRENT_PROGRESS": "04_currentProgress", "STATUS_CHANGE": "04_statusChange", "CORRECT_SQL_INPUT": "04_correctSqlInput", "EXCEPTION": "04_managementRouter"}.get(route, "04_dashboard")

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
