from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message

from lfx.base.flow_controls.loop_utils import validate_data_input
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
            types=["Data"],
            allows_loop=True,
            loop_types=["Data"],
            group_outputs=True,
        ),
        Output(
            display_name="Done",
            name="done",
            method="done_output",
            types=["Data"],
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
                f"{self._id}_aggregated": [],
                f"{self._id}_last_feedback_key": "",
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

    async def item_output(self) -> Data:
        self.initialize_data()
        self._consume_loop_feedback()
        data_list = self.ctx.get(f"{self._id}_data", [])
        index = int(self.ctx.get(f"{self._id}_index", 0) or 0)
        if index >= len(data_list):
            self.stop("item")
            self.start("done")
            return Data(data={"done": True, "total_jobs": len(data_list)})
        item = data_list[index]
        item_data = self._data_dict(item)
        item_data["history"] = list(self.ctx.get(f"{self._id}_aggregated", []) or [])
        self.update_ctx({f"{self._id}_index": index + 1})
        self.stop("done")
        return Data(data=item_data)

    async def done_output(self) -> Data:
        self.initialize_data()
        self._consume_loop_feedback()
        data_list = self.ctx.get(f"{self._id}_data", [])
        result_rows = list(self.ctx.get(f"{self._id}_aggregated", []) or [])
        total = len(data_list)
        success = len([item for item in result_rows if item.get("ok") is True or str(item.get("status") or "").upper() == "PASS"])
        failed = len([item for item in result_rows if item.get("ok") is False and str(item.get("status") or "").upper() != "WAITING"])
        waiting = len([item for item in result_rows if str(item.get("status") or "").upper() == "WAITING"])
        if len(result_rows) < total:
            self.stop("done")
            return Data(data={"component": "10B_migLoop", "pipeline_status": "RUNNING", "total_jobs": total, "processed_jobs": result_rows})
        self.stop("item")
        self.start("done")
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

    def _consume_loop_feedback(self) -> None:
        feedback = getattr(self, "item", None)
        if feedback in (None, ""):
            return
        row = self._data_dict(feedback)
        if not row or row.get("done"):
            return
        if row.get("map_id") is None and row.get("status") is None:
            return
        key = self._feedback_key(row)
        if key == self.ctx.get(f"{self._id}_last_feedback_key"):
            return
        aggregated = list(self.ctx.get(f"{self._id}_aggregated", []) or [])
        aggregated.append(row)
        self.update_ctx(
            {
                f"{self._id}_aggregated": aggregated,
                f"{self._id}_last_feedback_key": key,
            }
        )

    def _feedback_key(self, row: dict[str, Any]) -> str:
        return "|".join(
            [
                str(row.get("map_id") or ""),
                str(row.get("job_index") or ""),
                str(row.get("status") or ""),
                str(row.get("attempt_count") or ""),
                str(row.get("retry_count") or ""),
            ]
        )

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
