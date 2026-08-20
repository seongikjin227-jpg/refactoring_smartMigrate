from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


CLASSIFIER_PROMPT = """당신은 SmartMigrate의 1차 요청 분류기입니다. 반드시 JSON 객체만 반환하세요.

분류 route:
- GENERAL_CHAT: 일반 대화, 설명 요청, 개념 질문, 작업 실행/관리와 무관한 도움 요청.
- MANAGEMENT: Dashboard/상태/건수/실패 현황/작업 대상 조회, priority/status/USE_YN 변경, Correct SQL 입력 같은 관리 기능.
- JOB_EXECUTION: 실제 작업 실행 요청. DB Migration, SQL Conversion, SQL Tuning, SQL Formatting의 전체 pending 실행과 map_id/sql_id/space_nm 기반 단건 또는 복수건 실행을 포함합니다.

중요 규칙:
- 사용자가 "map_id=101 실행", "sql_id=Q001 변환", "space_nm=SALES 튜닝", "sql_id=Q002 포맷팅"처럼 특정 작업 실행을 요청하면 JOB_EXECUTION입니다.
- 사용자가 "대기 작업 실행", "전체 DB Migration 진행", "모든 SQL Conversion 실행", "SQL Tuning 전체 실행", "SQL Formatting 전체 진행"처럼 말하면 JOB_EXECUTION입니다.
- 사용자가 "SQL Conversion 작업 대상 조회", "SQL Tuning 대상 보여줘", "Formatting 대기 작업 몇 건이야", "DB Migration 대상 목록"처럼 조회를 요청하면 MANAGEMENT입니다.
- 사용자가 priority/status/USE_YN 변경, 제외/포함, Correct SQL 저장을 요청하면 MANAGEMENT입니다.
- 빠른 단순 답변이나 설명은 GENERAL_CHAT입니다.

반환 JSON schema:
{
  "route": "GENERAL_CHAT|MANAGEMENT|JOB_EXECUTION",
  "task_type": "CHAT|STATUS|JOB_EXECUTION",
  "expected_latency": "FAST|LONG",
  "needs_pending_jobs": true,
  "needs_llm_answer": false,
  "reason": "짧은 한국어 사유"
}
"""


class NewType01RequestClassifier(Component):
    display_name = "01 Request Classifier"
    description = "Classifies the user request into the first-level SmartMigrate route."
    name = "NewType01RequestClassifier"
    icon = "BrainCircuit"

    inputs = [
        MessageTextInput(name="user_request", display_name="User Request", required=True),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="https://api.openai.com/v1", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=True),
        StrInput(name="llm_model", display_name="LLM Model", value="gpt-4.1-mini", required=True),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=512, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=30, required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="classify")]

    def classify(self) -> Data:
        try:
            text = self._read_user_request()
            classification = self._classify_by_llm(text)
            payload = {
                "ok": True,
                "component": "01_requestClassifier",
                "user_request": text,
                "classification": classification,
                "route": classification["route"],
                "expected_latency": classification["expected_latency"],
                "next_node": "02_intentRouter",
                "history": [
                    {
                        "step": "request_classifier",
                        "message": f"route={classification['route']}, latency={classification['expected_latency']}",
                    }
                ],
            }
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "01_requestClassifier", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _read_user_request(self) -> str:
        raw = getattr(self, "user_request", "")
        if isinstance(raw, Data):
            data = raw.data or {}
            return str(data.get("text") or data.get("message") or data.get("user_request") or data)
        if isinstance(raw, dict):
            return str(raw.get("text") or raw.get("message") or raw.get("user_request") or raw)
        if hasattr(raw, "text"):
            return str(raw.text or "")
        return str(raw or "").strip()

    def _classify_by_llm(self, text: str) -> dict[str, Any]:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not api_key:
            raise ValueError("llm_api_key is required for 01 Request Classifier")
        if not model:
            raise ValueError("llm_model is required for 01 Request Classifier")
        if not base_url:
            raise ValueError("llm_base_url is required for 01 Request Classifier")

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": f"사용자 요청:\n{text}\n\nJSON만 반환하세요."},
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 512),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(getattr(self, "llm_timeout_seconds", None) or 30)) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"01 Request Classifier LLM HTTP {exc.code}: {detail[:1000]}") from exc
        content = str(raw["choices"][0]["message"].get("content") or "").strip()
        return self._normalize_classification(self._parse_json_object(content))

    def _normalize_classification(self, value: dict[str, Any]) -> dict[str, Any]:
        route = str(value.get("route") or "").strip().upper()
        if route not in {"GENERAL_CHAT", "MANAGEMENT", "JOB_EXECUTION"}:
            raise ValueError(f"Invalid classifier route: {route}")
        defaults = {
            "GENERAL_CHAT": {"task_type": "CHAT", "expected_latency": "FAST", "needs_pending_jobs": False, "needs_llm_answer": True},
            "MANAGEMENT": {"task_type": "STATUS", "expected_latency": "FAST", "needs_pending_jobs": False, "needs_llm_answer": False},
            "JOB_EXECUTION": {"task_type": "JOB_EXECUTION", "expected_latency": "LONG", "needs_pending_jobs": True, "needs_llm_answer": False},
        }[route]
        return {
            **defaults,
            **value,
            "route": route,
            "expected_latency": str(value.get("expected_latency") or defaults["expected_latency"]).upper(),
            "needs_pending_jobs": bool(value.get("needs_pending_jobs", defaults["needs_pending_jobs"])),
            "needs_llm_answer": bool(value.get("needs_llm_answer", defaults["needs_llm_answer"])),
        }

    def _parse_json_object(self, text: str) -> dict[str, Any]:
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
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
