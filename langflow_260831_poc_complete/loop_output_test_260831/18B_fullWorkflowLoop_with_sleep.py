from __future__ import annotations

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
from lfx.io import FloatInput, Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")


class LoopOutputTest18BFullWorkflowLoopWithSleep(Component):
    display_name = "Loop Output Test 18B Full Workflow Loop With Sleep"
    description = "Runs the Full Workflow queue one item at a time and sleeps between loop iterations for output timing tests."
    documentation = "https://docs.langflow.org/loop"
    name = "LoopOutputTest18BFullWorkflowLoopWithSleep"
    icon = "Infinity"

    inputs = [
        HandleInput(
            name="data",
            display_name="Full Workflow Jobs",
            info="Mixed DB Migration, SQL Conversion, SQL Tuning, and SQL Formatting job rows.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
        FloatInput(
            name="sleep_seconds",
            display_name="Sleep Seconds",
            info="Seconds to wait after each loop item finishes before the next item starts.",
            value=5.0,
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="Item", name="item", method="item_output", types=["Data"], allows_loop=True, loop_types=["Data"], group_outputs=True),
        Output(display_name="Done", name="done", method="done_output", types=["Data"]),
    ]

    def initialize_data(self) -> None:
        if self.ctx.get(f"{self._id}_initialized", False):
            return
        data_list = self._validate_data(self.data)
        for index, item in enumerate(data_list, start=1):
            self._validate_job(self._data_dict(item), index)
        self.update_ctx({f"{self._id}_data": data_list, f"{self._id}_index": 0, f"{self._id}_initialized": True})

    def _convert_message_to_data(self, message: Message) -> Data:
        return convert_to_data(message, auto_parse=False)

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

    def get_loop_body_vertices(self) -> set[str]:
        if not hasattr(self, "_vertex") or self._vertex is None:
            return set()
        return get_loop_body_vertices(vertex=self._vertex, graph=self.graph, get_incoming_edge_by_target_param_fn=self.get_incoming_edge_by_target_param)

    def _get_loop_body_start_vertex(self) -> str | None:
        if not hasattr(self, "_vertex") or self._vertex is None:
            return None
        return get_loop_body_start_vertex(vertex=self._vertex)

    def _extract_loop_output(self, results: list[Any]) -> Data:
        end_vertex_id = self.get_incoming_edge_by_target_param("item")
        return extract_loop_output(results=results, end_vertex_id=end_vertex_id)

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
            self.log(f"Starting Full Workflow loop over {len(data_list)} job(s)", name="Start")
            if not data_list:
                self.update_ctx({f"{self._id}_aggregated": [], f"{self._id}_iterated": True})
                return []
            first_payload = self._data_dict(data_list[0])
            workflow_db_config = dict(first_payload.get("db_config") or {})
            self._insert_log(workflow_db_config, 0, "WORKFLOW", "WORKFLOW_LOOP", "INFO", "LOOP_START", "START", f"start workflow loop total={len(data_list)}", 0, "")
            aggregated_results = []
            migration_failed = False
            abort_reason = ""
            skipped_plan_counts = {route: 0 for route in ROUTE_ORDER}
            sleep_seconds = self._sleep_seconds()
            for index, item in enumerate(data_list):
                item_payload = self._data_dict(item)
                if self._route(item_payload) != "MIG":
                    db_gate = self._db_migration_phase_gate(item_payload)
                    if db_gate.get("block_sql"):
                        skipped_plan_counts = self._plan_counts(data_list[index:])
                        abort_reason = str(db_gate.get("reason") or "DB Migration failed; SQL phases were not started.")
                        self.log(f"{abort_reason} stats={db_gate}", name="DB Phase Gate")
                        break

                if migration_failed and self._route(item_payload) != "MIG":
                    skipped_plan_counts = self._plan_counts(data_list[index:])
                    abort_reason = "DB Migration 결과에 실패/미완료 작업이 있어 SQL Conversion 이후 작업을 시작하지 않았습니다."
                    self.log(abort_reason, name="Phase Gate")
                    break

                db_config = dict(item_payload.get("db_config") or {})
                self._insert_log(db_config, 0, "WORKFLOW", "WORKFLOW_ITEM", "INFO", "ITEM_START", "START", f"start one item {index + 1}/{len(data_list)}", 0, "")
                item_results = await self.execute_loop_body([item], event_manager=self._event_manager)
                aggregated_results.extend(item_results)
                item_result_payload = self._data_dict(item_results[-1]) if item_results else {}
                item_status = str(item_result_payload.get("status") or "END")
                self._insert_log(db_config, 0, "WORKFLOW", "WORKFLOW_ITEM", "INFO", "ITEM_END", item_status, f"end one item {index + 1}/{len(data_list)}", 0, "")
                for result in item_results:
                    result_payload = self._data_dict(result)
                    if self._migration_abort_signal(result_payload):
                        migration_failed = True
                if sleep_seconds > 0 and index < len(data_list) - 1:
                    self._insert_log(db_config, 0, "WORKFLOW", "SLEEP_PROBE", "INFO", "SLEEP_BEFORE", "SLEEP_BEFORE", "SLEEP_BEFORE", 0, "")
                    time.sleep(sleep_seconds)
                    self._insert_log(db_config, 0, "WORKFLOW", "SLEEP_PROBE", "INFO", "SLEEP_AFTER", "SLEEP_AFTER", "SLEEP_AFTER", 0, "")

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
            self.log(f"Full Workflow loop failed after {elapsed:.3f}s: {exc}", name="Error")
            await logger.aexception(f"Full Workflow loop {self._id} failed while executing loop body")
            self.update_ctx({f"{self._id}_iteration_error": exc, f"{self._id}_iterated": True})
            raise

        elapsed = time.perf_counter() - started_at
        self.log(f"Completed {len(aggregated_results)} Full Workflow iteration(s) in {elapsed:.3f}s", name="Complete")
        self.update_ctx({f"{self._id}_aggregated": aggregated_results, f"{self._id}_iterated": True})
        return aggregated_results

    async def item_output(self) -> Data:
        self.stop("item")
        try:
            if self._vertex is not None:
                await self._iterate()
        finally:
            self.stop("item")
        data_list = self.ctx.get(f"{self._id}_data", [])
        return Data(data={"count": len(data_list), "items": [self._data_dict(item) for item in data_list]})

    async def done_output(self) -> Data:
        if self._vertex is not None:
            await self._iterate()
        data_list = self.ctx.get(f"{self._id}_data", [])
        first_payload = self._data_dict(data_list[0]) if data_list else {}
        results = [self._data_dict(item) for item in self.ctx.get(f"{self._id}_aggregated", [])]
        workflow_summary = self._summary(results, data_list, self.ctx.get(f"{self._id}_skipped_plan_counts", {}))
        self._insert_log(
            dict(first_payload.get("db_config") or {}),
            0,
            "WORKFLOW",
            "WORKFLOW_LOOP",
            "INFO",
            "LOOP_DONE",
            "DONE",
            f"done workflow loop completed={len(results)}/{len(data_list)}",
            0,
            "",
        )
        payload = {
            "component": "LoopOutputTest18BFullWorkflowLoopWithSleep",
            "job_route": "FULL_WORKFLOW",
            "full_workflow": True,
            "loop_done": True,
            "db_config": dict(first_payload.get("db_config") or {}),
            "workflow_plan_counts": dict(first_payload.get("workflow_plan_counts") or self._plan_counts(data_list)),
            "aggregated_results": results,
            "workflow_summary": workflow_summary,
            "workflow_aborted": bool(self.ctx.get(f"{self._id}_workflow_aborted", False)),
            "abort_reason": str(self.ctx.get(f"{self._id}_abort_reason", "") or ""),
            "skipped_plan_counts": dict(self.ctx.get(f"{self._id}_skipped_plan_counts", {}) or {}),
            "next_node": "18D_fullWorkflowDashboard",
        }
        self.status = payload
        return Data(data=payload)

    def _sleep_seconds(self) -> float:
        try:
            return max(0.0, min(3600.0, float(getattr(self, "sleep_seconds", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _validate_job(self, payload: dict[str, Any], index: int) -> None:
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").upper()
        if route == "MIG":
            if str(payload.get("map_id") or "").strip():
                return
            raise ValueError(f"18B MIG item {index} requires map_id")
        if route in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            if str(payload.get("row_id") or "").strip():
                return
            if str(payload.get("space_nm") or "").strip() and str(payload.get("sql_id") or "").strip():
                return
            raise ValueError(f"18B {route} item {index} requires row_id or space_nm+sql_id")
        raise ValueError(f"18B item {index} has invalid job_route={route}")

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

    def _is_failure_status(self, status: Any) -> bool:
        value = str(status or "").strip().upper()
        return value.startswith("FAIL-")

    def _migration_blocks_sql(self, result: dict[str, Any]) -> bool:
        status = str(result.get("status") or "").strip().upper()
        if status in {"PASS", "SUCCESS"} and bool(result.get("ok", True)):
            return False
        return True

    def _migration_abort_signal(self, result: dict[str, Any]) -> bool:
        if bool(result.get("full_workflow_abort")):
            return True
        return self._route(result) == "MIG" and self._migration_blocks_sql(result)

    def _db_migration_phase_gate(self, payload: dict[str, Any]) -> dict[str, Any]:
        db_config = dict(payload.get("db_config") or {})
        if not self._has_db_config(db_config):
            return {"block_sql": False, "reason": "DB config is missing; skipped DB phase gate."}
        try:
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
        except Exception as exc:
            self.log(f"DB migration phase gate query failed: {exc}", name="DB Phase Gate")
            return {"block_sql": False, "reason": f"DB phase gate query failed: {exc}"}

        pending_null_count = self._num(row[0])
        fail_count = self._num(row[1])
        block_sql = pending_null_count == 0 and fail_count > 0
        return {
            "block_sql": block_sql,
            "pending_null_count": pending_null_count,
            "fail_count": fail_count,
            "reason": (
                f"DB Migration 종료 후 실패 상태가 {fail_count}건 있어 SQL Conversion 이후 작업을 시작하지 않았습니다."
                if block_sql
                else ""
            ),
        }

    def _plan_counts(self, data_list: list[Any]) -> dict[str, int]:
        counts = {route: 0 for route in ROUTE_ORDER}
        for item in data_list:
            payload = self._data_dict(item)
            route = self._route(payload)
            if route in counts:
                counts[route] += 1
        return counts

    def _route(self, payload: dict[str, Any]) -> str:
        return str(payload.get("planned_job_route") or payload.get("job_route") or "").upper()

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _has_db_config(self, db_config: dict[str, Any]) -> bool:
        return all(str(db_config.get(key) or "").strip() for key in ("db_host", "db_service_name", "db_username"))

    def _insert_log(
        self,
        db_config: dict[str, Any],
        map_id: int,
        mig_kind: str,
        log_type: str,
        log_level: str,
        step_name: str,
        status: str,
        message: str,
        retry_count: int,
        generated_sql: str = "",
    ) -> None:
        """Insert one migration execution log row into NEXT_MIG_LOG."""
        table = self._qualify("NEXT_MIG_LOG", db_config.get("system_schema"))
        sequence = self._qualify("MIGRATION_LOG_SEQ", db_config.get("system_schema"))
        column_types = self._table_column_types(db_config, table)
        columns = set(column_types)
        ts_columns = [column for column in ("CREATED_AT",) if column in columns]
        generate_sql_column = ", GENERATE_SQL" if "GENERATE_SQL" in columns else ""
        generate_sql_value = ", :9" if "GENERATE_SQL" in columns else ""
        ts_column_sql = "".join(f", {column}" for column in ts_columns)
        ts_value_sql = "".join(", CURRENT_TIMESTAMP" for _ in ts_columns)
        params = [
            map_id,
            str(mig_kind or "")[:100],
            str(log_type or "")[:20],
            str(log_level or "")[:20],
            str(step_name or "")[:50],
            str(status or "")[:20],
            str(message or "")[:4000],
            retry_count,
        ]
        if "GENERATE_SQL" in columns:
            sql_text = str(generated_sql or "")
            if column_types.get("GENERATE_SQL") not in {"CLOB", "NCLOB"}:
                sql_text = sql_text[:4000]
            params.append(sql_text)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT{generate_sql_column}{ts_column_sql}
                ) VALUES ({sequence}.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8{generate_sql_value}{ts_value_sql})
                """,
                params,
            )
            conn.commit()

    def _table_column_types(self, db_config: dict[str, Any], table: str) -> dict[str, str]:
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = "SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2"
            params = [owner, table_name]
        else:
            sql = "SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1"
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper(): str(row[1]).upper() for row in cur.fetchall()}

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." in value:
            owner, table_name = value.split(".", 1)
            return owner.strip('"'), table_name.strip('"')
        return None, value.strip('"')

    @contextmanager
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

    def _qualify(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip().upper()
        if "." in value:
            return value
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if clean_schema:
            clean_schema = self._clean_identifier(clean_schema)
            return f"{clean_schema}.{clean_table}"
        return clean_table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

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
