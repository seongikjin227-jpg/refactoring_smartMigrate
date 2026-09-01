### Workflow logging helper
########################################################################################################
# 00A_logRuntimeStart.py registers the in-memory logging handler.
# Components only write Python logging events.
# 00B_logDbUpdate.py reads the handler records and writes them to SFAADM.NEXT_MIG_LOG.

import logging


def _log_workflow(
    map_id,
    log_type,
    log_level,
    step_name,
    status,
    message,
    retry_count=0,
):
    logging.getLogger("smartmigrate.workflow").log(
        logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO,
        str(message or ""),
        extra={
            "workflow_log": {
                "map_id": map_id,
                "mig_kind": "WORKFLOW",
                "log_type": str(log_type or "")[:20],
                "log_level": str(log_level or "")[:20],
                "step_name": str(step_name or "")[:50],
                "status": str(status or "")[:20],
                "message": str(message or "")[:4000],
                "retry_count": retry_count,
            }
        },
    )


# Example inside a Langflow component method:
#
# logging.getLogger("smartmigrate.workflow").info(
#     "before run_job",
#     extra={
#         "workflow_log": {
#             "map_id": 0,
#             "mig_kind": "WORKFLOW",
#             "log_type": "10C_MIG_EXEC",
#             "log_level": "INFO",
#             "step_name": "RUN_JOB",
#             "status": "START",
#             "message": "before run_job",
#             "retry_count": 0,
#         }
#     },
# )
########################################################################################################
