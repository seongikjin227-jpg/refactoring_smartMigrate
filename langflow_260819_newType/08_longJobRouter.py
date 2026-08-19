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


DEFAULT_ROUTING_PROMPT = """You are a Langflow long-running job router.

Decide which pipeline should handle the user's request by looking at BOTH:
1. user_request
2. pending_jobs

Return JSON only:
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|NO_RUNNABLE_JOB|NEED_MORE_INFO",
  "selected_job": {},
  "run_all_pending": true,
  "reason": "short reason"
}

Rules:
- MIG means DB migration work.
- SQL_CONVERSION means SQL conversion or SQL migration query conversion work.
- SQL_TUNING means performance tuning work.
- SQL_FORMATTING means SQL formatting/style work.
- Long-running execution always means running all pending jobs for the selected domain.
- For MIG, SQL_CONVERSION, SQL_TUNING, and SQL_FORMATTING, set run_all_pending=true.
- If no matching pending job exists, use NO_RUNNABLE_JOB.
- If the request is too ambiguous and pending jobs do not make the target clear, use NEED_MORE_INFO.
- selected_job should be {} because single-job execution belongs to the Fast Status flow, not Long Job.
"""


class NewType08LongJobRouter(Component):
    display_name = "08 Long Job LLM Router"
    description = "Routes long-running work by combining user request and pending job context."
    name = "NewType08LongJobRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(
            name="routing_prompt",
            display_name="Routing Prompt",
            value=DEFAULT_ROUTING_PROMPT,
            required=True,
        ),
        StrInput(
            name="router_mode",
            display_name="Router Mode",
            value="LLM",
            required=False,
            info="LLM uses OpenAI-compatible chat completions. RULE uses deterministic fallback routing.",
        ),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="http://localhost:11434/v1", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="gpt-4o-mini", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=400, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=30, required=False),
    ]

    outputs = [
        Output(display_name="MIG Job", name="mig_job", method="mig_response", group_outputs=True),
        Output(display_name="SQL Conversion Job", name="sql_conversion_job", method="sql_conversion_response", group_outputs=True),
        Output(display_name="SQL Tuning Job", name="sql_tuning_job", method="sql_tuning_response", group_outputs=True),
        Output(display_name="SQL Formatting Job", name="sql_formatting_job", method="sql_formatting_response", group_outputs=True),
        Output(display_name="No Runnable Job", name="no_runnable_job", method="no_runnable_response", group_outputs=True),
        Output(display_name="Need More Info", name="need_more_info", method="need_more_info_response", group_outputs=True),
    ]

    def mig_response(self) -> Data:
        return self._route_output("MIG", "mig_job")

    def sql_conversion_response(self) -> Data:
        return self._route_output("SQL_CONVERSION", "sql_conversion_job")

    def sql_tuning_response(self) -> Data:
        return self._route_output("SQL_TUNING", "sql_tuning_job")

    def sql_formatting_response(self) -> Data:
        return self._route_output("SQL_FORMATTING", "sql_formatting_job")

    def no_runnable_response(self) -> Data:
        return self._route_output("NO_RUNNABLE_JOB", "no_runnable_job")

    def need_more_info_response(self) -> Data:
        return self._route_output("NEED_MORE_INFO", "need_more_info")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        try:
            routed = self._get_routed_payload()
            if routed.get("job_route") != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "08_longJobRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached

        payload = self._parse_payload(getattr(self, "payload_json", ""))
        if not payload.get("should_execute", True):
            decision = {
                "job_route": "NO_RUNNABLE_JOB",
                "selected_job": {},
                "run_all_pending": False,
                "reason": "Execution flag is false.",
            }
        else:
            decision = self._route_with_llm(payload) if self._use_llm() else self._route_with_rules(payload)

        decision = self._normalize_decision(decision, payload)
        routed = {
            **payload,
            "component": "08_longJobRouter",
            "job_route": decision["job_route"],
            "selected_job": decision.get("selected_job") or {},
            "run_all_pending": bool(decision.get("run_all_pending")),
            "routing_reason": decision.get("reason") or "",
        }
        routed.setdefault("history", []).append(
            {
                "step": "long_job_route",
                "message": f"job_route={routed['job_route']}, run_all_pending={routed['run_all_pending']}",
            }
        )
        self._cached_routed_payload = routed
        return routed

    def _use_llm(self) -> bool:
        return str(getattr(self, "router_mode", "LLM") or "LLM").strip().upper() == "LLM"

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_request = payload.get("user_request") or payload.get("input") or ""
        request_body = {
            "model": str(getattr(self, "llm_model", "") or "").strip(),
            "messages": [
                {"role": "system", "content": str(getattr(self, "routing_prompt", DEFAULT_ROUTING_PROMPT) or DEFAULT_ROUTING_PROMPT)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_request": user_request,
                            "pending_jobs": payload.get("pending_jobs") or {},
                            "pending_summary": payload.get("pending_summary") or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 400),
        }
        base_url = str(getattr(self, "llm_base_url", "") or "").rstrip("/")
        if not base_url:
            return self._route_with_rules(payload)

        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._secret_to_str(getattr(self, 'llm_api_key', None))}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 30)) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return self._route_with_rules(payload)

        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    def _route_with_rules(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("user_request") or payload.get("input") or "").lower()
        jobs = payload.get("pending_jobs") or {}
        migration_jobs = list(jobs.get("migration_jobs") or [])
        conversion_jobs = list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [])
        tuning_jobs = list(jobs.get("sql_tuning_jobs") or [])
        formatting_jobs = list(jobs.get("sql_formatting_jobs") or jobs.get("formatting_jobs") or [])
        if re.search(r"(tuning|튜닝|성능|performance)", text):
            return self._decision("SQL_TUNING", tuning_jobs)
        if re.search(r"(format|formatting|포맷|포매팅|정렬)", text):
            return self._decision("SQL_FORMATTING", formatting_jobs)
        if re.search(r"(conversion|convert|변환|sql)", text) and not re.search(r"(migration|마이그레이션|mig|db)", text):
            return self._decision("SQL_CONVERSION", conversion_jobs)
        if re.search(r"(migration|마이그레이션|mig|db|대기|pending|작업|실행|run|start|처리)", text):
            if migration_jobs:
                return self._decision("MIG", migration_jobs)
            if conversion_jobs:
                return self._decision("SQL_CONVERSION", conversion_jobs)

        return {"job_route": "NO_RUNNABLE_JOB", "selected_job": {}, "run_all_pending": False, "reason": "No matching pending job found."}

    def _decision(self, route: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        if not jobs:
            return {"job_route": "NO_RUNNABLE_JOB", "selected_job": {}, "run_all_pending": False, "reason": f"No pending {route} jobs."}
        return {"job_route": route, "selected_job": {}, "run_all_pending": True, "reason": f"Run all pending {route} jobs."}

    def _normalize_decision(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        route = str(decision.get("job_route") or "").upper()
        allowed = {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "NO_RUNNABLE_JOB", "NEED_MORE_INFO"}
        route = route if route in allowed else "NEED_MORE_INFO"
        selected = {}
        run_all = bool(decision.get("run_all_pending"))

        if route in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            selected = {}
            run_all = True
            fallback = self._route_with_rules(payload)
            if fallback.get("job_route") == "NO_RUNNABLE_JOB":
                route = "NO_RUNNABLE_JOB"
                run_all = False

        return {
            "job_route": route,
            "selected_job": selected,
            "run_all_pending": run_all,
            "reason": str(decision.get("reason") or ""),
        }

    def _next_node(self, route: str) -> str:
        if route == "MIG":
            return "09_dbMigrationAgent"
        if route == "SQL_CONVERSION":
            return "11_sqlPipelineStub"
        return "13_finalSummary"

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
