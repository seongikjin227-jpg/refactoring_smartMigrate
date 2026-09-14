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


MANAGEMENT_ROUTER_PROMPT = """당신은 SmartMigrate 관리 요청 라우터입니다.
반드시 Markdown 없이 JSON 객체 1개만 반환하세요.

선택 가능한 route:
- DASHBOARD: 읽기 전용 전체 요약/대시보드 요청
- CURRENT_PROGRESS: 현재 실행 중인 작업 또는 단순 진행률 요청
- SELECT_AGENT: 작업 상태, 결과, 실패 원인, 로그, 잔여 작업 목록처럼 SELECT 조회와 해석이 필요한 요청
- UPDATE_COMMAND: DB row를 변경하는 모든 요청
- RAG_GUIDE_MANAGEMENT: NEXT_MIG_RAG_INFO의 RAG 가이드 조회/추가/수정/비활성화 요청
- VECTOR_DB_SYNC: Oracle 원천 데이터를 Milvus VectorDB에 업로드/동기화하는 요청
- EXCEPTION: 필수 정보가 없거나 모호해서 처리할 수 없는 경우

우선순위 규칙:
- DB row 값을 변경하는 요청은 UPDATE_COMMAND입니다.
- USER_EDITED, USE_YN, STATUS, RETRY_COUNT, PRIORITY, MIG_SQL, VERIFY_SQL, TO_SQL, BIND_SQL, TEST_SQL, TUNED_TO_SQL, FORMATTED_SQL 변경은 UPDATE_COMMAND입니다.
- "null로 바꿔줘", "비워줘", "초기화해줘"처럼 컬럼 값을 NULL로 바꾸는 요청은 UPDATE_COMMAND이며 updates.value에 JSON null을 넣습니다. 문자열 "null"을 넣지 마세요.
- 한 문장에 여러 변경이 있으면 updates 배열에 모두 넣습니다.
- "남은 작업", "잔여 작업", "remaining jobs", "todo list", "작업 리스트"처럼 실제 남은 row 목록을 묻는 요청은 CURRENT_PROGRESS가 아니라 SELECT_AGENT입니다.
- CURRENT_PROGRESS는 현재 실행 중인 작업/진행률만 묻는 경우에만 사용합니다.

UPDATE_COMMAND 추출 규칙:
- updates는 변경할 컬럼마다 1개씩 만듭니다.
- DB_MIGRATION은 map_id가 필요합니다.
- SQL_CONVERSION, SQL_TUNING, SQL_FORMATTING은 sql_id와 space_nm이 모두 필요합니다.
- work_type 값은 DB_MIGRATION, SQL_CONVERSION, SQL_TUNING, SQL_FORMATTING 중 하나입니다.
- field는 실제 변경할 컬럼명입니다.
- value는 변경할 값입니다. DB NULL은 JSON null입니다.

허용 UPDATE field:
- DB_MIGRATION: STATUS, RETRY_COUNT, PRIORITY, USER_EDITED, USE_YN, MIG_SQL, VERIFY_SQL
- SQL_CONVERSION/SQL_TUNING/SQL_FORMATTING: STATUS_CONVERSION, STATUS_TUNING, RETRY_COUNT, PRIORITY, USER_EDITED, TO_SQL, BIND_SQL, TEST_SQL, TUNED_TO_SQL, FORMATTED_SQL

RAG_GUIDE_MANAGEMENT 규칙:
- rag_action은 query, add, update, upsert, disable, delete 중 하나입니다.
- delete는 물리 삭제가 아니라 USE_YN='N' 비활성화입니다.
- RAG_ID는 DB identity 컬럼이 자동 생성하므로 신규 추가에서는 사용자가 명시하지 않는 한 rag_id를 채우지 마세요.
- SQL 변환/Conversion RAG/AS-IS SQL과 TO-BE SQL 매핑 규칙은 category=SQL_CONVERSION입니다.
- SQL 튜닝/성능 개선/힌트/실행계획 개선 규칙은 category=SQL_TUNING입니다.
- 조회 요청에서 "전체 내용", "전문", "원문까지"처럼 말하면 rag.full_text=true로 설정하세요.

필수 정보 누락 규칙:
- 필요한 값이 없으면 management_route=EXCEPTION으로 설정하고 exception_message에 한국어로 무엇이 부족한지 구체적으로 쓰세요.

JSON schema:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|SELECT_AGENT|UPDATE_COMMAND|RAG_GUIDE_MANAGEMENT|VECTOR_DB_SYNC|EXCEPTION","target":{"work_type":"","map_id":"","sql_id":"","space_nm":""},"updates":[{"work_type":"","map_id":"","sql_id":"","space_nm":"","field":"","value":null}],"rag_action":"","rag":{"rag_id":"","category":"","rule_type":"","source_tables":"","use_yn":"Y","keyword":"","limit":"","full_text":false,"guidance_text":"","source_sql":"","target_sql":""},"exception_message":"","reason":""}"""


