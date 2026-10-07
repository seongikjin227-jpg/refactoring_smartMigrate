from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import MessageInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


ROUTE_CLASSIFIER_PROMPT = """You are the SmartMigrate request route classifier.
Return exactly one JSON object and no other text:
{"route":"GENERAL_CHAT|MANAGEMENT|JOB_EXECUTION"}

Route definitions:
- GENERAL_CHAT: conceptual questions or conversation not requesting SmartMigrate work.
- MANAGEMENT: status/dashboard/log/remaining-work queries, data-management requests,
  RAG or VectorDB management, mapping-rule registration/import, or attached mapping files.
- JOB_EXECUTION: a request to actually start or execute a migration or SQL job.

An attached mapping/Excel/CSV file with a request to register, import, validate, preview,
or apply mapping rules is MANAGEMENT, never GENERAL_CHAT.
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
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
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
        route = self._route_from_response(raw_llm_response)
        if self._attachment_management_request(user_request, source_message["files"]):
            route = "MANAGEMENT"

        logger.info(
            raw_llm_response,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "02_LLM_ROUTE_RESPONSE",
                    "INFO",
                    "CLASSIFY",
                    "PASS",
                    0,
                    raw_llm_response,
                ]
            },
        )

        payload = {
            "route": route,
            "user_request": user_request,
            "resolved_user_request": user_request,
            "is_follow_up": False,
            "confirmation": "NOT_REQUIRED",
            "clarification_required": False,
            "clarification_message": "",
            "should_execute": route == "JOB_EXECUTION",
            "execution_scope": "unknown",
            "requested_domain": "UNKNOWN",
            "target_filter": {
                "map_ids": [],
                "sql_seqs": [],
                "sql_ids": [],
                "space_nms": [],
            },
            # Top-level copies make the attachment contract explicit for 04.
            "session_id": source_message["session_id"],
            "context_id": source_message["context_id"],
            "files": source_message["files"],
            "message_data": source_message["data"],
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
        response = llm.invoke(
            [
                SystemMessage(content=ROUTE_CLASSIFIER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {"user_request": user_request, "files": files},
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            ]
        )
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
