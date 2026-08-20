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
- 06 Get Pending Jobs가 전달한 pending_summary와 sample_pending_jobs를 참고합니다.
- 사용자 요청에 있는 map_id/sql_id/space_nm 값을 target_filter에 넣습니다.

실행 도메인:
- MIG: DB Migration 작업. 보통 map_id로 식별합니다.
- SQL_CONVERSION: SQL Conversion 작업. 보통 sql_id 또는 space_nm으로 식별합니다.
- SQL_TUNING: SQL Tuning 작업.
- SQL_FORMATTING: SQL Formatting 작업.
- PREREQUISITE_BLOCKED: 선행 작업 또는 상태 문제로 바로 실행하지 않는 것이 맞는 경우.
- NO_RUNNABLE_JOB: 실행할 대상이 없다고 판단되는 경우.

실행 모드:
- all_pending: 사용자가 전체 pending/대기 작업 실행을 요청한 경우.
- targeted: 사용자가 map_id/sql_id/space_nm으로 단건 또는 복수건 대상을 지정한 경우.

반환 JSON schema:
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|PREREQUISITE_BLOCKED|NO_RUNNABLE_JOB",
  "run_mode": "all_pending|targeted",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  },
  "reason": "짧은 한국어 사유"
}

규칙:
- map_id가 있으면 job_route는 MIG, run_mode는 targeted입니다.
- sql_id 또는 space_nm이 있고 튜닝 요청이면 SQL_TUNING입니다.
- sql_id 또는 space_nm이 있고 포맷팅 요청이면 SQL_FORMATTING입니다.
- sql_id 또는 space_nm이 있고 튜닝/포맷팅이 아니면 SQL_CONVERSION입니다.
- 명시 대상이 없고 전체/대기 작업 실행 요청이면 run_mode는 all_pending입니다.
- DB context상 요청 대상이 실행 불가 상태라고 판단하면 PREREQUISITE_BLOCKED를 선택할 수 있습니다.
- 선행 작업을 먼저 해야 한다고 판단하면 PREREQUISITE_BLOCKED를 선택할 수 있습니다.
"""


class NewType08JobExecutionRouter(Component):
    display_name = "08 Job Target Router"
    description = "Routes job execution requests by domain and target mode: all pending or explicit map_id/sql_id/space_nm targets."
    name = "NewType08JobExecutionRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="llm_base_url", display_name="LLM Base URL", value="https://api.openai.com/v1", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="gpt-4.1-mini", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=1500, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=90, required=False),
    ]

    outputs = [
        Output(display_name="MIG Targets", name="mig_job", method="mig_response", group_outputs=True),
        Output(display_name="SQL Conversion Targets", name="sql_conversion_job", method="sql_conversion_response", group_outputs=True),
        Output(display_name="SQL Tuning Targets", name="sql_tuning_job", method="sql_tuning_response", group_outputs=True),
        Output(display_name="SQL Formatting Targets", name="sql_formatting_job", method="sql_formatting_response", group_outputs=True),
        Output(display_name="Prerequisite Blocked Message", name="prerequisite_blocked", method="prerequisite_blocked_response", group_outputs=True, types=["Message"]),
        Output(display_name="No Runnable Target Message", name="no_runnable_job", method="no_runnable_response", group_outputs=True, types=["Message"]),
    ]

    def mig_response(self) -> Data:
        return self._route_output("MIG", "mig_job")

    def sql_conversion_response(self) -> Data:
        return self._route_output("SQL_CONVERSION", "sql_conversion_job")

    def sql_tuning_response(self) -> Data:
        return self._route_output("SQL_TUNING", "sql_tuning_job")

    def sql_formatting_response(self) -> Data:
        return self._route_output("SQL_FORMATTING", "sql_formatting_job")

    def prerequisite_blocked_response(self) -> Message:
        return self._message_route_output("PREREQUISITE_BLOCKED", "prerequisite_blocked")

    def no_runnable_response(self) -> Message:
        return self._message_route_output("NO_RUNNABLE_JOB", "no_runnable_job")

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
            result = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _message_route_output(self, expected_route: str, output_name: str) -> Message:
        try:
            routed = self._get_routed_payload()
            if routed.get("job_route") != expected_route:
                self.stop(output_name)
                return Message(text="")
            routed = {**routed, "selected_output": output_name, "next_node": "chat_output", "final": True}
            message = self._build_block_message(routed)
            self.status = {**routed, "answer_text": message}
            return Message(text=message)
        except Exception as exc:
            message = f"component=08_jobExecutionRouter\n작업 대상 라우팅 중 오류가 발생했습니다.\n오류: {exc}"
            self.status = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc), "answer_text": message}
            return Message(text=message)

    def _build_block_message(self, routed: dict[str, Any]) -> str:
        route = str(routed.get("job_route") or "")
        user_request = str(routed.get("user_request") or routed.get("original_request") or "").strip()
        reason = str(routed.get("routing_reason") or routed.get("reason") or "").strip()
        targets = routed.get("target_filter") or {}
        blocked_jobs = routed.get("blocked_jobs") or []

        lines = ["component=08_jobExecutionRouter"]
        if route == "PREREQUISITE_BLOCKED":
            lines.append("요청한 작업은 바로 실행할 수 없는 상태입니다.")
        else:
            lines.append("실행 가능한 작업 대상을 찾지 못했습니다.")
        if user_request:
            lines.append(f"사용자 요청: {user_request}")
        if reason:
            lines.append(f"사유: {reason}")
        if targets:
            lines.append("요청 대상:")
            lines.append(json.dumps(targets, ensure_ascii=False, indent=2, default=str))
        if blocked_jobs:
            lines.append("차단된 작업:")
            lines.append(json.dumps(blocked_jobs, ensure_ascii=False, indent=2, default=str))
        return "\n".join(lines)

    def _get_routed_payload(self) -> dict[str, Any]:
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
        elif route == "PREREQUISITE_BLOCKED":
            decision = {
                "job_route": "PREREQUISITE_BLOCKED",
                "run_mode": "blocked",
                "run_all_pending": False,
                "selected_jobs": [],
                "target_filter": targets,
                "blocker_route": str(decision_hint.get("blocker_route") or "LLM_BLOCKED"),
                "blocked_jobs": [],
                "reason": decision_hint.get("reason") or "LLM selected prerequisite blocked route.",
            }
        else:
            selected_jobs = self._selected_jobs_for_hint(payload, route, requested_run_mode, targets)
            decision = self._execution_decision(route, requested_run_mode, targets, selected_jobs)

        routed = {
            **payload,
            "component": "08_jobExecutionRouter",
            "job_route": decision["job_route"],
            "run_mode": decision["run_mode"],
            "run_all_pending": decision["run_all_pending"],
            "target_filter": decision["target_filter"],
            "selected_jobs": decision["selected_jobs"],
            "blocker_route": decision["blocker_route"],
            "blocked_jobs": decision.get("blocked_jobs") or [],
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
                            "sample_pending_jobs": self._sample_jobs(payload),
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
        extracted_targets = self._extract_targets(str(payload.get("user_request") or payload.get("input") or ""))
        route = str(hint.get("job_route") or "").upper()
        if route == "NO_RUNNABLE_JOB":
            route = "NO_RUNNABLE_JOB"
        if route not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "PREREQUISITE_BLOCKED", "NO_RUNNABLE_JOB"}:
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
            "blocker_route": str(hint.get("blocker_route") or ""),
            "reason": str(hint.get("reason") or ""),
            "source": "LLM",
        }

    def _normalize_list(self, value: Any, caster: Any) -> list[Any]:
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
        out: list[Any] = []
        for item in [*first, *second]:
            if item not in out:
                out.append(item)
        return out

    def _sample_jobs(self, payload: dict[str, Any]) -> dict[str, Any]:
        jobs = payload.get("pending_jobs") or {}
        return {
            "migration_jobs": list(jobs.get("migration_jobs") or [])[:5],
            "sql_conversion_jobs": list(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or [])[:5],
            "sql_tuning_jobs": list(jobs.get("sql_tuning_jobs") or [])[:5],
            "sql_formatting_jobs": list(jobs.get("sql_formatting_jobs") or [])[:5],
        }

    def _empty_decision(self, reason: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        return {
            "job_route": "NO_RUNNABLE_JOB",
            "run_mode": "none",
            "run_all_pending": False,
            "selected_jobs": [],
            "target_filter": targets,
            "blocker_route": "",
            "reason": reason,
        }

    def _execution_decision(
        self,
        route: str,
        run_mode: str,
        targets: dict[str, list[Any]],
        selected_jobs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "job_route": route,
            "run_mode": run_mode,
            "run_all_pending": run_mode == "all_pending",
            "selected_jobs": selected_jobs,
            "target_filter": targets,
            "blocker_route": "",
            "reason": f"Run {len(selected_jobs)} {route} job target(s) in {run_mode} mode.",
        }

    def _requested_route(self, text: str, targets: dict[str, list[Any]]) -> str | None:
        lowered = text.lower()
        if targets.get("map_ids"):
            return "MIG"
        if re.search(r"(tuning|튜닝|성능|performance)", lowered):
            return "SQL_TUNING"
        if re.search(r"(format|formatting|포맷|포매팅|정렬)", lowered):
            return "SQL_FORMATTING"
        if re.search(r"(conversion|convert|변환|sql\s*conversion)", lowered):
            return "SQL_CONVERSION"
        if re.search(r"(migration|마이그레이션|mig|db)", lowered):
            return "MIG"
        if targets.get("sql_ids") or targets.get("space_nms"):
            return "SQL_CONVERSION"
        return None

    def _select_jobs(self, payload: dict[str, Any], route: str, targets: dict[str, list[Any]]) -> list[dict[str, Any]]:
        jobs = self._jobs_for_route(payload, route)
        if not any(targets.values()):
            return jobs

        selected = [job for job in jobs if self._matches(job, targets)]
        if selected:
            return selected
        return self._synthetic_jobs(route, targets)

    def _selected_jobs_for_hint(
        self,
        payload: dict[str, Any],
        route: str,
        run_mode: str,
        targets: dict[str, list[Any]],
    ) -> list[dict[str, Any]]:
        if run_mode == "targeted" or any(targets.values()):
            lookup_jobs = self._lookup_jobs_for_route(payload, route)
            matched_lookup = [job for job in lookup_jobs if self._matches(job, targets)]
            if matched_lookup:
                return matched_lookup
            return self._synthetic_jobs(route, targets)
        return self._jobs_for_route(payload, route)

    def _target_status(self, payload: dict[str, Any], route: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        lookup_jobs = self._lookup_jobs_for_route(payload, route)
        matched_lookup = [job for job in lookup_jobs if self._matches(job, targets)]
        if not matched_lookup:
            return {
                "blocked": False,
                "selected_jobs": self._synthetic_jobs(route, targets),
                "blocked_jobs": [],
                "reason": "Explicit target not found in lookup context; using POC synthetic target.",
            }

        runnable = [job for job in matched_lookup if job.get("runnable")]
        blocked = [job for job in matched_lookup if not job.get("runnable")]
        if runnable:
            return {
                "blocked": False,
                "selected_jobs": runnable,
                "blocked_jobs": blocked,
                "reason": f"Run {len(runnable)} runnable explicit target(s).",
            }
        return {
            "blocked": True,
            "selected_jobs": [],
            "blocked_jobs": blocked,
            "reason": "Requested target exists but is not runnable. Check USE_YN/status and use Management Status Change before execution.",
        }

    def _jobs_for_route(self, payload: dict[str, Any], route: str) -> list[dict[str, Any]]:
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
        jobs = payload.get("pending_jobs") or {}
        lookup = list(jobs.get("job_lookup_jobs") or jobs.get("all_jobs") or [])
        return [job for job in lookup if str(job.get("job_route") or "").upper() == route]

    def _synthetic_jobs(self, route: str, targets: dict[str, list[Any]]) -> list[dict[str, Any]]:
        if route == "MIG":
            return [{"job_route": "MIG", "job_type": "MIG", "map_id": map_id, "source": "USER_TARGET"} for map_id in targets.get("map_ids", [])]

        sql_ids = targets.get("sql_ids") or []
        space_nms = targets.get("space_nms") or []
        if sql_ids:
            return [
                {"job_route": route, "job_type": "SQL", "sql_id": sql_id, "space_nm": space_nms[0] if space_nms else "", "source": "USER_TARGET"}
                for sql_id in sql_ids
            ]
        return [{"job_route": route, "job_type": "SQL", "space_nm": space_nm, "sql_id": "", "source": "USER_TARGET"} for space_nm in space_nms]

    def _matches(self, job: dict[str, Any], targets: dict[str, list[Any]]) -> bool:
        map_ids = {int(v) for v in targets.get("map_ids", []) if str(v).isdigit()}
        sql_ids = {str(v).lower() for v in targets.get("sql_ids", [])}
        space_nms = {str(v).lower() for v in targets.get("space_nms", [])}
        if map_ids and self._to_int(job.get("map_id")) in map_ids:
            return True
        if sql_ids and str(job.get("sql_id") or job.get("row_id") or "").lower() in sql_ids:
            return True
        if space_nms and str(job.get("space_nm") or "").lower() in space_nms:
            return True
        return False

    def _extract_targets(self, text: str) -> dict[str, list[Any]]:
        return {
            "map_ids": self._extract_map_ids(text),
            "sql_ids": self._extract_text_values(text, r"sql[_\s-]*id|sqlid"),
            "space_nms": self._extract_text_values(text, r"space[_\s-]*nm|spacenm|space"),
        }

    def _extract_map_ids(self, text: str) -> list[int]:
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
        values: list[int] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([0-9,\s]+)", text, flags=re.I):
            for item in re.findall(r"\d+", match.group(1)):
                values.append(int(item))
        return list(dict.fromkeys(values))

    def _extract_text_values(self, text: str, label_pattern: str) -> list[str]:
        values: list[str] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([A-Za-z0-9_.:-]+(?:\s*,\s*[A-Za-z0-9_.:-]+)*)", text, flags=re.I):
            values.extend([item.strip() for item in match.group(1).split(",") if item.strip()])
        return list(dict.fromkeys(values))

    def _counts(self, jobs: dict[str, Any]) -> dict[str, int]:
        return {
            "MIG": len(jobs.get("migration_jobs") or []),
            "SQL_CONVERSION": len(jobs.get("sql_conversion_jobs") or jobs.get("sql_jobs") or []),
            "SQL_TUNING": len(jobs.get("sql_tuning_jobs") or []),
            "SQL_FORMATTING": len(jobs.get("sql_formatting_jobs") or []),
        }

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

    def _next_node(self, route: str) -> str:
        if route in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            return "09_executionPlanSummary"
        return "13_finalSummary"

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    def _parse_json_object(self, text: str) -> dict[str, Any]:
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
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
