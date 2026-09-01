### NEXT_MIG_LOG insert helper
########################################################################################################
# Copy these values into the component class and fill them directly.
DB_HOST = ""
DB_PORT = 1521
DB_SERVICE_NAME = ""
DB_USERNAME = ""
DB_PASSWORD = ""


def _insert_log(
    self,
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


# Examples:
#
# self._insert_log(0, "WORKFLOW", "WORKFLOW_LOOP", "INFO", "LOOP_START", "START", f"before total={len(data_list)}", 0, "")
# self._insert_log(0, "WORKFLOW", "WORKFLOW_LOOP", "INFO", "LOOP_END", "END", "after", 0, "")
########################################################################################################
