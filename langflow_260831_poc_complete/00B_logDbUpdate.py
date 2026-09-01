from __future__ import annotations

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


class NewType00BLogDbUpdate(Component):
    display_name = "00B Log DB Update"
    description = "Write a workflow terminal row to NEXT_MIG_LOG and pass the chat output through."
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
        self._write_final_log_once(text)
        return Message(text=text)

    def result_table(self) -> DataFrame:
        text = str(getattr(self, "input_text", "") or "")
        self._write_final_log_once(text)
        return DataFrame(self._fetch_workflow_logs())

    def _write_final_log_once(self, text: str) -> None:
        if getattr(self, "_final_log_written", False):
            return
        status = "ERROR" if self._looks_like_error(text) else "END"
        log_level = "ERROR" if status == "ERROR" else "INFO"
        self._insert_log(
            0,
            "WORKFLOW",
            "LOG_DB_UPDATE",
            log_level,
            "RUN",
            status,
            f"workflow final status={status} output_len={len(text)}",
            0,
            "",
        )
        self._final_log_written = True

    def _fetch_workflow_logs(self) -> list[dict]:
        conn = None
        try:
            import oracledb

            dsn = oracledb.makedsn(self.DB_HOST, int(self.DB_PORT or 1521), service_name=self.DB_SERVICE_NAME)
            conn = oracledb.connect(user=self.DB_USERNAME, password=self.DB_PASSWORD, dsn=dsn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT LOG_ID,
                       TO_CHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT,
                       MAP_ID,
                       MIG_KIND,
                       LOG_TYPE,
                       LOG_LEVEL,
                       STEP_NAME,
                       STATUS,
                       MESSAGE,
                       RETRY_COUNT
                  FROM SFAADM.NEXT_MIG_LOG
                 WHERE MAP_ID = 0
                 ORDER BY LOG_ID
                """
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as exc:
            self.status = f"NEXT_MIG_LOG result table failed: {exc}"
            return [
                {
                    "LOG_ID": None,
                    "CREATED_AT": "",
                    "MAP_ID": 0,
                    "MIG_KIND": "WORKFLOW",
                    "LOG_TYPE": "LOG_DB_UPDATE",
                    "LOG_LEVEL": "ERROR",
                    "STEP_NAME": "RESULT_TABLE",
                    "STATUS": "ERROR",
                    "MESSAGE": str(exc)[:4000],
                    "RETRY_COUNT": 0,
                }
            ]
        finally:
            if conn is not None:
                conn.close()

    def _looks_like_error(self, text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("error", "exception", "fail", "failed", "실패", "오류", "예외"))

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
