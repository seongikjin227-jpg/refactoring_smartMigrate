"""SQL tuning workflow.

The tuning path is fixed, so this module keeps the sequence in one readable
workflow instead of spreading it across many node files.
"""

from smart_migrate.shared.SharedLogging import logger
from smart_migrate.repositories.MappingRuleRepository import get_all_mapping_rules
from smart_migrate.repositories.SqlJobRepository import update_cycle_result, update_tuning_error
from smart_migrate.shared.SqlStatuses import FAIL_TEST, is_fail
from smart_migrate.agents.sql_conversion.SqlConversionCoordinator import SqlTuningAgent as _SqlTuningRunner
from smart_migrate.agents.sql_tuning.SqlTuningState import SqlTuningState


class SqlTuningWorkflow:
    """Run one SQL tuning job from TO_SQL to TUNED_TO_SQL validation."""

    def __init__(self) -> None:
        self._runner = _SqlTuningRunner()

    def run(self, job) -> str:
        job_key = f"{job.space_nm}.{job.sql_id}"
        state = None
        try:
            state = SqlTuningState(
                job=job,
                job_key=job_key,
                mapping_rules=get_all_mapping_rules(),
                last_error=None,
            )
            state.tobe_sql = job.to_sql_text
            state.bind_set_for_db = job.bind_set

            self._runner.run(state)

            final_status = state.tuned_test if state.tuned_test else (state.failure_status or FAIL_TEST)
            final_log = self._build_final_log(state=state, final_status=final_status, job_key=job_key)
            update_cycle_result(
                row_id=job.row_id,
                tobe_sql=state.tobe_sql,
                tuned_sql=state.tuned_sql if state.tuned_sql else None,
                tuned_result=state.tuned_result if state.tuned_result else None,
                tuned_test=final_status,
                bind_sql=job.bind_sql,
                bind_set=job.bind_set,
                test_sql=job.test_sql,
                status=job.status,
                final_log=final_log,
                formatted_sql=state.formatted_sql if state.formatted_sql else None,
            )
            logger.info(f"[SqlTuningWorkflow] {job_key} tuning completed (status={final_status})")
            return final_status

        except Exception as exc:
            logger.error(f"[SqlTuningWorkflow] {job_key} failed: {exc}")
            update_tuning_error(
                job.row_id,
                str(exc),
                tuned_sql=state.tuned_sql if state and state.tuned_sql else None,
            )
            return state.failure_status if state and state.failure_status else FAIL_TEST

    @staticmethod
    def _get_case_insensitive_value(row: dict, key: str):
        lowered = key.lower()
        for existing_key, value in row.items():
            if str(existing_key).lower() == lowered:
                return value
        return None

    @classmethod
    def _summarize_tuned_test_rows(cls, rows: list[dict]) -> str:
        if not rows:
            return "no_tuned_test_rows"

        samples: list[str] = []
        for row in rows[:5]:
            case_no = cls._get_case_insensitive_value(row, "case_no")
            baseline_count = cls._get_case_insensitive_value(row, "from_count")
            tuned_count = cls._get_case_insensitive_value(row, "to_count")
            samples.append(f"CASE_NO={case_no},BASELINE_COUNT={baseline_count},TUNED_COUNT={tuned_count}")
        return " ; ".join(samples)

    @classmethod
    def _build_final_log(cls, state: SqlTuningState, final_status: str, job_key: str) -> str:
        base_log = f"TUNING COMPLETED status={final_status} job={job_key} changed={bool(state.tuned_sql)}"
        if is_fail(final_status):
            reason = state.last_error or "TUNED_TEST_VALIDATION_FAIL"
            details = cls._summarize_tuned_test_rows(state.tuned_test_rows)
            return f"{base_log} reason={reason} details={details}"
        return base_log
