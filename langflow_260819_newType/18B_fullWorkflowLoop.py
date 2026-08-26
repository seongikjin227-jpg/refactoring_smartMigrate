from __future__ import annotations

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
            aggregated_results = await self.execute_loop_body(data_list, event_manager=self._event_manager)
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
        payload = {
            "component": "18B_fullWorkflowLoop",
            "job_route": "FULL_WORKFLOW",
            "full_workflow": True,
            "loop_done": True,
            "db_config": dict(first_payload.get("db_config") or {}),
            "workflow_plan_counts": dict(first_payload.get("workflow_plan_counts") or self._plan_counts(data_list)),
            "aggregated_results": results,
            "workflow_summary": self._summary(results, data_list),
            "next_node": "18D_fullWorkflowDashboard",
        }
        self.status = payload
        return Data(data=payload)

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

    def _summary(self, results: list[dict[str, Any]], data_list: list[Any]) -> dict[str, Any]:
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
            elif result.get("not_runnable") or result.get("tuning_skipped") or result.get("formatting_skipped") or result.get("skipped"):
                summary[route]["skipped"] += 1
            else:
                summary[route]["fail"] += 1
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
        return value == "FAIL" or value.startswith("FAIL-")

    def _plan_counts(self, data_list: list[Any]) -> dict[str, int]:
        counts = {route: 0 for route in ROUTE_ORDER}
        for item in data_list:
            payload = self._data_dict(item)
            route = str(payload.get("planned_job_route") or payload.get("job_route") or "").upper()
            if route in counts:
                counts[route] += 1
        return counts

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
