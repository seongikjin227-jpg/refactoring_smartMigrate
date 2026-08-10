"""SQL tuning workflow state."""

from dataclasses import dataclass, field


@dataclass
class SqlTuningState:
    """Mutable state for one SQL tuning job."""

    job: object
    job_key: str
    mapping_rules: list
    last_error: str | None = None
    tuning_examples: list[dict] = field(default_factory=list)
    tobe_sql: str = ""
    tuned_sql: str = ""
    tuned_result: str = ""
    tuned_test: str | None = None
    bind_sql: str = ""
    bind_set_for_db: str | None = None
    bind_set_json_for_test: str = "[]"
    bind_param_names: list[str] = field(default_factory=list)
    test_sql: str = ""
    formatted_sql: str = ""
    test_rows: list[dict] = field(default_factory=list)
    tuned_test_rows: list[dict] = field(default_factory=list)
    status: str | None = None
    failure_status: str | None = None
