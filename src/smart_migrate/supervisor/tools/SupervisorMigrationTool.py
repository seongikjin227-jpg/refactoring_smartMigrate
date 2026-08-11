from langchain_core.tools import tool

from smart_migrate.supervisor.SupervisorJobRegistry import (
    callbacks,
    clear_active_job,
    claim_job_execution,
    migration_job_display_id,
    mig_registry,
    refresh_jobs_after_tool,
    set_active_job,
)


@tool
def run_data_migration(map_id: int) -> str:
    """Run the DB Migration Agent for one selected job.

    This is a LangChain tool wrapper, not the agent itself. ToolNode calls this
    wrapper, then the wrapper calls the registered MigrationOrchestrator
    callback. Retry, SQL regeneration, execution, and verification are handled
    inside MigrationGraph.
    """
    job = mig_registry.get(map_id)
    logger = callbacks.get("logger")

    if job is None:
        return f"ERROR: map_id={map_id} was not found in the current registry."

    try:
        if not claim_job_execution():
            return "SKIP: another job already ran in this supervisor cycle."
        set_active_job("DB Migration", migration_job_display_id(job, map_id), "RUN")
        # mig_proc is MigrationOrchestrator.process_job(job). It invokes
        # MigrationGraph, where the DB migration retry logic lives.
        final_status = callbacks["mig_proc"](job)
        if logger:
            logger.info(f"[DataMigrationTool] map_id={map_id} completed")
        return f"DataMigration map_id={map_id} completed"
    except Exception as exc:
        if logger:
            logger.error(f"[DataMigrationTool] map_id={map_id} error: {exc}")
        return f"ERROR: map_id={map_id} failed: {exc}"
    finally:
        clear_active_job()
        refresh_jobs_after_tool()
