from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


CLASSIFIER_PROMPT = """You are a SmartMigrate intent classifier. Return JSON only.

Routes:
- GENERAL_CHAT: normal conversation, explanation request, concept question, or non-operational help.
- FAST_STATUS: fast DB status/control requests, status summary, counts, failure report, dashboard-like request, or a request to run/control one specific job by map_id/sql_id/row_id/priority/status.
- LONG_RUNNING_JOB: run/execute/process ALL pending jobs for a domain. This route is only for full pending execution, for example all DB migration, all SQL conversion, all SQL tuning, or all SQL formatting.

Important routing policy:
- Single-job execution is NOT LONG_RUNNING_JOB.
- If the user says one job, single job, this map_id, specific sql_id, priority N, only this, or similar, route FAST_STATUS.
- FAST_STATUS may update status/priority/USE_YN so that a later full Long Job run includes only the intended target.
- LONG_RUNNING_JOB never selects a single job. It always means run_all_pending=true for the selected domain.
- If the user says "대기 작업 실행해줘" or "pending jobs run" without a specific ID, route LONG_RUNNING_JOB.

Required JSON schema:
{
  "route": "GENERAL_CHAT|FAST_STATUS|LONG_RUNNING_JOB",
  "task_type": "CHAT|STATUS|JOB_EXECUTION",
  "expected_latency": "FAST|LONG",
  "needs_pending_jobs": true,
  "needs_llm_answer": false,
  "reason": "short reason"
}
"""


