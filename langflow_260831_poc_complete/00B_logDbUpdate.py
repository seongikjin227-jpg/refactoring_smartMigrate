from __future__ import annotations

import logging

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


LOGGER_NAME = "smartmigrate.workflow"


class NewType00BLogDbUpdate(Component):
    display_name = "00B Log DB Update"
    description = "Read workflow logs from the logging handler, save them to NEXT_MIG_LOG, and expose them as a result table."
    name = "NewType00BLogDbUpdate"

    DB_HOST = ""
    DB_PORT = 1521
    DB_SERVICE_NAME = ""
    DB_USERNAME = ""
    DB_PASSWORD = ""

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text", required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="run", types=["Message"]),
        Output(display_name="Result Table", name="result_table", method="result_table", types=["DataFrame"]),
    ]

    def run(self) -> Message:
        text = str(getattr(self, "input_text", "") or "")
        self._finalize_once(text)
        return Message(text=text)

    def result_table(self) -> DataFrame:
        text = str(getattr(self, "input_text", "") or "")
        rows = self._finalize_once(text)
        return DataFrame(rows)

    def _finalize_once(self, text: str) -> list[dict]:
        handler = self._workflow_handler()
        if handler is None:
            rows = [self._fallback_row("ERROR", "workflow logging handler is not registered")]
            self.status = {"ok": False, "message": "workflow logging handler is not registered", "rows": rows}
            return rows
        if not getattr(handler, "persisted", False):
            if not getattr(handler, "final_event_logged", False):
                self._log_final_event(text)
                handler.final_event_logged = True
            rows = list(getattr(handler, "records", []) or [])
            db_error = self._insert_rows(rows)
            handler.persisted = db_error is None
            handler.persist_result_rows = rows
            handler.persist_db_error = db_error
        rows = list(getattr(handler, "persist_result_rows", None) or getattr(handler, "records", []) or [])
        db_error = getattr(handler, "persist_db_error", None)
        self.status = {"ok": db_error is None, "saved_count": len(rows), "db_error": db_error, "rows": rows}
        return rows

    def _log_final_event(self, text: str) -> None:
        status = "ERROR" if self._looks_like_error(text) else "END"
        log_level = "ERROR" if status == "ERROR" else "INFO"
        message = f"workflow final status={status} output_len={len(text)}"
        logging.getLogger(LOGGER_NAME).log(
            logging.ERROR if log_level == "ERROR" else logging.INFO,
            message,
            extra={
                "workflow_log": {
                    "map_id": 0,
                    "mig_kind": "WORKFLOW",
                    "log_type": "LOG_DB_UPDATE",
                    "log_level": log_level,
                    "step_name": "RUN",
                    "status": status,
                    "message": message,
                    "retry_count": 0,
                }
            },
        )

    def _workflow_handler(self):
        logger = logging.getLogger(LOGGER_NAME)
        for handler in logger.handlers:
            if getattr(handler, "smartmigrate_workflow_handler", False):
                return handler
        return None

    def _insert_rows(self, rows: list[dict]) -> str | None:
        if not rows:
            return None
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO SFAADM.NEXT_MIG_LOG (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT
                ) VALUES (
                    SFAADM.MIGRATION_LOG_SEQ.NEXTVAL,
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
                [self._db_row(row) for row in rows],
            )
            conn.commit()
            return None
        except Exception as exc:
            return f"NEXT_MIG_LOG batch insert failed: {exc}"
        finally:
            if conn is not None:
                conn.close()

    def _db_row(self, row: dict) -> dict:
        return {
            "map_id": int(row.get("MAP_ID") or 0),
            "mig_kind": str(row.get("MIG_KIND") or "WORKFLOW")[:100],
            "log_type": str(row.get("LOG_TYPE") or "")[:20],
            "log_level": str(row.get("LOG_LEVEL") or "")[:20],
            "step_name": str(row.get("STEP_NAME") or "")[:50],
            "status": str(row.get("STATUS") or "")[:20],
            "message": str(row.get("MESSAGE") or "")[:4000],
            "retry_count": int(row.get("RETRY_COUNT") or 0),
            "created_at": str(row.get("CREATED_AT") or ""),
        }

    def _fallback_row(self, status: str, message: str) -> dict:
        return {
            "RUN_ID": "",
            "SEQ": 1,
            "CREATED_AT": "",
            "MAP_ID": 0,
            "MIG_KIND": "WORKFLOW",
            "LOG_TYPE": "LOG_DB_UPDATE",
            "LOG_LEVEL": "ERROR",
            "STEP_NAME": "RUN",
            "STATUS": status,
            "MESSAGE": message,
            "RETRY_COUNT": 0,
        }

    def _looks_like_error(self, text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("error", "exception", "fail", "failed")) or any(
            token in text for token in ("\uc2e4\ud328", "\uc624\ub958", "\uc608\uc678")
        )

