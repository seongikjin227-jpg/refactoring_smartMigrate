### NEXT_MIG_LOG insert helper
########################################################################################################
from __future__ import annotations

from typing import Any


# Fill these values directly in this file before using the helper.
DB_HOST = ""
DB_PORT = 1521
DB_SERVICE_NAME = ""
DB_USERNAME = ""
DB_PASSWORD = ""
SYSTEM_SCHEMA = ""


def _insert_log(
    self: Any,
    map_id: int,
    mig_kind: str,
    log_type: str,
    log_level: str,
    step_name: str,
    status: str,
    message: str,
    retry_count: int,
    generated_sql: str = "",
) -> None:
    """Copy this method into a component and call self._insert_log(...)."""
    table = f"{SYSTEM_SCHEMA}.NEXT_MIG_LOG" if SYSTEM_SCHEMA else "NEXT_MIG_LOG"
    sequence = f"{SYSTEM_SCHEMA}.MIGRATION_LOG_SEQ" if SYSTEM_SCHEMA else "MIGRATION_LOG_SEQ"

    import oracledb

    dsn = oracledb.makedsn(DB_HOST, int(DB_PORT or 1521), service_name=DB_SERVICE_NAME)
    conn = oracledb.connect(user=DB_USERNAME, password=DB_PASSWORD, dsn=dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO {table} (
                LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, CREATED_AT, UPD_TS
            ) VALUES (
                {sequence}.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            [
                map_id,
                str(mig_kind or ""),
                str(log_type or ""),
                str(log_level or ""),
                str(step_name or ""),
                str(status or ""),
                str(message or "")[:4000],
                retry_count,
            ],
        )
        conn.commit()
    finally:
        conn.close()


# Examples:
#
# self._insert_log(0, "WORKFLOW", "WORKFLOW_LOOP", "INFO", "LOOP_START", "START", f"before total={len(data_list)}", 0, "")
# self._insert_log(0, "WORKFLOW", "WORKFLOW_LOOP", "INFO", "LOOP_END", "END", "after", 0, "")
########################################################################################################
