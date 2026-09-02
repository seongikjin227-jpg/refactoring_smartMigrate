from __future__ import annotations

import logging
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "LOOP_TEST_04C_PROGRESS_SIDE_CHANNEL_SINK", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], 0]})

class LoopOutputTest04CProgressSideChannelSink(Component):
    display_name = "Loop Output Test 04C Progress Side Channel Sink"
    description = "Writes one progress event per loop iteration to file and/or Oracle so the platform can poll it in real time."
    name = "LoopOutputTest04CProgressSideChannelSink"
    icon = "RadioTower"

    inputs = [
        DataInput(name="message_payload", display_name="18D Message", required=False),
        DataInput(name="loop_result_input", display_name="18D Loop Result", required=True),
        DropdownInput(name="sink_mode", display_name="Sink Mode", options=["FILE", "ORACLE_DB", "BOTH"], value="FILE", required=False),
        StrInput(name="run_key", display_name="Run Key Override", required=False),
        StrInput(name="output_file", display_name="Output JSONL File", value="progress_events.jsonl", required=False),
        StrInput(name="progress_table", display_name="Progress Table", value="NEXT_WORKFLOW_PROGRESS", required=False),
        BoolInput(name="include_full_payload", display_name="Include Full Payload", value=False, required=False),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        result = self._build()
        self.status = result
        return Message(text=result["answer_text"])

    def build_loop_result(self) -> Data:
        result = self._build()
        self.status = result
        return Data(data=result["loop_result"])

    def _build(self) -> dict[str, Any]:
        _workflow_log("_BUILD", "START", "before _build")
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        message_payload = self._parse_any(getattr(self, "message_payload", ""))
        loop_result = self._parse_any(getattr(self, "loop_result_input", ""))
        run_key = self._run_key(message_payload, loop_result)
        event = self._event(run_key, message_payload, loop_result)
        sink_mode = str(getattr(self, "sink_mode", "") or "FILE").upper()
        sink_results: list[str] = []

        if sink_mode in {"FILE", "BOTH"}:
            path = self._output_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            sink_results.append(f"file={path}")

        if sink_mode in {"ORACLE_DB", "BOTH"}:
            self._insert_oracle_event(event)
            sink_results.append(f"table={self._qualify(self._progress_table())}")

        text = self._message_text(message_payload) or self._message_text(loop_result)
        result = {
            **message_payload,
            "component": "LoopOutputTest04CProgressSideChannelSink",
            "answer_text": text or f"progress event written: {', '.join(sink_results)}",
            "progress_event": event,
            "progress_sink_results": sink_results,
            "loop_result": {
                **loop_result,
                "progress_run_key": run_key,
                "progress_sink_mode": sink_mode,
            },
        }
        self._cached_payload = result
        _workflow_log("_BUILD", "END", "after _build")
        return result

    def _event(self, run_key: str, message_payload: dict[str, Any], loop_result: dict[str, Any]) -> dict[str, Any]:
        text = self._message_text(message_payload) or self._message_text(loop_result)
        event = {
            "event_ts": time.time(),
            "run_key": run_key,
            "final": bool(loop_result.get("final") or loop_result.get("loop_done") or message_payload.get("final")),
            "job_route": loop_result.get("planned_job_route") or loop_result.get("job_route"),
            "job_index": loop_result.get("job_index"),
            "total_jobs": loop_result.get("total_jobs"),
            "status": loop_result.get("status"),
            "ok": loop_result.get("ok"),
            "map_id": loop_result.get("map_id"),
            "space_nm": loop_result.get("space_nm"),
            "sql_id": loop_result.get("sql_id"),
            "message_text": text,
        }
        if bool(getattr(self, "include_full_payload", False)):
            event["payload_json"] = json.dumps({"message": message_payload, "loop_result": loop_result}, ensure_ascii=False, default=str)
        return event

    def _insert_oracle_event(self, event: dict[str, Any]) -> None:
        self._db_config = self._db_config()
        self._require_db_config(self._db_config)
        table = self._qualify(self._progress_table())
        columns = self._available_columns(self._progress_table())
        values = {
            "RUN_KEY": event.get("run_key"),
            "EVENT_TS": "CURRENT_TIMESTAMP",
            "IS_FINAL": "Y" if event.get("final") else "N",
            "JOB_ROUTE": event.get("job_route"),
            "JOB_INDEX": event.get("job_index"),
            "TOTAL_JOBS": event.get("total_jobs"),
            "STATUS": event.get("status"),
            "OK_YN": "Y" if event.get("ok") else "N",
            "MAP_ID": event.get("map_id"),
            "SPACE_NM": event.get("space_nm"),
            "SQL_ID": event.get("sql_id"),
            "MESSAGE_TEXT": event.get("message_text"),
            "PAYLOAD_JSON": event.get("payload_json"),
        }
        insert_columns: list[str] = []
        value_exprs: list[str] = []
        params: dict[str, Any] = {}
        for column, value in values.items():
            if column not in columns:
                continue
            insert_columns.append(column)
            if column == "EVENT_TS":
                value_exprs.append("CURRENT_TIMESTAMP")
            else:
                bind_name = column.lower()
                value_exprs.append(f":{bind_name}")
                params[bind_name] = value
        if not insert_columns:
            raise ValueError(f"{table} has none of the expected progress columns")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {table} ({', '.join(insert_columns)}) VALUES ({', '.join(value_exprs)})", params)
            conn.commit()

    def _available_columns(self, table_name: str) -> set[str]:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2", [schema, table])
            else:
                cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [table])
            return {str(row[0]).upper() for row in cur.fetchall()}

    @contextmanager
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(
            str(self._db_config.get("db_host") or "").strip(),
            int(self._db_config.get("db_port") or 1521),
            service_name=str(self._db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(self._db_config.get("db_username") or "").strip(),
            password=str(self._db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _message_text(self, payload: dict[str, Any]) -> str:
        for key in ("answer_text", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _parse_any(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"text": text}

    def _run_key(self, message_payload: dict[str, Any], loop_result: dict[str, Any]) -> str:
        override = str(getattr(self, "run_key", "") or "").strip()
        if override:
            return self._safe_key(override)
        for source in (message_payload, loop_result):
            for key in ("workflow_run_id", "run_id", "batch_id", "session_id", "chat_id"):
                value = source.get(key)
                if str(value or "").strip():
                    return self._safe_key(value)
        return "default"

    def _output_path(self) -> Path:
        configured = str(getattr(self, "output_file", "") or "progress_events.jsonl").strip()
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parent / path

    def _db_config(self) -> dict[str, Any]:
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Progress DB sink requires database settings: missing {', '.join(missing)}")

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        return f"{self._clean_identifier(schema)}.{table}" if schema else table

    def _progress_table(self) -> str:
        return str(getattr(self, "progress_table", "") or "NEXT_WORKFLOW_PROGRESS").strip().upper()

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _safe_key(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)
        return text[:120] or "default"

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
