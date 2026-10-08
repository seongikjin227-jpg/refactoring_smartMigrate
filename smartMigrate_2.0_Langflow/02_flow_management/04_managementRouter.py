from __future__ import annotations

import json
import logging
import re
import traceback
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
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
- "Mig 실행 결과", "SQL Conversion 실행 결과", "최근 실행 결과", "방금 작업 결과"처럼 최근 작업 결과를 묻는 요청도 MANAGEMENT_AGENT입니다. 식별자가 없어도 해당 도메인 또는 전체 도메인의 최근 작업·로그 조회가 가능하므로 EXCEPTION으로 보내지 않습니다. 현재 실행 여부만 묻는 CURRENT_PROGRESS 및 전체 건수만 묻는 DASHBOARD와 구분합니다.
- DB row 변경 요청은 MANAGEMENT_AGENT입니다.
- USER_EDITED, USE_YN, priority, 상태 초기화, SQL 저장, SQL 비우기 요청은 MANAGEMENT_AGENT입니다.
- RAG Guide 조회/추가/수정/비활성화 요청은 MANAGEMENT_AGENT입니다.
- "비슷한 AS-IS SQL", "유사 SQL", "유사한 실패 SQL", "Fail-* 재시도 후보"처럼 AS-IS SQL 벡터 검색 또는 그 결과의 재시도 요청은 MANAGEMENT_AGENT입니다.
- 유사 SQL 검색 결과의 FAIL-* 행을 유지한 채 RETRY_COUNT를 0으로 바꾸는 요청도 MANAGEMENT_AGENT입니다. 검색만 요청한 경우에는 상태를 변경하지 않습니다.
- VectorDB, Milvus, 벡터DB, vector upload, vector sync 요청도 MANAGEMENT_AGENT입니다. Agent가 연결된 Sync Milvus Vector DB Tool을 직접 호출합니다.
- RAG Guide를 추가/수정/비활성화한 직후라도 VectorDB 동기화를 자동으로 이어서 실행하지 않습니다. 사용자가 별도로 요청한 경우에만 Agent가 sync_all Tool command를 호출합니다.
- 매핑 룰 등록·수정·가져오기·검증·적용 요청은 MAPPING_RULE_UPDATE입니다. 매핑 내용 전체가 들어 있는 원본 user_request를 전용 컴포넌트로 전달합니다. 일반 파일 관련 조회는 MANAGEMENT_AGENT입니다.

필수 정보 누락 규칙:
- route 자체를 판단할 수 없으면 EXCEPTION으로 보내고 exception_message에 필요한 정보를 한국어로 적으세요.
- MANAGEMENT_AGENT가 세부 파라미터 누락을 직접 물어볼 수 있으므로, route가 명확하면 EXCEPTION으로 보내지 마세요.
- EXCEPTION의 exception_message에는 한 줄 거절 대신 어떤 목적이나 식별자가 불명확한지,
  필요한 정보와 완전한 재요청 예시를 한국어로 안내하세요. 조회·변경·실행을 완료했다고 말하지 마세요.

JSON schema:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|MANAGEMENT_AGENT|EXCEPTION","exception_message":"","reason":""}"""


MAPPING_RULE_UPDATE_ROUTE_HINT = """
Additional route:
- MAPPING_RULE_UPDATE: The request asks to add, change, correct, import, or
  apply mapping rules in NEXT_MIG_INFO / NEXT_MIG_INFO_DTL. Choose this route
  when the supplied text contains mapping rules, including a natural-language
  request plus table/column mapping definitions. Do not choose MANAGEMENT_AGENT
  for this case.

The JSON schema is:
{"management_route":"DASHBOARD|CURRENT_PROGRESS|MANAGEMENT_AGENT|MAPPING_RULE_UPDATE|EXCEPTION","exception_message":"","reason":""}
"""

