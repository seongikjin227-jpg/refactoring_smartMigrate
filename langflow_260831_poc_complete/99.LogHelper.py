### Workflow 로그 helper
########################################################################################################
# 00A_logRuntimeStart.py가 SmartMigrate DB logging handler를 등록한다.
# 각 컴포넌트는 Python logging event만 남기면 된다.
# SmartMigrateDBHandler가 각 event를 system_schema.NEXT_MIG_LOG에 저장한다.
# system_schema가 없으면 현재 Oracle schema에 저장되지만, 운영 flow에서는 system_schema를 명시한다.
# workflow_log의 선택 8번째 값은 GENERATE_SQL에 저장되며, 생략하면 NULL이다.

import logging


LOGGER_NAME = "smartmigrate.workflow"


# 공통 workflow logger에 NEXT_MIG_LOG 저장용 logging event를 남긴다.
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


# Langflow component method 안에서 사용하는 예시:
#
# logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "START", 0]})
# logging.getLogger("smartmigrate.workflow").info("prompt built", extra={"workflow_log": [map_id, "DB_MIGRATION", "PROMPT_BUILD", "INFO", "PROMPT_BUILD", "PASS", 0, prompt]})
########################################################################################################
