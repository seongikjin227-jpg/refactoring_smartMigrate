from __future__ import annotations

import logging

from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message


LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


def create_db_connection(db_config: dict[str, Any]):
    import oracledb

    dsn = oracledb.makedsn(
        db_config["host"],
        int(db_config.get("port") or 1521),
        service_name=db_config["service_name"],
    )
    return oracledb.connect(user=db_config["username"], password=db_config["password"], dsn=dsn)


class SmartMigrateDBHandler(logging.Handler):

    def __init__(self, db_config: dict[str, Any]):
        super().__init__(level=logging.DEBUG)
        self.handler_marker = HANDLER_MARKER
        self.db_config = dict(db_config)
        self.connection = create_db_connection(db_config)
        self.records: list[dict[str, Any]] = []
        self.insert_error = None

    def emit(self, record: logging.LogRecord) -> None:
        event = self._event(record)
        row = {
            "created_at": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "map_id": int(event.get("map_id") or 0),
            "mig_kind": str(event.get("mig_kind") or "WORKFLOW")[:100],
            "log_type": str(event.get("log_type") or "")[:20],
            "log_level": str(event.get("log_level") or "noLevelName")[:20],
            "step_name": str(event.get("step_name") or "")[:50],
            "status": str(event.get("status") or "noStatus")[:20],
            "message": str(event.get("message") or "noMessage")[:4000],
            "retry_count": int(event.get("retry_count") or 0),
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
            message = event[6] if len(event) > 7 else record.getMessage()
            retry_count = event[7] if len(event) > 7 else (event[6] if len(event) > 6 else 0)
            return {
                "map_id": event[0] if len(event) > 0 else 0,
                "mig_kind": event[1] if len(event) > 1 else "WORKFLOW",
                "log_type": event[2] if len(event) > 2 else "PY_LOG",
                "log_level": event[3] if len(event) > 3 else "noLevelName",
                "step_name": event[4] if len(event) > 4 else "LOGGING",
                "status": event[5] if len(event) > 5 else "noStatus",
                "message": message,
                "retry_count": retry_count,
            }
        if isinstance(event, dict):
            event = dict(event)
            event["message"] = event.get("message") or record.getMessage() or "noMessage"
            return event
        return {
            "map_id": 0,
            "mig_kind": "WORKFLOW",
            "log_type": "PY_LOG",
            "log_level": "noLevelName",
            "step_name": "LOGGING",
            "status": "noStatus",
            "message": record.getMessage() or "noMessage",
            "retry_count": 0,
        }

    def _insert_row(self, row: dict[str, Any]) -> None:
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO {self._schema()}.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    {self._schema()}.MIGRATION_LOG_SEQ.NEXTVAL,
                    :map_id,
                    :mig_kind,
                    :log_type,
                    :log_level,
                    :step_name,
                    :status,
                    :message,
                    :retry_count,
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

    def _schema(self) -> str:
        return str(self.db_config.get("system_schema") or "SFAADM").strip().upper()


class NewType00ALogRuntimeStart(Component):
    display_name = "00A Log Runtime Start"
    description = "Register SmartMigrate workflow DB logging handler and pass the chat input through."
    name = "NewType00ALogRuntimeStart"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", value="SFAADM", required=False),
    ]

    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        text = str(getattr(self, "input_text", "") or "")
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
        logger.info(f"workflow start input_len={len(text)}", extra={"workflow_log": [0, "WORKFLOW", "LOG_RUNTIME_START", "INFO", "RUN", "START", 0]})
        self.status = {"ok": handler.insert_error is None, "db_insert_error": handler.insert_error}
        return Message(text=text)

    def _db_config(self) -> dict[str, Any]:
        return {
            "host": str(getattr(self, "db_host", "") or "").strip(),
            "port": int(getattr(self, "db_port", 1521) or 1521),
            "service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "username": str(getattr(self, "db_username", "") or "").strip(),
            "password": self._secret_to_str(getattr(self, "db_password", "")),
            "system_schema": str(getattr(self, "system_schema", "SFAADM") or "SFAADM").strip() or "SFAADM",
        }

    def _secret_to_str(self, value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")