EXCEPTION_MESSAGE = """관리 요청을 이어가려면 원하는 작업과 대상을 조금 더 구체적으로 알려주세요.
Management에서는 전체 현황·현재 진행 조회, 특정 작업의 상태·SQL·로그 조회, 재시도 준비·우선순위 변경, Correct SQL 저장, 매핑 등록·수정, RAG Guide와 VectorDB 관리를 할 수 있습니다.

요청에 포함할 정보:
- Migration 조회/변경: MAP_ID, 확인하거나 바꿀 항목, 변경 요청이면 새 값.
- SQL 조회/변경: SQL_SEQ 또는 SQL_ID + SPACE_NM, Conversion·Tuning·Formatting 중 도메인.
- Correct SQL: 대상 식별자, 저장할 SQL 종류와 검토한 SQL 전문. BIND_SQL은 BIND_SET도 필요합니다.
- 파일/매핑: 파일 업로드 기능의 경우 현재 보안 문제로 인해 기능 제한이 있을 수 있습니다. 다음 템플릿을 복사하여 파일을 다시 첨부하고 요청해 주세요!
  "Super Agent의 파일 처리 기능을 활용하여 첨부파일의 내용을 조회해줘. Code Interpreter Tool은 사용하지 말고 해당 내용을 빠짐 없이 Smart Migrate 에이전트를 호출하여 전달하고 매핑룰을 등록해줘"
- RAG: 규칙/사례 내용, 수정 시 RAG_ID. VectorDB 동기화는 별도의 요청으로 명시합니다.

아래 예시의 번호와 이름을 실제 정보로 바꿔 보내주세요.
1. "마이그레이션 59번의 상태, RETRY_COUNT와 최근 실패 로그를 보여줘."
2. "SQL_ID S001, SPACE_NM PAYMENT의 Conversion 상태와 TO_SQL을 보여줘."
3. "MAP_ID 59의 PRIORITY를 5로 바꿔줘."
4. "SQL_SEQ 42의 Correct TO_SQL을 저장해줘. SQL: [검토한 SQL 전문]"
5. "MAP_ID 101, FR_TABLE CUSTOMER, TO_TABLE MEMBER로 매핑 등록 SQL을 만들어줘."
6. "RAG Guide와 Correct SQL을 VectorDB에 동기화해줘."

조회·변경·실제 실행은 서로 다른 요청입니다. RETRY_COUNT 초기화나 SQL 저장 뒤 바로 실행됐다고 가정하지 말고 결과를 확인한 다음 실행을 요청해 주세요.
원하시는 관리 기능과 작업 식별자를 알려주시면 그 범위로 요청을 이어갈 수 있습니다."""


