"""SQL tuning agent."""

from smart_migrate.agents.sql_tuning.SqlTuningWorkflow import SqlTuningWorkflow


class SqlTuningAgent:
    """Supervisor-facing entrypoint for one SQL tuning job."""

    def __init__(self) -> None:
        self._workflow = SqlTuningWorkflow()

    def process_job(self, job) -> str:
        return self._workflow.run(job)
