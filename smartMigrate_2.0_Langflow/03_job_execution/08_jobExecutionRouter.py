from __future__ import annotations

import logging
import json
import re
import traceback
from collections.abc import Mapping
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType08JobExecutionRouter(Component):

    display_name = "08 Job Target Router"
    description = "Routes validated 02 intent and 06 DB results without reinterpreting targets."
    name = "NewType08JobExecutionRouter"
    icon = "Route"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        # Kept optional for existing graph connections; 08 no longer invokes an LLM.
        HandleInput(name="llm", display_name="Language Model (unused)", input_types=["LanguageModel"], required=False),
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
            self._log_exception("ROUTE_OUTPUT", exc)
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
            self._log_detail("08_MESSAGE_OUTPUT", "SEND_CHAT", "PASS", "08 final user response", self.status)
            return Message(text=message)
        except Exception as exc:
            self._log_exception("MESSAGE_OUTPUT", exc)
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
        self._log_detail("08_INPUT_PAYLOAD", "RECEIVE_06", "PASS", "08 received payload from 06", payload)
        if payload.get("ok") is False:
            raise ValueError(f"06 job lookup failed: {payload.get('error') or 'unknown error'}")
        targets = payload.get("target_filter")
        if not isinstance(targets, dict):
            raise ValueError("02 target_filter must be preserved through 06")
        if not payload.get("should_execute", True) or payload.get("clarification_required", False):
            decision = self._empty_decision(
                str(payload.get("clarification_message") or "실행 요청이 확정되지 않았습니다. 대상과 실행 의도를 확인해 주세요."), targets)
        else:
            scope = str(payload.get("execution_scope") or "unknown").lower()
            route = str(payload.get("requested_domain") or "UNKNOWN").upper()
            has_mig = bool(targets.get("map_ids"))
            has_sql = bool(targets.get("sql_seqs") or targets.get("sql_ids") or targets.get("space_nms"))
            if scope not in {"all", "domain", "targeted"} or route not in {"MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "FULL_WORKFLOW"}:
                raise ValueError("02 must provide a validated requested_domain and execution_scope")
            if (scope == "all") != (route == "FULL_WORKFLOW"):
                raise ValueError("FULL_WORKFLOW requires all scope")
            if has_mig and has_sql:
                raise ValueError("Mixed Migration/SQL targets cannot be routed")
            if (scope == "targeted") != (has_mig or has_sql):
                raise ValueError("Targeted scope and target identifiers do not match; cannot widen execution")
            if (has_mig and route != "MIG") or (has_sql and route not in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}):
                raise ValueError("Requested domain does not match target identifiers")
            if has_sql and not targets.get("sql_seqs") and not (targets.get("sql_ids") and targets.get("space_nms")):
                raise ValueError("SQL target requires SQL_SEQ or SQL_ID and SPACE_NM")
            if scope == "targeted" and payload.get("job_detail_mode") != "requested_jobs":
                raise ValueError("06 did not query the requested target; missing lookup is not a non-runnable job")
            if not isinstance(payload.get("job_availability"), dict):
                raise ValueError("06 job_availability is missing; DB lookup must complete before routing")
            run_mode = "targeted" if scope == "targeted" else "all_pending"
            counts = self._counts(payload)
            selected_jobs = self._selected_jobs(payload, route, run_mode)
            self._log_detail("08_ROUTE_INPUT", "CHECK_RUNNABLE", "PASS", "08 routing using 02 interpretation and 06 DB results", {
                "requested_domain": route, "execution_scope": scope, "run_mode": run_mode,
                "target_filter": targets, "counts": counts,
                "requested_job_identifiers": self._requested_job_identifiers(payload),
                "requested_target_status": payload.get("requested_target_status"),
            })
            prereq_reason = self._prerequisite_reason(route, run_mode, counts)
            if prereq_reason:
                decision = self._prerequisite_decision(prereq_reason, targets)
            elif run_mode == "targeted" and not selected_jobs:
                statuses = payload.get("requested_target_status") or {}
                domain_status = statuses.get("migration" if route == "MIG" else "sql") or []
                reason = ("요청 대상은 DB에서 확인되었지만 해당 단계의 실행 조건을 충족하지 않습니다. 상태·사용 여부·재시도 횟수를 확인해 주세요."
                          if domain_status else "요청한 식별자에 해당하는 작업이 DB에서 조회되지 않았습니다.")
                decision = self._empty_decision(reason, targets)
            elif run_mode == "all_pending" and self._route_count(route, counts) <= 0:
                decision = self._empty_decision(f"{route}에서 현재 실행 가능한 작업이 없습니다.", targets)
            else:
                decision = self._execution_decision(route, run_mode, targets, selected_jobs)
        routed = {
            **payload, "component": "08_jobExecutionRouter",
            "effective_user_request": self._effective_user_request(payload),
            "job_route": decision["job_route"], "run_mode": decision["run_mode"],
            "run_all_pending": decision["run_all_pending"], "target_filter": decision["target_filter"],
            "selected_jobs": decision["selected_jobs"], "routing_reason": decision["reason"],
            "routing_source": "02_intent_and_06_db",
        }
        routed.setdefault("history", []).append({
            "step": "job_target_route",
            "message": f"job_route={routed['job_route']}, run_mode={routed['run_mode']}, selected={len(routed['selected_jobs'])}",
        })
        self._log_detail("08_FINAL_OUTPUT", "ROUTE", "END",
                         f"08 route={routed['job_route']}, mode={routed['run_mode']}, selected={len(routed['selected_jobs'])}: {routed['routing_reason']}", routed)
        self._cached_routed_payload = routed
        return routed

    def _log_detail(self, log_type: str, step: str, status: str, message: str, detail: Any) -> None:
        level = "ERROR" if status == "ERROR" else "INFO"
        logging.getLogger("smartmigrate.workflow").log(
            logging.ERROR if level == "ERROR" else logging.INFO,
            message,
            extra={"workflow_log": [0, "WORKFLOW", log_type, level, step, status, 0,
                                    json.dumps(detail, ensure_ascii=False, default=str)]},
        )

    def _log_exception(self, step: str, exc: Exception) -> None:
        self._log_detail("08_JOB_ROUTER", step, "ERROR", f"08 failed: {exc}", {
            "error": str(exc), "traceback": traceback.format_exc(),
        })


    def _effective_user_request(self, payload: dict[str, Any]) -> str:
        """Use the original request preserved by 02 for diagnostic messages."""
        return str(
            payload.get("resolved_user_request")
            or payload.get("user_request")
            or payload.get("original_request")
            or payload.get("input")
            or ""
        ).strip()


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

    # 06이 조회한 도메인별 실행 가능 식별자를 로그에 기록한다.
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
        sql_seqs = targets.get("sql_seqs") or []
        sql_ids = targets.get("sql_ids") or []
        space_nms = targets.get("space_nms") or []
        if map_ids:
            return f"map_id={', '.join(str(item) for item in map_ids)}"
        if sql_seqs:
            return f"sql_seq={', '.join(str(item) for item in sql_seqs)}"
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