class NewType04ManagementRouter(Component):
    display_name = "04 Management LLM Router"
    description = "Routes management requests to dashboard, progress, agent, mapping-rule DML, or exception."
    name = "NewType04ManagementRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"]),
    ]
    outputs = [
        Output(display_name="Dashboard", name="dashboard", method="dashboard_response", group_outputs=True),
        Output(display_name="Current Progress", name="current_progress", method="current_progress_response", group_outputs=True),
        Output(display_name="Management Agent", name="management_agent", method="management_agent_response", group_outputs=True),
        Output(display_name="Mapping Rule Update", name="mapping_rule_update", method="mapping_rule_update_response", group_outputs=True, types=["Message"]),
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

    def mapping_rule_update_response(self) -> Message:
        routed = self._get_routed_payload()
        if routed.get("management_route") != "MAPPING_RULE_UPDATE":
            self.stop("mapping_rule_update")
            return Message(text="")
        user_request = str(routed.get("user_request") or "")
        self.status = {"management_route": "MAPPING_RULE_UPDATE", "user_request": user_request,
                       "selected_output": "mapping_rule_update", "next_node": "04_mappingRuleUpdateSqlGenerate"}
        return Message(text=user_request)

    # LLM이 route를 정할 수 없을 때만 사용자에게 보낼 최종 Message branch를 연다.
    def exception_response(self) -> Message:
        routed = self._get_routed_payload()
        if routed.get("management_route") != "EXCEPTION":
            self.stop("exception")
            return Message(text="")
        reason = str(routed.get("exception_message") or "").strip()
        answer = reason if routed.get("exception_guidance_complete") else "\n\n".join(part for part in (reason, EXCEPTION_MESSAGE) if part)
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
        try:
            return self._compute_routed_payload()
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"04 management routing failed: {exc}",
                extra={"workflow_log": [0, "WORKFLOW", "04_MGMT_ROUTER", "ERROR", "ROUTE", "ERROR", 0,
                                        json.dumps({"error": str(exc), "traceback": traceback.format_exc()}, ensure_ascii=False)]},
            )
            answer = (
                "관리 요청을 해석하거나 다음 단계로 전달하는 중 문제가 발생했습니다. 현재 조회·변경 결과를 확인하지 못했습니다."
                "\n잠시 후 같은 요청을 다시 보내 주세요. 문제가 반복되면 운영자에게 요청 시각과 요청문을 전달해 LLM 연결·응답과 전달 데이터를 확인해 주세요."
                "\n이전에 변경을 요청했다면 먼저 해당 작업의 현재 상태와 로그를 확인한 뒤 다시 변경할지 결정해 주세요.\n\n"
                + EXCEPTION_MESSAGE
            )
            try:
                original_payload = self._parse_payload(getattr(self, "payload_json", ""))
            except Exception:
                original_payload = {}
            result = {**original_payload, "ok": False, "component": "04_managementRouter", "management_route": "EXCEPTION",
                      "error": str(exc), "exception_message": answer, "exception_guidance_complete": True,
                      "should_execute": False, "next_node": "chat_output", "final": True}
            self._cached_routed_payload = result
            return result

    def _compute_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached
        logging.getLogger("smartmigrate.workflow").info(
            "04 Management Router started",
            extra={"workflow_log": [0, "WORKFLOW", "04_MGMT_ROUTER", "INFO", "ROUTE", "START", 0]},
        )
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        if payload.get("ok") is False:
            raise ValueError(payload.get("error") or "Upstream request interpretation failed")
        if payload.get("clarification_required"):
            result = {**payload, "component": "04_managementRouter", "management_route": "EXCEPTION",
                      "exception_message": payload.get("clarification_message") or "대상과 요청 의도를 확인해 주세요.",
                      "should_execute": False, "next_node": "chat_output"}
            self._cached_routed_payload = result
            return result
        decision = self._normalize_decision(self._route_with_llm(payload))
        attachment_file_reference = self._attachment_file_reference(payload)
        if attachment_file_reference and decision["management_route"] == "EXCEPTION":
            decision = {
                "management_route": "MANAGEMENT_AGENT",
                "exception_message": "",
                "reason": "attachment metadata is preserved; the Agent can request missing details",
            }
        routed = {
            **payload,
            "component": "04_managementRouter",
            "effective_user_request": self._effective_user_request(payload),
            "management_route": decision["management_route"],
            "exception_message": decision.get("exception_message", ""),
            "management_routing_reason": decision.get("reason", ""),
            "attachment_file_reference": attachment_file_reference,
        }
        routed.setdefault("history", []).append({"step": "management_route", "message": f"management_route={routed['management_route']}"})
        self._cached_routed_payload = routed
        return routed

    # 자연어 요청은 여기서만 LLM에 전달한다. 이후 분기에서는 검증된 route 값만 사용한다.
    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._required_llm()
        response = llm.invoke(
            [
                SystemMessage(content=MANAGEMENT_ROUTER_PROMPT + MAPPING_RULE_UPDATE_ROUTE_HINT),
                HumanMessage(content=json.dumps({
                    "user_request": self._effective_user_request(payload),
                    "original_user_request": payload.get("user_request") or "",
                    "is_follow_up": bool(payload.get("is_follow_up", False)),
                    "confirmation": payload.get("confirmation") or "NOT_REQUIRED",
                    "target_filter": payload.get("target_filter") or {},
                    "clarification_required": bool(payload.get("clarification_required", False)),
                    "has_parsed_workbook": bool(payload.get("uploaded_attachment")),
                    "files": payload.get("files") or [],
                }, ensure_ascii=False, default=str)),
            ]
        )
        return self._parse_json_object(self._response_text(response))

    def _required_llm(self) -> Any:
        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("Connect a LanguageModel to 04 Management Router.")
        return llm

    @staticmethod
    def _response_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(item if isinstance(item, str) else str(item.get("text") or "") for item in content).strip()
        return str(content or "").strip()

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
        allowed = {"DASHBOARD", "CURRENT_PROGRESS", "MANAGEMENT_AGENT", "MAPPING_RULE_UPDATE", "EXCEPTION"}
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
            "MAPPING_RULE_UPDATE": "04_mappingRuleUpdateSqlGenerate",
            "EXCEPTION": "04_managementRouter",
        }.get(route, "04_managementAgent")

    def _attachment_file_reference(self, payload: dict[str, Any]) -> str:
        # Preserve the upload path from Chat Input whenever it survived the
        # upstream handoff.  This is authoritative; prose is only a fallback.
        for container in (
            payload,
            payload.get("source_message"),
            payload.get("message_data"),
            payload.get("data"),
        ):
            if not isinstance(container, dict):
                continue
            file_reference = self._file_reference_from_value(container.get("files"))
            if file_reference:
                return file_reference

        text = self._effective_user_request(payload)
        match = re.search(
            r"(?i)(?<![\w/\\])([^\s'\"]+\.(?:csv|tsv|txt|json|md|xml|yaml|yml|xlsx|xls|pdf))(?![\w])",
            text,
        )
        if not match:
            return ""

        # This fallback is for classifier prose such as "file(2026-...csv".
        file_reference = match.group(1).strip(".,:;!?)]}")
        if "(" in file_reference and "/" not in file_reference:
            prefix, possible_name = file_reference.rsplit("(", 1)
            if len(prefix) <= 16:
                return possible_name
        return file_reference

    @classmethod
    def _file_reference_from_value(cls, value: Any) -> str:
        if isinstance(value, (list, tuple)):
            for item in value:
                file_reference = cls._file_reference_from_value(item)
                if file_reference:
                    return file_reference
            return ""
        if isinstance(value, dict):
            for key in ("path", "file_path", "file", "name", "filename"):
                file_reference = cls._file_reference_from_value(value.get(key))
                if file_reference:
                    return file_reference
            return ""
        if not isinstance(value, str):
            return ""

        text = value.strip()
        if not text:
            return ""
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            decoded = None
        if decoded is not None and decoded != text:
            return cls._file_reference_from_value(decoded)

        candidate = text.strip(" \t\r\n'\"[]{}")
        return candidate if re.search(
            r"(?i)\.(?:csv|tsv|txt|json|md|xml|yaml|yml|xlsx|xls|pdf)$",
            candidate,
        ) else ""

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

