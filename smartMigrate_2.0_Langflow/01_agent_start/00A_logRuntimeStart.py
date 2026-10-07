from __future__ import annotations

import asyncio
import json
from io import BytesIO
from urllib.parse import urlparse
import logging
import threading
from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"
LOGGER_SETUP_LOCK = threading.RLock()


def create_db_connection(db_config: dict[str, Any]):
    import oracledb
    dsn = oracledb.makedsn(
        db_config["host"], int(db_config.get("port") or 1521), service_name=db_config["service_name"]
    )
    return oracledb.connect(user=db_config["username"], password=db_config["password"], dsn=dsn)


class SmartMigrateDBHandler(logging.Handler):
    """Write workflow records to NEXT_MIG_LOG."""

    def __init__(self, db_config: dict[str, Any]):
        super().__init__(level=logging.DEBUG)
        self.handler_marker = HANDLER_MARKER
        self.db_config = dict(db_config)
        self.connection = create_db_connection(db_config)
        self.records: list[dict[str, Any]] = []
        self.insert_error: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        event = self._event(record)
        row = {
            "created_at": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "map_id": str(event.get("map_id") or 0)[:100],
            "mig_kind": str(event.get("mig_kind") or "WORKFLOW")[:100],
            "log_type": str(event.get("log_type") or "")[:20],
            "log_level": str(event.get("log_level") or "noLevelName")[:20],
            "step_name": str(event.get("step_name") or "")[:50],
            "status": str(event.get("status") or "noStatus")[:20],
            "message": str(event.get("message") or "noMessage")[:4000],
            "retry_count": self._to_int(event.get("retry_count"), 0),
            "generate_sql": event.get("generate_sql"),
        }
        self.records.append(row)
        self._insert_row(row)

    def close(self) -> None:
        try:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
                self.connection = None
        finally:
            super().close()

    def _event(self, record: logging.LogRecord) -> dict[str, Any]:
        event = getattr(record, "workflow_log", None)
        if isinstance(event, (list, tuple)):
            return {
                "map_id": event[0] if len(event) > 0 else 0,
                "mig_kind": event[1] if len(event) > 1 else "WORKFLOW",
                "log_type": event[2] if len(event) > 2 else "PY_LOG",
                "log_level": event[3] if len(event) > 3 else "noLevelName",
                "step_name": event[4] if len(event) > 4 else "LOGGING",
                "status": event[5] if len(event) > 5 else "noStatus",
                "message": record.getMessage() or "noMessage",
                "retry_count": event[6] if len(event) > 6 else 0,
                "generate_sql": event[7] if len(event) > 7 else None,
            }
        if isinstance(event, dict):
            event = dict(event)
            event["message"] = event.get("message") or record.getMessage() or "noMessage"
            event["generate_sql"] = event.get("generate_sql")
            return event
        return {
            "map_id": 0, "mig_kind": "WORKFLOW", "log_type": "PY_LOG",
            "log_level": "noLevelName", "step_name": "LOGGING", "status": "noStatus",
            "message": record.getMessage() or "noMessage", "retry_count": 0, "generate_sql": None,
        }

    def _insert_row(self, row: dict[str, Any]) -> None:
        import oracledb

        cursor = None
        try:
            cursor = self.connection.cursor()
            # Explicitly bind the diagnostic payload as CLOB.  Depending on the
            # driver mode, an implicitly bound Python string may otherwise be
            # treated as VARCHAR or be lost when the destination is a CLOB.
            cursor.setinputsizes(generate_sql=oracledb.DB_TYPE_CLOB)
            cursor.execute(
                f"""
                INSERT INTO {self._qualify("NEXT_MIG_LOG")} (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, GENERATE_SQL, CREATED_AT
                ) VALUES (
                    {self._qualify("MIGRATION_LOG_SEQ")}.NEXTVAL,
                    :map_id, :mig_kind, :log_type, :log_level, :step_name, :status,
                    :message, :retry_count, :generate_sql,
                    TO_TIMESTAMP(:created_at, 'YYYY-MM-DD HH24:MI:SS.FF6')
                )
                """,
                row,
            )
            self.connection.commit()
            self.insert_error = None
        except Exception as exc:
            self.insert_error = str(exc)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def _qualify(self, object_name: str) -> str:
        schema = str(self.db_config.get("system_schema") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required.")
        return f"{schema}.{object_name}"

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default


class NewType00ALogRuntimeStart(Component):
    display_name = "00A Log Runtime Start"
    description = "Log Chat Input, parse a supplied workbook URL, and preserve original Message metadata."
    name = "NewType00ALogRuntimeStart"

    inputs = [
        # Optional so 00A can record an explicit EMPTY CHAT_INPUT diagnostic
        # when an upstream caller triggers the flow without a Message.
        MessageInput(name="input_message", display_name="Chat Input Message", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]
    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        message = getattr(self, "input_message", None)
        logger = logging.getLogger(LOGGER_NAME)
        # The logger is process-global.  Serialise only its setup plus the
        # mandatory first record so a concurrent Super-Agent request cannot
        # remove this handler before CHAT_INPUT reaches NEXT_MIG_LOG.
        with LOGGER_SETUP_LOCK:
            for existing_handler in list(logger.handlers):
                if getattr(existing_handler, "handler_marker", None) != HANDLER_MARKER:
                    continue
                logger.removeHandler(existing_handler)
                try:
                    existing_handler.close()
                except Exception:
                    pass

            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            handler = SmartMigrateDBHandler(self._db_config())
            logger.addHandler(handler)

            # A missing Chat Input must be visible in NEXT_MIG_LOG instead of
            # failing before the first workflow log is written.
            if not isinstance(message, Message):
                raw_payload = json.dumps(
                    {
                        "input_present": message is not None,
                        "received_type": type(message).__name__,
                        "received_value": self._json_value(message),
                        "reason": "00A executed without a Langflow Chat Input Message.",
                    },
                    ensure_ascii=False,
                    default=str,
                )
                logger.warning(
                    raw_payload[:4000],
                    extra={
                        "workflow_log": [
                            0, "WORKFLOW", "CHAT_INPUT", "WARNING", "MESSAGE", "EMPTY", 0, raw_payload,
                        ]
                    },
                )
                self.status = {
                    "ok": handler.insert_error is None,
                    "input_present": message is not None,
                    "input_type": type(message).__name__,
                    "db_insert_error": handler.insert_error,
                }
                # Logging is diagnostic-only.  Keep the workflow alive so 02
                # can leave its own empty-input diagnostic as well.
                return Message(text="")

            # Diagnostic boundary: retain the original Message object.  Do not
            # reduce it to Message.text before logging or handing it onward.
            raw_payload = self._message_payload_json(message)
            logger.info(
                raw_payload[:4000],
                extra={
                    "workflow_log": [
                        0, "WORKFLOW", "CHAT_INPUT", "INFO", "MESSAGE", "START", 0, raw_payload,
                    ]
                },
            )
        self.status = {
            "ok": handler.insert_error is None,
            "db_insert_error": handler.insert_error,
        }
        # Logging is observational, not a workflow gate.  Keep the database
        # error in component status but always pass a valid Message onward.
        # Do not replace Message.text with the diagnostic JSON.  Downstream
        # components must receive precisely the Message that Langflow provided.
        # (The full diagnostic envelope is stored in NEXT_MIG_LOG.GENERATE_SQL.)
        self._parse_uploaded_attachment(message, logger)
        return message

    def _parse_uploaded_attachment(self, message: Message, logger: logging.Logger) -> None:
        metadata = self._json_value(getattr(message, "data", None))
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if isinstance(metadata.get("uploaded_attachment"), dict):
            return
        url = self._find_url(metadata)
        if not url:
            return
        try:
            content = asyncio.run(self._download(url))
            parsed = self._parse_excel(content)
            attachment = {"byte_count": len(content), "parsed_excel": parsed}
            status = "PASS"
        except Exception as exc:
            attachment = {"error": str(exc)}
            status = "FAIL"
        metadata["uploaded_attachment"] = attachment
        message.data = metadata
        logger.info(
            "Workbook URL parsing completed: " + status,
            extra={"workflow_log": [0, "WORKFLOW", "00A_ATTACHMENT_PARSE", "INFO", "PARSE", status, 0,
                                    json.dumps(attachment, ensure_ascii=False, default=str)]},
        )
        self.status = {**self.status, "attachment_parse_status": status}

    @staticmethod
    async def _download(url: str) -> bytes:
        import aiohttp
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Presigned URL must be an HTTP or HTTPS URL.")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                content = await response.read()
                if not content.startswith(b"PK"):
                    preview = content[:120].decode("utf-8", errors="replace").replace("\n", " ")
                    raise ValueError(
                        f"Downloaded response is not an XLSX ZIP file. "
                        f"content_type={response.headers.get('Content-Type')!r}, preview={preview!r}"
                    )
                return content

    @staticmethod
    def _parse_excel(content: bytes) -> dict[str, Any]:
        import pandas as pd
        try:
            sheets = pd.read_excel(BytesIO(content), sheet_name=None, dtype=object, engine="openpyxl")
        except ImportError as exc:
            raise RuntimeError("xlsx parsing requires openpyxl on the Langflow server.") from exc
        return {"format": "xlsx", "sheets": [
            {"name": str(name), "columns": [str(column) for column in frame.columns],
             "rows": frame.where(frame.notna(), None).to_dict(orient="records")}
            for name, frame in sheets.items()
        ]}

    @staticmethod
    def _find_url(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                if normalized in {"downloadurl", "presignedurl", "fileurl"} and isinstance(item, str):
                    return item.strip()
            for item in value.values():
                found = NewType00ALogRuntimeStart._find_url(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = NewType00ALogRuntimeStart._find_url(item)
                if found:
                    return found
        return ""

    @staticmethod
    def _message_payload_json(message: Message) -> str:
        """Record every field currently available on Langflow's Message object.

        This component is downstream of Langflow's request adapter.  Therefore
        this dump is the complete *Langflow Message*, not necessarily the raw
        A2A JSON-RPC request received by that adapter.
        """
        data = getattr(message, "data", None)
        payload = {
            # model_dump is deliberately retained in addition to the stable,
            # easy-to-query fields below.  It exposes newly added Message
            # fields and any A2A metadata that the adapter preserved.
            "langflow_message": NewType00ALogRuntimeStart._json_value(message),
            "text": getattr(message, "text", None),
            "sender": getattr(message, "sender", None),
            "sender_name": getattr(message, "sender_name", None),
            "session_id": getattr(message, "session_id", None),
            "context_id": getattr(message, "context_id", None),
            "content_blocks": NewType00ALogRuntimeStart._json_value(
                getattr(message, "content_blocks", None)
            ),
            "properties": NewType00ALogRuntimeStart._json_value(
                getattr(message, "properties", None)
            ),
            "data": NewType00ALogRuntimeStart._json_value(data),
            "files": NewType00ALogRuntimeStart._json_value(getattr(message, "files", None)),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @classmethod
    def _json_value(cls, value: Any, seen: set[int] | None = None) -> Any:
        """Preserve nested Langflow/Pydantic values as inspectable JSON data."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        seen = seen if seen is not None else set()
        value_id = id(value)
        if value_id in seen:
            return "<circular reference>"

        if isinstance(value, dict):
            seen.add(value_id)
            result = {str(key): cls._json_value(item, seen) for key, item in value.items()}
            seen.remove(value_id)
            return result
        if isinstance(value, (list, tuple, set)):
            seen.add(value_id)
            result = [cls._json_value(item, seen) for item in value]
            seen.remove(value_id)
            return result

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._json_value(model_dump(), seen)
            except Exception:
                pass

        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict):
            return cls._json_value(attributes, seen)
        return str(value)

    def _db_config(self) -> dict[str, Any]:
        return {
            "host": str(getattr(self, "db_host", "") or "").strip(),
            "port": int(getattr(self, "db_port", 1521) or 1521),
            "service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "username": str(getattr(self, "db_username", "") or "").strip(),
            "password": self._secret_to_str(getattr(self, "db_password", "")),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    @staticmethod
    def _secret_to_str(value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")
