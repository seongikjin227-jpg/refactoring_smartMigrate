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


DEFAULT_ROUTING_PROMPT = """You are a SmartMigrate long-running job router. Return JSON only.

Long Job never runs a single job. It runs all pending jobs for one domain.

Routes:
- MIG: run all pending DB Migration jobs.
- SQL_CONVERSION: run all pending SQL Conversion jobs.
- SQL_TUNING: run all pending SQL Tuning jobs.
- SQL_FORMATTING: run all pending SQL Formatting jobs.
- PREREQUISITE_BLOCKED: user requested a later phase but earlier pending work remains.
- NO_RUNNABLE_JOB: no runnable pending work exists for the request.

Prerequisite rules:
- SQL_CONVERSION requires no pending MIG jobs.
- SQL_TUNING requires no pending MIG or SQL_CONVERSION jobs.
- SQL_FORMATTING requires no pending MIG, SQL_CONVERSION, or SQL_TUNING jobs.

Return JSON:
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|PREREQUISITE_BLOCKED|NO_RUNNABLE_JOB",
  "run_all_pending": true,
  "blocker_route": "",
  "reason": "short reason"
}
"""


class NewType08LongJobRouter(Component):
    display_name = "08 Long Job LLM Router"
    description = "Routes all-pending long jobs and blocks later phases when prerequisites remain."
    name = "NewType08LongJobRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(name="routing_prompt", display_name="Routing Prompt", value=DEFAULT_ROUTING_PROMPT, required=False),
        StrInput(name="router_mode", display_name="Router Mode", value="LLM", required=False),
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
        Output(display_name="Prerequisite Blocked", name="prerequisite_blocked", method="prerequisite_blocked_response", group_outputs=True),
        Output(display_name="No Runnable Job", name="no_runnable_job", method="no_runnable_response", group_outputs=True),
    ]

    def mig_response(self) -> Data:
        return self._route_output("MIG", "mig_job")

    def sql_conversion_response(self) -> Data:
        return self._route_output("SQL_CONVERSION", "sql_conversion_job")

    def sql_tuning_response(self) -> Data:
        return self._route_output("SQL_TUNING", "sql_tuning_job")

    def sql_formatting_response(self) -> Data:
        return self._route_output("SQL_FORMATTING", "sql_formatting_job")

    def prerequisite_blocked_response(self) -> Data:
        return self._route_output("PREREQUISITE_BLOCKED", "prerequisite_blocked")

    def no_runnable_response(self) -> Data:
        return self._route_output("NO_RUNNABLE_JOB", "no_runnable_job")

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
        decision = self._route_with_rules(payload)
        if self._use_llm() and decision["job_route"] not in {"PREREQUISITE_BLOCKED", "NO_RUNNABLE_JOB"}:
            llm_decision = self._route_with_llm(payload)
            decision = self._normalize_decision(llm_decision, payload, fallback=decision)
        else:
            decision = self._normalize_decision(decision, payload)

        routed = {
            **payload,
            "component": "08_longJobRouter",
            "job_route": decision["job_route"],
            "selected_job": {},
            "run_all_pending": bool(decision.get("run_all_pending")),
            "blocker_route": decision.get("blocker_route") or "",
            "routing_reason": decision.get("reason") or "",
        }
        routed.setdefault("history", []).append(
            {
                "step": "long_job_route",
                "message": f"job_route={routed['job_route']}, blocker={routed['blocker_route']}",
            }
        )
        self._cached_routed_payload = routed
        return routed

    def _use_llm(self) -> bool:
        return str(getattr(self, "router_mode", "LLM") or "LLM").strip().upper() == "LLM"

    def _route_with_rules(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = self._requested_route(str(payload.get("user_request") or payload.get("input") or ""))
        jobs = payload.get("pending_jobs") or {}
        counts = {
            "MIG": len(jobs.get("migration_jobs") or []),
            "SQL_CONVERSION": len(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or []),
            "SQL_TUNING": len(jobs.get("sql_tuning_jobs") or []),
            "SQL_FORMATTING": len(jobs.get("sql_formatting_jobs") or []),
        }

        if requested is None:
            requested = self._first_available_route(counts)
        if requested is None:
            return self._decision("NO_RUNNABLE_JOB", False, "", "No runnable pending jobs found.")

        blocker = self._blocking_route(requested, counts)
        if blocker:
            return self._decision(
                "PREREQUISITE_BLOCKED",
                False,
                blocker,
                f"{requested} cannot start because pending {blocker} jobs remain.",
            )
        if counts.get(requested, 0) <= 0:
            return self._decision("NO_RUNNABLE_JOB", False, "", f"No pending {requested} jobs found.")
        return self._decision(requested, True, "", f"Run all pending {requested} jobs.")

    def _requested_route(self, text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"(tuning|튜닝|성능|performance)", lowered):
            return "SQL_TUNING"
        if re.search(r"(format|formatting|포맷|포매팅|정렬)", lowered):
            return "SQL_FORMATTING"
        if re.search(r"(conversion|convert|변환|sql\s*conversion)", lowered):
            return "SQL_CONVERSION"
        if re.search(r"(migration|마이그레이션|mig|db)", lowered):
            return "MIG"
        return None

    def _first_available_route(self, counts: dict[str, int]) -> str | None:
        for route in ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"):
            if counts.get(route, 0) > 0:
                return route
        return None

    def _blocking_route(self, requested: str, counts: dict[str, int]) -> str:
        order = ["MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"]
        if requested not in order:
            return ""
        for route in order[: order.index(requested)]:
            if counts.get(route, 0) > 0:
                return route
        return ""

    def _decision(self, route: str, run_all: bool, blocker: str, reason: str) -> dict[str, Any]:
        return {"job_route": route, "selected_job": {}, "run_all_pending": run_all, "blocker_route": blocker, "reason": reason}

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = str(getattr(self, "llm_base_url", "") or "").rstrip("/")
        if not base_url:
            return self._route_with_rules(payload)
        body = {
            "model": str(getattr(self, "llm_model", "") or "").strip(),
            "messages": [
                {"role": "system", "content": str(getattr(self, "routing_prompt", "") or DEFAULT_ROUTING_PROMPT)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_request": payload.get("user_request") or "",
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
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._secret_to_str(getattr(self, 'llm_api_key', None))}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 30)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return self._route_with_rules(payload)
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    def _normalize_decision(self, decision: dict[str, Any], payload: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        route = str(decision.get("job_route") or "").upper()
        allowed = {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "PREREQUISITE_BLOCKED", "NO_RUNNABLE_JOB"}
        if route not in allowed:
            return fallback or self._route_with_rules(payload)
        rules = self._route_with_rules(payload)
        if rules["job_route"] in {"PREREQUISITE_BLOCKED", "NO_RUNNABLE_JOB"}:
            return rules
        if route in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            return {**decision, "job_route": route, "run_all_pending": True, "selected_job": {}, "blocker_route": ""}
        return decision

    def _next_node(self, route: str) -> str:
        if route == "MIG":
            return "09_dbMigrationAgent"
        if route == "SQL_CONVERSION":
            return "11_sqlConversionAgent"
        if route == "SQL_TUNING":
            return "14_sqlTuningAgent"
        if route == "SQL_FORMATTING":
            return "16_sqlFormattingAgent"
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
