from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message


class ChatAgent(Component):
    display_name = "SmartMigrate Chat Agent"
    description = "Routes user chat into an answer and an optional Supervisor command."
    name = "SmartMigrateChatAgent"
    icon = "MessagesSquare"

    inputs = [
        MessageTextInput(
            name="chat_text",
            display_name="Chat Text",
            required=True,
            info="User chat message from Langflow Chat Input.",
        ),
        StrInput(
            name="llm_base_url",
            display_name="LLM Base URL",
            required=False,
            info="OpenAI-compatible base URL. Leave blank to use OPENAI_BASE_URL/LLM_BASE_URL or OpenAI default.",
        ),
        SecretStrInput(
            name="llm_api_key",
            display_name="LLM API Key",
            required=False,
        ),
        StrInput(
            name="llm_model",
            display_name="LLM Model",
            value="claude-haiku-4-5-20251001",
            required=False,
        ),
        IntInput(
            name="llm_max_tokens",
            display_name="LLM Max Tokens",
            value=1024,
            required=False,
        ),
        IntInput(
            name="llm_timeout_seconds",
            display_name="LLM Timeout Seconds",
            value=60,
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="Answer", name="answer", method="answer_text", types=["Message"], group_outputs=True),
        Output(display_name="Supervisor Command", name="supervisor_command", method="supervisor_command", types=["Message"], group_outputs=True),
        Output(display_name="Result JSON", name="result", method="route_chat", group_outputs=True),
    ]

    def route_chat(self) -> Data:
        result = self._route()
        self.status = result
        return Data(data=result)

    def answer_text(self) -> Message:
        return Message(text=str(self._route().get("answer_text") or ""))

    def supervisor_command(self) -> Message:
        return Message(text=str(self._route().get("supervisor_command") or ""))

    def _route(self) -> dict[str, Any]:
        text = str(getattr(self, "chat_text", "") or "").strip()
        if not text:
            return {
                "ok": False,
                "intent": "answer_only",
                "answer_text": "입력된 메시지가 없습니다.",
                "supervisor_command": "",
                "reason": "empty_input",
            }

        parsed_json = self._parse_json(text)
        if parsed_json:
            return self._route_json(parsed_json, text)

        llm_result = self._route_with_llm(text)
        if llm_result:
            return llm_result

        target = self._extract_target(text)
        wants_run = self._looks_like_run_request(text)
        wants_query = self._looks_like_question_or_lookup(text)

        if target.get("map_id") is not None and wants_run:
            command = f"run_data_migration map_id={target['map_id']}"
            return {
                "ok": True,
                "intent": "supervisor_command",
                "answer_text": f"map_id={target['map_id']} 마이그레이션 실행 요청을 전달했습니다.",
                "supervisor_command": command,
                "target": target,
                "reason": "run_request_with_map_id",
            }

        if target.get("sql_id") and wants_run:
            action = self._sql_action(text)
            if action != "run_sql_conversion":
                return {
                    "ok": True,
                    "intent": "answer_only",
                    "answer_text": (
                        "현재 Langflow Supervisor_Agent는 SQL conversion 실행만 직접 받을 수 있습니다. "
                        "튜닝/포맷팅 요청은 conversion 성공 후 batch 흐름에서 처리되도록 두는 구성이 안전합니다."
                    ),
                    "supervisor_command": "",
                    "target": target,
                    "reason": "unsupported_direct_sql_action",
                }
            command = f"{action} sql_id={target['sql_id']}"
            if target.get("space_nm"):
                command += f" space_nm={target['space_nm']}"
            action_label = {
                "run_sql_conversion": "SQL 변환",
                "run_sql_tuning": "SQL 튜닝",
                "run_sql_formatting": "SQL 포맷팅",
            }.get(action, "SQL 작업")
            return {
                "ok": True,
                "intent": "supervisor_command",
                "answer_text": f"sql_id={target['sql_id']} {action_label} 실행 요청을 전달했습니다.",
                "supervisor_command": command,
                "target": target,
                "reason": "run_request_with_sql_id",
            }

        if wants_query:
            return {
                "ok": True,
                "intent": "answer_only",
                "answer_text": (
                    "이 메시지는 조회/질문으로 판단했습니다. "
                    "Supervisor에는 실행 명령을 전달하지 않습니다."
                ),
                "supervisor_command": "",
                "target": target,
                "reason": "question_or_lookup",
            }

        return {
            "ok": True,
            "intent": "answer_only",
            "answer_text": (
                "실행할 작업을 특정하지 못했습니다. "
                "예: map_id=101 마이그레이션 실행, sql_id=SEL_001 변환 실행"
            ),
            "supervisor_command": "",
            "target": target,
            "reason": "no_runnable_target",
        }

    def _route_with_llm(self, text: str) -> dict[str, Any] | None:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)) or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        model = str(getattr(self, "llm_model", "") or os.getenv("LLM_MODEL", "")).strip()
        if not api_key or not model:
            return None

        base_url = str(getattr(self, "llm_base_url", "") or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").strip()
        endpoint = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": max(128, int(getattr(self, "llm_max_tokens", None) or 1024)),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You route SmartMigrate user chat. Return JSON only. "
                        "Use intent=answer_only for questions/status/analysis. "
                        "Use intent=supervisor_command only when the user clearly asks to run/retry/execute a job. "
                        "Allowed supervisor commands are only: "
                        "run_data_migration map_id=<number>, "
                        "run_sql_conversion sql_id=<id> [space_nm=<namespace>]. "
                        "Do not emit tuning or formatting commands. "
                        "Schema: {\"intent\":\"answer_only|supervisor_command\","
                        "\"answer_text\":\"short Korean response\","
                        "\"supervisor_command\":\"\" or command string,"
                        "\"reason\":\"short reason\"}."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(5, int(getattr(self, "llm_timeout_seconds", None) or 60)),
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = str(data["choices"][0]["message"].get("content") or "").strip()
            parsed = self._parse_json(content)
            if not parsed:
                return None
            routed = self._normalize_llm_route(parsed)
            routed["source"] = "llm"
            return routed
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, ValueError):
            return None

    def _normalize_llm_route(self, parsed: dict[str, Any]) -> dict[str, Any]:
        intent = str(parsed.get("intent") or "answer_only").strip()
        command = str(parsed.get("supervisor_command") or "").strip()
        answer = str(parsed.get("answer_text") or "").strip()
        reason = str(parsed.get("reason") or "").strip()

        if intent == "supervisor_command" and self._is_allowed_supervisor_command(command):
            return {
                "ok": True,
                "intent": "supervisor_command",
                "answer_text": answer or "Supervisor 실행 요청을 전달했습니다.",
                "supervisor_command": command,
                "reason": reason or "llm_supervisor_command",
            }

        return {
            "ok": True,
            "intent": "answer_only",
            "answer_text": answer or "Supervisor 실행 명령은 없습니다.",
            "supervisor_command": "",
            "reason": reason or "llm_answer_only",
        }

    def _is_allowed_supervisor_command(self, command: str) -> bool:
        return bool(
            re.fullmatch(r"run_data_migration\s+map_id=\d+", command.strip(), flags=re.IGNORECASE)
            or re.fullmatch(
                r"run_sql_conversion\s+sql_id=[A-Za-z0-9_$#.\-]+(?:\s+space_nm=[A-Za-z0-9_$#.\-/]+)?",
                command.strip(),
                flags=re.IGNORECASE,
            )
        )

    def _route_json(self, command: dict[str, Any], original_text: str) -> dict[str, Any]:
        action = str(command.get("action") or command.get("command") or "").strip()
        map_id = command.get("map_id")
        sql_id = str(command.get("sql_id") or "").strip()
        space_nm = str(command.get("space_nm") or command.get("namespace") or "").strip()

        if action in {"run_data_migration", "data_migration", "migration"} and map_id is not None:
            supervisor_command = f"run_data_migration map_id={int(map_id)}"
            return {
                "ok": True,
                "intent": "supervisor_command",
                "answer_text": f"map_id={int(map_id)} 마이그레이션 실행 요청을 전달했습니다.",
                "supervisor_command": supervisor_command,
                "source": "json",
            }

        if action in {"run_sql_conversion", "sql_conversion", "conversion"} and sql_id:
            supervisor_command = f"run_sql_conversion sql_id={sql_id}"
            if space_nm:
                supervisor_command += f" space_nm={space_nm}"
            return {
                "ok": True,
                "intent": "supervisor_command",
                "answer_text": f"sql_id={sql_id} SQL 변환 실행 요청을 전달했습니다.",
                "supervisor_command": supervisor_command,
                "source": "json",
            }

        if action in {"answer_only", "none", "noop", "no_op"}:
            return {
                "ok": True,
                "intent": "answer_only",
                "answer_text": str(command.get("answer_text") or "Supervisor 실행 명령은 없습니다."),
                "supervisor_command": "",
                "source": "json",
            }

        return {
            "ok": False,
            "intent": "answer_only",
            "answer_text": f"지원하지 않는 작업 JSON입니다: {original_text}",
            "supervisor_command": "",
            "source": "json",
            "reason": "unsupported_json_action",
        }

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        if not stripped.startswith("{"):
            return None
        try:
            parsed = json.loads(stripped)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _extract_target(self, text: str) -> dict[str, Any]:
        target: dict[str, Any] = {}

        map_match = re.search(r"\bmap[\s_-]*id\s*[:=]?\s*(\d+)\b", text, flags=re.IGNORECASE)
        if not map_match:
            map_match = re.search(r"\bmap\s*[:=]?\s*(\d+)\b", text, flags=re.IGNORECASE)
        if map_match:
            target["map_id"] = int(map_match.group(1))

        sql_match = re.search(
            r"\bsql[\s_-]*id\s*[:=]?\s*([A-Za-z0-9_$#.\-]+)",
            text,
            flags=re.IGNORECASE,
        )
        if sql_match:
            target["sql_id"] = sql_match.group(1)

        space_match = re.search(
            r"\b(?:space[\s_-]*nm|namespace)\s*[:=]?\s*([A-Za-z0-9_$#.\-/]+)",
            text,
            flags=re.IGNORECASE,
        )
        if space_match:
            target["space_nm"] = space_match.group(1)

        return target

    def _looks_like_run_request(self, text: str) -> bool:
        lowered = text.lower()
        keywords = [
            "run",
            "rerun",
            "retry",
            "execute",
            "start",
            "convert",
            "conversion",
            "migration",
            "migrate",
            "tuning",
            "formatting",
            "실행",
            "돌려",
            "돌려줘",
            "재실행",
            "다시",
            "변환",
            "마이그레이션",
            "이관",
            "튜닝",
            "포맷",
        ]
        return any(keyword in lowered for keyword in keywords)

    def _looks_like_question_or_lookup(self, text: str) -> bool:
        lowered = text.lower()
        keywords = [
            "?",
            "what",
            "why",
            "how",
            "show",
            "list",
            "status",
            "summary",
            "count",
            "조회",
            "보여",
            "알려",
            "현황",
            "상태",
            "몇",
            "왜",
            "원인",
            "분석",
        ]
        return any(keyword in lowered for keyword in keywords)

    def _sql_action(self, text: str) -> str:
        lowered = text.lower()
        if "튜닝" in lowered or "tuning" in lowered or "tune" in lowered:
            return "run_sql_tuning"
        if "포맷" in lowered or "formatting" in lowered or "format" in lowered:
            return "run_sql_formatting"
        return "run_sql_conversion"

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
