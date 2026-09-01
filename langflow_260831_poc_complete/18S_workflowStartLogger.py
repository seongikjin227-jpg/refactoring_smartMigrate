from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from lfx.base.flow_controls.loop_utils import validate_data_input
from lfx.components.processing.converter import convert_to_data
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import IntInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


ROUTE_ORDER = ("MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING")


class NewType18SWorkflowStartLogger(Component):
    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    display_name = "18S Workflow Start Logger"
    description = "Writes one workflow start marker to NEXT_MIG_LOG and NEXT_SQL_LOG, then passes jobs through to 18B."
    name = "NewType18SWorkflowStartLogger"
    icon = "Flag"

    inputs = [
        HandleInput(
            name="data",
            display_name="Full Workflow Jobs",
            info="Full Workflow job rows from 18A. The rows are returned unchanged except for workflow start metadata.",
            input_types=["DataFrame", "Table", "Data", "Message"],
        ),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Jobs Table", name="jobs_table", method="build_jobs_table")]

    def build_jobs_table(self) -> DataFrame:
        self._insert_log(0, "WORKFLOW", "18S_START_LOG", "INFO", "BUILD_JOBS_TABLE", "START", "before build_jobs_table", 0, "")
        try:
            data_list = self._validate_data(getattr(self, "data", None))
            rows = [self._data_dict(item) for item in data_list]
            db_config = self._db_config(rows[0] if rows else {})
            self._require_db_config(db_config)

            started_at = datetime.now().isoformat(timespec="seconds")
            plan_counts = self._plan_counts(rows)
            message = self._start_message(started_at, len(rows), plan_counts)
            warnings = self._insert_start_markers(db_config, message, plan_counts)

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
            self._insert_log(0, "WORKFLOW", "18S_START_LOG", "INFO", "BUILD_JOBS_TABLE", "END", "after build_jobs_table", 0, "")
            return __log_result
        except Exception as exc:
            self._insert_log(0, "WORKFLOW", "18S_START_LOG", "ERROR", "BUILD_JOBS_TABLE", "ERROR", f"error build_jobs_table: {exc}", 0, "")
            raise

    def _insert_start_markers(self, db_config: dict[str, Any], message: str, plan_counts: dict[str, int]) -> list[str]:
        warnings: list[str] = []
        try:
            self._insert_mig_start_marker(db_config, message)
        except Exception as exc:
            warnings.append(f"NEXT_MIG_LOG start marker failed: {exc}")
        try:
            self._insert_sql_start_marker(db_config, message, plan_counts)
        except Exception as exc:
            warnings.append(f"NEXT_SQL_LOG start marker failed: {exc}")
        return warnings

    def _insert_mig_start_marker(self, db_config: dict[str, Any], message: str) -> None:
        table = self._qualify("NEXT_MIG_LOG", db_config)
        sequence = self._qualify("MIGRATION_LOG_SEQ", db_config)
        columns = self._table_columns(db_config, table)
        values: dict[str, Any] = {
            "MAP_ID": 0,
            "MIG_KIND": self._fit_text("FULL_WORKFLOW_POC", 100),
            "LOG_TYPE": "RUN_MARKER",
            "LOG_LEVEL": "INFO",
            "STEP_NAME": self._fit_text("WORKFLOW_START", 50),
            "STATUS": self._fit_text("RUNNING", 20),
            "MESSAGE": self._fit_text(message, 4000),
            "RETRY_COUNT": 0,
        }
        insert_columns: list[str] = []
        value_exprs: list[str] = []
        params: dict[str, Any] = {}
        if "LOG_ID" in columns:
            insert_columns.append("LOG_ID")
            value_exprs.append(f"{sequence}.NEXTVAL")
        for column, value in values.items():
            if column not in columns:
                continue
            insert_columns.append(column)
            bind_name = column.lower()
            value_exprs.append(f":{bind_name}")
            params[bind_name] = value
        for column in ("CREATED_AT",):
            if column in columns:
                insert_columns.append(column)
                value_exprs.append("CURRENT_TIMESTAMP")
        if not insert_columns:
            return
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} ({", ".join(insert_columns)})
                VALUES ({", ".join(value_exprs)})
                """,
                params,
            )
            conn.commit()

    def _insert_sql_start_marker(self, db_config: dict[str, Any], message: str, plan_counts: dict[str, int]) -> None:
        table = self._qualify("NEXT_SQL_LOG", db_config)
        columns = self._table_columns(db_config, table)
        values: dict[str, Any] = {
            "SPACE_NM": "__WORKFLOW__",
            "SQL_ID": "__START__",
            "SQL_INFO_ROWID": None,
            "SQL_KIND": "FULL_WORKFLOW",
            "SQL_CONTENT": json.dumps({"event": "WORKFLOW_START", "plan_counts": plan_counts}, ensure_ascii=False),
            "STATUS": "RUNNING",
            "PROMPT_NAME": None,
            "MODEL_NAME": None,
            "BATCH_NO": None,
            "CYCLE_NO": None,
            "ELAPSED_SECONDS": None,
            "ATTEMPT_NO": 0,
            "STAGE_NAME": "WORKFLOW_START",
            "ERROR_MESSAGE": self._fit_text(message, 4000),
        }
        insert_columns: list[str] = []
        value_exprs: list[str] = []
        params: dict[str, Any] = {}
        for column, value in values.items():
            if column not in columns:
                continue
            insert_columns.append(column)
            bind_name = column.lower()
            value_exprs.append(f":{bind_name}")
            params[bind_name] = value
        if "CREATED_AT" in columns:
            insert_columns.append("CREATED_AT")
            value_exprs.append("CURRENT_TIMESTAMP")
        if not insert_columns:
            return
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} ({", ".join(insert_columns)})
                VALUES ({", ".join(value_exprs)})
                """,
                params,
            )
            conn.commit()

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

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(payload_config.get("db_host") or getattr(self, "db_host", "") or "").strip(),
            "db_port": int(payload_config.get("db_port") or getattr(self, "db_port", None) or 1521),
            "db_service_name": str(payload_config.get("db_service_name") or getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(payload_config.get("db_username") or getattr(self, "db_username", "") or "").strip(),
            "db_password": str(payload_config.get("db_password") or "") or self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(payload_config.get("system_schema") or getattr(self, "system_schema", "") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"18S Workflow Start Logger is not connected to database settings: missing {', '.join(missing)}")

    def _table_columns(self, db_config: dict[str, Any], qualified_table: str) -> set[str]:
        owner, table_name = self._split_qualified_table(qualified_table)
        sql = """
            SELECT COLUMN_NAME
              FROM ALL_TAB_COLUMNS
             WHERE TABLE_NAME = :table_name
        """
        params: dict[str, Any] = {"table_name": table_name}
        if owner:
            sql += " AND OWNER = :owner"
            params["owner"] = owner
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper() for row in cur.fetchall()}

    def _split_qualified_table(self, qualified_table: str) -> tuple[str, str]:
        parts = str(qualified_table or "").split(".", 1)
        if len(parts) == 2:
            return parts[0].upper(), parts[1].upper()
        return "", parts[0].upper()

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

    def _qualify(self, table_name: str, db_config: dict[str, Any]) -> str:
        table = self._clean_identifier(table_name)
        schema = str(db_config.get("system_schema") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _fit_text(self, value: Any, limit: int) -> str:
        return str(value or "")[:limit]

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _insert_log(
        self,
        map_id,
        mig_kind,
        log_type,
        log_level,
        step_name,
        status,
        message,
        retry_count,
        generated_sql="",
    ):
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO SFAADM.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    SFAADM.MIGRATION_LOG_SEQ.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8, CURRENT_TIMESTAMP
                )
                """,
                [
                    map_id,
                    str(mig_kind or "")[:100],
                    str(log_type or "")[:20],
                    str(log_level or "")[:20],
                    str(step_name or "")[:50],
                    str(status or "")[:20],
                    str(message or "")[:4000],
                    retry_count,
                ],
            )
            conn.commit()
        except Exception as exc:
            self.status = f"NEXT_MIG_LOG insert failed: {exc}"
        finally:
            if conn is not None:
                conn.close()