EXCEPTION_MESSAGE = "Management 요청을 처리할 수 없습니다. 작업 종류와 필요한 식별자를 다시 알려주세요."


class NewType04ManagementRouter(Component):
    display_name = "04 Management LLM Router"
    description = "Routes management requests to SELECT, UPDATE, dashboard, RAG, and sync flows."
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
        Output(display_name="Select Agent", name="select_agent", method="select_agent_response", group_outputs=True),
        Output(display_name="Update Command", name="update_command", method="update_command_response", group_outputs=True),
        Output(display_name="RAG Guide Manager", name="rag_guide_manager", method="rag_guide_manager_response", group_outputs=True),
        Output(display_name="Vector DB Sync", name="vector_db_sync", method="vector_db_sync_response", group_outputs=True),
        Output(display_name="Exception Message", name="exception", method="exception_response", group_outputs=True, types=["Message"]),
    ]

    def dashboard_response(self) -> Data:
        return self._route_output("DASHBOARD", "dashboard")

    def current_progress_response(self) -> Data:
        return self._route_output("CURRENT_PROGRESS", "current_progress")

    def select_agent_response(self) -> Data:
        return self._route_output("SELECT_AGENT", "select_agent")

    def update_command_response(self) -> Data:
        return self._route_output("UPDATE_COMMAND", "update_command")

    def rag_guide_manager_response(self) -> Data:
        return self._route_output("RAG_GUIDE_MANAGEMENT", "rag_guide_manager")

    def vector_db_sync_response(self) -> Data:
        return self._route_output("VECTOR_DB_SYNC", "vector_db_sync")

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
        logging.getLogger("smartmigrate.workflow").info(
            "04 Management Router started",
            extra={"workflow_log": [0, "WORKFLOW", "04_MGMT_ROUTER", "INFO", "ROUTE", "START", 0]},
        )
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision = self._validate_management_request(self._normalize_decision(self._route_with_llm(payload)))
        routed = {
            **payload,
            "component": "04_managementRouter",
            "management_route": decision["management_route"],
            "target": decision["target"],
            "updates": decision.get("updates", []),
            "rag_action": decision.get("rag_action", ""),
            "rag": decision.get("rag", {}),
            "exception_message": decision.get("exception_message", ""),
            "management_routing_reason": decision.get("reason", ""),
        }
        routed.setdefault("history", []).append({"step": "management_route", "message": f"management_route={routed['management_route']}"})
        self._cached_routed_payload = routed
        return routed

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
            raise ValueError(f"04 Management Router LLM HTTP {exc.code}: {exc.read().decode('utf-8', errors='ignore')[:1000]}") from exc
        return self._parse_json_object((((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip())

    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("management_route") or "").upper()
        allowed = {"DASHBOARD", "CURRENT_PROGRESS", "SELECT_AGENT", "UPDATE_COMMAND", "RAG_GUIDE_MANAGEMENT", "VECTOR_DB_SYNC", "EXCEPTION"}
        if route not in allowed:
            raise ValueError(f"Invalid management_route: {route}")
        return {
            "management_route": route,
            "target": dict(decision.get("target") or {}),
            "updates": list(decision.get("updates") or []),
            "rag_action": str(decision.get("rag_action") or ""),
            "rag": dict(decision.get("rag") or {}),
            "exception_message": str(decision.get("exception_message") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    def _validate_management_request(self, decision: dict[str, Any]) -> dict[str, Any]:
        route = decision["management_route"]
        if route == "UPDATE_COMMAND":
            return self._validate_update_command(decision)
        if route == "RAG_GUIDE_MANAGEMENT":
            return self._validate_rag_guide_request(decision)
        return decision

    def _validate_update_command(self, decision: dict[str, Any]) -> dict[str, Any]:
        updates = decision.get("updates") or []
        if not isinstance(updates, list) or not updates:
            return self._exception(decision, "UPDATE_COMMAND에는 updates 배열이 필요합니다.")
        normalized = []
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                return self._exception(decision, f"updates[{index}]는 객체여야 합니다.")
            work_type = self._normalize_work_type(update.get("work_type") or update.get("domain"))
            field = str(update.get("field") or update.get("column") or update.get("sql_column") or "").strip().upper()
            if not field:
                return self._exception(decision, f"updates[{index}].field가 필요합니다.")
            if "value" not in update and "correct_sql" not in update:
                return self._exception(decision, f"updates[{index}].value가 필요합니다. NULL 저장은 JSON null로 명시해야 합니다.")
            if work_type == "DB_MIGRATION":
                if not str(update.get("map_id") or "").strip():
                    return self._exception(decision, f"updates[{index}] DB_MIGRATION에는 map_id가 필요합니다.")
            elif not str(update.get("sql_id") or "").strip() or not str(update.get("space_nm") or "").strip():
                return self._exception(decision, f"updates[{index}] SQL 작업에는 sql_id와 space_nm이 모두 필요합니다.")
            normalized.append({**update, "work_type": work_type, "field": field})
        return {**decision, "updates": normalized}

    def _validate_rag_guide_request(self, decision: dict[str, Any]) -> dict[str, Any]:
        action = str(decision.get("rag_action") or "").strip().lower()
        if action not in {"query", "add", "insert", "create", "update", "modify", "upsert", "disable", "soft_delete", "delete"}:
            return self._exception(decision, "RAG Guide 관리를 위해 query/add/update/upsert/disable/delete 중 작업 종류를 알려주세요.")
        rule = dict(decision.get("rag") or {})
        if action in {"update", "modify", "disable", "soft_delete", "delete"} and not str(rule.get("rag_id") or "").strip():
            return self._exception(decision, "RAG Guide 수정/삭제에는 RAG_ID가 필요합니다. 먼저 조회해서 대상 RAG_ID를 확인해주세요.")
        return {**decision, "rag_action": action, "rag": rule}

    def _exception(self, decision: dict[str, Any], message: str) -> dict[str, Any]:
        return {**decision, "management_route": "EXCEPTION", "exception_message": message}

    def _next_node(self, route: str) -> str:
        return {
            "DASHBOARD": "04_dashboard",
            "CURRENT_PROGRESS": "04_currentProgress",
            "SELECT_AGENT": "04_selectAgent",
            "UPDATE_COMMAND": "04_updateCommandTool",
            "RAG_GUIDE_MANAGEMENT": "04_ragGuideManager",
            "VECTOR_DB_SYNC": "04_saveVectorDB",
            "EXCEPTION": "04_managementRouter",
        }.get(route, "04_dashboard")

    def _normalize_work_type(self, value: Any) -> str:
        text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "MIG": "DB_MIGRATION",
            "DB": "DB_MIGRATION",
            "DB_MIG": "DB_MIGRATION",
            "MIGRATION": "DB_MIGRATION",
            "SQL": "SQL_CONVERSION",
            "CONVERSION": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
            "FORMAT": "SQL_FORMATTING",
        }
        normalized = aliases.get(text, text)
        if normalized not in {"DB_MIGRATION", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            raise ValueError(f"Unsupported work_type: {value}")
        return normalized

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

