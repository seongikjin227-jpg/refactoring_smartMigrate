"""LangGraph definition for one SQL tuning job."""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from smart_migrate.shared.SharedLogging import logger
from smart_migrate.shared.SqlStatuses import FAIL_TUNED, TUNING_PASS
from smart_migrate.agents.sql_tuning.SqlTuningState import SqlTuningGraphState


def build_sql_tuning_workflow(tuning_runner):
    def apply_tuning_rules_node(state: SqlTuningGraphState) -> SqlTuningGraphState:
        execution = state["execution"]
        execution.should_retry_tuning = False
        if tuning_runner.max_iterations <= 0:
            execution.tuned_sql = ""
            execution.tuned_result = "TUNING DISABLED"
            execution.tuned_test = FAIL_TUNED
            execution.failure_status = FAIL_TUNED
            execution.last_error = "TUNING_DISABLED: TOBE_SQL_TUNING_MAX_ITERATIONS <= 0"
            return {"execution": execution}

        tuning_runner._apply_tuning_rules(execution)
        if _is_missing_tuning_rule_result(execution.tuned_result):
            logger.warning(
                f"[sql_tuning_graph] ({execution.job_key}) stage=FAIL_TUNED "
                "completed (reason=tuning_rule_not_found)"
            )
        elif _is_no_tuning_result(execution.tuned_result):
            execution.tuned_test = TUNING_PASS
            logger.info(
                f"[sql_tuning_graph] ({execution.job_key}) stage=PASS_TUNED_TEST_FOR_NO_TUNING "
                "completed (reason=no_tuning)"
            )
        elif (execution.job.tag_kind or "").strip().upper() != "SELECT":
            tag_kind = (execution.job.tag_kind or "").strip().upper()
            execution.tuned_test = TUNING_PASS
            logger.info(
                f"[sql_tuning_graph] ({execution.job_key}) stage=PASS_TUNED_TEST_FOR_NON_SELECT "
                f"completed (tag_kind={tag_kind or 'UNKNOWN'})"
            )
        return {"execution": execution}

    def validate_tuned_sql_node(state: SqlTuningGraphState) -> SqlTuningGraphState:
        execution = state["execution"]
        execution.should_retry_tuning = False
        try:
            tuning_runner._run_tuned_sql_validation(execution)
        except Exception as exc:
            if execution.tuning_attempt >= execution.max_tuning_attempts:
                raise
            execution.last_error = f"TUNED_TEST_SQL_ERROR: {exc}"
            logger.warning(
                f"[sql_tuning_graph] ({execution.job_key}) stage=TUNING_RETRY_CONTEXT "
                f"attempt={execution.tuning_attempt + 1}/{execution.max_tuning_attempts} "
                f"last_error={execution.last_error}"
            )
            execution.tuning_attempt += 1
            execution.should_retry_tuning = True
            return {"execution": execution}

        if execution.tuned_test != TUNING_PASS and execution.tuning_attempt < execution.max_tuning_attempts:
            execution.last_error = (
                "TUNED_TEST_VALIDATION_FAIL: "
                + _summarize_test_rows_for_retry(execution.tuned_test_rows)
            )
            logger.warning(
                f"[sql_tuning_graph] ({execution.job_key}) stage=TUNING_RETRY_CONTEXT "
                f"attempt={execution.tuning_attempt + 1}/{execution.max_tuning_attempts} "
                f"last_error={execution.last_error}"
            )
            execution.tuning_attempt += 1
            execution.should_retry_tuning = True
        return {"execution": execution}

    graph = StateGraph(SqlTuningGraphState)
    graph.add_node("apply_tuning_rules", apply_tuning_rules_node)
    graph.add_node("validate_tuned_sql", validate_tuned_sql_node)

    graph.add_edge(START, "apply_tuning_rules")
    graph.add_conditional_edges(
        "apply_tuning_rules",
        route_after_apply_tuning,
        {
            "validate": "validate_tuned_sql",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "validate_tuned_sql",
        route_after_validate_tuned_sql,
        {
            "retry_apply": "apply_tuning_rules",
            "end": END,
        },
    )
    return graph.compile()


def route_after_apply_tuning(state: SqlTuningGraphState) -> Literal["validate", "end"]:
    execution = state["execution"]

    if execution.tuned_test == FAIL_TUNED:
        return "end"

    if _is_missing_tuning_rule_result(execution.tuned_result):
        return "end"

    if _is_no_tuning_result(execution.tuned_result):
        return "end"

    tag_kind = (execution.job.tag_kind or "").strip().upper()
    if tag_kind != "SELECT":
        return "end"

    return "validate"


def route_after_validate_tuned_sql(state: SqlTuningGraphState) -> Literal["retry_apply", "end"]:
    execution = state["execution"]
    return "retry_apply" if execution.should_retry_tuning else "end"


def _is_no_tuning_result(tuned_result: str | None) -> bool:
    return (tuned_result or "").strip().upper() == "NO TUNING"


def _is_missing_tuning_rule_result(tuned_result: str | None) -> bool:
    return (tuned_result or "").strip().upper() == "TUNING RULE NOT FOUND"


def _get_case_insensitive_value(row: dict, key: str):
    lowered = key.lower()
    for existing_key, value in row.items():
        if str(existing_key).lower() == lowered:
            return value
    return None


def _summarize_test_rows_for_retry(rows: list[dict]) -> str:
    if not rows:
        return "no_rows_returned"

    samples: list[str] = []
    for row in rows[:5]:
        case_no = _get_case_insensitive_value(row, "case_no")
        from_count = _get_case_insensitive_value(row, "from_count")
        to_count = _get_case_insensitive_value(row, "to_count")
        samples.append(f"CASE_NO={case_no},BASELINE_COUNT={from_count},TUNED_COUNT={to_count}")
    return " ; ".join(samples)
