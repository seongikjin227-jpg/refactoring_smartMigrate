from langchain_core.tools import tool

from smart_migrate.supervisor.SupervisorJobRegistry import (
    callbacks,
    clear_active_job,
    formatting_registry,
    claim_job_execution,
    refresh_jobs_after_tool,
    set_active_job,
    sql_job_display_id,
)


@tool
def run_sql_formatting(row_ids: list) -> str:
    """Run the SQL Formatting Agent for selected NEXT_SQL_INFO row IDs.

    This is a LangChain tool wrapper around SqlFormattingAgent.process_job().
    Formatting generation and persistence are handled by the formatting
    workflow, not in the supervisor.
    """
    results = []
    logger = callbacks.get("logger")

    for row_id in row_ids:
        job = formatting_registry.get(str(row_id))
        if job is None:
            results.append(f"row_id={row_id} not found")
            continue

        job_label = sql_job_display_id(job, row_id)
        try:
            if not claim_job_execution():
                return "SKIP: another job already ran in this supervisor cycle."
            row_key = str(row_id)
            set_active_job("SQL Formatting", job_label, "FORMATTING")
            callbacks["sql_inc"](row_key)
            # format_proc is SqlFormattingAgent.process_job(job). Its workflow
            # owns formatting generation and result persistence.
            final_status = callbacks["format_proc"](job)
            results.append(f"{job_label} completed")
        except Exception as exc:
            if logger:
                logger.error(f"[SqlFormattingTool] {job_label} error: {exc}")
            results.append(f"{job_label} failed: {exc}")
        finally:
            clear_active_job()
            refresh_jobs_after_tool()
        break

    return "SqlFormatting result: " + " | ".join(results)
