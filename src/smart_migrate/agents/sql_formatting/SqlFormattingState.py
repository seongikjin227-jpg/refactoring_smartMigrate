"""SQL formatting workflow state."""

from dataclasses import dataclass


@dataclass
class SqlFormattingState:
    """Mutable state for one SQL formatting job."""

    job: object
    job_key: str
    source_sql: str = ""
    formatted_sql: str = ""
    status: str | None = None
    error_message: str | None = None
