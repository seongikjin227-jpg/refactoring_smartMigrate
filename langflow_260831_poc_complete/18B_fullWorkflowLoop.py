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


class NewType18BFullWorkflowLoop(Component):

    display_name = "18B Full Workflow Loop"
    description = "Runs the Full Workflow queue one item at a time, preserving phase order."
    documentation = "https://docs.langflow.org/loop"
    name = "NewType18BFullWorkflowLoop"
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
            for index, item in enumerate(data_list):
                item_payload = self._data_dict(item)
                if self._route(item_payload) != "MIG":
                    db_gate = self._db_migration_phase_gate(item_payload)
                    if db_gate.get("block_sql"):
                        skipped_plan_counts = self._plan_counts(data_list[index:])
                        abort_reason = str(db_gate.get("reason") or "DB Migration failed; SQL phases were not started.")
                        break

                if migration_failed and self._route(item_payload) != "MIG":
                    skipped_plan_counts = self._plan_counts(data_list[index:])
                    abort_reason = "DB Migration failed; SQL Conversion and downstream phases were skipped because migration is still failing."
                    break

                item_results = await self.execute_loop_body([item], event_manager=self._event_manager)
                aggregated_results.extend(item_results)
                for result in item_results:
                    result_payload = self._data_dict(result)
                    if self._migration_abort_signal(result_payload):
                        migration_failed = True

            self.update_ctx(
                {
                    f"{self._id}_workflow_aborted": bool(abort_reason),
                    f"{self._id}_abort_reason": abort_reason,
                    f"{self._id}_skipped_plan_counts": skipped_plan_counts,
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
        logging.getLogger("smartmigrate.workflow").info("before item_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "INFO", "ITEM_OUTPUT", "START", 0]})
        try:
            self.stop("item")
            try:
                if self._vertex is not None:
                    await self._iterate()
            finally:
                self.stop("item")
            data_list = self.ctx.get(f"{self._id}_data", [])
            __log_result = Data(data={"count": len(data_list), "items": [self._data_dict(item) for item in data_list]})
            logging.getLogger("smartmigrate.workflow").info("after item_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "INFO", "ITEM_OUTPUT", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error item_output: {exc}", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "ERROR", "ITEM_OUTPUT", "ERROR", 0]})
            raise

    # Loop가 끝났을 때 dashboard/summary로 넘길 완료 payload를 반환한다.
    async def done_output(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before done_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "INFO", "DONE_OUTPUT", "START", 0]})
        try:
            if self._vertex is not None:
                await self._iterate()
            data_list = self.ctx.get(f"{self._id}_data", [])
            first_payload = self._data_dict(data_list[0]) if data_list else {}
            results = [self._data_dict(item) for item in self.ctx.get(f"{self._id}_aggregated", [])]
            payload = {
                "component": "18B_fullWorkflowLoop",
                "job_route": "FULL_WORKFLOW",
                "full_workflow": True,
                "loop_done": True,
                "db_config": dict(first_payload.get("db_config") or {}),
                "workflow_plan_counts": dict(first_payload.get("workflow_plan_counts") or self._plan_counts(data_list)),
                "aggregated_results": results,
                "workflow_summary": self._summary(results, data_list, self.ctx.get(f"{self._id}_skipped_plan_counts", {})),
                "workflow_aborted": bool(self.ctx.get(f"{self._id}_workflow_aborted", False)),
                "abort_reason": str(self.ctx.get(f"{self._id}_abort_reason", "") or ""),
                "skipped_plan_counts": dict(self.ctx.get(f"{self._id}_skipped_plan_counts", {}) or {}),
                "next_node": "18D_fullWorkflowDashboard",
            }
            self.status = payload
            __log_result = Data(data=payload)
            logging.getLogger("smartmigrate.workflow").info("after done_output", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "INFO", "DONE_OUTPUT", "END", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error done_output: {exc}", extra={"workflow_log": [0, "WORKFLOW", "18B_FULL_LOOP", "ERROR", "DONE_OUTPUT", "ERROR", 0]})
            raise

    # 입력 payload나 job item이 실행 가능한 구조인지 검증한다.
    def _validate_job(self, payload: dict[str, Any], index: int) -> None:
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").upper()
        if route == "MIG":
            if str(payload.get("map_id") or "").strip():
                return
            raise ValueError(f"18B MIG item {index} requires map_id")
        if route in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            if str(payload.get("space_nm") or "").strip() and str(payload.get("sql_id") or "").strip():
                return
            raise ValueError(f"18B {route} item {index} requires space_nm+sql_id")
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
                             AND UPPER(STATUS) LIKE 'FAIL-%'
                            THEN 1 ELSE 0
                        END
                    ) AS FAIL_COUNT
                  FROM {table}
                """
            )
            row = cur.fetchone() or (0, 0)

        pending_null_count = self._num(row[0])
        fail_count = self._num(row[1])
        block_sql = pending_null_count == 0 and fail_count > 0
        return {
            "block_sql": block_sql,
            "pending_null_count": pending_null_count,
            "fail_count": fail_count,
            "reason": (
                f"DB Migration 종료 후 실패 상태가 {fail_count}건 있어 SQL Conversion 이후 작업을 시작하지 않습니다."
                if block_sql
                else ""
            ),
        }

    # loop 입력 목록을 route별 예정 작업 수로 집계한다.
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
        return str(payload.get("planned_job_route") or payload.get("job_route") or "").upper()

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

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
