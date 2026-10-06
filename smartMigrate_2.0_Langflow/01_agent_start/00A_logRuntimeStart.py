from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


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
    description = "Log the complete Chat Input Message and pass that Message through unchanged."
    name = "NewType00ALogRuntimeStart"

    inputs = [
        MessageInput(name="input_message", display_name="Chat Input Message", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        IntInput(name="max_attachment_bytes", display_name="Maximum Download Bytes", value=20_000_000, required=False),
    ]
    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        # Diagnostic boundary: retain the original Message object.  Do not reduce
        # it to Message.text before logging or handing it to the next component.
        message = getattr(self, "input_message", None)
        if not isinstance(message, Message):
            raise TypeError("00A requires a Langflow Message from Chat Input.")
        raw_payload = self._message_payload_json(message)
        logger = logging.getLogger(LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = SmartMigrateDBHandler(self._db_config())
        logger.addHandler(handler)
        logger.info(
            raw_payload[:4000],
            extra={
                "workflow_log": [
                    0, "WORKFLOW", "CHAT_INPUT", "INFO", "MESSAGE", "START", 0, raw_payload,
                ]
            },
        )
        enriched_message, attachment_result = self._enrich_presigned_attachment(message)
        if attachment_result:
            attachment_json = json.dumps(attachment_result, ensure_ascii=False, default=str)
            logger.info(
                attachment_json,
                extra={"workflow_log": [0, "WORKFLOW", "00A_ATTACHMENT_PARSE", "INFO", "PRESIGNED_URL", attachment_result.get("status", "FAIL"), 0, attachment_json]},
            )
        self.status = {
            "ok": handler.insert_error is None,
            "db_insert_error": handler.insert_error,
            "attachment": attachment_result,
        }
        # Give the next component the exact envelope written to the runtime log.
        # Keep files/session/properties on the Message itself, so Langflow Agent
        # still performs its normal attachment parsing in addition to seeing this
        # inspectable JSON payload as Message.text.
        enriched_payload = self._message_payload_json(enriched_message)
        return enriched_message.model_copy(update={"text": enriched_payload})

    def _enrich_presigned_attachment(self, message: Message) -> tuple[Message, dict[str, Any] | None]:
        data = self._json_value(getattr(message, "data", None))
        source_data = dict(data) if isinstance(data, dict) else {}
        url = self._find_presigned_url(source_data)
        if not url:
            return message, None
        try:
            content = asyncio.run(self._download_presigned_url(url))
            parsed = self._parse_excel(content)
            source_data["uploaded_attachment"] = {
                "presigned_url": url,
                "byte_count": len(content),
                "parsed_excel": parsed,
            }
            return message.model_copy(update={"data": source_data}), {
                "status": "PASS",
                "url_host": urlparse(url).hostname,
                "byte_count": len(content),
                "sheet_count": len(parsed["sheets"]),
            }
        except Exception as exc:
            source_data["uploaded_attachment"] = {
                "presigned_url": url,
                "parse_error": str(exc),
            }
            return message.model_copy(update={"data": source_data}), {
                "status": "FAIL",
                "url_host": urlparse(url).hostname,
                "error": str(exc),
            }

    async def _download_presigned_url(self, url: str) -> bytes:
        import aiohttp

        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Presigned URL must use HTTPS and include a hostname.")
        max_bytes = int(getattr(self, "max_attachment_bytes", 20_000_000) or 20_000_000)
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                response.raise_for_status()
                content = await response.read()
        if len(content) > max_bytes:
            raise ValueError(f"Attachment exceeds max_attachment_bytes={max_bytes}.")
        return content

    @staticmethod
    def _find_presigned_url(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                if normalized in {"downloadurl", "presignedurl", "fileurl"} and isinstance(item, str):
                    return item.strip()
            for item in value.values():
                found = NewType00ALogRuntimeStart._find_presigned_url(item)
                if found:
                    return found
        if isinstance(value, (list, tuple)):
            for item in value:
                found = NewType00ALogRuntimeStart._find_presigned_url(item)
                if found:
                    return found
        return ""

    @staticmethod
    def _parse_excel(content: bytes) -> dict[str, Any]:
        import pandas as pd

        try:
            sheets = pd.read_excel(BytesIO(content), sheet_name=None, dtype=object, engine="openpyxl")
        except ImportError as exc:
            raise RuntimeError("xlsx parsing requires the openpyxl package on the Langflow server.") from exc
        parsed_sheets = []
        for name, frame in sheets.items():
            normalized = frame.where(frame.notna(), None)
            parsed_sheets.append(
                {
                    "name": str(name),
                    "columns": [str(column) for column in normalized.columns],
                    "rows": normalized.to_dict(orient="records"),
                }
            )
        return {"format": "xlsx", "sheets": parsed_sheets}

    @staticmethod
    def _message_payload_json(message: Message) -> str:
        """Record the Chat Input fields without changing the message itself."""
        data = getattr(message, "data", None)
        payload = {
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
