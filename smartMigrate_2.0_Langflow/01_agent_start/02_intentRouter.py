from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import MessageInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


ROUTE_CLASSIFIER_PROMPT = """You are the SmartMigrate request route classifier.
Return exactly one JSON object and no other text:
{
  "route": "GENERAL_CHAT|MANAGEMENT|JOB_EXECUTION",
  "request_action": "STATUS_QUERY|EXECUTE|OTHER",
  "requested_domain": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|FULL_WORKFLOW|UNKNOWN",
  "execution_scope": "all|domain|targeted|unknown",
  "target_filter": {"map_ids": [], "sql_seqs": [], "sql_ids": [], "space_nms": []},
  "clarification_required": false,
  "clarification_message": ""
}

Route definitions:
- GENERAL_CHAT: conceptual questions or conversation not requesting SmartMigrate work.
- MANAGEMENT: status/dashboard/log/remaining-work queries, data-management requests,
  RAG or VectorDB management, mapping-rule registration/import, or attached mapping files.
- JOB_EXECUTION: a request to actually start or execute a migration or SQL job.

An attached mapping/Excel/CSV file with a request to register, import, validate, preview,
or apply mapping rules is MANAGEMENT, never GENERAL_CHAT.

You are the only natural-language target interpreter. Extract targets before any DB query.
- Interpret identifier aliases using the domain in the current request; literal column names are not required.
- In Migration/MIG/이관/마이그레이션/맵 context, "번호", "순번", "작업 번호", "작업 순번",
  "마이그레이션 번호", "이관 번호", "맵 번호", "맵 아이디", "MAP_ID" and "N번" refer to MAP_ID.
  "마이그레이션 59번", "59번 맵", "MAP_ID 59", "마이그레이션 번호 59",
  "마이그레이션 순번 59", "이관 작업 번호 59" all identify MIG and map_ids=[59].
- In SQL Conversion/SQL 변환/SQL 컨버전/SQL_CONVERSION context, "번호", "순번", "작업 번호",
  "작업 순번", "SQL 번호", "SQL 순번", "변환 번호", "변환 순번", "SQL_SEQ" and "N번"
  refer to SQL_SEQ and populate sql_seqs, not sql_ids or map_ids.
  "SQL Conversion 59번 실행", "SQL 변환 번호 59 실행", "SQL 변환 순번 59 실행",
  "SQL 컨버전 작업 번호 59 실행" mean SQL_CONVERSION / targeted / sql_seqs=[59].
- Apply the same SQL_SEQ number/sequence aliases in SQL Tuning/SQL 튜닝 and SQL Formatting/SQL 포맷팅
  context, keeping the requested domain SQL_TUNING or SQL_FORMATTING respectively.
  "SQL 튜닝 순번 59 실행" means SQL_TUNING / targeted / sql_seqs=[59].
- Numbers in these aliases are actual database MAP_ID/SQL_SEQ values, never list row positions.
  "마이그레이션 번호 59, 60 실행" gives map_ids=[59,60]; "SQL 변환 순번 59, 60 실행" gives sql_seqs=[59,60].
  Do not convert retry counts, row counts, priorities, dates, or a request's numbered instructions into targets.
- Explicit SQL_ID/SQL 아이디 labels denote sql_ids even when the value is numeric; require SPACE_NM as well.
  "SQL 변환 SQL_ID 59 SPACE_NM PAYMENT 실행" gives sql_ids=["59"], space_nms=["PAYMENT"], not sql_seqs=[59].
- A bare "59번 실행", "번호 59 실행", or "순번 59 실행" without a resolvable domain is targeted
  but ambiguous: use UNKNOWN, empty target arrays, clarification_required=true, and ask whether it is
  a Migration MAP_ID or a SQL_SEQ and which SQL stage. Never guess the domain or execute all jobs.
- "마이그레이션 59번 다시 실행해줘" is EXECUTE / JOB_EXECUTION / MIG / targeted.
- "마이그레이션 59번 현재 상태를 다시 확인해주세요" is STATUS_QUERY / MANAGEMENT / MIG / targeted.
  Mentioning an earlier execution or retry request does not itself request execution now.
- SQL targets require sql_seq, or both sql_id and space_nm. Preserve string identifier case.
- "전체 작업 실행", "남은 작업 다 실행", or generic "작업 실행" without any target/domain
  is EXECUTE / JOB_EXECUTION / FULL_WORKFLOW / all, with empty target arrays.
- "마이그레이션 전체 실행" is EXECUTE / JOB_EXECUTION / MIG / domain, with empty arrays.
- "SQL 튜닝 전체 실행" is EXECUTE / JOB_EXECUTION / SQL_TUNING / domain.
- A specific-target request is targeted even if its identifier cannot be resolved.
  Never widen it to all/domain; set clarification_required=true and ask for the missing target.
- Do not invent identifiers or infer previous chat targets: only the current request is provided.
- If current intent is ambiguous between checking status and executing, set clarification_required=true.
- Use positive integers for map_ids/sql_seqs and nonempty strings for sql_ids/space_nms.
- Do not combine migration and SQL identifiers. For specific SQL execution, choose exactly one SQL domain.
- For multiple SQL_ID/SPACE_NM pairs, ask for SQL_SEQ identifiers rather than flattening pairs into arrays.
- FULL_WORKFLOW is for all execution, never targeted execution. Unknown domain/scope for execution
  requires clarification; it is not permission to execute all remaining work.
- For GENERAL_CHAT use OTHER / UNKNOWN / unknown with empty arrays.
"""

LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


class NewType02IntentRouter(Component):
    display_name = "02 Intent LLM Router"
    description = "Classifies a 00A Chat Input Message and preserves all original runtime metadata."
    name = "NewType02IntentRouter"
    icon = "Route"

    inputs = [
        MessageInput(name="input_message", display_name="00A Chat Input Message", required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"]),
    ]

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
        try:
            payload = self._build_payload()
            route = str(payload["route"])
            if route != expected_route:
                self.stop(output_name)
                return Data(data={})

            routed = {
                **payload,
                "component": "02_intentRouter",
                "selected_output": output_name,
                "next_node": {
                    "GENERAL_CHAT": "03_llmResponse",
                    "MANAGEMENT": "04_managementRouter",
                    "JOB_EXECUTION": "06_getRemainingJobs",
                }[route],
            }
            routed.setdefault("history", []).append(
                {"step": "intent_router", "message": f"route={route}"}
            )
            if expected_route == "MANAGEMENT":
                outgoing_payload = json.dumps(routed, ensure_ascii=False, default=str)
                logging.getLogger(LOGGER_NAME).info(
                    "02 final JSON payload to 04",
                    extra={
                        "workflow_log": [
                            0, "WORKFLOW", "02_TO_04_PAYLOAD", "INFO", "SEND_04", "PASS", 0,
                            outgoing_payload,
                        ]
                    },
                )
            if expected_route == "JOB_EXECUTION":
                logging.getLogger(LOGGER_NAME).info(
                    "02 final JSON payload to 06",
                    extra={"workflow_log": [0, "WORKFLOW", "02_TO_06_PAYLOAD", "INFO", "SEND_06", "PASS", 0,
                                            json.dumps(routed, ensure_ascii=False, default=str)]},
                )
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            logging.getLogger(LOGGER_NAME).error(
                f"02 intent routing failed: {exc}",
                extra={"workflow_log": [0, "WORKFLOW", "02_INTENT_ROUTER", "ERROR", "ROUTE", "ERROR", 0,
                                        json.dumps({"error": str(exc), "traceback": traceback.format_exc()}, ensure_ascii=False)]},
            )
            result = {"ok": False, "component": "02_intentRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _build_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_compiled_payload", None)
        if cached is not None:
            return cached

        logger = logging.getLogger(LOGGER_NAME)
        handler_available = any(
            getattr(handler, "handler_marker", None) == HANDLER_MARKER
            for handler in logger.handlers
        )
        # Observability must not block routing.  When 00A was bypassed this
        # warning has no DB handler to persist to, but execution continues and
        # the normal component status/output remains available to the caller.
        if not handler_available:
            logger.warning("02 started without the SmartMigrate DB log handler; 00A was not executed or did not complete.")

        message = getattr(self, "input_message", None)
        if not isinstance(message, Message):
            error = "02 input_message is missing or is not a Langflow Message."
            logger.error(
                error,
                extra={
                    "workflow_log": [
                        0, "WORKFLOW", "02_INPUT_MESSAGE", "ERROR", "RECEIVE_00A", "FAIL", 0,
                        json.dumps({"received_type": type(message).__name__, "received_value": str(message)[:1000]}, ensure_ascii=False),
                    ]
                },
            )
            self.status = {"ok": False, "component": "02_intentRouter", "stage": "INPUT_CHECK", "error": error}
            raise TypeError(error)

        # Record arrival before extracting/parsing any field.  Therefore an
        # unexpected Message shape still leaves a diagnostic input record.
        raw_message_snapshot = json.dumps(self._json_value(message), ensure_ascii=False, default=str)
        logger.info(
            raw_message_snapshot,
            extra={
                "workflow_log": [
                    0, "WORKFLOW", "02_INPUT_ENVELOPE", "INFO", "RECEIVE_00A", "START", 0,
                    raw_message_snapshot,
                ]
            },
        )

        envelope = self._input_envelope(message)
        source_message = self._source_message(message, envelope)
        user_request = str(envelope.get("text") or "").strip()
        if not user_request:
            user_request = str(getattr(message, "text", "") or "").strip()

        if not user_request:
            logger.warning(
                "02 received a Message with empty text.",
                extra={
                    "workflow_log": [
                        0, "WORKFLOW", "02_INPUT_MESSAGE", "WARNING", "VALIDATE_TEXT", "EMPTY", 0,
                        json.dumps(source_message, ensure_ascii=False, default=str),
                    ]
                },
            )
        logger.info(
            "02 Intent LLM Router started",
            extra={"workflow_log": [0, "WORKFLOW", "02_INTENT_ROUTER", "INFO", "ROUTE", "START", 0]},
        )
        input_snapshot = json.dumps(source_message, ensure_ascii=False, default=str)
        logger.info(
            input_snapshot,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "02_INPUT_MESSAGE",
                    "INFO",
                    "RECEIVE_00A",
                    "PASS",
                    0,
                    input_snapshot,
                ]
            },
        )

        raw_llm_response = self._classify(user_request, source_message["files"])
        logger.info(
            "02 raw LLM intent/target response",
            extra={"workflow_log": [0, "WORKFLOW", "02_LLM_ROUTE_RESPONSE", "INFO", "CLASSIFY", "PASS", 0,
                                    raw_llm_response]},
        )
        intent = self._intent_from_response(raw_llm_response)
        route = intent["route"]
        if self._attachment_management_request(user_request, source_message["files"]) or (
            isinstance(source_message["data"], dict) and source_message["data"].get("uploaded_attachment")
        ):
            route = "MANAGEMENT"

        payload = {
            "route": route,
            "user_request": user_request,
            "resolved_user_request": user_request,
            "is_follow_up": False,
            "confirmation": "NOT_REQUIRED",
            "request_action": intent["request_action"],
            "clarification_required": intent["clarification_required"],
            "clarification_message": intent["clarification_message"],
            "should_execute": route == "JOB_EXECUTION" and intent["request_action"] == "EXECUTE" and not intent["clarification_required"],
            "execution_scope": intent["execution_scope"],
            "requested_domain": intent["requested_domain"],
            "target_filter": intent["target_filter"],
            # Top-level copies make the attachment contract explicit for 04.
            "session_id": source_message["session_id"],
            "context_id": source_message["context_id"],
            "files": source_message["files"],
            "message_data": source_message["data"],
            "uploaded_attachment": (source_message["data"] or {}).get("uploaded_attachment") if isinstance(source_message["data"], dict) else None,
            "source_message": source_message,
        }
        full_payload = json.dumps(payload, ensure_ascii=False, default=str)
        logger.info(
            full_payload,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "02_FINAL_OUTPUT",
                    "INFO",
                    "COMPILE_PAYLOAD",
                    "PASS",
                    0,
                    full_payload,
                ]
            },
        )
        self._compiled_payload = payload
        return payload

    def _classify(self, user_request: str, files: list[Any]) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("Connect a Language Model to 02 Intent LLM Router.")
        messages = [
            SystemMessage(content=ROUTE_CLASSIFIER_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {"user_request": user_request, "files": files},
                    ensure_ascii=False,
                    default=str,
                )
            ),
        ]
        prompt_snapshot = json.dumps(
            [
                {"role": "system", "content": messages[0].content},
                {"role": "user", "content": messages[1].content},
            ],
            ensure_ascii=False,
            default=str,
        )
        logging.getLogger(LOGGER_NAME).info(
            "02 LLM route prompt",
            extra={
                "workflow_log": [
                    0, "WORKFLOW", "02_LLM_ROUTE_PROMPT", "INFO", "CLASSIFY", "START", 0,
                    prompt_snapshot,
                ]
            },
        )
        response = llm.invoke(messages)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                item if isinstance(item, str) else str(item.get("text") or "")
                for item in content
            ).strip()
        return str(content or "").strip()

    @staticmethod
    def _route_from_response(raw_response: str) -> str:
        match = re.search(r"\{.*\}", raw_response, flags=re.S)
        decoded = json.loads(match.group(0) if match else raw_response)
        route = str(decoded.get("route") or "").upper()
        if route not in {"GENERAL_CHAT", "MANAGEMENT", "JOB_EXECUTION"}:
            raise ValueError("02 LLM response must contain a valid route.")
        return route

    def _intent_from_response(self, raw_response: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", raw_response, flags=re.S)
        decoded = json.loads(match.group(0) if match else raw_response)
        if not isinstance(decoded, dict):
            raise ValueError("02 LLM intent response must be an object.")
        route = self._route_from_response(raw_response)
        action = str(decoded.get("request_action") or "OTHER").upper()
        domain = str(decoded.get("requested_domain") or "UNKNOWN").upper()
        scope = str(decoded.get("execution_scope") or "unknown").lower()
        if action not in {"STATUS_QUERY", "EXECUTE", "OTHER"}:
            raise ValueError(f"Invalid request_action: {action}")
        if domain not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "FULL_WORKFLOW", "UNKNOWN"}:
            raise ValueError(f"Invalid requested_domain: {domain}")
        if scope not in {"all", "domain", "targeted", "unknown"}:
            raise ValueError(f"Invalid execution_scope: {scope}")
        targets = self._normalize_target_filter(decoded.get("target_filter", {}))
        clarification = decoded.get("clarification_required", False)
        if not isinstance(clarification, bool):
            raise ValueError("clarification_required must be a boolean.")
        reason = str(decoded.get("clarification_message") or "").strip()
        has_mig = bool(targets["map_ids"])
        has_sql = any(targets[key] for key in ("sql_seqs", "sql_ids", "space_nms"))
        has_target = has_mig or has_sql
        if action == "STATUS_QUERY":
            route = "MANAGEMENT"
        problem = ""
        if has_mig and has_sql:
            problem = "Migration과 SQL 작업을 나누어 요청해 주세요."
        elif has_target and scope != "targeted":
            problem = "특정 대상 실행인지 전체 실행인지 확인해 주세요."
        elif scope == "targeted" and not has_target:
            problem = "작업 대상 MAP_ID 또는 SQL_SEQ를 알려주세요."
        elif has_sql and not targets["sql_seqs"] and not (targets["sql_ids"] and targets["space_nms"]):
            problem = "SQL_SEQ 또는 SQL_ID와 SPACE_NM을 함께 알려주세요."
        elif len(targets["sql_ids"]) > 1 and len(targets["space_nms"]) > 1:
            problem = "여러 SQL 대상은 각각의 SQL_SEQ로 지정해 주세요."
        elif route == "JOB_EXECUTION":
            if action != "EXECUTE":
                problem = "상태 확인인지 실제 실행인지 명확히 요청해 주세요."
            elif scope == "unknown" or domain == "UNKNOWN":
                problem = "실행할 도메인과 전체 또는 특정 대상 범위를 알려주세요."
            elif (scope == "all") != (domain == "FULL_WORKFLOW"):
                problem = "전체 워크플로우인지 특정 도메인 실행인지 확인해 주세요."
            elif (has_mig and domain != "MIG") or (has_sql and domain not in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}):
                problem = "작업 도메인과 대상 식별자가 일치하지 않습니다."
        if problem:
            clarification, reason = True, problem
        if clarification and not reason:
            reason = "대상과 상태 확인 또는 실행 의도를 명확히 알려주세요."
        return {"route": route, "request_action": action, "requested_domain": domain,
                "execution_scope": scope, "target_filter": targets,
                "clarification_required": clarification, "clarification_message": reason}

    @staticmethod
    def _normalize_target_filter(raw: Any) -> dict[str, list[Any]]:
        if not isinstance(raw, dict):
            raise ValueError("target_filter must be an object.")
        targets: dict[str, list[Any]] = {}
        for key in ("map_ids", "sql_seqs", "sql_ids", "space_nms"):
            values = raw.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"target_filter.{key} must be an array.")
            normalized = []
            for value in values:
                if key in {"map_ids", "sql_seqs"}:
                    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
                        raise ValueError(f"target_filter.{key} must contain positive integers.")
                    value = int(value)
                elif not isinstance(value, str) or not value.strip():
                    raise ValueError(f"target_filter.{key} must contain nonempty strings.")
                else:
                    value = value.strip()
                if value not in normalized:
                    normalized.append(value)
            targets[key] = normalized
        return targets

    @staticmethod
    def _attachment_management_request(user_request: str, files: list[Any]) -> bool:
        if not files:
            return False
        return bool(
            re.search(
                r"(?i)register|add|import|upload|preview|validate|apply|mapping|map[ _-]?rule|"
                r"excel|csv|매핑|등록|가져오기|업로드|검증|적용",
                user_request,
            )
        )

    @classmethod
    def _input_envelope(cls, message: Message) -> dict[str, Any]:
        text = str(getattr(message, "text", "") or "").strip()
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def _source_message(cls, message: Message, envelope: dict[str, Any]) -> dict[str, Any]:
        def value(name: str) -> Any:
            actual = getattr(message, name, None)
            return actual if actual is not None else envelope.get(name)

        files = cls._json_value(value("files"))
        return {
            "text": envelope.get("text", getattr(message, "text", None)),
            "sender": value("sender"),
            "sender_name": value("sender_name"),
            "session_id": value("session_id"),
            "context_id": value("context_id"),
            "files": files if isinstance(files, list) else ([] if files is None else [files]),
            "data": cls._json_value(value("data")),
            "properties": cls._json_value(value("properties")),
            "content_blocks": cls._json_value(value("content_blocks")),
        }

    @classmethod
    def _json_value(cls, value: Any, seen: set[int] | None = None) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        seen = seen if seen is not None else set()
        value_id = id(value)
        if value_id in seen:
            return "<circular reference>"
        if isinstance(value, dict):
            seen.add(value_id)
            result = {str(key): cls._json_value(item, seen) for key, item in value.items()}
            seen.remove(value_id)
            return result
        if isinstance(value, (list, tuple, set)):
            seen.add(value_id)
            result = [cls._json_value(item, seen) for item in value]
            seen.remove(value_id)
            return result
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._json_value(model_dump(), seen)
            except Exception:
                pass
        return str(value)
