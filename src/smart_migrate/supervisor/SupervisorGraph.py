"""Supervisor LangGraph — LLM 기반 ReAct 루프.

수퍼바이저 LLM이 poll_jobs → 실행 도구들 → request_wait
순서로 도구를 호출하여 한 사이클을 처리합니다.
사이클 반복은 SupervisorAgent.run()의 외부 while 루프가 담당합니다.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from smart_migrate.supervisor.SupervisorState import SupervisorState
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
from smart_migrate.supervisor.SupervisorJobRegistry import (
    _stop_event,
    formatting_registry,
    init_callbacks,
    mig_registry,
    sql_registry,
    tuning_registry,
)
from smart_migrate.supervisor.tools.SupervisorCycleTool import request_wait
from smart_migrate.supervisor.tools.SupervisorMigrationTool import run_data_migration
from smart_migrate.supervisor.SupervisorJobPolling import (
    MIGRATION_JOB_BATCH_SIZE,
    SQL_CONVERSION_JOB_BATCH_SIZE,
    SQL_FORMATTING_JOB_BATCH_SIZE,
    SQL_TUNING_JOB_BATCH_SIZE,
    _agent_flags,
    build_poll_jobs_tool,
    priority_gate_jobs,
)
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


def _parse_decision(raw_decision: str) -> dict:
    text = str(raw_decision or "").strip()
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {"route": "", "reason": "decision JSON parse failed"}
        try:
            parsed = json.loads(text[start : end + 1])
        except Exception:
            return {"route": "", "reason": "decision JSON object parse failed"}
    if not isinstance(parsed, dict):
        return {"route": "", "reason": "decision is not an object"}
    return {
        "route": str(parsed.get("route") or "").strip(),
        "reason": str(parsed.get("reason") or "").strip(),
    }


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

    def poll_jobs_node(state: SupervisorState) -> dict:
        if _stop_event.is_set() or state.get("stop_requested"):
            return {"stop_requested": True}
        result = poll_jobs.invoke({})
        logger.info("[SupervisorGraph] poll_jobs completed")
        return {"poll_result": result}

    def supervisor_decide_node(state: SupervisorState) -> dict:
        if _stop_event.is_set() or state.get("stop_requested"):
            return {"stop_requested": True}
        payload = {
            "poll_result": state.get("poll_result") or "{}",
            "available_routes": [
                "run_data_migration",
                "run_sql_conversion",
                "run_sql_tuning",
                "run_sql_formatting",
                "no_job",
            ],
            "policy": [
                "Choose exactly one route for this cycle.",
                "DB migration has priority, then SQL conversion, SQL tuning, SQL formatting.",
                "Choose no_job only when all job lists are empty.",
                "Return JSON only with route and reason.",
            ],
            "required_json_schema": {
                "route": "run_data_migration | run_sql_conversion | run_sql_tuning | run_sql_formatting | no_job",
                "reason": "short reason",
            },
        }
        messages = [
            SystemMessage(
                content=(
                    "You are the SmartMigrate batch supervisor. "
                    "Decide the single route for this already-polled cycle. "
                    "Return JSON only. Do not call tools."
                )
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
        candidates = model_candidates(LLM_MODEL)
        last_exc: Exception | None = None

        for idx, candidate_model in enumerate(candidates):
            try:
                response = _build_llm(candidate_model).invoke(messages)
                set_active_model(candidate_model)
                raw_decision = str(getattr(response, "content", "") or "")
                decision = _parse_decision(raw_decision)
                logger.info(
                    "[SupervisorGraph] decision "
                    f"route={decision.get('route') or '-'} "
                    f"reason={decision.get('reason') or '-'}"
                )
                return {"decision": decision}
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

    def route_after_decision(state: SupervisorState) -> str:
        if _stop_event.is_set() or state.get("stop_requested"):
            return END
        requested = str((state.get("decision") or {}).get("route") or "").strip()
        if requested == "run_data_migration" and mig_registry:
            return "run_action"
        if requested == "run_sql_conversion" and not mig_registry and sql_registry:
            return "run_action"
        if requested == "run_sql_tuning" and not mig_registry and not sql_registry and tuning_registry:
            return "run_action"
        if requested == "run_sql_formatting" and not mig_registry and not sql_registry and not tuning_registry and formatting_registry:
            return "run_action"
        if requested == "no_job" and not any((mig_registry, sql_registry, tuning_registry, formatting_registry)):
            return "wait"

        corrected = _select_route_from_registries()
        if corrected != requested:
            logger.warning(
                "[SupervisorGraph] corrected invalid decision route "
                f"requested={requested or '-'} corrected={corrected}"
            )
        if corrected == "no_job":
            return "wait"
        return "run_action"

    def _select_route_from_registries() -> str:
        if mig_registry:
            return "run_data_migration"
        if sql_registry:
            return "run_sql_conversion"
        if tuning_registry:
            return "run_sql_tuning"
        if formatting_registry:
            return "run_sql_formatting"
        return "no_job"

    def run_action_node(state: SupervisorState) -> dict:
        route = _select_route_from_registries()
        if route == "run_data_migration":
            map_id = next(iter(mig_registry.keys()))
            logger.info(f"[SupervisorGraph] run_data_migration map_id={map_id}")
            return {"action_result": run_data_migration.invoke({"map_id": map_id})}
        if route == "run_sql_conversion":
            row_id = next(iter(sql_registry.keys()))
            logger.info(f"[SupervisorGraph] run_sql_conversion row_id={row_id}")
            return {"action_result": run_sql_conversion.invoke({"row_id": row_id})}
        if route == "run_sql_tuning":
            row_id = next(iter(tuning_registry.keys()))
            logger.info(f"[SupervisorGraph] run_sql_tuning row_id={row_id}")
            return {"action_result": run_sql_tuning.invoke({"row_ids": [row_id]})}
        if route == "run_sql_formatting":
            row_id = next(iter(formatting_registry.keys()))
            logger.info(f"[SupervisorGraph] run_sql_formatting row_id={row_id}")
            return {"action_result": run_sql_formatting.invoke({"row_ids": [row_id]})}
        return {"action_result": "No job selected."}

    def wait_node(state: SupervisorState) -> dict:
        seconds = 1 if state.get("action_result") else 30
        result = request_wait.invoke({"seconds": seconds})
        logger.info(f"[SupervisorGraph] cycle wait finished: {result}")
        return {"wait_result": result}

    workflow = StateGraph(SupervisorState)
    workflow.add_node("poll_jobs", poll_jobs_node)
    workflow.add_node("supervisor_decide", supervisor_decide_node)
    workflow.add_node("run_action", run_action_node)
    workflow.add_node("wait", wait_node)

    workflow.set_entry_point("poll_jobs")
    workflow.add_edge("poll_jobs", "supervisor_decide")
    workflow.add_conditional_edges(
        "supervisor_decide",
        route_after_decision,
        {"run_action": "run_action", "wait": "wait", END: END},
    )
    workflow.add_edge("run_action", "wait")
    workflow.add_edge("wait", END)

    return workflow.compile()
