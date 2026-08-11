"""Supervisor LangGraph.

The cycle shape is fixed in code:

    poll_jobs -> supervisor_tool_call -> tools or wait -> END

Code always runs poll_jobs first. The LLM receives the already-polled registry
snapshot and chooses one agent tool wrapper. ToolNode executes that wrapper,
which then calls the real Agent.process_job() callback.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from smart_migrate.config.AppSettings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
)
from smart_migrate.integrations.llm.LlmFallback import (
    is_model_fallback_error,
    model_candidates,
    set_active_model,
)
from smart_migrate.supervisor.SupervisorJobPolling import (
    MIGRATION_JOB_BATCH_SIZE,
    SQL_CONVERSION_JOB_BATCH_SIZE,
    SQL_FORMATTING_JOB_BATCH_SIZE,
    SQL_TUNING_JOB_BATCH_SIZE,
    _agent_flags,
    build_poll_jobs_tool,
    priority_gate_jobs,
)
from smart_migrate.supervisor.SupervisorJobRegistry import (
    _stop_event,
    formatting_registry,
    init_callbacks,
    mig_registry,
    sql_registry,
    tuning_registry,
)
from smart_migrate.supervisor.SupervisorState import SupervisorState
from smart_migrate.supervisor.tools.SupervisorCycleTool import request_wait
from smart_migrate.supervisor.tools.SupervisorMigrationTool import run_data_migration
from smart_migrate.supervisor.tools.SupervisorSqlConversionTool import run_sql_conversion
from smart_migrate.supervisor.tools.SupervisorSqlFormattingTool import run_sql_formatting
from smart_migrate.supervisor.tools.SupervisorSqlTuningTool import run_sql_tuning


def _build_llm(model_name: str) -> ChatOpenAI:
    kwargs: dict = {
        "model": model_name,
        "api_key": LLM_API_KEY,
        "max_tokens": LLM_MAX_TOKENS,
    }
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return ChatOpenAI(**kwargs)


def _tool_call_name(call) -> str | None:
    if isinstance(call, dict):
        return call.get("name")
    return getattr(call, "name", None)


def build_supervisor_graph(
    get_migration_jobs,
    get_sql_jobs,
    get_tuning_jobs,
    get_formatting_jobs,
    mig_increment_batch,
    mig_process_job,
    sql_increment_batch,
    sql_process_job,
    tune_process_job,
    format_process_job,
    logger,
):
    def refresh_jobs_after_run() -> None:
        run_mig, run_sql, run_tuning, run_fmt = _agent_flags()
        mig_jobs, sql_jobs, tuning_jobs, formatting_jobs = [], [], [], []

        try:
            if run_mig:
                mig_jobs = get_migration_jobs()
        except Exception as exc:
            logger.error(f"[refresh_jobs] DataMigration query error: {exc}")

        try:
            if run_sql:
                sql_jobs = get_sql_jobs()
            if run_tuning:
                tuning_jobs = get_tuning_jobs()
            if run_fmt:
                formatting_jobs = get_formatting_jobs()
        except Exception as exc:
            logger.error(f"[refresh_jobs] SQL/Tuning/Formatting query error: {exc}")

        raw_mig_total = len(mig_jobs)
        raw_sql_total = len(sql_jobs)
        raw_tuning_total = len(tuning_jobs)
        raw_formatting_total = len(formatting_jobs)

        mig_jobs, sql_jobs, tuning_jobs, formatting_jobs = priority_gate_jobs(
            mig_jobs, sql_jobs, tuning_jobs, formatting_jobs
        )

        mig_registry.clear()
        sql_registry.clear()
        tuning_registry.clear()
        formatting_registry.clear()

        for job in mig_jobs[:MIGRATION_JOB_BATCH_SIZE]:
            mig_registry[job.map_id] = job
        for job in sql_jobs[:SQL_CONVERSION_JOB_BATCH_SIZE]:
            sql_registry[str(job.row_id)] = job
        for job in tuning_jobs[:SQL_TUNING_JOB_BATCH_SIZE]:
            tuning_registry[str(job.row_id)] = job
        for job in formatting_jobs[:SQL_FORMATTING_JOB_BATCH_SIZE]:
            formatting_registry[str(job.row_id)] = job

        logger.info(
            "[refresh_jobs] refreshed after job "
            f"(Mig={len(mig_registry)}/{raw_mig_total}, "
            f"Sql={len(sql_registry)}/{raw_sql_total}, "
            f"Tuning={len(tuning_registry)}/{raw_tuning_total}, "
            f"Formatting={len(formatting_registry)}/{raw_formatting_total})"
        )

    init_callbacks(
        mig_inc=mig_increment_batch,
        mig_proc=mig_process_job,
        sql_inc=sql_increment_batch,
        sql_proc=sql_process_job,
        tune_proc=tune_process_job,
        format_proc=format_process_job,
        refresh_jobs=refresh_jobs_after_run,
        logger=logger,
    )

    poll_jobs = build_poll_jobs_tool(
        get_migration_jobs,
        get_sql_jobs,
        get_tuning_jobs,
        get_formatting_jobs,
    )

    # These are LangChain tool wrappers around actual agents. The LLM chooses
    # one wrapper, then the wrapper calls the registered Agent.process_job()
    # callback. Retry/validation lives inside each agent graph/workflow.
    agent_tools = [
        run_data_migration,
        run_sql_conversion,
        run_sql_tuning,
        run_sql_formatting,
    ]
    tool_node = ToolNode(agent_tools)

    def poll_jobs_node(state: SupervisorState) -> dict:
        if _stop_event.is_set() or state.get("stop_requested"):
            return {"stop_requested": True}

        result = poll_jobs.invoke({})
        logger.info("[SupervisorGraph] poll_jobs completed")
        return {"poll_result": result}

    def supervisor_tool_call_node(state: SupervisorState) -> dict:
        if _stop_event.is_set() or state.get("stop_requested"):
            return {"stop_requested": True}

        poll_result = state.get("poll_result") or "{}"
        payload = {
            "poll_result": poll_result,
            "available_agent_tools": [
                {
                    "name": "run_data_migration",
                    "args": {"map_id": "one key from mig_registry"},
                    "agent": "DB Migration Agent",
                    "when": "Use only when migration jobs exist. This has highest priority.",
                },
                {
                    "name": "run_sql_conversion",
                    "args": {"row_id": "one key from sql_registry"},
                    "agent": "SQL Conversion Agent",
                    "when": "Use only when no migration job exists and SQL conversion jobs exist.",
                },
                {
                    "name": "run_sql_tuning",
                    "args": {"row_ids": ["one key from tuning_registry"]},
                    "agent": "SQL Tuning Agent",
                    "when": "Use only when migration and conversion jobs are empty and tuning jobs exist.",
                },
                {
                    "name": "run_sql_formatting",
                    "args": {"row_ids": ["one key from formatting_registry"]},
                    "agent": "SQL Formatting Agent",
                    "when": "Use only when migration, conversion, and tuning jobs are empty and formatting jobs exist.",
                },
            ],
            "policy": [
                "poll_jobs has already run. Do not request poll_jobs.",
                "Call exactly one agent tool wrapper when a runnable job exists.",
                "Call no tool when all registries are empty.",
                "Never call more than one agent tool wrapper in this cycle.",
                "Respect priority: migration, conversion, tuning, formatting.",
                "Use only IDs that appear in poll_result.",
                "The selected wrapper will call the actual agent. Do not reason about internal retries here.",
            ],
        }
        messages = [
            SystemMessage(
                content=(
                    "You are the SmartMigrate batch supervisor. "
                    "The job registry has already been polled. "
                    "Choose exactly one agent tool call if work exists. "
                    "If no work exists, return a short response with no tool calls."
                )
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]

        candidates = model_candidates(LLM_MODEL)
        last_exc: Exception | None = None
        for idx, candidate_model in enumerate(candidates):
            try:
                response = _build_llm(candidate_model).bind_tools(agent_tools).invoke(messages)
                set_active_model(candidate_model)
                tool_calls = getattr(response, "tool_calls", None) or []
                tool_names = [_tool_call_name(call) for call in tool_calls]
                logger.info(
                    "[SupervisorGraph] supervisor tool choice "
                    f"tools={','.join(name or '-' for name in tool_names) or '-'}"
                )
                return {"messages": [response]}
            except Exception as exc:
                message = str(exc)
                if idx < len(candidates) - 1 and is_model_fallback_error(message):
                    logger.warning(
                        f"[Supervisor LLM] model fallback: {candidate_model} failed ({message}); "
                        f"trying {candidates[idx + 1]}"
                    )
                    last_exc = exc
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("No supervisor LLM model candidates are configured.")

    def route_after_tool_choice(state: SupervisorState) -> str:
        if _stop_event.is_set() or state.get("stop_requested"):
            return END

        messages = state.get("messages") or []
        if not messages:
            return "wait"

        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if not tool_calls:
            return "wait"

        first_tool_name = _tool_call_name(tool_calls[0])
        if len(tool_calls) > 1:
            logger.warning(
                "[SupervisorGraph] LLM requested multiple tools; ToolNode will run them, "
                "but job tools enforce one execution per cycle. "
                f"first={first_tool_name or '-'} count={len(tool_calls)}"
            )
        return "tools"

    def wait_node(state: SupervisorState) -> dict:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        had_tool_result = isinstance(last, ToolMessage) if last else False
        seconds = 1 if had_tool_result else 30
        result = request_wait.invoke({"seconds": seconds})
        logger.info(f"[SupervisorGraph] cycle wait finished: {result}")
        return {"wait_result": result}

    workflow = StateGraph(SupervisorState)
    workflow.add_node("poll_jobs", poll_jobs_node)
    workflow.add_node("supervisor_tool_call", supervisor_tool_call_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("wait", wait_node)

    workflow.set_entry_point("poll_jobs")
    workflow.add_edge("poll_jobs", "supervisor_tool_call")
    workflow.add_conditional_edges(
        "supervisor_tool_call",
        route_after_tool_choice,
        {"tools": "tools", "wait": "wait", END: END},
    )
    workflow.add_edge("tools", "wait")
    workflow.add_edge("wait", END)

    return workflow.compile()
