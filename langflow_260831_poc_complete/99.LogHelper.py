### Workflow logging helper
########################################################################################################
# 00A_logRuntimeStart.py registers the SmartMigrate DB logging handler.
# Components only write Python logging events.
# SmartMigrateDBHandler writes each event to SFAADM.NEXT_MIG_LOG.
# The optional 8th workflow_log value is written to GENERATE_SQL; omit it for NULL.

import logging


LOGGER_NAME = "smartmigrate.workflow"


def _log_workflow(
    map_id,
    log_type,
    log_level,
    step_name,
    status,
    message,
    retry_count=0,
    generated_sql=None,
):
    logger = logging.getLogger(LOGGER_NAME)
    event = [map_id, "WORKFLOW", str(log_type or "")[:20], str(log_level or "")[:20], str(step_name or "")[:50], str(status or "")[:20], retry_count]
    if generated_sql is not None:
        event.append(generated_sql)
    logger.log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": event})


# Example inside a Langflow component method:
#
# logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "START", 0]})
# logging.getLogger("smartmigrate.workflow").info("prompt built", extra={"workflow_log": [map_id, "DB_MIGRATION", "PROMPT_BUILD", "INFO", "PROMPT_BUILD", "PASS", 0, prompt]})
########################################################################################################
