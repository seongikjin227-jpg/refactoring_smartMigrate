from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any

from lfx.base.flow_controls.loop_utils import (
    execute_loop_body,
    extract_loop_output,
    get_loop_body_start_edge,
    get_loop_body_start_vertex,
    get_loop_body_vertices,
    validate_data_input,
)
from lfx.components.processing.converter import convert_to_data
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")


class NewType18BFullWorkflowLoop2(Component):

    display_name = "18B Full Workflow Loop2"
    description = "Runs the Full Workflow queue with dynamic DB refresh and phase-aware insertion."
    documentation = "https://docs.langflow.org/loop"
    name = "NewType18BFullWorkflowLoop2"
    icon = "Infinity"

    inputs = [
        HandleInput(
            name="data",
            display_name="Full Workflow Jobs",
            info="Mixed DB Migration, SQL Conversion, SQL Tuning, and SQL Formatting job rows.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
    ]

    outputs = [
        Output(display_name="Item", name="item", method="item_output", types=["Data"], allows_loop=True, loop_types=["Data"], group_outputs=True),
        Output(display_name="Done", name="done", method="done_output", types=["Data"]),
    ]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def initialize_data(self) -> None:
        if self.ctx.get(f"{self._id}_initialized", False):
            return
        data_list = self._validate_data(self.data)
        for index, item in enumerate(data_list, start=1):
            self._validate_job(self._data_dict(item), index)
        self.update_ctx({f"{self._id}_data": data_list, f"{self._id}_index": 0, f"{self._id}_initialized": True})

    # Langflow Message 입력을 Loop가 처리할 Data 객체로 변환한다.
    def _convert_message_to_data(self, message: Message) -> Data:
        return convert_to_data(message, auto_parse=False)

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_data(self, data: Any) -> list[Data]:
        if isinstance(data, Message):
            data = self._convert_message_to_data(data)
        elif isinstance(data, list):
            normalized: list[Any] = []
            for item in data:
                if isinstance(item, Message):
                    normalized.append(self._convert_message_to_data(item))
                elif isinstance(item, DataFrame):
                    normalized.extend(item.to_data_list())
                else:
                    normalized.append(item)
            data = normalized
        return validate_data_input(data)

    # Langflow Loop body에 포함될 graph vertex 집합을 반환한다.
    def get_loop_body_vertices(self) -> set[str]:
        if not hasattr(self, "_vertex") or self._vertex is None:
            return set()
        return get_loop_body_vertices(vertex=self._vertex, graph=self.graph, get_incoming_edge_by_target_param_fn=self.get_incoming_edge_by_target_param)

    # payload나 graph 설정에서 필요한 값을 꺼내 표준 형태로 반환한다.
    def _get_loop_body_start_vertex(self) -> str | None:
        if not hasattr(self, "_vertex") or self._vertex is None:
            return None
        return get_loop_body_start_vertex(vertex=self._vertex)

    # 문자열이나 payload에서 후속 로직에 필요한 값을 추출한다.
    def _extract_loop_output(self, results: list[Any]) -> Data:
        end_vertex_id = self.get_incoming_edge_by_target_param("item")
        return extract_loop_output(results=results, end_vertex_id=end_vertex_id)

    # Langflow Loop body graph를 각 Data item에 대해 비동기로 실행한다.
    async def execute_loop_body(self, data_list: list[Data], event_manager=None) -> list[Data]:
        loop_body_vertex_ids = self.get_loop_body_vertices()
        start_vertex_id = self._get_loop_body_start_vertex()
        start_edge = get_loop_body_start_edge(self._vertex)
        end_vertex_id = self.get_incoming_edge_by_target_param("item")
        return await execute_loop_body(
            graph=self.graph,
            data_list=data_list,
            loop_body_vertex_ids=loop_body_vertex_ids,
            start_vertex_id=start_vertex_id,
            start_edge=start_edge,
            end_vertex_id=end_vertex_id,
            event_manager=event_manager,
        )

    # 현재 loop index의 item을 실행하고 다음 item 또는 완료 상태를 계산한다.
    async def _iterate(self) -> list[Data]:
        if self.ctx.get(f"{self._id}_iterated", False):
            cached_error = self.ctx.get(f"{self._id}_iteration_error")
            if cached_error is not None:
                raise cached_error
            return self.ctx.get(f"{self._id}_aggregated", [])

        import time

        started_at = time.perf_counter()
        try:
            self.initialize_data()
            data_list = self.ctx.get(f"{self._id}_data", [])
            if not data_list:
                self.update_ctx({f"{self._id}_aggregated": [], f"{self._id}_iterated": True})
                return []
            aggregated_results = []
            migration_failed = False
            abort_reason = ""
            skipped_plan_counts = {route: 0 for route in ROUTE_ORDER}

            # cursor는 "다음에 실행할 data_list index"다.
            # 예: cursor=6이면 data_list[0]~data_list[5]는 이미 실행/스킵 판단이 끝났고,
            # data_list[6]이 지금 실행 후보라는 뜻이다. 리스트에서 item을 pop하지 않고 cursor만 전진시킨다.
            cursor = int(self.ctx.get(f"{self._id}_index", 0) or 0)
            dynamic_added_count = int(self.ctx.get(f"{self._id}_dynamic_added_count", 0) or 0)
            while cursor < len(data_list):
                # item을 loop body로 넘기기 전에 DB 자동 실행 대상 목록을 먼저 refresh한다.
                # 이렇게 해야 "바로 다음에 실행할 job"도 data_list[cursor:]에 포함된 상태로 중복 비교된다.
                # 예: 다음 job이 104이고 DB 자동 실행 대상 조회에도 104가 있으면, 새 job으로 added 처리하지 않는다.
                next_payload = self._data_dict(data_list[cursor])
                dynamic_added_count += self._refresh_dynamic_queue(data_list, cursor, self._route(next_payload))

                # refresh 과정에서 cursor 위치에 더 앞 phase job이 삽입될 수 있다.
                # 따라서 refresh 후에 다시 data_list[cursor]를 읽어 실제 실행할 item을 확정한다.
                # 예: SQL_CONVERSION 104를 실행하려던 순간 MIG 101이 새 자동 실행 대상으로 들어오면,
                # MIG 101이 cursor 위치에 삽입되고 이번 loop에서는 101이 실행된다.
                index = cursor
                item = data_list[cursor]
                item_payload = self._data_dict(item)
                if self._route(item_payload) != "MIG":
                    db_gate = self._db_migration_phase_gate(item_payload)
                    if db_gate.get("block_sql"):
                        skipped_plan_counts = self._plan_counts(data_list[index:])
                        abort_reason = str(db_gate.get("reason") or "DB Migration is not 100% PASS; SQL phases were not started.")
                        self._log_workflow_abort(abort_reason, db_gate)
                        break

                if migration_failed and self._route(item_payload) != "MIG":
                    skipped_plan_counts = self._plan_counts(data_list[index:])
                    abort_reason = "DB Migration failed; SQL Conversion and downstream phases were skipped because DB Migration is not 100% PASS."
                    self._log_workflow_abort(abort_reason, {"pending_null_count": 0, "fail_count": 1})
                    break

                item_results = await self.execute_loop_body([item], event_manager=self._event_manager)
                aggregated_results.extend(item_results)
                for result in item_results:
                    result_payload = self._data_dict(result)
                    if self._migration_abort_signal(result_payload):
                        migration_failed = True

                cursor += 1
                self.update_ctx({f"{self._id}_index": cursor})

            self.update_ctx(
                {
                    f"{self._id}_workflow_aborted": bool(abort_reason),
                    f"{self._id}_abort_reason": abort_reason,
                    f"{self._id}_skipped_plan_counts": skipped_plan_counts,
                    f"{self._id}_dynamic_added_count": dynamic_added_count,
                }
            )
        except Exception as exc:
            from lfx.log.logger import logger

            elapsed = time.perf_counter() - started_at
            await logger.aexception(f"Full Workflow loop {self._id} failed while executing loop body")
            self.update_ctx({f"{self._id}_iteration_error": exc, f"{self._id}_iterated": True})
            raise

        elapsed = time.perf_counter() - started_at
        self.update_ctx({f"{self._id}_aggregated": aggregated_results, f"{self._id}_iterated": True})
        return aggregated_results

    # Loop body로 전달할 현재 item payload를 반환한다.
    async def item_output(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before item_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "INFO", "ITEM_OUTPUT", "START", 0]})
        try:
            self.stop("item")
            try:
                if self._vertex is not None:
                    await self._iterate()
            finally:
                self.stop("item")
            data_list = self.ctx.get(f"{self._id}_data", [])
            __log_result = Data(data={"count": len(data_list), "items": [self._data_dict(item) for item in data_list]})
            logging.getLogger("smartmigrate.workflow").info("after item_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "INFO", "ITEM_OUTPUT", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error item_output: {exc}", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "ERROR", "ITEM_OUTPUT", "ERROR", 0]})
            raise

    # Loop가 끝났을 때 dashboard/summary로 넘길 완료 payload를 반환한다.
    async def done_output(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before done_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "INFO", "DONE_OUTPUT", "START", 0]})
        try:
            if self._vertex is not None:
                await self._iterate()
            data_list = self.ctx.get(f"{self._id}_data", [])
            first_payload = self._data_dict(data_list[0]) if data_list else {}
            results = [self._data_dict(item) for item in self.ctx.get(f"{self._id}_aggregated", [])]
            workflow_summary = self._summary(results, data_list, self.ctx.get(f"{self._id}_skipped_plan_counts", {}))
            workflow_aborted = bool(self.ctx.get(f"{self._id}_workflow_aborted", False))
            abort_reason = str(self.ctx.get(f"{self._id}_abort_reason", "") or "")
            skipped_plan_counts = dict(self.ctx.get(f"{self._id}_skipped_plan_counts", {}) or {})
            payload = {
                "component": "18B_fullWorkflowLoop2",
                "job_route": "FULL_WORKFLOW",
                "full_workflow": True,
                "loop_done": True,
                "db_config": dict(first_payload.get("db_config") or {}),
                "workflow_plan_counts": dict(first_payload.get("workflow_plan_counts") or self._plan_counts(data_list)),
                "aggregated_results": results,
                "workflow_summary": workflow_summary,
                "workflow_aborted": workflow_aborted,
                "abort_reason": abort_reason,
                "skipped_plan_counts": skipped_plan_counts,
                "done_reason": self._done_reason(data_list, results, workflow_aborted, abort_reason, skipped_plan_counts),
                "next_node": "18D_fullWorkflowDashboard",
            }
            self.status = payload
            __log_result = Data(data=payload)
            self._log_done_output(payload)
            logging.getLogger("smartmigrate.workflow").info("after done_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "INFO", "DONE_OUTPUT", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error done_output: {exc}", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP2", "ERROR", "DONE_OUTPUT", "ERROR", 0]})
            raise

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_job(self, payload: dict[str, Any], index: int) -> None:
        route = self._route(payload)
        if route == "MIG":
            if str(self._payload_value(payload, "map_id") or "").strip():
                return
            raise ValueError(f"18B MIG item {index} requires map_id")
        if route in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            if str(self._payload_value(payload, "sql_seq") or "").strip() or (
                str(self._payload_value(payload, "space_nm") or "").strip()
                and str(self._payload_value(payload, "sql_id") or "").strip()
            ):
                return
            raise ValueError(f"18B {route} item {index} requires sql_seq or space_nm+sql_id")
        raise ValueError(f"18B item {index} has invalid job_route={route}")

    # 단계별 실행 결과를 dashboard용 집계 구조로 요약한다.
    def _summary(self, results: list[dict[str, Any]], data_list: list[Any], skipped_plan_counts: dict[str, Any] | None = None) -> dict[str, Any]:
        plan_counts = self._plan_counts(data_list)
        summary: dict[str, dict[str, int]] = {
            route: {"planned": int(plan_counts.get(route) or 0), "completed": 0, "pass": 0, "fail": 0, "skipped": 0}
            for route in ROUTE_ORDER
        }
        for result in results:
            route = str(result.get("planned_job_route") or result.get("job_route") or "").upper()
            if route not in summary:
                continue
            summary[route]["completed"] += 1
            if self._is_failure_status(result.get("status")):
                summary[route]["fail"] += 1
            elif self._is_success(route, result):
                summary[route]["pass"] += 1
            elif result.get("workflow_blocked") or result.get("not_runnable") or result.get("tuning_skipped") or result.get("formatting_skipped") or result.get("skipped"):
                summary[route]["skipped"] += 1
            else:
                summary[route]["fail"] += 1
        for route, count in dict(skipped_plan_counts or {}).items():
            if route in summary:
                summary[route]["skipped"] += int(count or 0)
        return summary

    # 상태나 값이 특정 조건에 해당하는지 boolean으로 판단한다.
    def _is_success(self, route: str, result: dict[str, Any]) -> bool:
        stages = result.get("stages") or {}
        status = str(result.get("status") or "").upper()
        if route == "MIG":
            return bool(result.get("ok")) and status == "PASS"
        if route == "SQL_CONVERSION":
            stage = stages.get("conversion") or {}
            return bool(stage.get("ok")) or status in {"PASS", "PASS-CONVERSION", "PASS-TUNING", "FORMATTED"}
        if route == "SQL_TUNING":
            stage = stages.get("tuning") or {}
            return bool(stage.get("ok")) or status in {"PASS", "PASS-TUNING", "FORMATTED"}
        if route == "SQL_FORMATTING":
            stage = stages.get("formatting") or {}
            return bool(stage.get("ok")) or status == "FORMATTED"
        return bool(result.get("ok"))

    # 상태나 값이 특정 조건에 해당하는지 boolean으로 판단한다.
    def _is_failure_status(self, status: Any) -> bool:
        value = str(status or "").strip().upper()
        return value.startswith("FAIL-")

    # DB Migration 결과가 SQL 후속 단계를 막아야 하는 상태인지 판단한다.
    def _migration_blocks_sql(self, result: dict[str, Any]) -> bool:
        status = str(result.get("status") or "").strip().upper()
        if status in {"PASS", "SUCCESS"} and bool(result.get("ok", True)):
            return False
        return True

    # full workflow loop를 중단해야 하는 migration 실패 신호인지 판단한다.
    def _migration_abort_signal(self, result: dict[str, Any]) -> bool:
        if bool(result.get("full_workflow_abort")):
            return True
        return self._route(result) == "MIG" and self._migration_blocks_sql(result)

    # DB Migration 전체 상태를 DB에서 확인해 SQL 단계 진입 가능 여부를 결정한다.
    def _db_migration_phase_gate(self, payload: dict[str, Any]) -> dict[str, Any]:
        db_config = dict(payload.get("db_config") or {})
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    SUM(CASE WHEN NVL(UPPER(USE_YN), 'N') = 'Y' AND STATUS IS NULL THEN 1 ELSE 0 END) AS PENDING_NULL_COUNT,
                    SUM(
                        CASE
                            WHEN NVL(UPPER(USE_YN), 'N') = 'Y'
                             AND (UPPER(STATUS) = 'FAIL' OR UPPER(STATUS) LIKE 'FAIL-%')
                            THEN 1 ELSE 0
                        END
                    ) AS FAIL_COUNT
                  FROM {table}
                """
            )
            row = cur.fetchone() or (0, 0)

        pending_null_count = self._num(row[0])
        fail_count = self._num(row[1])
        block_sql = pending_null_count > 0 or fail_count > 0
        return {
            "block_sql": block_sql,
            "pending_null_count": pending_null_count,
            "fail_count": fail_count,
            "reason": (
                f"DB Migration is not 100% PASS; pending_null={pending_null_count}, fail={fail_count}. SQL Conversion and downstream phases were not started."
                if block_sql
                else ""
            ),
        }

    # loop 입력 목록을 route별 예정 작업 수로 집계한다.
    # DB Migration phase가 100% PASS가 아니어서 SQL phase를 시작하지 않는 event를 남긴다.
    # 18B Done output이 반환되는 이유를 payload와 log에 남길 문장으로 만든다.
    def _done_reason(
        self,
        data_list: list[Any],
        results: list[dict[str, Any]],
        workflow_aborted: bool,
        abort_reason: str,
        skipped_plan_counts: dict[str, Any],
    ) -> str:
        if not data_list:
            return "NO_PLANNED_JOB: Full Workflow had no planned jobs."
        if workflow_aborted:
            skipped_total = sum(self._num(value) for value in skipped_plan_counts.values())
            suffix = f" skipped={skipped_total}" if skipped_total else ""
            return f"ABORTED: {abort_reason or 'Full Workflow stopped before all planned jobs were executed.'}{suffix}"
        planned_total = len(data_list)
        completed_total = len(results)
        return f"COMPLETED: all planned jobs were handled. completed={completed_total}, planned={planned_total}"

    # 18B Done output 직전에 종료 사유를 workflow log로 남긴다.
    def _log_done_output(self, payload: dict[str, Any]) -> None:
        reason = str(payload.get("done_reason") or "").strip()
        workflow_aborted = bool(payload.get("workflow_aborted"))
        logging.getLogger("smartmigrate.workflow").log(
            logging.WARNING if workflow_aborted else logging.INFO,
            reason or "Full Workflow Done output emitted.",
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "18B_FULL_LOOP2",
                    "WARN" if workflow_aborted else "INFO",
                    "DONE_OUTPUT",
                    "ABORTED" if workflow_aborted else "DONE",
                    0,
                    reason,
                ]
            },
        )

    def _log_workflow_abort(self, reason: str, gate: dict[str, Any]) -> None:
        logging.getLogger("smartmigrate.workflow").warning(
            reason,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "18B_FULL_LOOP2",
                    "WARN",
                    "DB_MIGRATION_GATE",
                    "ABORT",
                    0,
                    f"pending_null={gate.get('pending_null_count', 0)}, fail={gate.get('fail_count', 0)}; {reason}",
                ]
            },
        )

    # 실행 중 DB를 다시 조회해 현재 남은 큐에 없는 자동 실행 대상 job을 phase 순서에 맞게 추가한다.
    def _refresh_dynamic_queue(self, data_list: list[Data], cursor: int, next_route: str) -> int:
        if not data_list:
            return 0
        first_payload = self._data_dict(data_list[0])
        db_config = dict(first_payload.get("db_config") or {})
        if not db_config:
            return 0

        # Oracle 원본 테이블에서 지금 시점의 자동 실행 대상 job을 다시 읽는다.
        # 이 값은 "DB 기준으로 현재 새로 실행 가능해 보이는 후보"일 뿐이며,
        # 이미 메모리 큐의 남은 구간에 있는 job은 아래 remaining_keys 비교로 제외한다.
        pending_jobs = self._load_pending_jobs_from_db(db_config, first_payload)

        # cursor 위치의 "지금 실행할 job"과 그 뒤의 남은 job만 중복 기준으로 본다.
        # cursor 앞쪽에서 이미 실행된 job은 사용자가 FAIL stage를 유지한 채 RETRY_COUNT를 0으로 바꾸면 새 요청으로 재추가될 수 있다.
        # 따라서 전체 data_list가 아니라 data_list[cursor:]만 비교한다.
        # 이 정책의 의미:
        # - data_list[cursor:]에 있으면 이미 이번 run에서 실행 예정이므로 추가하지 않는다.
        # - data_list[:cursor]에만 있으면 이미 지나간 작업이므로, DB가 다시 자동 실행 대상으로 선정되면 새 요청으로 본다.
        remaining_keys = {
            self._job_key(self._data_dict(item))
            for item in data_list[cursor:]
            if self._job_key(self._data_dict(item)) is not None
        }
        added = 0
        added_jobs: list[dict[str, Any]] = []
        seen_new_keys: set[tuple[Any, ...]] = set()
        for job in pending_jobs:
            key = self._job_key(job)

            # key가 없으면 안전하게 무시한다.
            # key가 remaining_keys에 있으면 "지금 실행할 job 또는 앞으로 실행할 job"이므로 중복 추가하지 않는다.
            # key가 seen_new_keys에 있으면 같은 refresh 안에서 DB query 결과가 중복된 것이므로 한 번만 추가한다.
            if key is None or key in remaining_keys or key in seen_new_keys:
                continue

            # 새 자동 실행 대상 job은 route phase와 priority를 기준으로 cursor 이후 적절한 위치에 삽입한다.
            # 삽입 후에는 remaining_keys에도 즉시 등록해 같은 refresh 안에서 다시 추가되지 않게 한다.
            insert_at = self._insert_dynamic_job(data_list, cursor, next_route, Data(data=job))
            remaining_keys.add(key)
            seen_new_keys.add(key)
            added += 1
            added_jobs.append({"job": job, "key": key, "insert_at": insert_at})

        if added:
            # 삽입 때문에 job_index, total_jobs, route_total_jobs가 바뀌므로 전체 큐 metadata를 다시 계산한다.
            # output payload 형식은 기존 18B와 맞춰야 하므로, 동적 큐 내부 상태는 로그로만 남긴다.
            self._reindex_jobs(data_list)
            self.update_ctx({f"{self._id}_data": data_list})
            self._log_dynamic_jobs_added(added_jobs, cursor, next_route, len(data_list))
        return added

    # Oracle 원본 테이블에서 현재 자동 실행 대상 job을 다시 읽는다. USER_EDITED는 대상 선정에 사용하지 않는다.
    def _load_pending_jobs_from_db(self, db_config: dict[str, Any], template_payload: dict[str, Any]) -> list[dict[str, Any]]:
        mig_table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        sql_table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            jobs: list[dict[str, Any]] = []

            # MIG 자동 실행 대상: USE_YN='Y', STATUS=NULL/FAIL/FAIL-*, RETRY_COUNT<2.
            jobs.extend(
                self._query_pending_jobs(
                    cur,
                    f"""
                    SELECT MAP_ID, PRIORITY, PRIOR_MAP_ID
                      FROM {mig_table}
                     WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                       AND (STATUS IS NULL OR UPPER(TRIM(NVL(STATUS, ''))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS, ''))) LIKE 'FAIL-%')
                       AND NVL(RETRY_COUNT, 0) < 2
                     ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                    """,
                    "MIG",
                    ["map_id", "priority", "prior_map_id"],
                    db_config,
                    template_payload,
                )
            )

            # SQL Conversion 자동 실행 대상: STATUS_CONVERSION=NULL/FAIL/FAIL-*, RETRY_COUNT<2.
            # Correct SQL 저장 action은 다음 FAIL stage와 RETRY_COUNT=0을 직접 저장한다.
            jobs.extend(
                self._query_pending_jobs(
                    cur,
                    f"""
                    SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                      FROM {sql_table}
                     WHERE (STATUS_CONVERSION IS NULL OR UPPER(TRIM(NVL(STATUS_CONVERSION, ''))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_CONVERSION, ''))) LIKE 'FAIL-%')
                       AND NVL(RETRY_COUNT, 0) < 2
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS ASC NULLS FIRST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_CONVERSION",
                    ["sql_seq", "sql_id", "space_nm", "priority"],
                    db_config,
                    template_payload,
                )
            )

            # SQL Tuning 자동 실행 대상: Conversion PASS, STATUS_TUNING=NULL/FAIL/FAIL-*, RETRY_COUNT<2.
            jobs.extend(
                self._query_pending_jobs(
                    cur,
                    f"""
                    SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                      FROM {sql_table}
                     WHERE UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                       AND (STATUS_TUNING IS NULL OR UPPER(TRIM(NVL(STATUS_TUNING, ''))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_TUNING, ''))) LIKE 'FAIL-%')
                       AND NVL(RETRY_COUNT, 0) < 2
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS ASC NULLS FIRST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_TUNING",
                    ["sql_seq", "sql_id", "space_nm", "priority"],
                    db_config,
                    template_payload,
                )
            )

            # SQL Formatting 자동 실행 대상 선정 조건: Tuning이 PASS 계열이고 FORMATTED_SQL이 비어 있는 row만 대상이다.
            # Formatting은 별도 STATUS 컬럼이 없으므로 formatted SQL 존재 여부로 자동 실행 대상을 판단한다.
            jobs.extend(
                self._query_pending_jobs(
                    cur,
                    f"""
                    SELECT SQL_SEQ, TO_CHAR(SQL_ID) AS SQL_ID, TO_CHAR(SPACE_NM) AS SPACE_NM, PRIORITY
                      FROM {sql_table}
                     WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                       AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                     ORDER BY PRIORITY ASC NULLS LAST, UPD_TS ASC NULLS FIRST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                    """,
                    "SQL_FORMATTING",
                    ["sql_seq", "sql_id", "space_nm", "priority"],
                    db_config,
                    template_payload,
                )
            )
            return jobs

    # cursor가 이미 지난 앞 구간은 건드리지 않고, 새 job을 다음 실행 가능 위치에 삽입한다.
    def _insert_dynamic_job(self, data_list: list[Data], cursor: int, next_route: str, item: Data) -> int:
        payload = self._data_dict(item)
        new_phase = self._phase_index(self._route(payload))
        insert_at = len(data_list)
        for pos in range(cursor, len(data_list)):
            existing_payload = self._data_dict(data_list[pos])
            existing_phase = self._phase_index(self._route(existing_payload))

            # 새 job보다 앞 phase의 기존 job은 반드시 먼저 실행되어야 하므로 지나간다.
            # 예: 새 job이 SQL_TUNING이면 남아 있는 MIG/SQL_CONVERSION 뒤에 배치한다.
            if existing_phase < new_phase:
                continue

            # 새 job보다 뒤 phase를 처음 만나면 그 직전에 삽입한다.
            # 예: MIG 실행 중 새 SQL_CONVERSION이 들어오면 SQL_CONVERSION phase 시작 지점에 들어간다.
            # 예: SQL_CONVERSION 실행 중 새 MIG가 들어오면 cursor 위치, 즉 다음 실행 대상으로 들어간다.
            if existing_phase > new_phase:
                insert_at = pos
                break

            # 같은 phase에서는 PRIORITY ASC NULLS LAST 기준을 유지한다.
            # priority 값이 더 작은 새 job은 기존 같은 phase job 앞에 들어간다.
            if self._priority_key(payload) < self._priority_key(existing_payload):
                insert_at = pos
                break
        data_list.insert(insert_at, item)
        return insert_at

    # refresh 한 번에 추가된 동적 job 목록을 한 줄의 workflow log로 남긴다.
    def _log_dynamic_jobs_added(
        self,
        added_jobs: list[dict[str, Any]],
        cursor: int,
        next_route: str,
        queue_size: int,
    ) -> None:
        summaries = []
        for item in added_jobs:
            job = dict(item.get("job") or {})
            key = item.get("key")
            insert_at = item.get("insert_at")
            summaries.append(
                f"{self._route(job)} key={key} priority={self._payload_value(job, 'priority')} insert_at={insert_at}"
            )

        # 동적 추가 로그는 job마다 여러 줄로 찍지 않고 refresh 1회당 한 줄로 남긴다.
        # count/cursor/next_route/queue_size/jobs를 같이 남겨 테스트 중 큐 삽입 결과를 한눈에 확인한다.
        message = (
            f"dynamic automatic execution candidate jobs added count={len(added_jobs)}, cursor={cursor}, "
            f"next_route={next_route}, queue_size={queue_size}, jobs=[{'; '.join(summaries)}]"
        )
        logging.getLogger("smartmigrate.workflow").info(
            message,
            extra={
                "workflow_log": [
                    0,
                    "WORKFLOW",
                    "18B_FULL_LOOP2",
                    "INFO",
                    "DYNAMIC_QUEUE_REFRESH",
                    "ADD",
                    len(added_jobs),
                    message,
                ]
            },
        )

    # route별 workflow log 첫 번째 식별자에 넣을 값을 고른다.
    def _workflow_log_job_id(self, job: dict[str, Any]) -> Any:
        if self._route(job) == "MIG":
            return self._payload_value(job, "map_id") or 0
        sql_seq = self._payload_value(job, "sql_seq")
        if str(sql_seq or "").strip():
            return sql_seq
        return f"{self._payload_value(job, 'sql_id') or ''} / {self._payload_value(job, 'space_nm') or ''}"[:100]

    # cursor 이후 큐에 같은 job이 있는지 비교하기 위한 route별 고유 key를 만든다.
    def _job_key(self, payload: dict[str, Any]) -> tuple[Any, ...] | None:
        route = self._route(payload)
        if route == "MIG":
            # DB Migration은 MAP_ID 하나로 row가 식별된다.
            map_id = str(self._payload_value(payload, "map_id") or "").strip()
            return (route, map_id) if map_id else None
        if route in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            # SQL 계열은 SQL_SEQ 또는 SPACE_NM + SQL_ID로 row를 식별한다.
            # 같은 SQL_ID라도 conversion/tuning/formatting은 서로 다른 phase job이므로 route도 key에 포함한다.
            sql_seq = str(self._payload_value(payload, "sql_seq") or "").strip()
            if sql_seq:
                return (route, "SQL_SEQ", sql_seq)
            space_nm = str(self._payload_value(payload, "space_nm") or "").strip().upper()
            sql_id = str(self._payload_value(payload, "sql_id") or "").strip().upper()
            return (route, "SQL_KEY", space_nm, sql_id) if space_nm and sql_id else None
        return None

    # route를 phase 비교용 숫자로 변환한다.
    def _phase_index(self, route: str) -> int:
        try:
            return ROUTE_ORDER.index(str(route or "").upper())
        except ValueError:
            return len(ROUTE_ORDER)

    # PRIORITY가 작을수록 먼저 실행되도록 정렬 key를 만든다.
    def _priority_key(self, payload: dict[str, Any]) -> tuple[int, str]:
        priority = self._num(self._payload_value(payload, "priority"))
        if priority <= 0:
            priority = 999999999
        key = self._job_key(payload)
        return priority, "|".join(str(part) for part in key or ())

    # 동적 조회 결과 row를 18B loop item payload 형태로 맞춘다.
    def _query_pending_jobs(
        self,
        cur: Any,
        sql: str,
        route: str,
        columns: list[str],
        db_config: dict[str, Any],
        template_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cur.execute(sql)
        jobs: list[dict[str, Any]] = []
        for row in cur.fetchall():
            # 동적으로 조회한 row도 18A가 만든 DataFrame row와 같은 payload 형태로 맞춘다.
            # 그래야 10C/12C/15C/17C가 기존 loop item과 동일하게 처리할 수 있다.
            job: dict[str, Any] = {
                "component": "18A_fullWorkflowJobsToLoopTable",
                "job_route": route,
                "planned_job_route": route,
                "job_name": self._job_name(route),
                "job_type": "MIG" if route == "MIG" else "SQL",
                "route_label": self._route_label(route),
                "run_mode": template_payload.get("run_mode") or "all_pending",
                "full_workflow": True,
                "db_config": dict(db_config),
                "history": list(template_payload.get("history") or []),
                "max_retry": template_payload.get("max_retry"),
            }
            for index, column in enumerate(columns):
                job[column] = self._json_value(row[index])
            jobs.append(job)
        return jobs

    # 삽입 이후 전체 job index와 route별 계획 수를 다시 계산한다.
    def _reindex_jobs(self, data_list: list[Data]) -> None:
        # 동적 insert가 발생하면 total_jobs, route_total_jobs, job_index가 모두 바뀔 수 있다.
        # downstream dashboard와 summary가 기존 payload contract를 그대로 읽도록 모든 item metadata를 재계산한다.
        route_totals = self._plan_counts(data_list)
        route_seen = {route: 0 for route in ROUTE_ORDER}
        total = len(data_list)
        for index, item in enumerate(data_list, start=1):
            payload = self._data_dict(item)
            route = self._route(payload)
            if route in route_seen:
                route_seen[route] += 1
            payload.update(
                {
                    "phase_index": self._phase_index(route) + 1,
                    "phase_count": len(ROUTE_ORDER),
                    "route_job_index": route_seen.get(route, 0),
                    "route_total_jobs": route_totals.get(route, 0),
                    "job_index": index,
                    "total_jobs": total,
                    "completed_before": index - 1,
                    "workflow_plan_counts": dict(route_totals),
                }
            )
            if isinstance(item, Data):
                item.data = payload

    # route 값을 loop item에 저장할 내부 job_name 문자열로 변환한다.
    def _job_name(self, route: str) -> str:
        return {
            "MIG": "migration",
            "SQL_CONVERSION": "conversion",
            "SQL_TUNING": "tuning",
            "SQL_FORMATTING": "formatting",
        }.get(route, str(route or "").lower())

    # route 값을 사용자 표시용 라벨로 변환한다.
    def _route_label(self, route: str) -> str:
        return {
            "MIG": "DB Migration",
            "SQL_CONVERSION": "SQL Conversion",
            "SQL_TUNING": "SQL Tuning",
            "SQL_FORMATTING": "SQL Formatting",
        }.get(route, str(route or ""))

    def _plan_counts(self, data_list: list[Any]) -> dict[str, int]:
        counts = {route: 0 for route in ROUTE_ORDER}
        for item in data_list:
            payload = self._data_dict(item)
            route = self._route(payload)
            if route in counts:
                counts[route] += 1
        return counts

    # payload에서 workflow route 값을 읽어 표준 route 이름으로 정규화한다.
    def _route(self, payload: dict[str, Any]) -> str:
        route = str(self._payload_value(payload, "planned_job_route") or self._payload_value(payload, "job_route") or "").strip().upper()
        aliases = {
            "DB_MIGRATION": "MIG",
            "MIGRATION": "MIG",
            "SQL": "SQL_CONVERSION",
            "CONVERSION": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
        }
        return aliases.get(route, route)

    # payload key가 대문자/소문자/혼합 표기로 들어와도 같은 값으로 읽는다.
    def _payload_value(self, payload: dict[str, Any], key: str) -> Any:
        for candidate in (key, key.upper(), key.lower()):
            if candidate in payload:
                return payload.get(candidate)
        target = key.lower()
        for candidate, value in payload.items():
            if str(candidate).lower() == target:
                return value
        return None

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    # DB cursor 값이나 CLOB 값을 JSON 직렬화 가능한 기본 타입으로 변환한다.
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
    def _connect(self, db_config: dict[str, Any]):
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(db_config.get("db_username") or "").strip(),
            password=str(db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip().upper()
        if "." in value:
            return value
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if not clean_schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        clean_schema = self._clean_identifier(clean_schema)
        return f"{clean_schema}.{clean_table}"

    # 동적 SQL identifier에 안전한 Oracle 문자만 허용한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # Loop item/Data/Message 값을 dict로 변환해 공통 처리한다.
    def _data_dict(self, item: Any) -> dict[str, Any]:
        if isinstance(item, Data):
            return dict(item.data or {})
        if isinstance(item, Message):
            parsed = self._parse_json_text(item.text)
            if parsed is not None:
                return parsed
            return dict(self._convert_message_to_data(item).data or {})
        if isinstance(item, dict):
            return dict(item)
        return {"value": item}

    # 문자열 입력에서 JSON 객체를 파싱해 후속 로직이 쓰는 dict로 만든다.
    def _parse_json_text(self, text: Any) -> dict[str, Any] | None:
        import json
        import re

        value = str(text or "").strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
            value = re.sub(r"\s*```$", "", value)
        try:
            parsed = json.loads(value) if value else None
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
