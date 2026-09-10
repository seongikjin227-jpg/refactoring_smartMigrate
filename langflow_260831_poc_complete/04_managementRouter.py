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


MANAGEMENT_ROUTER_PROMPT = """당신은 SmartMigrate 관리 요청 라우터입니다. 반드시 Markdown 없이 JSON 객체 1개만 반환하세요.

선택 가능한 route:
- DASHBOARD: 읽기 전용 전체 요약/대시보드 요청
- CURRENT_PROGRESS: 단순 진행 중/실행 중 작업 현황 요청
- JOB_QA: 작업 상태, 결과, 실패 원인, 로그, 진단처럼 DB 조회 근거를 바탕으로 LLM 해석이 필요한 요청
- STATUS_CHANGE: 작업 상태 초기화 요청
- CORRECT_SQL_INPUT: 사용자가 제공한 SQL 원문을 저장하는 요청
- RAG_GUIDE_MANAGEMENT: NEXT_MIG_RAG_INFO의 SQL Conversion RAG 가이드 또는 SQL Tuning 가이드를 조회/추가/수정/비활성화하는 요청
- VECTOR_DB_SYNC: Oracle의 RAG/Correct SQL 원천 데이터를 Milvus VectorDB에 업로드/동기화하는 요청
- EXCEPTION: 필수 정보가 없거나 요청이 모호해서 처리할 수 없는 경우

STATUS_CHANGE 규칙:
- reset은 status 컬럼을 NULL로 바꾸고 RETRY_COUNT를 0으로 바꾸는 작업입니다.
- reset은 SQL 본문을 삭제하거나 수정하지 않습니다.
- 사용자가 우선순위 상향, 긴급, 바로 처리 같은 표현을 쓰면 target.priority=1로 설정하세요.
- 그 외에는 target.priority=5로 설정하세요.

STATUS_CHANGE와 CORRECT_SQL_INPUT 공통 추출 규칙:
- target.work_type은 DB_MIGRATION, SQL_CONVERSION, SQL_TUNING, SQL_FORMATTING 중 하나입니다.
- DB_MIGRATION은 target.map_id가 필요합니다.
- SQL_* 작업은 target.sql_id와 target.space_nm이 모두 필요합니다.
- CORRECT_SQL_INPUT은 target.sql_column이 필요합니다.
- DB_MIGRATION에서 저장 가능한 SQL 컬럼은 MIG_SQL, VERIFY_SQL입니다.
- SQL_*에서 저장 가능한 SQL 컬럼은 TO_SQL, BIND_SQL, TEST_SQL, TUNED_TO_SQL, FORMATTED_SQL입니다.
- correct_sql에는 사용자가 직접 제공한 SQL 본문만 그대로 넣으세요.
- SQL, 식별자, 컬럼명, 가이드 내용을 절대 추측해서 만들지 마세요.

RAG_GUIDE_MANAGEMENT 규칙:
- 사용자가 튜닝 가이드, SQL Conversion RAG 가이드, 변환 규칙, 튜닝 규칙, NEXT_MIG_RAG_INFO row를 조회/추가/수정/삭제/비활성화하려고 하면 이 route를 선택하세요.
- rag_action은 query, add, update, upsert, disable, delete 중 하나로 채우세요.
- delete라고 해도 실제 물리 삭제가 아니라 USE_YN='N' 비활성화 작업입니다.
- rag에는 확인 가능한 값만 채우세요: rag_id, category, rule_type, source_tables, use_yn, keyword, limit, full_text, guidance_text, source_sql, target_sql.
- RAG_ID는 DB identity 컬럼이 자동 생성합니다. 신규 추가(add/create/insert)에서는 사용자가 명시하지 않는 한 rag_id를 채우지 마세요.
- category는 사용자 요청을 보고 SQL_CONVERSION 또는 SQL_TUNING 중 하나로 분류하세요.
- SQL 변환/Conversion RAG/변환 예시/AS-IS SQL과 TO-BE SQL 매핑 규칙은 category=SQL_CONVERSION입니다.
- SQL 튜닝/튜닝 가이드/성능 개선/힌트/실행계획 개선 규칙은 category=SQL_TUNING입니다.
- rule_type은 사용자 요청을 보고 GENERAL 또는 SEARCH 중 하나로 분류하세요.
- 전체 공통 원칙, 작성 지침, 금지 규칙처럼 특정 SQL 예시 검색이 필요 없는 내용은 rule_type=GENERAL입니다.
- SOURCE_SQL/TARGET_SQL 예시를 기반으로 유사 SQL을 검색해 적용할 내용은 rule_type=SEARCH입니다.
- SQL_CONVERSION + SEARCH는 source_tables가 필수입니다. 사용자가 대상 테이블을 말하지 않았으면 EXCEPTION으로 보내세요.
- SEARCH rule을 추가할 때는 source_sql과 target_sql을 모두 요구하세요. 둘 중 하나만 있으면 EXCEPTION으로 보내세요.
- GENERAL rule은 guidance_text 중심으로 저장하세요. source_sql/target_sql은 사용자가 명시한 경우에만 넣으세요.
- 조회 요청이면 사용자가 찾고 싶은 테이블명, 업무명, SQL 조각, 규칙 키워드를 rag.keyword에 넣으세요.
- 조회 요청에서 "전체 내용", "전문", "원문까지"처럼 말하면 rag.full_text=true로 설정하세요.
- 사용자가 가이드 내용 자체를 주지 않았으면 guidance_text, source_sql, target_sql을 지어내지 마세요.

VECTOR_DB_SYNC 규칙:
- 사용자가 "VectorDB 업로드", "Milvus 업로드", "RAG 벡터 동기화", "00B 실행", "가이드 추가 후 벡터DB 반영"처럼 요청하면 이 route를 선택하세요.
- 이 route는 NEXT_MIG_RAG_INFO, NEXT_SQL_INFO, NEXT_MIG_INFO 원천 데이터를 00B_saveVectorDB.py에서 Milvus collection으로 동기화하는 작업입니다.
- 사용자가 특정 RAG_ID만 말해도 현재 00B는 전체 snapshot 동기화 방식이므로 부분 업로드 조건을 만들지 마세요.

JOB_QA와 CURRENT_PROGRESS 선택 규칙:
- 작업 상태/결과/실패/로그를 조회하고 원인 해석이 필요하면 JOB_QA를 선택하세요.
- "전체 Fail 분석해줘", "최근 실패 원인 알려줘", "SQL Tuning만 분석해줘" 같은 요청은 JOB_QA입니다.
- 단순히 현재 실행 중인 작업이나 진행 현황만 묻는 요청은 CURRENT_PROGRESS입니다.
- RAG 가이드 테이블 자체가 주제이면 JOB_QA가 아니라 RAG_GUIDE_MANAGEMENT를 선택하세요.
- RAG 가이드 변경분을 VectorDB/Milvus에 반영하는 실행 요청이면 RAG_GUIDE_MANAGEMENT가 아니라 VECTOR_DB_SYNC를 선택하세요.

필수 정보 누락 규칙:
- 필요한 값이 없으면 management_route=EXCEPTION으로 설정하고 exception_message에 한국어로 무엇이 부족한지 구체적으로 쓰세요.
- 예: "DB Migration Correct SQL 입력을 위해 MAP_ID를 알려주셔야 합니다."
- 예: "Status Change(Reset)를 위해 SQL_ID와 SPACE_NM을 모두 알려주셔야 합니다."
- 예: "RAG Guide 수정/삭제에는 RAG_ID가 필요합니다. 먼저 조회해서 대상 RAG_ID를 확인해주세요."

JSON schema:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|JOB_QA|STATUS_CHANGE|CORRECT_SQL_INPUT|RAG_GUIDE_MANAGEMENT|VECTOR_DB_SYNC|EXCEPTION","target":{"work_type":"","map_id":"","sql_id":"","space_nm":"","sql_column":"","priority":5},"correct_sql":"","rag_action":"","rag":{"rag_id":"","category":"","rule_type":"","source_tables":"","use_yn":"Y","keyword":"","limit":"","full_text":false,"guidance_text":"","source_sql":"","target_sql":""},"exception_message":"","reason":""}"""

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
        Output(display_name="Job QA Agent", name="job_qa", method="job_qa_response", group_outputs=True),
        Output(display_name="Status Change", name="status_change", method="status_change_response", group_outputs=True),
        Output(display_name="Correct SQL Input", name="correct_sql_input", method="correct_sql_input_response", group_outputs=True),
        Output(display_name="RAG Guide Manager", name="rag_guide_manager", method="rag_guide_manager_response", group_outputs=True),
        Output(display_name="Vector DB Sync", name="vector_db_sync", method="vector_db_sync_response", group_outputs=True),
        Output(display_name="Exception Message", name="exception", method="exception_response", group_outputs=True, types=["Message"]),
    ]

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def dashboard_response(self) -> Data:
        return self._route_output("DASHBOARD", "dashboard")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def current_progress_response(self) -> Data:
        return self._route_output("CURRENT_PROGRESS", "current_progress")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def job_qa_response(self) -> Data:
        return self._route_output("JOB_QA", "job_qa")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def status_change_response(self) -> Data:
        return self._route_output("STATUS_CHANGE", "status_change")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def correct_sql_input_response(self) -> Data:
        return self._route_output("CORRECT_SQL_INPUT", "correct_sql_input")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def rag_guide_manager_response(self) -> Data:
        return self._route_output("RAG_GUIDE_MANAGEMENT", "rag_guide_manager")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def vector_db_sync_response(self) -> Data:
        return self._route_output("VECTOR_DB_SYNC", "vector_db_sync")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def exception_response(self) -> Message:
        routed = self._get_routed_payload()
        if routed.get("management_route") != "EXCEPTION":
            self.stop("exception")
            return Message(text="")
        answer = str(routed.get("exception_message") or EXCEPTION_MESSAGE)
        self.status = {**routed, "selected_output": "exception", "answer_text": answer, "final": True}
        return Message(text=answer)

    # 예상 route와 실제 route를 비교해 해당 output 실행 여부를 결정한다.
    def _route_output(self, expected_route: str, output_name: str) -> Data:
        routed = self._get_routed_payload()
        if routed.get("management_route") != expected_route:
            self.stop(output_name)
            return Data(data={})
        routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
        self.status = routed
        return Data(data=routed)

    # payload나 graph 설정에서 필요한 값을 꺼내 표준 형태로 반환한다.
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
        routed = {**payload, "component": "04_managementRouter", "management_route": decision["management_route"], "target": decision["target"], "correct_sql": decision["correct_sql"], "rag_action": decision.get("rag_action", ""), "rag": decision.get("rag", {}), "exception_message": decision["exception_message"], "management_routing_reason": decision["reason"]}
        routed.setdefault("history", []).append({"step": "management_route", "message": f"management_route={routed['management_route']}"})
        self._cached_routed_payload = routed
        return routed

    # 관리 자연어 요청을 LLM에 보내 route/action/target 구조로 분류한다.
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

    # 비교와 검색이 안정적으로 동작하도록 입력 값을 정규화한다.
    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("management_route") or "").upper()
        if route not in {"DASHBOARD", "CURRENT_PROGRESS", "JOB_QA", "STATUS_CHANGE", "CORRECT_SQL_INPUT", "RAG_GUIDE_MANAGEMENT", "VECTOR_DB_SYNC", "EXCEPTION"}:
            raise ValueError(f"Invalid management_route: {route}")
        return {"management_route": route, "target": dict(decision.get("target") or {}), "correct_sql": str(decision.get("correct_sql") or ""), "rag_action": str(decision.get("rag_action") or ""), "rag": dict(decision.get("rag") or {}), "exception_message": str(decision.get("exception_message") or ""), "reason": str(decision.get("reason") or "")}

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_management_request(self, decision: dict[str, Any]) -> dict[str, Any]:
        # LLM router가 이미 구조화된 JSON을 반환하더라도 그대로 믿지 않는다.
        # DB update로 이어지는 route는 여기서 한 번 더 필수값과 allow-list를 검증한다.
        route, target = decision["management_route"], dict(decision["target"])
        if route == "RAG_GUIDE_MANAGEMENT":
            return self._validate_rag_guide_request(decision)
        if route not in {"STATUS_CHANGE", "CORRECT_SQL_INPUT"}:
            return decision
        work_type = str(target.get("work_type") or "").strip().upper()
        if work_type not in {"DB_MIGRATION", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            return self._exception(decision, "Status Change 또는 Correct SQL 입력을 위해 작업 종류(DB Migration, SQL Conversion, SQL Tuning, SQL Formatting)를 알려주셔야 합니다.")
        target["work_type"] = work_type
        if route == "STATUS_CHANGE" and work_type == "SQL_FORMATTING":
            return self._exception(decision, "SQL Formatting은 별도 reset 대상 상태 컬럼이 없으므로 Status Change(Reset)를 지원하지 않습니다.")
        if route == "STATUS_CHANGE":
            try:
                target["priority"] = 1 if int(target.get("priority") or 5) == 1 else 5
            except (TypeError, ValueError):
                target["priority"] = 5
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

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_rag_guide_request(self, decision: dict[str, Any]) -> dict[str, Any]:
        # RAG guide 변경은 NEXT_MIG_RAG_INFO에 직접 영향을 주므로 별도 검증한다.
        # delete 요청도 실제 삭제가 아니라 04_ragGuideManager에서 USE_YN='N'으로 처리된다.
        action = str(decision.get("rag_action") or "").strip().lower()
        if action not in {"query", "add", "insert", "create", "update", "modify", "upsert", "disable", "soft_delete", "delete"}:
            return self._exception(decision, "RAG Guide 관리를 위해 query/add/update/upsert/disable/delete 중 작업 종류를 알려주세요.")
        rule = dict(decision.get("rag") or {})
        if action in {"update", "modify", "disable", "soft_delete", "delete"} and not str(rule.get("rag_id") or "").strip():
            return self._exception(decision, "RAG Guide 수정/삭제에는 RAG_ID가 필요합니다. 먼저 조회해서 대상 RAG_ID를 확인해주세요.")
        if action in {"add", "insert", "create", "upsert"} and not str(rule.get("rag_id") or "").strip():
            category = str(rule.get("category") or "").strip().upper()
            if category not in {"SQL_CONVERSION", "SQL_TUNING"}:
                return self._exception(decision, "RAG Guide 추가에는 category(SQL_CONVERSION 또는 SQL_TUNING)가 필요합니다.")
            rule_type = str(rule.get("rule_type") or "").strip().upper()
            if rule_type not in {"GENERAL", "SEARCH"}:
                return self._exception(decision, "RAG Guide 추가에는 rule_type(GENERAL 또는 SEARCH)이 필요합니다.")
            source_tables = str(rule.get("source_tables") or "").strip()
            source_sql = str(rule.get("source_sql") or "").strip()
            target_sql = str(rule.get("target_sql") or "").strip()
            guidance_text = str(rule.get("guidance_text") or "").strip()
            if category == "SQL_CONVERSION" and rule_type == "SEARCH" and not source_tables:
                return self._exception(decision, "SQL_CONVERSION SEARCH RAG Guide 추가에는 SOURCE_TABLES가 필요합니다.")
            if rule_type == "SEARCH" and (not source_sql or not target_sql):
                return self._exception(decision, "SEARCH RAG Guide 추가에는 SOURCE_SQL과 TARGET_SQL을 모두 입력해야 합니다.")
            if rule_type == "GENERAL" and not guidance_text:
                return self._exception(decision, "GENERAL RAG Guide 추가에는 GUIDANCE_TEXT가 필요합니다.")
        return {**decision, "rag_action": action, "rag": rule}

    # 관리 라우팅 실패를 표준 exception payload와 사용자 메시지로 만든다.
    def _exception(self, decision: dict[str, Any], message: str) -> dict[str, Any]:
        return {**decision, "management_route": "EXCEPTION", "exception_message": message}

    # 사용자에게 보여줄 관리 작업명을 route/work_type 기준으로 만든다.
    def _operation_label(self, route: str, work_type: str) -> str:
        if route == "CORRECT_SQL_INPUT":
            return "DB Migration Correct SQL 입력" if work_type == "DB_MIGRATION" else "Correct SQL 입력"
        return "Status Change(Reset)"

    # 관리 route에 연결된 다음 Langflow node 이름을 반환한다.
    def _next_node(self, route: str) -> str:
        return {"DASHBOARD": "04_dashboard", "CURRENT_PROGRESS": "04_currentProgress", "JOB_QA": "04_jobQaAgent", "STATUS_CHANGE": "04_statusChange", "CORRECT_SQL_INPUT": "04_correctSqlInput", "RAG_GUIDE_MANAGEMENT": "04_ragGuideManager", "VECTOR_DB_SYNC": "00B_saveVectorDB", "EXCEPTION": "04_managementRouter"}.get(route, "04_dashboard")

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    # 문자열 입력에서 JSON 객체를 파싱해 후속 로직이 쓰는 dict로 만든다.
    def _parse_json_object(self, text: str) -> dict[str, Any]:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        parsed = json.loads(match.group(0) if match else clean)
        if not isinstance(parsed, dict):
            raise ValueError("LLM must return a JSON object")
        return parsed

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")

