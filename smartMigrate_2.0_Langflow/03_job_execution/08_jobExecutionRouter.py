from __future__ import annotations

import logging
import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


JOB_EXECUTION_ROUTER_PROMPT = """당신은 SmartMigrate 작업 실행 라우터입니다.
반드시 JSON 객체 하나만 반환하세요.

입력으로 받는 주요 값:
- user_request: 사용자 원문 요청
- execution_scope: 01 LLM이 판단한 범위. all, domain, targeted, unknown 중 하나
- requested_domain: 01 LLM이 판단한 도메인. MIG, SQL_CONVERSION, SQL_TUNING, SQL_FORMATTING, FULL_WORKFLOW, UNKNOWN 중 하나
- target_filter: 01 LLM이 추출한 map_ids, sql_ids, space_nms
- job_availability: 06이 DB에서 조회한 실행 가능 카운트
- requested_target_status: 특정 target 요청이 있을 때 해당 target의 현재 상태
- requested_job_identifiers: 특정 target 요청이 있을 때 현재 실행 가능한 대상 식별자

반환 JSON schema:
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|FULL_WORKFLOW|PREREQUISITE_REQUIRED|NO_RUNNABLE_JOB",
  "run_mode": "all_pending|targeted",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  },
  "reason": "짧은 한국어 이유"
}

라우팅 규칙:
- execution_scope가 targeted이면 run_mode는 targeted입니다.
- execution_scope가 all이면 job_route는 FULL_WORKFLOW, run_mode는 all_pending입니다.
- execution_scope가 domain이면 requested_domain을 job_route로 사용하고 run_mode는 all_pending입니다.
- target_filter.map_ids가 있으면 기본 job_route는 MIG입니다.
- target_filter.sql_ids 또는 target_filter.space_nms가 있으면 requested_domain을 우선 사용합니다. UNKNOWN이면 SQL_CONVERSION으로 둡니다.
- 사용자가 "전체 작업", "전체 진행", "남은 작업 다", "처음부터 끝까지"처럼 전체 흐름을 요청하면 FULL_WORKFLOW입니다.
- 사용자가 특정 도메인 없이 "작업 실행", "진행", "잔여 작업 실행"만 요청하면 FULL_WORKFLOW입니다.

실행 가능성 규칙:
- job_availability의 key는 total, migration_total, sql_conversion_total, sql_tuning_total, sql_formatting_total입니다.
- FULL_WORKFLOW 요청에서 total이 0이면 NO_RUNNABLE_JOB입니다.
- MIG all_pending 요청에서 migration_total이 0이면 NO_RUNNABLE_JOB입니다.
- SQL_CONVERSION all_pending 요청에서 sql_conversion_total이 0이면 NO_RUNNABLE_JOB입니다.
- SQL_TUNING all_pending 요청에서 sql_tuning_total이 0이면 NO_RUNNABLE_JOB입니다.
- SQL_FORMATTING all_pending 요청에서 sql_formatting_total이 0이면 NO_RUNNABLE_JOB입니다.
- targeted 요청에서 requested_job_identifiers에 실행 가능한 대상이 없으면 NO_RUNNABLE_JOB입니다.

선행 조건 규칙:
- FULL_WORKFLOW는 DB Migration부터 SQL Formatting까지 순서대로 처리하므로 선행 작업이 남아 있어도 PREREQUISITE_REQUIRED로 보내지 않습니다.
- SQL_CONVERSION 단독 실행 요청에서 migration_total이 1 이상이면 PREREQUISITE_REQUIRED입니다.
- SQL_TUNING 단독 실행 요청에서 migration_total 또는 sql_conversion_total이 1 이상이면 PREREQUISITE_REQUIRED입니다.
- SQL_FORMATTING 단독 실행 요청은 선행 migration_total, sql_conversion_total, sql_tuning_total이 남아 있어도 PREREQUISITE_REQUIRED로 보내지 않습니다. 현재 SQL Formatting 잔여 작업이 있으면 해당 잔여 작업을 실행합니다.
- targeted MIG 요청에서 requested_target_status의 PRIOR_MAP_ID 대상이 아직 완료되지 않았다고 확인되는 경우 PREREQUISITE_REQUIRED입니다.

반드시 입력 JSON에 있는 구조화 값을 우선 신뢰하세요.
사용자 원문은 보조 판단에만 사용하세요.
"""


