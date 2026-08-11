from langchain_core.tools import tool

from smart_migrate.shared.SqlStatuses import is_conversion_pass
from smart_migrate.supervisor.tools.SupervisorSqlContinuation import run_tuning_continuation
from smart_migrate.supervisor.SupervisorJobRegistry import (
    callbacks,
    clear_active_job,
    claim_job_execution,
    refresh_jobs_after_tool,
    set_active_job,
    sql_job_display_id,
    sql_registry,
)


@tool
def run_sql_conversion(row_id: str) -> str:
    """Run the SQL Conversion Agent for one selected job.

    This is a LangChain tool wrapper around SqlConversionAgent.process_job().
    Conversion retry, bind SQL generation, test SQL generation, and validation
    are handled inside the conversion coordinator/graph, not in the supervisor.
    """
    row_key = str(row_id)
    job = sql_registry.get(row_key)
    logger = callbacks.get("logger")

    if job is None:
        return f"ERROR: row_id={row_key} was not found in the current registry."

    job_label = sql_job_display_id(job, row_key)
    try:
        if not claim_job_execution():
            return "SKIP: another job already ran in this supervisor cycle."
        set_active_job("SQL Conversion", job_label, "CONVERSION")
        callbacks["sql_inc"](row_key)
        # sql_proc is SqlConversionAgent.process_job(job). Its coordinator owns
        # conversion retry, validation, and persistence.
        final_status = callbacks["sql_proc"](job)
        continuation_results = []
        if is_conversion_pass(final_status):
            continuation_results = run_tuning_continuation(row_key, logger=logger)
        if logger:
            logger.info(f"[SqlConversionTool] {job_label} completed (status={final_status})")
        suffix = f" | {' | '.join(continuation_results)}" if continuation_results else ""
        return f"SqlConversion {job_label} completed status={final_status}{suffix}"
    except Exception as exc:
        if logger:
            logger.error(f"[SqlConversionTool] {job_label} error: {exc}")
        return f"ERROR: {job_label} failed: {exc}"
    finally:
        clear_active_job()
        refresh_jobs_after_tool()
