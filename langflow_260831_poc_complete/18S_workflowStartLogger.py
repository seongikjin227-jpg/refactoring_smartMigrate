from __future__ import annotations

import logging
import json
from datetime import datetime
from typing import Any

from lfx.base.flow_controls.loop_utils import validate_data_input
from lfx.components.processing.converter import convert_to_data
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")


class NewType18SWorkflowStartLogger(Component):

    display_name = "18S Workflow Start Logger"
    description = "Adds workflow start metadata, writes a logging event, and passes jobs through to 18B."
    name = "NewType18SWorkflowStartLogger"
    icon = "Flag"

    inputs = [
        HandleInput(
            name="data",
            display_name="Full Workflow Jobs",
            info="Full Workflow job rows from 18A. The rows are returned unchanged except for workflow start metadata.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    def build_jobs_table(self) -> DataFrame:
        logging.getLogger("smartmigrate.workflow").info("before build_jobs_table", extra={"workflow_log": [0, "WORKFLOW", "18S_START_LOG", "INFO", "BUILD_JOBS_TABLE", "START", "before build_jobs_table", 0]})
        try:
            data_list = self._validate_data(getattr(self, "data", None))
            rows = [self._data_dict(item) for item in data_list]

            started_at = datetime.now().isoformat(timespec="seconds")
            plan_counts = self._plan_counts(rows)
            message = self._start_message(started_at, len(rows), plan_counts)
            warnings = self._insert_start_markers(message, plan_counts)

            out_rows = [
                {
                    **row,
                    "workflow_started_at": started_at,
                    "workflow_start_marker_logged": not warnings,
                    "workflow_start_log_warnings": warnings,
                }
                for row in rows
            ]
            self.status = {
                "component": "18S_workflowStartLogger",
                "full_workflow": True,
                "loop_job_count": len(out_rows),
                "workflow_started_at": started_at,
                "workflow_plan_counts": plan_counts,
                "workflow_start_marker_logged": not warnings,
                "workflow_start_log_warnings": warnings,
                "next_node": "18B_fullWorkflowLoop",
            }
            __log_result = DataFrame(out_rows)
            logging.getLogger("smartmigrate.workflow").info("after build_jobs_table", extra={"workflow_log": [0, "WORKFLOW", "18S_START_LOG", "INFO", "BUILD_JOBS_TABLE", "END", "after build_jobs_table", 0]})
            return __log_result
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error build_jobs_table: {exc}", extra={"workflow_log": [0, "WORKFLOW", "18S_START_LOG", "ERROR", "BUILD_JOBS_TABLE", "ERROR", f"error build_jobs_table: {exc}", 0]})
            raise

    def _insert_start_markers(self, message: str, plan_counts: dict[str, int]) -> list[str]:
        logging.getLogger("smartmigrate.workflow").info(message, extra={"workflow_log": [0, "WORKFLOW", "18S_START_LOG", "INFO", "WORKFLOW_START", "RUNNING", self._fit_text(message, 4000), 0]})
        return []

    def _start_message(self, started_at: str, total_jobs: int, plan_counts: dict[str, int]) -> str:
        return (
            "Full Workflow start marker; "
            f"started_at={started_at}; total_jobs={total_jobs}; "
            f"plan_counts={json.dumps(plan_counts, ensure_ascii=False, sort_keys=True)}"
        )

    def _plan_counts(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        counts = {route: 0 for route in ROUTE_ORDER}
        for row in rows:
            route = str(row.get("planned_job_route") or row.get("job_route") or "").strip().upper()
            if route in counts:
                counts[route] += 1
        return counts

    def _validate_data(self, data: Any) -> list[Data]:
        if isinstance(data, Message):
            data = convert_to_data(data, auto_parse=False)
        elif isinstance(data, list):
            normalized: list[Any] = []
            for item in data:
                if isinstance(item, Message):
                    normalized.append(convert_to_data(item, auto_parse=False))
                elif isinstance(item, DataFrame):
                    normalized.extend(item.to_data_list())
                else:
                    normalized.append(item)
            data = normalized
        return validate_data_input(data)

    def _data_dict(self, item: Any) -> dict[str, Any]:
        if isinstance(item, Data):
            return dict(item.data or {})
        if isinstance(item, dict):
            return dict(item)
        return {"value": item}

    def _fit_text(self, value: Any, limit: int) -> str:
        return str(value or "")[:limit]
