"""SQL formatting agent."""

from smart_migrate.agents.sql_formatting.SqlFormattingWorkflow import SqlFormattingWorkflow


class SqlFormattingAgent:
    """Supervisor-facing entrypoint for one SQL formatting job."""

    def __init__(self) -> None:
        self._workflow = SqlFormattingWorkflow()

    def process_job(self, job) -> str:
        return self._workflow.run(job)
