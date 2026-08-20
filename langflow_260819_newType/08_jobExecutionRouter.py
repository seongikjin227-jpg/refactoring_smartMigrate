from __future__ import annotations

import json
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


JOB_EXECUTION_ROUTER_PROMPT = """당신은 SmartMigrate의 작업 대상 실행 라우터입니다. 반드시 JSON 객체만 반환하세요.

역할:
- 사용자 실행 요청을 하나의 실행 도메인과 실행 모드로 분류합니다.
- 06 Get Pending Jobs가 전달한 pending_summary와 pending_job_identifiers를 참고합니다.
- 사용자 요청에 있는 map_id/sql_id/space_nm 값을 target_filter에 넣습니다.

실행 도메인:
- MIG: DB Migration 작업. 보통 map_id로 식별합니다.
- SQL_CONVERSION: SQL Conversion 작업. 보통 sql_id 또는 space_nm으로 식별합니다.
- SQL_TUNING: SQL Tuning 작업.
- SQL_FORMATTING: SQL Formatting 작업.
- PREREQUISITE_REQUIRED: 작업 대상은 있지만 선행 작업이 남아 있어 지금 실행하면 안 되는 경우.
- NO_RUNNABLE_JOB: 실행할 대상이 없다고 판단되는 경우.

실행 모드:
- all_pending: 사용자가 전체 pending/대기 작업 실행을 요청한 경우.
- targeted: 사용자가 map_id/sql_id/space_nm으로 단건 또는 복수건 대상을 지정한 경우.

반환 JSON schema:
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|PREREQUISITE_REQUIRED|NO_RUNNABLE_JOB",
  "run_mode": "all_pending|targeted",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  },
  "reason": "짧은 한국어 사유"
}

규칙:
- pending_summary의 count key는 migration_total, sql_conversion_total, sql_tuning_total, sql_formatting_total입니다.
- map_id가 있으면 job_route는 MIG, run_mode는 targeted입니다.
- sql_id 또는 space_nm이 있고 튜닝 요청이면 SQL_TUNING입니다.
- sql_id 또는 space_nm이 있고 포맷팅 요청이면 SQL_FORMATTING입니다.
- sql_id 또는 space_nm이 있고 튜닝/포맷팅이 아니면 SQL_CONVERSION입니다.
- 명시 대상이 없고 전체/대기 작업 실행 요청이면 run_mode는 all_pending입니다.
- SQL Conversion 실행 요청에서 pending_summary.migration_total이 1건 이상이면 PREREQUISITE_REQUIRED를 선택합니다.
- SQL Tuning 실행 요청에서 pending_summary.migration_total 또는 pending_summary.sql_conversion_total이 1건 이상이면 PREREQUISITE_REQUIRED를 선택합니다.
- SQL Formatting 실행 요청에서 pending_summary.migration_total, pending_summary.sql_conversion_total, pending_summary.sql_tuning_total 중 하나라도 1건 이상이면 PREREQUISITE_REQUIRED를 선택합니다.
- targeted 요청에서 사용자가 요청한 작업 대상은 있지만 priority/prior_map_id 등 06 payload에서 확인 가능한 선행 조건이 남아 있으면 PREREQUISITE_REQUIRED를 선택합니다.
- targeted 요청에서 사용자가 요청한 map_id 또는 sql_id+space_nm 조합이 pending_job_identifiers에 없으면 NO_RUNNABLE_JOB을 선택합니다.
- all_pending 요청에서 해당 도메인의 pending count가 0이면 NO_RUNNABLE_JOB을 선택합니다.
- PREREQUISITE_REQUIRED의 reason에는 어떤 선행 작업이 남았는지 사용자에게 보여줄 한국어 메시지를 작성합니다.
"""


