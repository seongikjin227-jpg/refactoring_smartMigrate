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


class NewType10BMigLoop(Component):
    display_name = "10B MIG Loop"
    description = "MIG-specific loop that iterates jobs and returns aggregate Data instead of a Table."
    documentation = "https://docs.langflow.org/loop"
    name = "NewType10BMigLoop"
    icon = "Infinity"

    inputs = [
        HandleInput(
            name="data",
            display_name="MIG Jobs",
            info="MIG job rows to iterate. Accepts DataFrame, Table, Data, or Message.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
    ]

    outputs = [
        Output(
            display_name="Item",
            name="item",
            method="item_output",
            types=["Message"],
            allows_loop=True,
            loop_types=["Message"],
            group_outputs=True,
        ),
        Output(
            display_name="Done",
            name="done",
            method="done_output",
            group_outputs=True,
        ),
    ]

    def initialize_data(self) -> None:
        if self.ctx.get(f"{self._id}_initialized", False):
            return
        data_list = self._validate_data(self.data)
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
        return get_loop_body_vertices(
            vertex=self._vertex,
            graph=self.graph,
            get_incoming_edge_by_target_param_fn=self.get_incoming_edge_by_target_param,
        )

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
            self.log(f"Starting MIG loop over {len(data_list)} job(s)", name="Start")
            if not data_list:
                self.update_ctx({f"{self._id}_aggregated": [], f"{self._id}_iterated": True})
                self.log("No MIG jobs to iterate", name="Skipped")
                return []
            aggregated_results = await self.execute_loop_body(data_list, event_manager=self._event_manager)
        except Exception as exc:
            from lfx.log.logger import logger

            elapsed = time.perf_counter() - started_at
            self.log(f"MIG loop failed after {elapsed:.3f}s: {exc}", name="Error")
            await logger.aexception(f"MIG loop {self._id} failed while executing loop body")
            self.update_ctx({f"{self._id}_iteration_error": exc, f"{self._id}_iterated": True})
            raise

        elapsed = time.perf_counter() - started_at
        self.log(f"Completed {len(aggregated_results)} MIG iteration(s) in {elapsed:.3f}s", name="Complete")
        self.update_ctx({f"{self._id}_aggregated": aggregated_results, f"{self._id}_iterated": True})
        return aggregated_results

    async def item_output(self) -> Message:
        self.stop("item")
        if self._vertex is not None and "done" not in self._vertex.edges_source_names:
            await self._iterate()
        data_list = self.ctx.get(f"{self._id}_data", [])
        payload = {"count": len(data_list), "items": [self._data_dict(item) for item in data_list]}
        return Message(text=self._json_text(payload))

    async def done_output(self) -> Data:
        aggregated_results = await self._iterate()
        result_rows = [self._data_dict(item) for item in aggregated_results]
        total = len(result_rows)
        success = len([item for item in result_rows if item.get("ok") is True or str(item.get("status") or "").upper() == "PASS"])
        failed = len([item for item in result_rows if item.get("ok") is False and str(item.get("status") or "").upper() != "WAITING"])
        waiting = len([item for item in result_rows if str(item.get("status") or "").upper() == "WAITING"])
        payload = {
            "component": "10B_migLoop",
            "pipeline_status": "DONE_WITH_FAILURES" if failed else "DONE",
            "job_route": "MIG",
            "total_jobs": total,
            "success_count": success,
            "failed_count": failed,
            "waiting_count": waiting,
            "processed_jobs": result_rows,
            "completed_jobs": [item for item in result_rows if item.get("ok") is True],
            "failed_jobs": [item for item in result_rows if item.get("ok") is False],
            "next_node": "10E_migFinalDashboard",
        }
        self.status = payload
        return Data(data=payload)

    def _data_dict(self, item: Any) -> dict[str, Any]:
        if isinstance(item, Data):
            return dict(item.data or {})
        if isinstance(item, Message):
            parsed = self._parse_json_text(item.text)
            if parsed is not None:
                return parsed
            data = self._convert_message_to_data(item)
            return dict(data.data or {})
        if isinstance(item, dict):
            return dict(item)
        return {"value": item}

    def _json_text(self, payload: dict[str, Any]) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False, default=str)

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
