"""SQL formatting workflow."""

from smart_migrate.shared.SharedLogging import logger
from smart_migrate.repositories.SqlJobRepository import update_formatted_sql
from smart_migrate.agents.sql_conversion.SqlLlmService import generate_formatted_sql
from smart_migrate.agents.sql_formatting.SqlFormattingState import SqlFormattingState


class SqlFormattingWorkflow:
    """Run one SQL formatting job."""

    name = "sql_formatting_workflow"

    def run(self, job) -> str:
        state = SqlFormattingState(
            job=job,
            job_key=f"{job.space_nm}.{job.sql_id}",
            source_sql=(job.tuned_sql or job.to_sql_text or "").strip(),
        )
        if not state.source_sql:
            logger.warning(f"[{self.name}] ({state.job_key}) stage=SKIP_FORMATTING completed (reason=no_sql)")
            state.status = "SKIP"
            return state.status

        try:
            state.formatted_sql = generate_formatted_sql(job=job, input_sql=state.source_sql)
            update_formatted_sql(row_id=job.row_id, formatted_sql=state.formatted_sql)
            logger.info(
                f"[{self.name}] ({state.job_key}) stage=GENERATE_FORMATTED_SQL "
                f"completed (sql_length={len(state.formatted_sql)})"
            )
            state.status = "PASS"
            return state.status
        except Exception as exc:
            state.error_message = str(exc)
            logger.error(f"[{self.name}] ({state.job_key}) stage=GENERATE_FORMATTED_SQL failed: {exc}")
            state.status = "FAIL"
            return state.status