class NewType01LlmClassifier(Component):
    display_name = "01 LLM Classifier"
    description = "Classifies user intent for routing with an OpenAI-compatible chat completion LLM."
    name = "NewType01LlmClassifier"
    icon = "BrainCircuit"

    inputs = [
        MessageTextInput(
            name="user_request",
            display_name="User Request",
            required=True,
            info="Connect Chat Input directly here.",
        ),
        MessageTextInput(
            name="classifier_prompt",
            display_name="Classifier Prompt",
            required=False,
            value=CLASSIFIER_PROMPT,
            info="System prompt for LLM classification. Keep it short; do not include migration/sql generation prompts.",
        ),
        StrInput(
            name="classifier_mode",
            display_name="Classifier Mode",
            value="LLM",
            required=False,
            info="LLM or RULE_POC. LLM calls an OpenAI-compatible /chat/completions endpoint.",
        ),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="https://api.openai.com/v1", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="gpt-4.1-mini", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=512, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=30, required=False),
        StrInput(
            name="fallback_to_rules",
            display_name="Fallback To Rules",
            value="Y",
            required=False,
            info="Y: use rule classifier if LLM config/call fails. N: return error.",
        ),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="classify")]

    def classify(self) -> Data:
        try:
            text = self._read_user_request()
            payload = {"ok": True, "user_request": text, "history": []}
            classification = self._classify(text)
            payload.update(
                {
                    "component": "01_llmClassifier",
                    "classification": classification,
                    "route": classification["route"],
                    "expected_latency": classification["expected_latency"],
                    "next_node": "02_intentRouter",
                }
            )
            payload.setdefault("history", []).append(
                {
                    "step": "llm_classifier",
                    "message": f"route={classification['route']}, latency={classification['expected_latency']}",
                }
            )
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "01_llmClassifier", "error": str(exc)}
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

    def _classify(self, text: str) -> dict[str, Any]:
        mode = str(getattr(self, "classifier_mode", "") or "LLM").strip().upper()
        if mode == "RULE_POC":
            return self._classify_by_rule(text)
        try:
            return self._classify_by_llm(text)
        except Exception:
            if self._as_bool(getattr(self, "fallback_to_rules", "Y")):
                result = self._classify_by_rule(text)
                result["fallback"] = "RULE_POC"
                return result
            raise

    def _classify_by_llm(self, text: str) -> dict[str, Any]:
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        if not api_key:
            raise ValueError("llm_api_key is required when classifier_mode=LLM")
        if not model:
            raise ValueError("llm_model is required when classifier_mode=LLM")

        system_prompt = str(getattr(self, "classifier_prompt", "") or CLASSIFIER_PROMPT).strip()
        user_prompt = (
            "Classify this user request for SmartMigrate routing.\n"
            f"User request:\n{text}\n\n"
            "Return only the required JSON object."
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 512),
        }
        base_url = str(getattr(self, "llm_base_url", "") or "https://api.openai.com/v1").strip().rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        timeout = int(getattr(self, "llm_timeout_seconds", None) or 30)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"LLM classifier HTTP {exc.code}: {detail[:1000]}") from exc
        data = json.loads(body)
        content = str(data["choices"][0]["message"].get("content") or "").strip()
        parsed = self._parse_llm_json(content)
        return self._normalize_classification(parsed)

    def _parse_llm_json(self, text: str) -> dict[str, Any]:
        value = text.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
            value = re.sub(r"\s*```$", "", value)
        match = re.search(r"\{.*\}", value, flags=re.S)
        if match:
            value = match.group(0)
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("LLM classifier must return a JSON object")
        return parsed

    def _normalize_classification(self, value: dict[str, Any]) -> dict[str, Any]:
        route = str(value.get("route") or "GENERAL_CHAT").strip().upper()
        allowed = {"GENERAL_CHAT", "FAST_STATUS", "LONG_RUNNING_JOB"}
        if route not in allowed:
            route = "GENERAL_CHAT"
        defaults = self._defaults_for_route(route)
        return {
            **defaults,
            **value,
            "route": route,
            "expected_latency": str(value.get("expected_latency") or defaults["expected_latency"]).upper(),
            "needs_pending_jobs": bool(value.get("needs_pending_jobs", defaults["needs_pending_jobs"])),
            "needs_llm_answer": bool(value.get("needs_llm_answer", defaults["needs_llm_answer"])),
        }

    def _defaults_for_route(self, route: str) -> dict[str, Any]:
        if route == "LONG_RUNNING_JOB":
            return {
                "task_type": "JOB_EXECUTION",
                "expected_latency": "LONG",
                "needs_pending_jobs": True,
                "needs_llm_answer": False,
                "reason": "",
            }
        if route == "FAST_STATUS":
            return {
                "task_type": "STATUS",
                "expected_latency": "FAST",
                "needs_pending_jobs": False,
                "needs_llm_answer": False,
                "reason": "",
            }
        return {
            "task_type": "CHAT",
            "expected_latency": "FAST",
            "needs_pending_jobs": False,
            "needs_llm_answer": True,
            "reason": "",
        }

    def _classify_by_rule(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        if self._is_single_job_control(lowered):
            return {
                "route": "FAST_STATUS",
                "task_type": "STATUS",
                "expected_latency": "FAST",
                "needs_pending_jobs": False,
                "needs_llm_answer": False,
                "reason": "single-job execution/control belongs to fast status control flow",
            }
        if re.search(r"(status|상태|현황|요약|몇\s*건|건수|카운트|dashboard|fail|실패|조회|priority|우선순위|use_yn)", lowered):
            return {
                "route": "FAST_STATUS",
                "task_type": "STATUS",
                "expected_latency": "FAST",
                "needs_pending_jobs": False,
                "needs_llm_answer": False,
                "reason": "status/dashboard/control keyword",
            }
        if self._is_all_pending_execution(lowered):
            return {
                "route": "LONG_RUNNING_JOB",
                "task_type": "JOB_EXECUTION",
                "expected_latency": "LONG",
                "needs_pending_jobs": True,
                "needs_llm_answer": False,
                "reason": "all-pending execution request",
            }
        return {
            "route": "GENERAL_CHAT",
            "task_type": "CHAT",
            "expected_latency": "FAST",
            "needs_pending_jobs": False,
            "needs_llm_answer": True,
            "reason": "general conversation",
        }

    def _is_single_job_control(self, text: str) -> bool:
        single_terms = r"(단건|한\s*건|1\s*건|하나만|하나\s*만|특정|지정|only\s+this|single|one\s+job|this\s+job)"
        id_terms = r"(map_id|mapid|sql_id|sqlid|row_id|rowid|id\s*[=:]?\s*\d+|map\s*[=:]?\s*\d+)"
        control_terms = r"(실행|run|start|처리|priority|우선순위|status|상태|use_yn|대상)"
        if re.search(single_terms, text, flags=re.I):
            return True
        return bool(re.search(id_terms, text, flags=re.I) and re.search(control_terms, text, flags=re.I))

    def _is_all_pending_execution(self, text: str) -> bool:
        all_terms = r"(전체|모든|전부|끝까지|일괄|배치|all|every|all\s+pending|run\s+all|process\s+all)"
        domain_terms = r"(db\s*migration|migration|마이그레이션|mig|sql\s*conversion|sql\s*tuning|튜닝|sql\s*formatting|포맷|포매팅|pending|대기\s*작업)"
        execution_terms = r"(실행|진행|처리|run|start|process)"
        if re.search(all_terms, text, flags=re.I) and re.search(domain_terms, text, flags=re.I):
            return True
        return bool(re.search(r"(대기\s*작업|pending\s*jobs?)", text, flags=re.I) and re.search(execution_terms, text, flags=re.I))

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}
