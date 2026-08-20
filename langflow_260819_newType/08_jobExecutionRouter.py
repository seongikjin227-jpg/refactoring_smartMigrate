from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType08JobExecutionRouter(Component):
    display_name = "08 Job Target Router"
    description = "Routes job execution requests by domain and target mode: all pending or explicit map_id/sql_id/space_nm targets."
    name = "NewType08JobExecutionRouter"
    icon = "Route"

    inputs = [DataInput(name="payload_json", display_name="Payload JSON", required=True)]

    outputs = [
        Output(display_name="MIG Targets", name="mig_job", method="mig_response", group_outputs=True),
        Output(display_name="SQL Conversion Targets", name="sql_conversion_job", method="sql_conversion_response", group_outputs=True),
        Output(display_name="SQL Tuning Targets", name="sql_tuning_job", method="sql_tuning_response", group_outputs=True),
        Output(display_name="SQL Formatting Targets", name="sql_formatting_job", method="sql_formatting_response", group_outputs=True),
        Output(display_name="Prerequisite Blocked", name="prerequisite_blocked", method="prerequisite_blocked_response", group_outputs=True),
        Output(display_name="No Runnable Target", name="no_runnable_job", method="no_runnable_response", group_outputs=True),
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
            result = {"ok": False, "component": "08_jobExecutionRouter", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _get_routed_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_routed_payload", None)
        if cached is not None:
            return cached

        payload = self._parse_payload(getattr(self, "payload_json", ""))
        text = str(payload.get("user_request") or payload.get("input") or "")
        targets = self._extract_targets(text)
        route = self._requested_route(text, targets)
        jobs = payload.get("pending_jobs") or {}
        counts = self._counts(jobs)

        if route is None:
            route = self._first_available_route(counts)

        if route is None:
            decision = self._empty_decision("No explicit target and no runnable pending jobs found.", targets)
        else:
            explicit_target = any(targets.values())
            if explicit_target:
                target_status = self._target_status(payload, route, targets)
                if target_status["blocked"]:
                    decision = {
                        "job_route": "PREREQUISITE_BLOCKED",
                        "run_mode": "blocked",
                        "run_all_pending": False,
                        "selected_jobs": [],
                        "target_filter": targets,
                        "blocker_route": "TARGET_NOT_RUNNABLE",
                        "blocked_jobs": target_status["blocked_jobs"],
                        "reason": target_status["reason"],
                    }
                else:
                    decision = self._execution_decision(route, "targeted", targets, target_status["selected_jobs"])
            else:
                selected_jobs = self._select_jobs(payload, route, targets)
                blocker = self._blocking_route(route, counts)
                if blocker:
                    decision = {
                        "job_route": "PREREQUISITE_BLOCKED",
                        "run_mode": "blocked",
                        "run_all_pending": False,
                        "selected_jobs": [],
                        "target_filter": targets,
                        "blocker_route": blocker,
                        "reason": f"{route} cannot start because pending {blocker} jobs remain.",
                    }
                elif not selected_jobs:
                    decision = self._empty_decision(f"No pending {route} jobs found.", targets)
                else:
                    decision = self._execution_decision(route, "all_pending", targets, selected_jobs)

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
        }
        routed.setdefault("history", []).append(
            {
                "step": "job_target_route",
                "message": f"job_route={routed['job_route']}, run_mode={routed['run_mode']}, count={len(routed['selected_jobs'])}",
            }
        )
        self._cached_routed_payload = routed
        return routed

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
            "map_ids": self._extract_number_values(text, r"map[_\s-]*id|mapid"),
            "sql_ids": self._extract_text_values(text, r"sql[_\s-]*id|sqlid"),
            "space_nms": self._extract_text_values(text, r"space[_\s-]*nm|spacenm|space"),
        }

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
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