class NewType08JobExecutionRouter(Component):
    display_name = "08 Job Target Router"
    description = "Routes job execution requests by domain and target mode: all pending or explicit map_id/sql_id/space_nm targets."
    name = "NewType08JobExecutionRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="", required=True),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=True),
        StrInput(name="llm_model", display_name="LLM Model", value="", required=True),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=1500, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=90, required=False),
    ]

    outputs = [
        Output(display_name="MIG Targets", name="mig_job", method="mig_response", group_outputs=True),
        Output(display_name="SQL Conversion Targets", name="sql_conversion_job", method="sql_conversion_response", group_outputs=True),
        Output(display_name="SQL Tuning Targets", name="sql_tuning_job", method="sql_tuning_response", group_outputs=True),
        Output(display_name="SQL Formatting Targets", name="sql_formatting_job", method="sql_formatting_response", group_outputs=True),
        Output(display_name="Prerequisite Required Message", name="prerequisite_required", method="prerequisite_required_response", group_outputs=True, types=["Message"]),
        Output(display_name="No Runnable Target Message", name="no_runnable_job", method="no_runnable_response", group_outputs=True, types=["Message"]),
    ]

    def mig_response(self) -> Data:
        # Return the MIG execution branch when selected.
        return self._route_output("MIG", "mig_job")

    def sql_conversion_response(self) -> Data:
        # Return the SQL Conversion execution branch when selected.
        return self._route_output("SQL_CONVERSION", "sql_conversion_job")

    def sql_tuning_response(self) -> Data:
        # Return the SQL Tuning execution branch when selected.
        return self._route_output("SQL_TUNING", "sql_tuning_job")

    def sql_formatting_response(self) -> Data:
        # Return the SQL Formatting execution branch when selected.
        return self._route_output("SQL_FORMATTING", "sql_formatting_job")

    def prerequisite_required_response(self) -> Message:
        # Return a message when prerequisite work must be completed first.
        return self._message_route_output("PREREQUISITE_REQUIRED", "prerequisite_required")

    def no_runnable_response(self) -> Message:
        # Return a message when no runnable target exists.
        return self._message_route_output("NO_RUNNABLE_JOB", "no_runnable_job")

    def _route_output(self, expected_route: str, output_name: str) -> Data:
        # Build a routed payload for the active output branch.
        try:
            routed = self._get_routed_payload()
            if routed.get("job_route") != expected_route:
                self.stop(output_name)
                return Data(data={})
            routed = {**routed, "selected_output": output_name, "next_node": self._next_node(expected_route)}
            self.status = routed
            return Data(data=routed)
        except Exception as exc:
            result = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _message_route_output(self, expected_route: str, output_name: str) -> Message:
        # Build a direct Message output for non-pipeline routes.
        try:
            routed = self._get_routed_payload()
            if routed.get("job_route") != expected_route:
                self.stop(output_name)
                return Message(text="")
            routed = {**routed, "selected_output": output_name, "next_node": "chat_output", "final": True}
            message = self._build_message_route_text(routed)
            self.status = {**routed, "answer_text": message}
            return Message(text=message)
        except Exception as exc:
            message = f"component=08_jobExecutionRouter\n작업 대상 라우팅 중 오류가 발생했습니다.\n오류: {exc}"
            self.status = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc), "answer_text": message}
            return Message(text=message)

    def _build_message_route_text(self, routed: dict[str, Any]) -> str:
        # Format a direct message route response.
        route = str(routed.get("job_route") or "")
        reason = str(routed.get("routing_reason") or "").strip()
        user_request = str(routed.get("user_request") or routed.get("original_request") or "").strip()
        targets = routed.get("target_filter") or {}
        target_label = self._target_label(targets) or "요청하신 작업"
        if route == "PREREQUISITE_REQUIRED":
            message = reason or f"{target_label}은 선행 작업이 남아 있어 지금 실행할 수 없습니다."
        else:
            message = reason or f"{target_label}이 작업 대상에서 조회되지 않았습니다."
        lines = [message]
        if user_request:
            lines.append(f"요청: {user_request}")
        if route == "PREREQUISITE_REQUIRED":
            lines.append("선행 작업을 먼저 완료한 뒤 다시 실행해주세요.")
        else:
            lines.append("대상 상태를 변경하거나 Dashboard에서 작업 대상을 먼저 확인해주세요.")
        return "\n".join(lines)

    def _target_label(self, targets: dict[str, Any]) -> str:
        # Build a short Korean label for requested target filters.
        map_ids = targets.get("map_ids") or []
        sql_ids = targets.get("sql_ids") or []
        space_nms = targets.get("space_nms") or []
        if map_ids:
            return f"map_id={', '.join(str(item) for item in map_ids)}"
        if sql_ids and space_nms:
            return f"space_nm={', '.join(str(item) for item in space_nms)}, sql_id={', '.join(str(item) for item in sql_ids)}"
        if sql_ids:
            return f"sql_id={', '.join(str(item) for item in sql_ids)}"
        if space_nms:
            return f"space_nm={', '.join(str(item) for item in space_nms)}"
        return ""

    def _get_routed_payload(self) -> dict[str, Any]:
        # Compute and cache the routed payload for this component.
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached

        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision_hint = self._normalize_llm_hint(self._route_with_llm(payload), payload)
        targets = decision_hint["target_filter"]
        route = decision_hint["job_route"]
        requested_run_mode = decision_hint["run_mode"]
        jobs = payload.get("pending_jobs") or {}
        counts = self._counts(jobs)

        if route is None:
            route = self._first_available_route(counts)

        if route is None:
            decision = self._empty_decision("No explicit target and no runnable pending jobs found.", targets)
        elif route == "NO_RUNNABLE_JOB":
            decision = self._empty_decision(decision_hint.get("reason") or "No runnable job target selected by LLM.", targets)
        elif route == "PREREQUISITE_REQUIRED":
            decision = self._prerequisite_decision(decision_hint.get("reason") or "선행 작업이 남아 있어 지금 실행할 수 없습니다.", targets)
        else:
            selected_jobs = self._selected_jobs_for_hint(payload, route, requested_run_mode, targets)
            if not selected_jobs:
                decision = self._empty_decision(
                    decision_hint.get("reason") or "Requested target was not found in pending job identifiers.",
                    targets,
                )
            else:
                decision = self._execution_decision(route, requested_run_mode, targets, selected_jobs)

        routed = {
            **payload,
            "component": "08_jobExecutionRouter",
            "job_route": decision["job_route"],
            "run_mode": decision["run_mode"],
            "run_all_pending": decision["run_all_pending"],
            "target_filter": decision["target_filter"],
            "selected_jobs": decision["selected_jobs"],
            "routing_reason": decision["reason"],
            "llm_job_route": decision_hint.get("job_route"),
            "llm_run_mode": decision_hint.get("run_mode"),
            "llm_target_filter": decision_hint.get("target_filter"),
        }
        routed.setdefault("history", []).append(
            {
                "step": "job_target_route",
                "message": f"job_route={routed['job_route']}, run_mode={routed['run_mode']}, count={len(routed['selected_jobs'])}, router={decision_hint['source']}",
            }
        )
        self._cached_routed_payload = routed
        return routed

    def _route_with_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Call the configured LLM to decide the route.
        api_key = self._secret_to_str(getattr(self, "llm_api_key", None)).strip()
        model = str(getattr(self, "llm_model", "") or "").strip()
        base_url = str(getattr(self, "llm_base_url", "") or "").strip().rstrip("/")
        if not api_key:
            raise ValueError("llm_api_key is required for 08 Job Target Router")
        if not model:
            raise ValueError("llm_model is required for 08 Job Target Router")
        if not base_url:
            raise ValueError("llm_base_url is required for 08 Job Target Router")

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": JOB_EXECUTION_ROUTER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_request": payload.get("user_request") or "",
                            "pending_summary": payload.get("pending_summary") or {},
                            "pending_job_identifiers": self._sample_jobs(payload),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": int(getattr(self, "llm_max_tokens", None) or 1500),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=int(getattr(self, "llm_timeout_seconds", None) or 90)) as response:
            raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    def _normalize_llm_hint(self, hint: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        # Validate and enrich the LLM job-routing decision.
        extracted_targets = self._extract_targets(str(payload.get("user_request") or payload.get("input") or ""))
        route = str(hint.get("job_route") or "").upper()
        if route == "NO_RUNNABLE_JOB":
            route = "NO_RUNNABLE_JOB"
        if route not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "PREREQUISITE_REQUIRED", "NO_RUNNABLE_JOB"}:
            raise ValueError(f"Invalid LLM job_route: {route}")
        run_mode = str(hint.get("run_mode") or "all_pending").lower()
        if run_mode not in {"all_pending", "targeted"}:
            raise ValueError(f"Invalid LLM run_mode: {run_mode}")
        llm_targets = hint.get("target_filter") if isinstance(hint.get("target_filter"), dict) else {}
        targets = {
            "map_ids": self._merge_lists(
                self._normalize_list(llm_targets.get("map_ids"), int),
                self._normalize_list(extracted_targets.get("map_ids"), int),
            ),
            "sql_ids": self._merge_lists(
                self._normalize_list(llm_targets.get("sql_ids"), str),
                self._normalize_list(extracted_targets.get("sql_ids"), str),
            ),
            "space_nms": self._merge_lists(
                self._normalize_list(llm_targets.get("space_nms"), str),
                self._normalize_list(extracted_targets.get("space_nms"), str),
            ),
        }
        return {
            "job_route": route,
            "run_mode": run_mode,
            "target_filter": targets,
            "reason": str(hint.get("reason") or ""),
            "source": "LLM",
        }

    def _normalize_list(self, value: Any, caster: Any) -> list[Any]:
        # Normalize scalar or list values with a caster.
        if value is None:
            return []
        raw_values = value if isinstance(value, list) else [value]
        out: list[Any] = []
        for item in raw_values:
            try:
                casted = caster(item)
            except (TypeError, ValueError):
                continue
            if casted not in out:
                out.append(casted)
        return out

    def _merge_lists(self, first: list[Any], second: list[Any]) -> list[Any]:
        # Merge two lists while preserving first-seen order.
        out: list[Any] = []
        for item in [*first, *second]:
            if item not in out:
                out.append(item)
        return out

    def _sample_jobs(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Build pending-job identifiers for LLM routing context.
        jobs = payload.get("pending_jobs") or {}
        return {
            "migration_jobs": list(jobs.get("migration_jobs") or []),
            "sql_conversion_jobs": list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or []),
            "sql_tuning_jobs": list(jobs.get("sql_tuning_jobs") or []),
            "sql_formatting_jobs": list(jobs.get("sql_formatting_jobs") or []),
        }

    def _empty_decision(self, reason: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        # Create a no-runnable execution decision.
        return {
            "job_route": "NO_RUNNABLE_JOB",
            "run_mode": "none",
            "run_all_pending": False,
            "selected_jobs": [],
            "target_filter": targets,
            "reason": reason,
        }

    def _prerequisite_decision(self, reason: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        # Create a prerequisite-required execution decision.
        return {
            "job_route": "PREREQUISITE_REQUIRED",
            "run_mode": "none",
            "run_all_pending": False,
            "selected_jobs": [],
            "target_filter": targets,
            "reason": reason,
        }

    def _execution_decision(
        self,
        route: str,
        run_mode: str,
        targets: dict[str, list[Any]],
        selected_jobs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Create a runnable execution decision.
        return {
            "job_route": route,
            "run_mode": run_mode,
            "run_all_pending": run_mode == "all_pending",
            "selected_jobs": selected_jobs,
            "target_filter": targets,
            "reason": f"Run {len(selected_jobs)} {route} job target(s) in {run_mode} mode.",
        }

    def _selected_jobs_for_hint(
        self,
        payload: dict[str, Any],
        route: str,
        run_mode: str,
        targets: dict[str, list[Any]],
    ) -> list[dict[str, Any]]:
        # Select jobs according to the normalized LLM hint.
        if run_mode == "targeted" or any(targets.values()):
            lookup_jobs = self._lookup_jobs_for_route(payload, route)
            matched_lookup = [job for job in lookup_jobs if self._matches(job, targets)]
            if matched_lookup:
                return matched_lookup
            return []
        return self._jobs_for_route(payload, route)

    def _jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
        # Select the planned jobs for the chosen execution route.
        jobs = payload.get("pending_jobs") or {}
        if route == "MIG":
            return list(jobs.get("migration_jobs") or [])
        if route == "SQL_CONVERSION":
            return list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [])
        if route == "SQL_TUNING":
            return list(jobs.get("sql_tuning_jobs") or [])
        if route == "SQL_FORMATTING":
            return list(jobs.get("sql_formatting_jobs") or [])
        return []

    def _lookup_jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
        # Return all lookup jobs for a specific route.
        jobs = payload.get("pending_jobs") or {}
        lookup = list(jobs.get("job_lookup_jobs") or jobs.get("all_jobs") or [])
        return [job for job in lookup if str(job.get("job_route") or "").upper() == route]

    def _matches(self, job: dict[str, Any], targets: dict[str, list[Any]]) -> bool:
        # Check whether a job matches requested target filters.
        map_ids = {int(v) for v in targets.get("map_ids", []) if str(v).isdigit()}
        sql_ids = {str(v).lower() for v in targets.get("sql_ids", [])}
        space_nms = {str(v).lower() for v in targets.get("space_nms", [])}
        if map_ids and self._to_int(job.get("map_id")) in map_ids:
            return True
        job_sql_id = str(job.get("sql_id") or "").lower()
        job_space_nm = str(job.get("space_nm") or "").lower()
        if sql_ids and space_nms:
            return job_sql_id in sql_ids and job_space_nm in space_nms
        if sql_ids and job_sql_id in sql_ids:
            return True
        if space_nms and job_space_nm in space_nms:
            return True
        return False

    def _extract_targets(self, text: str) -> dict[str, list[Any]]:
        # Extract target identifiers from the user request text.
        return {
            "map_ids": self._extract_map_ids(text),
            "sql_ids": self._extract_text_values(text, r"sql[_\s-]*id|sqlid"),
            "space_nms": self._extract_text_values(text, r"space[_\s-]*nm|spacenm|space"),
        }

    def _extract_map_ids(self, text: str) -> list[int]:
        # Extract map_id values from request text.
        values: list[int] = []
        patterns = [
            r"(?:map[_\s-]*id|mapid|map|맵\s*id|맵아이디)\s*[=:]?\s*([0-9,\s]+)",
            r"([0-9]+)\s*번?\s*(?:map[_\s-]*id|mapid|map|맵\s*id|맵아이디)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                for item in re.findall(r"\d+", match.group(1)):
                    values.append(int(item))
        return list(dict.fromkeys(values))

    def _extract_number_values(self, text: str, label_pattern: str) -> list[int]:
        # Extract numeric values following a label pattern.
        values: list[int] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([0-9,\s]+)", text, flags=re.I):
            for item in re.findall(r"\d+", match.group(1)):
                values.append(int(item))
        return list(dict.fromkeys(values))

    def _extract_text_values(self, text: str, label_pattern: str) -> list[str]:
        # Extract text identifiers following a label pattern.
        values: list[str] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([A-Za-z0-9_.:-]+(?:\s*,\s*[A-Za-z0-9_.:-]+)*)", text, flags=re.I):
            values.extend([item.strip() for item in match.group(1).split(",") if item.strip()])
        return list(dict.fromkeys(values))

    def _counts(self, jobs: dict[str, Any]) -> dict[str, int]:
        # Count runnable pending jobs by route.
        return {
            "MIG": len(jobs.get("migration_jobs") or []),
            "SQL_CONVERSION": len(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or []),
            "SQL_TUNING": len(jobs.get("sql_tuning_jobs") or []),
            "SQL_FORMATTING": len(jobs.get("sql_formatting_jobs") or []),
        }

    def _first_available_route(self, counts: dict[str, int]) -> str | None:
        # Return the first route with runnable jobs in priority order.
        for route in ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"):
            if counts.get(route, 0) > 0:
                return route
        return None

    def _next_node(self, route: str) -> str:
        # Resolve the next component name for a route.
        if route in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            return "09_executionPlanSummary"
        return "13_finalSummary"

    def _to_int(self, value: Any) -> int | None:
        # Convert a value to int when possible.
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Parse a Langflow Data, dict, or JSON string payload.
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        # Parse a JSON object from raw LLM or text output.
        text = str(text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.S)
        text = match.group(0) if match else text
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed

    def _secret_to_str(self, value: Any) -> str:
        # Convert a Langflow secret value into a plain string.
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
