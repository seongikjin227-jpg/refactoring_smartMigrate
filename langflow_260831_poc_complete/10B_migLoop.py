from __future__ import annotations

import logging
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
    description = "MIG-specific loop that iterates jobs and emits a Done signal for the final MIG message."
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
            types=["Data"],
            allows_loop=True,
            loop_types=["Data"],
            group_outputs=True,
        ),
        Output(display_name="Done", name="done", method="done_output", types=["Data"]),
    ]

    def initialize_data(self) -> None:
        if self.ctx.get(f"{self._id}_initialized", False):
            return
        data_list = self._validate_data(self.data)
        map_ids: list[int] = []
        for index, item in enumerate(data_list, start=1):
            payload = self._data_dict(item)
            try:
                map_ids.append(int(payload.get("map_id")))
            except (TypeError, ValueError):
                raise ValueError(f"10B MIG job row {index} requires map_id") from None
        self.log(f"Normalized MIG loop map_ids={map_ids}", name="Input")
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

    async def item_output(self) -> Data:
        # The Item output is only the loop-body entry point. Its normal return
        # value must never continue through the outer graph into 10C.
        logging.getLogger("smartmigrate.workflow").info(
            "before item_output",
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "10B_MIG_LOOP",
                    "log_level": "INFO",
                    "step_name": "ITEM_OUTPUT",
                    "status": "START",
                    "message": "before item_output",
                    "retry_count": 0,
                }
            },
        )
        try:
            self.stop("item")
            try:
                if self._vertex is not None:
                    await self._iterate()
            finally:
                # Running the loop body builds a nested graph. Re-assert the stop
                # after it finishes so the inspection payload below cannot be
                # dispatched to 10C as one additional job.
                self.stop("item")
            data_list = self.ctx.get(f"{self._id}_data", [])
            __log_result = Data(data={"count": len(data_list), "items": [self._data_dict(item) for item in data_list]})
            logging.getLogger("smartmigrate.workflow").info(
                "after item_output",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "10B_MIG_LOOP",
                        "log_level": "INFO",
                        "step_name": "ITEM_OUTPUT",
                        "status": "END",
                        "message": "after item_output",
                        "retry_count": 0,
                    }
                },
            )
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"error item_output: {exc}",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "10B_MIG_LOOP",
                        "log_level": "ERROR",
                        "step_name": "ITEM_OUTPUT",
                        "status": "ERROR",
                        "message": f"error item_output: {exc}",
                        "retry_count": 0,
                    }
                },
            )
            raise

    async def done_output(self) -> Data:
        # The Done output is the post-loop path. Connect it to 11.
        logging.getLogger("smartmigrate.workflow").info(
            "before done_output",
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "10B_MIG_LOOP",
                    "log_level": "INFO",
                    "step_name": "DONE_OUTPUT",
                    "status": "START",
                    "message": "before done_output",
                    "retry_count": 0,
                }
            },
        )
        try:
            if self._vertex is not None:
                await self._iterate()
            data_list = self.ctx.get(f"{self._id}_data", [])
            first_payload = self._data_dict(data_list[0]) if data_list else {}
            payload = {
                "component": "10B_migLoop",
                "job_route": "MIG",
                "loop_done": True,
                "db_config": dict(first_payload.get("db_config") or {}),
                "next_node": "11_finalDashboard",
            }
            self.status = payload
            __log_result = Data(data=payload)
            logging.getLogger("smartmigrate.workflow").info(
                "after done_output",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "10B_MIG_LOOP",
                        "log_level": "INFO",
                        "step_name": "DONE_OUTPUT",
                        "status": "END",
                        "message": "after done_output",
                        "retry_count": 0,
                    }
                },
            )
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(
                f"error done_output: {exc}",
                extra={
                    "workflow_log": {
                        "map_id": 0,
                        "mig_kind": "WORKFLOW",
                        "log_type": "10B_MIG_LOOP",
                        "log_level": "ERROR",
                        "step_name": "DONE_OUTPUT",
                        "status": "ERROR",
                        "message": f"error done_output: {exc}",
                        "retry_count": 0,
                    }
                },
            )
            raise

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