class NewType08JobExecutionRouter(Component):

    display_name = "08 Job Target Router"
    description = "Routes job execution requests by domain and target mode."
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
        Output(display_name="Full Workflow Targets", name="full_workflow_job", method="full_workflow_response", group_outputs=True),
        Output(display_name="Prerequisite Required Message", name="prerequisite_required", method="prerequisite_required_response", group_outputs=True, types=["Message"]),
        Output(display_name="No Runnable Target Message", name="no_runnable_job", method="no_runnable_response", group_outputs=True, types=["Message"]),
    ]

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def mig_response(self) -> Data:
        return self._route_output("MIG", "mig_job")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def sql_conversion_response(self) -> Data:
        return self._route_output("SQL_CONVERSION", "sql_conversion_job")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def sql_tuning_response(self) -> Data:
        return self._route_output("SQL_TUNING", "sql_tuning_job")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def sql_formatting_response(self) -> Data:
        return self._route_output("SQL_FORMATTING", "sql_formatting_job")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def full_workflow_response(self) -> Data:
        return self._route_output("FULL_WORKFLOW", "full_workflow_job")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def prerequisite_required_response(self) -> Message:
        return self._message_route_output("PREREQUISITE_REQUIRED", "prerequisite_required")

    # Langflow group output별로 현재 route가 맞을 때만 payload를 반환한다.
    def no_runnable_response(self) -> Message:
        return self._message_route_output("NO_RUNNABLE_JOB", "no_runnable_job")

    # 예상 route와 실제 route를 비교해 해당 output 실행 여부를 결정한다.
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

    # 라우터 output이 Message 타입일 때 route 일치 여부에 따라 메시지를 반환한다.
    def _message_route_output(self, expected_route: str, output_name: str) -> Message:
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
            message = f"component=08_jobExecutionRouter\n작업 실행 라우팅 중 오류가 발생했습니다.\n오류: {exc}"
            self.status = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc), "answer_text": message}
            return Message(text=message)

    # payload나 graph 설정에서 필요한 값을 꺼내 표준 형태로 반환한다.
    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached

        logging.getLogger("smartmigrate.workflow").info("08 Job Execution Router started", extra={"workflow_log": [0, "WORKFLOW", "08_JOB_ROUTER", "INFO", "ROUTE", "START", 0]})

        payload = self._parse_payload(getattr(self, "payload_json", ""))
        decision_hint = self._normalize_llm_hint(self._route_with_llm(payload), payload)
        targets = decision_hint["target_filter"]
        route = decision_hint["job_route"]
        run_mode = decision_hint["run_mode"]
        counts = self._counts(payload)

        if route is None:
            route = self._route_from_payload(payload, targets)
        if route is None:
            route = self._first_available_route(counts)

        if route is None:
            decision = self._empty_decision("실행 가능한 작업이 없습니다.", targets)
        elif route == "NO_RUNNABLE_JOB":
            decision = self._empty_decision(decision_hint.get("reason") or "실행 가능한 작업이 없습니다.", targets)
        elif route == "PREREQUISITE_REQUIRED":
            decision = self._prerequisite_decision(decision_hint.get("reason") or "선행 작업이 남아 있어 지금 실행할 수 없습니다.", targets)
        elif route == "FULL_WORKFLOW" and counts["total"] <= 0:
            decision = self._empty_decision("전체 워크플로우에서 실행 가능한 작업이 없습니다.", targets)
        else:
            prereq_reason = self._prerequisite_reason(route, run_mode, counts)
            if prereq_reason:
                decision = self._prerequisite_decision(prereq_reason, targets)
            else:
                selected_jobs = self._selected_jobs(payload, route, run_mode)
                if run_mode == "targeted" and not selected_jobs:
                    decision = self._empty_decision(decision_hint.get("reason") or "요청한 대상은 현재 실행 가능한 작업이 아닙니다.", targets)
                elif run_mode == "all_pending" and self._route_count(route, counts) <= 0:
                    decision = self._empty_decision(decision_hint.get("reason") or "해당 도메인에 실행 가능한 작업이 없습니다.", targets)
                else:
                    decision = self._execution_decision(route, run_mode, targets, selected_jobs)

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
                "message": f"job_route={routed['job_route']}, run_mode={routed['run_mode']}, selected={len(routed['selected_jobs'])}",
            }
        )
        self._cached_routed_payload = routed
        return routed

    # 관리 자연어 요청을 LLM에 보내 route/action/target 구조로 분류한다.
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
                            "user_request": payload.get("user_request") or payload.get("original_request") or payload.get("input") or "",
                            "execution_scope": payload.get("execution_scope") or "unknown",
                            "requested_domain": payload.get("requested_domain") or "UNKNOWN",
                            "target_filter": payload.get("target_filter") or {},
                            "job_availability": payload.get("job_availability") or payload.get("remaining_summary") or {},
                            "requested_target_status": payload.get("requested_target_status") or {},
                            "requested_job_identifiers": self._requested_job_identifiers(payload),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None), 1500),
        }
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._positive_int(getattr(self, "llm_timeout_seconds", None), 90)) as response:
                raw = json.loads(response.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"08 Job Target Router LLM HTTP {exc.code}: {detail[:1000]}") from exc
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return self._parse_json_object(content)

    # 비교와 검색이 안정적으로 동작하도록 입력 값을 정규화한다.
    def _normalize_llm_hint(self, hint: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        # target_filter는 01이 제공해야 한다. 여기의 로컬 텍스트 추출은 예전 01 출력 호환용이다.
        extracted_targets = self._extract_targets(str(payload.get("user_request") or payload.get("original_request") or payload.get("input") or ""))
        payload_targets = payload.get("target_filter") if isinstance(payload.get("target_filter"), dict) else {}
        llm_targets = hint.get("target_filter") if isinstance(hint.get("target_filter"), dict) else {}
        route = str(hint.get("job_route") or "").upper() or self._route_from_payload(payload, payload_targets)
        if route is not None and route not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "FULL_WORKFLOW", "PREREQUISITE_REQUIRED", "NO_RUNNABLE_JOB"}:
            raise ValueError(f"Invalid LLM job_route: {route}")
        run_mode = str(hint.get("run_mode") or self._run_mode_from_payload(payload, payload_targets)).lower()
        if run_mode not in {"all_pending", "targeted"}:
            raise ValueError(f"Invalid LLM run_mode: {run_mode}")
        targets = {
            "map_ids": self._merge_lists(
                self._normalize_list(payload_targets.get("map_ids"), int),
                self._normalize_list(llm_targets.get("map_ids"), int),
                self._normalize_list(extracted_targets.get("map_ids"), int),
            ),
            "sql_ids": self._merge_lists(
                self._normalize_list(payload_targets.get("sql_ids"), str),
                self._normalize_list(llm_targets.get("sql_ids"), str),
                self._normalize_list(extracted_targets.get("sql_ids"), str),
            ),
            "space_nms": self._merge_lists(
                self._normalize_list(payload_targets.get("space_nms"), str),
                self._normalize_list(llm_targets.get("space_nms"), str),
                self._normalize_list(extracted_targets.get("space_nms"), str),
            ),
        }
        return {
            "job_route": route,
            "run_mode": run_mode,
            "target_filter": targets,
            "reason": str(hint.get("reason") or ""),
        }

    # 실행 route와 run_mode에 맞춰 실제 loop에 넘길 작업 목록을 선택한다.
    def _selected_jobs(self, payload: dict[str, Any], route: str, run_mode: str) -> list[dict[str, Any]]:
        if run_mode == "all_pending":
            return []
        requested = payload.get("requested_jobs") if isinstance(payload.get("requested_jobs"), dict) else {}
        if route == "MIG":
            return [dict(job) for job in requested.get("migration_jobs") or [] if isinstance(job, dict)]
        if route == "SQL_CONVERSION":
            return [dict(job) for job in requested.get("sql_conversion_jobs") or requested.get("sql_jobs") or [] if isinstance(job, dict)]
        if route == "SQL_TUNING":
            return [dict(job) for job in requested.get("sql_tuning_jobs") or [] if isinstance(job, dict)]
        if route == "SQL_FORMATTING":
            return [dict(job) for job in requested.get("sql_formatting_jobs") or [] if isinstance(job, dict)]
        return []

    # 사용자 요청에서 특정 작업 식별자만 추려 실행 범위를 제한한다.
    def _requested_job_identifiers(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = payload.get("requested_jobs") if isinstance(payload.get("requested_jobs"), dict) else {}
        return {
            "migration_jobs": list(requested.get("migration_jobs") or []),
            "sql_conversion_jobs": list(requested.get("sql_conversion_jobs") or requested.get("sql_jobs") or []),
            "sql_tuning_jobs": list(requested.get("sql_tuning_jobs") or []),
            "sql_formatting_jobs": list(requested.get("sql_formatting_jobs") or []),
        }

    # 선행 단계가 부족해 현재 route를 실행할 수 없는 이유 문장을 만든다.
    def _prerequisite_reason(self, route: str, run_mode: str, counts: dict[str, int]) -> str:
        if run_mode != "all_pending" or route in {"MIG", "FULL_WORKFLOW", "PREREQUISITE_REQUIRED", "NO_RUNNABLE_JOB"}:
            return ""
        blockers: list[str] = []
        if route in {"SQL_CONVERSION", "SQL_TUNING"} and counts.get("MIG", 0) > 0:
            blockers.append(f"DB Migration 잔여 {counts.get('MIG', 0)}건")
        if route == "SQL_TUNING" and counts.get("SQL_CONVERSION", 0) > 0:
            blockers.append(f"SQL Conversion 잔여 {counts.get('SQL_CONVERSION', 0)}건")
        return "선행 작업이 남아 있어 요청한 단계를 실행할 수 없습니다: " + ", ".join(blockers) if blockers else ""

    # payload와 target 정보를 바탕으로 사용자가 실행하려는 route를 결정한다.
    def _route_from_payload(self, payload: dict[str, Any], targets: dict[str, Any]) -> str | None:
        domain = str(payload.get("requested_domain") or "").upper()
        if domain in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "FULL_WORKFLOW"}:
            return domain
        if targets.get("map_ids"):
            return "MIG"
        if targets.get("sql_ids") or targets.get("space_nms"):
            return "SQL_CONVERSION"
        scope = str(payload.get("execution_scope") or "").lower()
        return "FULL_WORKFLOW" if scope == "all" else None

    # 전체 실행인지 특정 작업 실행인지 run_mode를 결정한다.
    def _run_mode_from_payload(self, payload: dict[str, Any], targets: dict[str, Any]) -> str:
        scope = str(payload.get("execution_scope") or "").lower()
        if scope == "targeted" or any(targets.get(key) for key in ("map_ids", "sql_ids", "space_nms")):
            return "targeted"
        return "all_pending"

    # route별 남은 작업 수 집계에서 현재 route의 count를 꺼낸다.
    def _route_count(self, route: str, counts: dict[str, int]) -> int:
        if route == "FULL_WORKFLOW":
            return counts["total"]
        return counts.get(route, 0)

    # 이전 단계가 계산한 route별 남은 작업 수를 정수 dict로 정리한다.
    def _counts(self, payload: dict[str, Any]) -> dict[str, int]:
        summary = payload.get("job_availability") or payload.get("remaining_summary") or payload.get("pending_summary") or {}
        counts = {
            "MIG": self._to_int(summary.get("migration_total")) or 0,
            "SQL_CONVERSION": self._to_int(summary.get("sql_conversion_total")) or 0,
            "SQL_TUNING": self._to_int(summary.get("sql_tuning_total")) or 0,
            "SQL_FORMATTING": self._to_int(summary.get("sql_formatting_total")) or 0,
        }
        counts["total"] = self._to_int(summary.get("total")) or sum(counts.values())
        return counts

    # 실행할 작업이 없을 때 router가 반환할 표준 decision payload를 만든다.
    def _empty_decision(self, reason: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        return {
            "job_route": "NO_RUNNABLE_JOB",
            "run_mode": "none",
            "run_all_pending": False,
            "selected_jobs": [],
            "target_filter": targets,
            "reason": reason,
        }

    # 선행 조건 미충족으로 실행을 막을 때의 표준 decision payload를 만든다.
    def _prerequisite_decision(self, reason: str, targets: dict[str, list[Any]]) -> dict[str, Any]:
        return {
            "job_route": "PREREQUISITE_REQUIRED",
            "run_mode": "none",
            "run_all_pending": False,
            "selected_jobs": [],
            "target_filter": targets,
            "reason": reason,
        }

    # 선택된 route와 job 목록을 loop 진입용 decision payload로 만든다.
    def _execution_decision(self, route: str, run_mode: str, targets: dict[str, list[Any]], selected_jobs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "job_route": route,
            "run_mode": run_mode,
            "run_all_pending": run_mode == "all_pending",
            "selected_jobs": selected_jobs,
            "target_filter": targets,
            "reason": f"{route} 작업을 {run_mode} 모드로 실행합니다.",
        }

    # 남은 작업이 있는 route 중 실행 우선순위가 가장 높은 route를 고른다.
    def _first_available_route(self, counts: dict[str, int]) -> str | None:
        for route in ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"):
            if counts.get(route, 0) > 0:
                return route
        return None

    # 후속 단계나 사용자 응답에 필요한 구조화된 결과를 조립한다.
    def _build_message_route_text(self, routed: dict[str, Any]) -> str:
        route = str(routed.get("job_route") or "")
        reason = str(routed.get("routing_reason") or "").strip()
        user_request = str(routed.get("user_request") or routed.get("original_request") or "").strip()
        target_label = self._target_label(routed.get("target_filter") or {}) or "요청하신 작업"
        if route == "PREREQUISITE_REQUIRED":
            message = reason or f"{target_label}은 선행 작업이 남아 있어 지금 실행할 수 없습니다."
        else:
            message = reason or f"{target_label}은 현재 실행 가능한 작업이 아닙니다."
        return "\n".join([message, f"요청: {user_request}"] if user_request else [message])

    # 사용자가 지정한 target 범위를 status 메시지에 넣을 짧은 label로 만든다.
    def _target_label(self, targets: dict[str, Any]) -> str:
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

    # 관리 route에 연결된 다음 Langflow node 이름을 반환한다.
    def _next_node(self, route: str) -> str:
        if route == "MIG":
            return "10A_migJobsToLoopTable"
        if route == "SQL_CONVERSION":
            return "12A_sqlConversionJobsToLoopTable"
        if route == "SQL_TUNING":
            return "15A_sqlTuningJobsToLoopTable"
        if route == "SQL_FORMATTING":
            return "17A_sqlFormattingJobsToLoopTable"
        if route == "FULL_WORKFLOW":
            return "18A_fullWorkflowJobsToLoopTable"
        return "chat_output"

    # 문자열이나 payload에서 후속 로직에 필요한 값을 추출한다.
    def _extract_targets(self, text: str) -> dict[str, list[Any]]:
        return {
            "map_ids": self._extract_map_ids(text),
            "sql_ids": self._extract_text_values(text, r"sql[_\s-]*id|sqlid"),
            "space_nms": self._extract_text_values(text, r"space[_\s-]*nm|spacenm|space"),
        }

    # 문자열이나 payload에서 후속 로직에 필요한 값을 추출한다.
    def _extract_map_ids(self, text: str) -> list[int]:
        values: list[int] = []
        patterns = [
            r"(?:map[_\s-]*id|mapid|map|맵\s*아이디|맵아이디)\s*[=:]?\s*([0-9,\s]+)",
            r"([0-9]+)\s*번?\s*(?:map[_\s-]*id|mapid|map|맵\s*아이디|맵아이디|맵)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                for item in re.findall(r"\d+", match.group(1)):
                    values.append(int(item))
        return list(dict.fromkeys(values))

    # 문자열이나 payload에서 후속 로직에 필요한 값을 추출한다.
    def _extract_text_values(self, text: str, label_pattern: str) -> list[str]:
        values: list[str] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([A-Za-z0-9_.:-]+(?:\s*,\s*[A-Za-z0-9_.:-]+)*)", text, flags=re.I):
            values.extend([item.strip() for item in match.group(1).split(",") if item.strip()])
        return list(dict.fromkeys(values))

    # 비교와 검색이 안정적으로 동작하도록 입력 값을 정규화한다.
    def _normalize_list(self, value: Any, caster: Any) -> list[Any]:
        if value is None:
            return []
        raw_values = value if isinstance(value, list) else [value]
        out: list[Any] = []
        for item in raw_values:
            try:
                casted = self._to_int(item) if caster is int else caster(item)
            except (TypeError, ValueError):
                continue
            if casted is not None and casted not in out:
                out.append(casted)
        return out

    # 여러 출처의 값이나 목록을 중복 없이 하나로 합친다.
    def _merge_lists(self, *lists: list[Any]) -> list[Any]:
        out: list[Any] = []
        for values in lists:
            for item in values:
                if item not in out:
                    out.append(item)
        return out

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _to_int(self, value: Any) -> int | None:
        if isinstance(value, Mapping):
            for key in ("value", "count", "total", "number", "amount"):
                if key in value:
                    return self._to_int(value.get(key))
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # 숫자 입력을 양의 정수로 변환하고 실패하면 기본값을 사용한다.
    def _positive_int(self, value: Any, default: int) -> int:
        converted = self._to_int(value)
        return converted if converted is not None and converted > 0 else default

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_json_object(str(raw or "").strip()) if str(raw or "").strip() else {}

    # 문자열 입력에서 JSON 객체를 파싱해 후속 로직이 쓰는 dict로 만든다.
    def _parse_json_object(self, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
            clean = re.sub(r"\s*```$", "", clean)
        match = re.search(r"\{.*\}", clean, flags=re.S)
        clean = match.group(0) if match else clean
        parsed = json.loads(clean) if clean else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
