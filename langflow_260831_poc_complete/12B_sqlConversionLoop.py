from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message

from lfx.base.flow_controls.loop_utils import (
    execute_loop_body,
    extract_loop_output,
    get_loop_body_start_edge,
    get_loop_body_start_vertex,
    get_loop_body_vertices,
    validate_data_input,
)
from lfx.components.processing.converter import convert_to_data


class NewType12BSqlConversionLoop(Component):
    display_name = "12B SQL Conversion Loop"
    description = "SQL Conversion loop that iterates one SQL row at a time and emits Done."
    documentation = "https://docs.langflow.org/loop"
    name = "NewType12BSqlConversionLoop"
    icon = "Infinity"

    inputs = [
        HandleInput(
            name="data",
            display_name="SQL Conversion Jobs",
            info="SQL conversion job rows to iterate. Accepts DataFrame, Table, Data, or Message.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
    ]

    outputs = [
        Output(
            display_name="Item",
            name="item",
            method="item_output",
            types=["Data"],
            allows_loop=True,
            loop_types=["Data"],
            group_outputs=True,
        ),
        Output(display_name="Done", name="done", method="done_output", types=["Data"]),
    ]

    def initialize_data(self) -> None:
        """Normalize input rows and cache them for a single loop run."""
        if self.ctx.get(f"{self._id}_initialized", False):
            return
        data_list = self._validate_data(self.data)
        job_keys: list[str] = []
        for index, item in enumerate(data_list, start=1):
            payload = self._data_dict(item)
            self._validate_sql_key(payload, index)
            job_keys.append(self._job_key(payload))
        self.log(f"Normalized SQL Conversion loop jobs={job_keys}", name="Input")
        self.update_ctx(
            {
                f"{self._id}_data": data_list,
                f"{self._id}_index": 0,
                f"{self._id}_initialized": True,
            }
        )

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
            self.log(f"Starting SQL Conversion loop over {len(data_list)} job(s)", name="Start")
            if not data_list:
                self.update_ctx({f"{self._id}_aggregated": [], f"{self._id}_iterated": True})
                self.log("No SQL Conversion jobs to iterate", name="Skipped")
                return []
            aggregated_results = await self.execute_loop_body(data_list, event_manager=self._event_manager)
        except Exception as exc:
            from lfx.log.logger import logger

            elapsed = time.perf_counter() - started_at
            self.log(f"SQL Conversion loop failed after {elapsed:.3f}s: {exc}", name="Error")
            await logger.aexception(f"SQL Conversion loop {self._id} failed while executing loop body")
            self.update_ctx({f"{self._id}_iteration_error": exc, f"{self._id}_iterated": True})
            raise
        elapsed = time.perf_counter() - started_at
        self.log(f"Completed {len(aggregated_results)} SQL Conversion iteration(s) in {elapsed:.3f}s", name="Complete")
        self.update_ctx({f"{self._id}_aggregated": aggregated_results, f"{self._id}_iterated": True})
        return aggregated_results

    async def item_output(self) -> Data:
        # The Item output is only the loop-body entry point. Its normal return
        # value must never continue through the outer graph into 12C.
        self.stop("item")
        try:
            if self._vertex is not None:
                await self._iterate()
        finally:
            # Running the loop body builds a nested graph. Re-assert the stop
            # after it finishes so the inspection payload below cannot be
            # dispatched to 12C as one additional job.
            self.stop("item")
        data_list = self.ctx.get(f"{self._id}_data", [])
        return Data(data={"count": len(data_list), "items": [self._data_dict(item) for item in data_list]})

    async def done_output(self) -> Data:
        # The Done output is the post-loop path. Connect it to 11.
        if self._vertex is not None:
            await self._iterate()
        data_list = self.ctx.get(f"{self._id}_data", [])
        first_payload = self._data_dict(data_list[0]) if data_list else {}
        payload = {
            "component": "12B_sqlConversionLoop",
            "job_route": "SQL_CONVERSION",
            "loop_done": True,
            "db_config": dict(first_payload.get("db_config") or {}),
            "next_node": "11_finalDashboard",
        }
        self.status = payload
        return Data(data=payload)

    def _validate_sql_key(self, payload: dict[str, Any], index: int) -> None:
        if str(payload.get("row_id") or "").strip():
            return
        if str(payload.get("space_nm") or "").strip() and str(payload.get("sql_id") or "").strip():
            return
        raise ValueError(f"12B SQL Conversion item {index} requires row_id or space_nm+sql_id")

    def _job_key(self, payload: dict[str, Any]) -> str:
        row_id = str(payload.get("row_id") or "").strip()
        if row_id:
            return f"row_id={row_id}"
        return f"space_nm={payload.get('space_nm')}, sql_id={payload.get('sql_id')}"

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
