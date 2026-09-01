from __future__ import annotations

import logging
import json
import os
import re
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType10CMigOneJobPocExecutor(Component):

    display_name = "10C MIG One Job Executor"
    description = "Runs one DB Migration job with real DB status/log updates and internal retry."
    name = "NewType10CMigOneJobPocExecutor"
    icon = "DatabaseZap"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        StrInput(name="source_schema", display_name="Source Schema", required=False),
        StrInput(name="target_schema", display_name="Target Schema", required=False),
        StrInput(name="llm_base_url", display_name="LLM Base URL", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_provider", display_name="LLM Provider", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="GLM-5.1", required=False),
        StrInput(name="llm_fallback_models", display_name="LLM Fallback Models", value="GLM-5.1,Qwen3.6-35B-A3B,Kimi-K2.5", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=4096, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=900, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "START", "before run_job", 0]})
        try:
            """Run one migration job and return the final job result payload."""
            started = time.perf_counter()
            job = self._parse_payload(getattr(self, "job_item", ""))
            if not self._should_run_migration(job):
                result = self._pass_through(job, started, "10C skipped because job_name is not migration.")
                self.status = result
                __log_result = Data(data=result)
                logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", "after run_job", 0]})
                return __log_result
            map_id = self._to_int(job.get("map_id"))
            if map_id is None:
                raise ValueError("MIG job item requires map_id")
            max_retry = max(0, int(job.get("max_retry") if job.get("max_retry") is not None else (getattr(self, "max_retry", None) or 2)))
            db_config = self._db_config(job)
            attempts: list[dict[str, Any]] = []

            try:
                dep_status = self._dependency_status(db_config, map_id, job.get("prior_map_id"))
                if dep_status != "READY":
                    elapsed = int(time.perf_counter() - started)
                    if self._is_dependency_failure_status(dep_status):
                        status = "SKIP-PRIOR-FAIL"
                        self._update_job(db_config, map_id, status, elapsed, 0)
                        self._insert_mig_log(
                            db_config,
                            map_id,
                            "JOB_SKIP",
                            "WARN",
                            "DEP_CHECK",
                            status,
                            f"prior_map_id={job.get('prior_map_id')} status={dep_status}",
                            0,
                            "",
                        )
                        result = self._result(job, ok=False, status=status, elapsed=elapsed, attempts=attempts)
                        result.update({"skipped": True, "db_status_updated": True})
                    else:
                        result = self._result(job, ok=False, status="NOT_RUNNABLE", elapsed=elapsed, attempts=attempts)
                        result.update({"not_runnable": True, "db_status_updated": False})
                    result.update(
                        {
                            "error_type": "DEPENDENCY_NOT_READY",
                            "message": f"prior_map_id={job.get('prior_map_id')} status={dep_status}",
                        }
                    )
                    self.status = result
                    __log_result = Data(data=result)
                    logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", "after run_job", 0]})
                    return __log_result

                self._mark_running(db_config, map_id)
                base_context = {"job": job, "map_id": map_id, "attempt": 1, "llm_config": self._llm_config(job)}
                fetch_step = self._node_fetch_ddl(base_context)
                if fetch_step.get("status") != "PASS":
                    raise ValueError(fetch_step.get("message") or "FETCH_DDL failed")
                self._insert_mig_log(
                    db_config,
                    map_id,
                    "GENERATE_SQL",
                    "INFO",
                    "FETCH_DDL",
                    "PASS",
                    fetch_step.get("message") or "FETCH_DDL completed",
                    0,
                    "",
                )
                pipeline_context = {**base_context, **(fetch_step.get("outputs") or {})}
                graph_result = self._run_migration_graph(pipeline_context, db_config, max_retry)
                attempts = list(graph_result.get("attempts") or [])
                final_status = str(graph_result.get("final_status") or "FAIL-TEST")
                final_ok = final_status == "PASS"
                retry_count = max(0, int(graph_result.get("db_attempts") or 1) - 1)
                message = str(graph_result.get("message") or "")

                elapsed = int(time.perf_counter() - started)
                if final_ok:
                    self._update_job(db_config, map_id, "PASS", elapsed, retry_count)
                    self._insert_mig_log(
                        db_config,
                        map_id,
                        "INFO",
                        "INFO",
                        "VERIFY",
                        "PASS",
                        message,
                        retry_count,
                        graph_result.get("verification_sql", ""),
                    )
                else:
                    self._update_job(db_config, map_id, final_status, elapsed, retry_count)
                    self._insert_mig_log(
                        db_config,
                        map_id,
                        "JOB_FAIL",
                        "ERROR",
                        "FINAL",
                        final_status,
                        message,
                        retry_count,
                        graph_result.get("stage_sql", ""),
                    )

                elapsed = int(time.perf_counter() - started)
                result = self._result(job, ok=final_ok, status="PASS" if final_ok else final_status, elapsed=elapsed, attempts=attempts)
                result.update(
                    {
                        "retry_count": retry_count,
                        "message": message,
                        "migration_sql": graph_result.get("current_migration_sql", ""),
                        "verification_sql": graph_result.get("current_v_sql", ""),
                        "llm_model": graph_result.get("llm_model", ""),
                        "generated_sql_saved": bool(graph_result.get("generated_sql_saved")),
                        "next_node": "12C_sqlConversionOneJobPocExecutor" if job.get("full_workflow") else "10D_migIterationDashboard",
                    }
                )
                self.status = result
                __log_result = Data(data=result)
                logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", "after run_job", 0]})
                return __log_result
            except Exception as exc:
                elapsed = int(time.perf_counter() - started)
                try:
                    self._update_job(db_config, map_id, "FAIL-INSERT", elapsed, max(0, len(attempts) - 1))
                    self._insert_mig_log(db_config, map_id, "JOB_FAIL", "ERROR", "FINAL", "FAIL-INSERT", f"System error: {exc}", max(0, len(attempts) - 1), "")
                except Exception:
                    pass
                result = self._result(job, ok=False, status="FAIL-INSERT", elapsed=elapsed, attempts=attempts)
                result.update({"error_type": "SYSTEM_ERROR", "error": str(exc), "message": f"migration executor error: {exc}"})
                self.status = result
                __log_result = Data(data=result)
                logging.getLogger("smartmigrate.workflow").error("error run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "ERROR", "RUN_JOB", "ERROR", "error run_job", 0]})
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", "after run_job", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error run_job: {exc}", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "ERROR", "RUN_JOB", "ERROR", f"error run_job: {exc}", 0]})
            raise

    def _should_run_migration(self, job: dict[str, Any]) -> bool:
        return self._job_name(job) == "migration"

    def _job_name(self, payload: dict[str, Any]) -> str:
        value = str(payload.get("job_name") or "").strip().lower()
        if value:
            return value
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()
        return {
            "MIG": "migration",
            "SQL_CONVERSION": "conversion",
            "SQL_TUNING": "tuning",
            "SQL_FORMATTING": "formatting",
        }.get(route, "")

    def _pass_through(self, job: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = int(time.perf_counter() - started)
        total = int(job.get("total_jobs") or 1)
        index = int(job.get("job_index") or 1)
        result = {
            **job,
            "component": "10C_migOneJobExecutor",
            "ok": bool(job.get("ok", True)),
            "status": job.get("status") or "PASS-THROUGH",
            "elapsed_seconds": elapsed,
            "attempts": list(job.get("attempts") or []),
            "attempt_count": int(job.get("attempt_count") or 0),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
            "component_pass_through": True,
            "pass_through_component": "10C",
            "message": job.get("message") or message,
            "next_node": "12C_sqlConversionOneJobPocExecutor",
        }
        history = list(result.get("history") or [])
        history.append({"step": "10C_pass_through", "message": message})
        result["history"] = history
        return result

    def _run_migration_graph(self, context: dict[str, Any], db_config: dict[str, Any], max_retry: int) -> dict[str, Any]:
        """Build and execute the internal retry graph for one migration job."""
        from langgraph.graph import END, StateGraph

        def generate_node(state: dict[str, Any]) -> dict[str, Any]:
            """Run the migration SQL generation node."""
            step = self._node_generate_sql(state)
            return self._apply_step(state, step)

        def execute_node(state: dict[str, Any]) -> dict[str, Any]:
            """Run truncate and migration SQL execution for the current attempt."""
            step = self._node_execute_sql(state)
            next_state = self._apply_step(state, step)
            if step.get("status") == "PASS":
                next_state.update({"status": "EXECUTED", "error_type": "", "failure_status": ""})
            return next_state

        def verify_node(state: dict[str, Any]) -> dict[str, Any]:
            """Run verification SQL for the current attempt."""
            step = self._node_verify(state)
            return self._apply_step(state, step)

        def retry_prepare_node(state: dict[str, Any]) -> dict[str, Any]:
            """Persist the failed attempt and prepare the next retry state."""
            attempt = self._attempt_result_from_state(state, ok=False)
            retry_count = int(state.get("db_attempts") or 1)
            self._update_job(db_config, int(state["map_id"]), attempt["status"], 0, retry_count)
            self._insert_mig_log(
                db_config,
                int(state["map_id"]),
                "ROW_ERROR",
                "WARN",
                attempt["failed_stage"],
                attempt["status"],
                attempt["message"],
                retry_count,
                attempt.get("migration_sql", ""),
            )
            next_status = "EXECUTED" if attempt["status"] == "FAIL-TEST" else ""
            return {
                **state,
                "attempts": [*(state.get("attempts") or []), attempt],
                "current_steps": [],
                "db_attempts": retry_count + 1,
                "attempt": retry_count + 1,
                "error_type": "",
                "status": next_status,
                "failure_status": attempt["status"],
            }

        def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
            """Build the graph terminal state after success or retry exhaustion."""
            attempts = list(state.get("attempts") or [])
            if state.get("current_steps"):
                attempts.append(self._attempt_result_from_state(state, ok=state.get("status") == "PASS"))
            final_status = "PASS" if state.get("status") == "PASS" else self._failure_status_from_state(state)
            return {
                **state,
                "attempts": attempts,
                "final_status": final_status,
                "message": "Migration Success" if final_status == "PASS" else f"Max Attempts Reached after {final_status}: {state.get('last_error') or ''}",
                "stage_sql": self._stage_sql_from_state(state, final_status),
            }

        def should_continue(state: dict[str, Any]) -> str:
            """Route the graph based on the current status and retry budget."""
            if state.get("status") == "PASS":
                return "finalize"
            if state.get("error_type") == "BIZ_RETRY":
                return "retry_prepare" if int(state.get("db_attempts") or 1) < int(state.get("max_attempts") or 1) else "finalize"
            if state.get("status") == "EXECUTED":
                return "verify"
            if not state.get("current_migration_sql"):
                return "generate"
            return "execute"

        def after_retry_prepare(state: dict[str, Any]) -> str:
            """Choose the next node after a retry is scheduled."""
            return "execute" if state.get("failure_status") == "FAIL-TRUNCATE" else "generate"

        workflow = StateGraph(dict)
        workflow.add_node("generate", generate_node)
        workflow.add_node("execute", execute_node)
        workflow.add_node("verify", verify_node)
        workflow.add_node("retry_prepare", retry_prepare_node)
        workflow.add_node("finalize", finalize_node)
        workflow.set_entry_point("generate")
        workflow.add_conditional_edges("generate", should_continue, {"execute": "execute", "verify": "verify", "retry_prepare": "retry_prepare", "finalize": "finalize", "generate": "generate"})
        workflow.add_conditional_edges("execute", should_continue, {"verify": "verify", "retry_prepare": "retry_prepare", "finalize": "finalize", "generate": "generate", "execute": "execute"})
        workflow.add_conditional_edges("verify", should_continue, {"finalize": "finalize", "retry_prepare": "retry_prepare", "generate": "generate"})
        workflow.add_conditional_edges("retry_prepare", after_retry_prepare, {"generate": "generate", "execute": "execute"})
        workflow.add_edge("finalize", END)
        graph = workflow.compile()
        initial_state = {
            **context,
            "db_attempts": 1,
            "db_config": db_config,
            # max_retry means retries after the first attempt, so total attempts are max_retry + 1.
            "max_attempts": max_retry + 1,
            "current_steps": [],
            "attempts": [],
            "current_migration_sql": context.get("migration_sql", ""),
            "current_v_sql": context.get("verification_sql", ""),
            "last_error": "",
            "last_sql": "",
            "error_type": "",
            "failure_status": "",
            "status": "",
        }
        return dict(graph.invoke(initial_state))

    def _apply_step(self, state: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
        """Merge one node result into graph state and persist its step log."""
        outputs = dict(step.get("outputs") or {})
        next_state = {
            **state,
            **outputs,
            "current_steps": [*(state.get("current_steps") or []), step],
            "attempt": state.get("db_attempts", state.get("attempt", 1)),
        }
        if step.get("status") == "PASS":
            next_state.update(
                {
                    "error_type": "",
                    "failure_status": "",
                    "last_error": "",
                    "last_sql": outputs.get("migration_sql") or state.get("current_migration_sql") or state.get("last_sql") or "",
                    "current_migration_sql": outputs.get("migration_sql") or state.get("current_migration_sql") or "",
                    "current_v_sql": outputs.get("verification_sql") or state.get("current_v_sql") or "",
                }
            )
            if step.get("stage") == "GENERATE_SQL":
                self._save_generated_sql(
                    self._db_config(state.get("job") or {}),
                    int(state["map_id"]),
                    next_state.get("current_migration_sql", ""),
                    next_state.get("current_v_sql", ""),
                )
                next_state["generated_sql_saved"] = True
            if step.get("stage") == "VERIFY":
                next_state["status"] = "PASS"
            elif step.get("stage") == "GENERATE_SQL" and state.get("failure_status") == "FAIL-TEST":
                next_state["status"] = "EXECUTED"
            self._insert_step_log(next_state, step)
            return next_state

        status = str(step.get("status") or "FAIL-INSERT")
        next_state.update(
            {
                "status": "",
                "error_type": "BIZ_RETRY",
                "failure_status": status,
                "last_error": str(step.get("message") or ""),
                "last_sql": self._step_sql_for_status(next_state, status),
                "current_migration_sql": outputs.get("migration_sql") or state.get("current_migration_sql") or "",
                "current_v_sql": outputs.get("verification_sql") or state.get("current_v_sql") or "",
            }
        )
        self._insert_step_log(next_state, step)
        return next_state

    def _attempt_result_from_state(self, state: dict[str, Any], *, ok: bool) -> dict[str, Any]:
        """Create the public attempt record from the current graph state."""
        steps = list(state.get("current_steps") or [])
        failed_step = next((step for step in reversed(steps) if step.get("status") != "PASS"), None)
        status = "PASS" if ok else self._failure_status_from_state(state)
        failed_stage = "" if ok else str((failed_step or {}).get("stage") or "FINAL")
        message = (
            f"[MIG] map_id={state.get('map_id')} attempt={state.get('db_attempts')} migration pipeline passed"
            if ok
            else str(state.get("last_error") or (failed_step or {}).get("message") or "")
        )
        return {
            "attempt": int(state.get("db_attempts") or 1),
            "ok": ok,
            "failed_stage": failed_stage,
            "failed_stage_status": "" if ok else status,
            "status": status,
            "message": message,
            "migration_sql": state.get("current_migration_sql", ""),
            "verification_sql": state.get("current_v_sql", ""),
            "outputs": self._attempt_outputs(state),
            "steps": steps,
        }

    def _failure_status_from_state(self, state: dict[str, Any]) -> str:
        """Resolve the final business failure status for a graph state."""
        explicit = str(state.get("failure_status") or "").strip()
        if explicit:
            return explicit
        if state.get("status") == "EXECUTED":
            return "FAIL-TEST"
        steps = list(state.get("current_steps") or [])
        failed_step = next((step for step in reversed(steps) if step.get("status") != "PASS"), None)
        return str((failed_step or {}).get("status") or "FAIL-INSERT")

    def _stage_sql_from_state(self, state: dict[str, Any], failure_status: str | None = None) -> str:
        """Return the SQL text most relevant to the given failure status."""
        status = failure_status or self._failure_status_from_state(state)
        if status == "FAIL-TEST":
            return str(state.get("current_v_sql") or "")
        return str(state.get("current_migration_sql") or state.get("last_sql") or "")

    def _step_sql_for_status(self, state: dict[str, Any], status: str) -> str:
        """Return the SQL text that should be logged for one failed step."""
        if status == "FAIL-TEST":
            return str(state.get("current_v_sql") or "")
        return str(state.get("current_migration_sql") or state.get("migration_sql") or "")

    def _insert_step_log(self, state: dict[str, Any], step: dict[str, Any]) -> None:
        """Insert a migration step log for the current graph node result."""
        db_config = dict(state.get("db_config") or {})
        map_id = self._to_int(state.get("map_id"))
        if map_id is None:
            return
        status = str(step.get("status") or "")
        stage = str(step.get("stage") or "UNKNOWN")
        retry_count = max(0, int(state.get("db_attempts") or 1) - 1)
        log_level = "INFO" if status == "PASS" else "WARN"
        stage_sql = self._log_sql_for_step(state, step)
        route_note = self._route_note(state, step)
        message = f"attempt={state.get('db_attempts')} stage={stage} status={status}; {step.get('message') or ''}{route_note}"
        log_type = "ROW_ERROR" if status.startswith("FAIL-") else ("VERIFY_SQL" if stage == "VERIFY" else "GENERATE_SQL")
        self._insert_mig_log(db_config, map_id, log_type, log_level, stage, status, message, retry_count, stage_sql)

    def _log_sql_for_step(self, state: dict[str, Any], step: dict[str, Any]) -> str:
        """Choose the SQL snippet that matches the step being logged."""
        stage = str(step.get("stage") or "")
        if stage == "VERIFY":
            return str(state.get("current_v_sql") or "")
        if stage == "GENERATE_SQL" and state.get("status") == "EXECUTED":
            return str(state.get("current_v_sql") or "")
        return self._stage_sql_from_state(state, str(step.get("status") or ""))

    def _route_note(self, state: dict[str, Any], step: dict[str, Any]) -> str:
        """Describe the graph route selected after a step result."""
        if step.get("stage") == "GENERATE_SQL" and state.get("status") == "EXECUTED":
            return "; route=verify_retry_generate_only,next=VERIFY"
        if step.get("stage") == "GENERATE_SQL" and step.get("status") == "PASS":
            return "; route=normal,next=EXECUTE_SQL"
        if step.get("stage") == "EXECUTE_SQL" and step.get("status") == "PASS":
            return "; route=normal,next=VERIFY"
        if step.get("status") == "FAIL-TEST":
            return "; route=retry,next=GENERATE_SQL_VERIFY_ONLY"
        if step.get("status") == "FAIL-TRUNCATE":
            return "; route=retry,next=EXECUTE_SQL"
        if str(step.get("status") or "").startswith("FAIL-"):
            return "; route=retry,next=GENERATE_SQL"
        if step.get("status") == "PASS":
            return "; route=finalize"
        return ""

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Future development area: replace these nodes as the migration workflow evolves.
    # - _node_generate_sql: connect to the LLM and generate MIG_SQL / VERIFY_SQL.
    # - _node_execute_sql: run TRUNCATE and INSERT against the target database.
    # - _node_verify: run verification SQL and classify validation failures.
    # Keep the returned business statuses limited to:
    # PASS, FAIL-TRUNCATE, FAIL-INSERT, FAIL-TEST.
    # ------------------------------------------------------------------
    # Keep the returned business statuses limited to:
    # PASS, FAIL-TRUNCATE, FAIL-INSERT, FAIL-TEST.
    # ------------------------------------------------------------------

    def _node_fetch_ddl(self, context: dict[str, Any]) -> dict[str, Any]:
        """Load mapping metadata and source/target DDL for SQL generation."""
        map_id = self._to_int(context.get("map_id"))
        db_config = self._db_config(context["job"])
        metadata = self._load_mig_metadata(db_config, map_id)
        return {
            "stage": "FETCH_DDL",
            "status": "PASS",
            "message": (
                "[REAL] migration mapping and DDL metadata loaded; "
                f"system_schema={db_config.get('system_schema') or ''}, "
                f"source_schema={db_config.get('source_schema') or ''}, "
                f"target_schema={db_config.get('target_schema') or ''}"
            ),
            "outputs": {
                **metadata,
            },
        }

    def _node_generate_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate migration and verification SQL with the configured LLM."""
        job = context["job"]
        try:
            user_edited = str(context.get("user_edited") or "").strip().upper() == "Y"
            saved_migration_sql = str(context.get("saved_migration_sql") or "").strip()
            saved_verification_sql = str(context.get("saved_verification_sql") or "").strip()
            if user_edited and saved_migration_sql:
                if not saved_verification_sql:
                    _, _, generated_verification_sql, used_model = self._generate_migration_sqls(
                        {**context, "current_migration_sql": saved_migration_sql, "migration_sql": saved_migration_sql},
                        verify_only=True,
                    )
                    saved_verification_sql = generated_verification_sql
                    message = f"[REAL] user-edited MIG_SQL reused; VERIFY_SQL generated by LLM model={used_model}"
                else:
                    message = "[REAL] user-edited MIG_SQL/VERIFY_SQL reused"
                return {
                    "stage": "GENERATE_SQL",
                    "status": "PASS",
                    "message": message,
                    "outputs": {"migration_sql": saved_migration_sql, "verification_sql": saved_verification_sql},
                }

            verify_only = context.get("failure_status") == "FAIL-TEST"
            ddl_sql, migration_sql, verification_sql, used_model = self._generate_migration_sqls(context, verify_only=verify_only)
            if verify_only:
                migration_sql = str(context.get("current_migration_sql") or context.get("migration_sql") or "").strip()
            migration_sql = self._clean_sql_statement(migration_sql)
            verification_sql = self._clean_sql_statement(verification_sql)
            if not migration_sql:
                raise ValueError("LLM response did not include migration_sql")
            if not verification_sql:
                raise ValueError("LLM response did not include verification_sql")
            return {
                "stage": "GENERATE_SQL",
                "status": "PASS",
                "message": f"[REAL] SQL generated by LLM model={used_model}",
                "outputs": {
                    "ddl_sql": ddl_sql,
                    "migration_sql": migration_sql,
                    "verification_sql": verification_sql,
                    "llm_model": used_model,
                    "generated_sql_saved": False,
                },
            }
        except Exception as exc:
            return {
                "stage": "GENERATE_SQL",
                "status": "FAIL-INSERT",
                "message": f"[REAL] migration SQL generation failed: {exc}",
                "outputs": {
                    "migration_sql": context.get("current_migration_sql") or context.get("migration_sql", ""),
                    "verification_sql": context.get("current_v_sql") or context.get("verification_sql", ""),
                },
            }

    def _node_execute_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run target truncate and migration SQL against Oracle."""
        db_config = dict(context.get("db_config") or {})
        target_table = str(context.get("to_table") or "").strip()
        try:
            if str(context.get("trunc_yn") or "").strip().upper() == "Y":
                self._truncate_table(db_config, target_table)
            affected_rows = self._execute_sql_script(db_config, str(context.get("current_migration_sql") or context.get("migration_sql") or ""))
            message = "[REAL] migration SQL executed"
            if affected_rows == 0:
                message = "[REAL] migration SQL executed; affected_rows=0 treated as PASS"
            return {
                "stage": "EXECUTE_SQL",
                "status": "PASS",
                "message": message,
                "outputs": {"affected_rows": affected_rows},
            }
        except Exception as exc:
            status = "FAIL-TRUNCATE" if "TRUNCATE" in str(exc).upper() else "FAIL-INSERT"
            stage = "TRUNCATE" if status == "FAIL-TRUNCATE" else "EXECUTE_SQL"
            return {
                "stage": stage,
                "status": status,
                "message": str(exc),
                "outputs": {"affected_rows": 0},
            }

    def _node_verify(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run verification SQL and require every returned value to be zero."""
        db_config = dict(context.get("db_config") or {})
        try:
            ok, message, rows = self._execute_verification(db_config, str(context.get("current_v_sql") or context.get("verification_sql") or ""))
            if not ok:
                return {
                    "stage": "VERIFY",
                    "status": "FAIL-TEST",
                    "message": message,
                    "outputs": {"verification_rows": rows},
                }
            return {
                "stage": "VERIFY",
                "status": "PASS",
                "message": message,
                "outputs": {"verification_rows": rows, "diff_count": 0},
            }
        except Exception as exc:
            return {
                "stage": "VERIFY",
                "status": "FAIL-TEST",
                "message": str(exc),
                "outputs": {"diff_count": -1},
            }

    def _build_deterministic_migration_sql(self, context: dict[str, Any], source_table: str, target_table: str, map_id: Any) -> str:
        """Build a simple deterministic INSERT SQL."""
        mapped_columns = self._mapped_columns(context.get("mapping_details") or [])
        if mapped_columns:
            to_cols = ", ".join(item["to_col"] for item in mapped_columns)
            fr_cols = ", ".join(item["fr_col"] for item in mapped_columns)
            return f"INSERT INTO {target_table} ({to_cols}) SELECT {fr_cols} FROM {source_table} /* map_id={map_id} */"
        return f"INSERT INTO {target_table} SELECT * FROM {source_table} /* map_id={map_id} */"

    def _generate_migration_sqls(self, context: dict[str, Any], *, verify_only: bool) -> tuple[str, str, str, str]:
        prompt_template = self._load_migration_prompt_template()
        from_table = self._source_table_prompt_value(context)
        to_table = self._qualify_to_table(str(context.get("to_table") or "").strip(), dict(context.get("db_config") or {}))
        mapping_info = self._mapping_info(context.get("mapping_details") or [])
        ddl_info_block = self._ddl_info_block(context, from_table, to_table)
        is_append = not self._is_first_target_run(context)
        verification_key = "verification_append" if is_append else "verification_regular"
        verification_instruction = prompt_template[verification_key].format(from_table=from_table, to_table=to_table)
        prompt = prompt_template["main_prompt"].format(
            from_table=from_table,
            to_table=to_table,
            mapping_info=mapping_info,
            ddl_info_block=ddl_info_block,
            verification_instruction=verification_instruction,
            condition=str(context.get("condition") or ""),
        )
        last_error = str(context.get("last_error") or "").strip()
        last_sql = str(context.get("last_sql") or "").strip()
        if verify_only:
            prompt += (
                "\n\n[Verification retry mode]\n"
                "- Keep the existing migration_sql unchanged.\n"
                "- Regenerate verification_sql so it correctly validates the migration result.\n"
                f"- Existing migration_sql:\n{str(context.get('current_migration_sql') or context.get('migration_sql') or '')}\n"
            )
        if last_error:
            prompt += prompt_template["error_suffix"].format(last_sql=last_sql, last_error=last_error)
            if "ORA-00001" in last_error:
                prompt += prompt_template["dup_key_suffix"].format(to_table=to_table, from_table=from_table)
        if is_append:
            prompt += prompt_template["append_mode_suffix"].format(to_table=to_table)
        prompt += "\n\n[Output constraint]\n- Do not end migration_sql or verification_sql with a semicolon (;).\n"

        content, used_model = self._call_llm_json(
            system_anthropic=str(prompt_template.get("system_anthropic") or prompt_template.get("system_openai") or ""),
            system_openai=str(prompt_template.get("system_openai") or ""),
            prompt=prompt,
            config=dict(context.get("llm_config") or {}),
        )
        result = self._extract_json_object(content)
        return (
            self._merge_sql_value(result.get("ddl_sql", "")),
            self._merge_sql_value(result.get("migration_sql", "")),
            self._merge_sql_value(result.get("verification_sql", "")),
            used_model,
        )

    def _load_migration_prompt_template(self) -> dict[str, Any]:
        return dict(MIGRATION_PROMPT_TEMPLATE)

    def _source_table_prompt_value(self, context: dict[str, Any]) -> str:
        raw_from = str(context.get("fr_table") or "").strip()
        map_type = str(context.get("map_type") or "").strip().upper()
        if map_type != "COMPLEX":
            return self._qualify_fr_table(raw_from, dict(context.get("db_config") or {}))
        stripped = raw_from.rstrip(";").strip()
        if not stripped or stripped.startswith("("):
            return self._qualify_source_tables_in_sql(stripped, dict(context.get("db_config") or {}))
        if re.match(r"^(SELECT|WITH)\b", stripped, flags=re.I):
            return f"({self._qualify_source_tables_in_sql(stripped, dict(context.get('db_config') or {}))})"
        return self._qualify_source_tables_in_sql(stripped, dict(context.get("db_config") or {}))

    def _mapping_info(self, details: list[dict[str, Any]]) -> str:
        lines = []
        for item in details:
            fr_col = str(item.get("fr_col") or "").strip()
            to_col = str(item.get("to_col") or "").strip()
            if fr_col and to_col:
                lines.append(f"  - {fr_col} -> {to_col}")
        return "\n".join(lines) if lines else "  (no column mappings found)"

    def _ddl_info_block(self, context: dict[str, Any], from_table: str, to_table: str) -> str:
        parts: list[str] = []
        source_ddl = context.get("source_ddl") or []
        if isinstance(source_ddl, dict):
            table_blocks = []
            for table_name, rows in source_ddl.items():
                table_blocks.append(
                    f"Table: {table_name}\n"
                    f"{'COLUMN':<30} {'DATA_TYPE':<25} NULLABLE\n"
                    f"{'-' * 70}\n"
                    f"{self._format_ddl_rows(list(rows or []))}"
                )
            if table_blocks:
                parts.append("[Source table DDL]\n" + "\n\n".join(table_blocks))
        else:
            source_rows = list(source_ddl or [])
            if source_rows:
                parts.append(
                    "[Source table DDL]\n"
                    f"Table: {from_table}\n"
                    f"{'COLUMN':<30} {'DATA_TYPE':<25} NULLABLE\n"
                    f"{'-' * 70}\n"
                    f"{self._format_ddl_rows(source_rows)}"
                )
        target_ddl = list(context.get("target_ddl") or [])
        if target_ddl:
            parts.append(
                "[Target table DDL]\n"
                f"Table: {to_table}\n"
                f"{'COLUMN':<30} {'DATA_TYPE':<25} NULLABLE\n"
                f"{'-' * 70}\n"
                f"{self._format_ddl_rows(target_ddl)}"
            )
        return "\n\n".join(parts)

    def _format_ddl_rows(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "  (no DDL rows found)"
        lines = []
        for row in rows:
            data_type = str(row.get("data_type") or "")
            precision = row.get("data_precision")
            scale = row.get("data_scale")
            length = row.get("data_length")
            if data_type == "NUMBER" and precision is not None:
                type_text = f"NUMBER({precision},{scale})" if scale not in (None, 0) else f"NUMBER({precision})"
            elif data_type in {"VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"} and length:
                type_text = f"{data_type}({length})"
            else:
                type_text = data_type
            lines.append(f"{str(row.get('column_name') or ''):<30} {type_text:<25} {str(row.get('nullable') or '')}")
        return "\n".join(lines)

    def _is_first_target_run(self, context: dict[str, Any]) -> bool:
        db_config = dict(context.get("db_config") or {})
        map_id = self._to_int(context.get("map_id"))
        to_table = str(context.get("raw_to_table") or context.get("to_table") or "").strip()
        if map_id is None or not to_table:
            return True
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        column_types = self._table_column_types(db_config, table)
        to_table_expr = "DBMS_LOB.SUBSTR(TO_TABLE, 4000, 1)" if column_types.get("TO_TABLE") in {"CLOB", "NCLOB"} else "TO_CHAR(TO_TABLE)"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COUNT(*)
                  FROM {table}
                 WHERE {to_table_expr} = :1
                   AND MAP_ID <> :2
                   AND UPPER(TRIM(NVL(STATUS, ''))) = 'PASS'
                """,
                [to_table, map_id],
            )
            row = cur.fetchone()
        return int(row[0] if row else 0) == 0

    def _call_llm_json(self, *, system_anthropic: str, system_openai: str, prompt: str, config: dict[str, Any] | None = None) -> tuple[str, str]:
        self._load_env_files()
        llm_config = dict(config or {})
        api_key = str(llm_config.get("llm_api_key") or os.getenv("LLM_API_KEY") or os.getenv("OPEN_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("LLM API key is required for DB Migration SQL generation")
        base_url = str(llm_config.get("llm_base_url") or os.getenv("LLM_BASE_URL") or "").strip() or None
        model = str(llm_config.get("llm_model") or os.getenv("LLM_MODEL") or "GLM-5.1").strip()
        max_tokens = self._positive_int(llm_config.get("llm_max_tokens") or os.getenv("LLM_MAX_TOKENS"), 4096)
        timeout_seconds = self._positive_int(llm_config.get("llm_timeout_seconds") or os.getenv("LLM_TIMEOUT_SECONDS"), 900)
        provider = self._resolve_llm_provider(llm_config, base_url, model)
        candidates = self._model_candidates(model, llm_config)
        last_error: Exception | None = None

        for idx, candidate_model in enumerate(candidates):
            try:
                if provider == "anthropic":
                    from anthropic import Anthropic

                    client = Anthropic(
                        api_key=api_key,
                        base_url=(base_url or "https://api.anthropic.com").rstrip("/"),
                        timeout=timeout_seconds,
                    )
                    response = client.messages.create(
                        model=candidate_model,
                        max_tokens=max_tokens,
                        temperature=0,
                        system=system_anthropic,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = self._extract_anthropic_text(response)
                else:
                    text = self._call_openai_compatible_http(
                        api_key=api_key,
                        base_url=base_url,
                        model=candidate_model,
                        system_prompt=system_openai,
                        user_prompt=prompt,
                        max_tokens=max_tokens,
                        timeout_seconds=timeout_seconds,
                    )
                if not str(text or "").strip():
                    raise ValueError(f"LLM returned an empty migration response. provider={provider} model={candidate_model}")
                return text.strip(), candidate_model
            except Exception as exc:
                last_error = exc
                if idx < len(candidates) - 1 and self._is_model_fallback_error(str(exc)):
                    continue
                raise

        raise ValueError(f"LLM call failed for all model candidates: {last_error}")

    def _call_openai_compatible_http(
        self,
        *,
        api_key: str,
        base_url: str | None,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        timeout_seconds: int,
    ) -> str:
        import urllib.error
        import urllib.request

        if not base_url:
            from openai import OpenAI

            response = OpenAI(api_key=api_key, timeout=timeout_seconds).chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            )
            return (response.choices[0].message.content or "").strip()

        root = str(base_url or "").strip().rstrip("/")
        url = root if root.endswith("/chat/completions") else f"{root}/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_text = response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"LLM HTTP {exc.code}: {detail[:1000]}") from exc

        if not raw_text.strip():
            raise ValueError(f"LLM HTTP response body was empty. url={url} model={model}")
        payload = json.loads(raw_text)
        content = str((((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
        if not content:
            preview = raw_text[:1000].replace("\n", "\\n")
            raise ValueError(f"LLM returned empty message content. url={url} model={model} response_preview={preview}")
        return content

    def _resolve_llm_provider(self, llm_config: dict[str, Any], base_url: str | None, model: str) -> str:
        provider = str(llm_config.get("llm_provider") or os.getenv("LLM_PROVIDER") or "").strip().lower()
        if provider:
            if provider not in {"anthropic", "openai"}:
                raise ValueError("LLM_PROVIDER must be either 'anthropic' or 'openai'.")
            return provider
        base_text = str(base_url or "").lower()
        model_text = str(model or "").lower()
        if "anthropic" in base_text or model_text.startswith("claude"):
            return "anthropic"
        return "openai"

    def _model_candidates(self, primary_model: str, llm_config: dict[str, Any]) -> list[str]:
        fallback_raw = str(
            llm_config.get("llm_fallback_models")
            or os.getenv("LLM_FALLBACK_MODELS")
            or "GLM-5.1,Qwen3.6-35B-A3B,Kimi-K2.5"
        )
        candidates = [str(primary_model or "").strip()]
        candidates.extend(model.strip() for model in fallback_raw.split(",") if model.strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.lower()
            if candidate and key not in seen:
                deduped.append(candidate)
                seen.add(key)
        return deduped

    def _is_model_fallback_error(self, message: str) -> bool:
        text = str(message or "").lower()
        patterns = (
            "no deployments available",
            "no deployment available",
            "deployment unavailable",
            "selected model",
            "rate limit exceed for api_key",
            "rate limit exceeded for api_key",
            "rate_limit_exceed_for_api_key",
            "rate_limit_exceeded_for_api_key",
            "error code: 500",
            "status code: 500",
            "internal server error",
            "server error",
            "http 500",
            "connection reset",
            "temporarily unavailable",
            "service unavailable",
            "bad gateway",
            "502",
            "model not allow",
            "model_not_allow",
            "model not allowed",
            "model_not_allowed",
            "not allowed to access model",
            "team not allowed",
            "team_not_allowed",
            "model not found",
            "model_not_found",
            "model does not exist",
            "does not exist",
            "not supported",
            "unsupported model",
        )
        return any(pattern in text for pattern in patterns)

    def _extract_anthropic_text(self, response: Any) -> str:
        chunks: list[str] = []
        for item in getattr(response, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                chunks.append(str(text))
        return "".join(chunks).strip()

    def _load_env_files(self) -> None:
        for path in (Path.cwd() / ".env", Path.cwd() / "src" / ".env"):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                text = line.strip()
                if not text or text.startswith("#") or "=" not in text:
                    continue
                key, value = text.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("LLM returned an empty migration response.")
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                preview = raw[:500].replace("\n", "\\n")
                raise ValueError(f"LLM response did not contain a JSON object. preview={preview}") from None
            parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("LLM response JSON must be an object")
        return parsed

    def _merge_sql_value(self, value: Any) -> str:
        if isinstance(value, list):
            return "\n/\n".join(str(item.get("sql") if isinstance(item, dict) else item) for item in value)
        return str(value or "").strip()

    def _truncate_table(self, db_config: dict[str, Any], table_name: str) -> None:
        if not self._looks_like_table(table_name):
            raise ValueError(f"TRUNCATE target is not a safe table identifier: {table_name}")
        try:
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.execute(f"TRUNCATE TABLE {table_name}")
                conn.commit()
        except Exception as exc:
            raise ValueError(f"TRUNCATE failed: {exc}") from exc

    def _execute_sql_script(self, db_config: dict[str, Any], sql_script: str) -> int:
        statements = self._split_sql_script(sql_script)
        if not statements:
            raise ValueError("migration SQL is empty")
        total_rowcount = 0
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            for statement in statements:
                clean = self._clean_sql_statement(statement)
                if not clean:
                    continue
                is_plsql = clean.upper().startswith(("BEGIN", "DECLARE"))
                try:
                    cur.execute(clean + ("\n" if is_plsql else ""))
                except Exception as exc:
                    raise ValueError(f"Migration SQL execution failed: {exc}; SQL={clean[:1000]}") from exc
                if cur.rowcount and cur.rowcount > 0:
                    total_rowcount += int(cur.rowcount)
            conn.commit()
        return total_rowcount

    def _execute_verification(self, db_config: dict[str, Any], sql_script: str) -> tuple[bool, str, list[list[Any]]]:
        statements = self._split_sql_script(sql_script)
        if not statements:
            return False, "No verification SQL provided", []
        rows: list[Any] = []
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            for statement in statements:
                clean = self._clean_sql_statement(statement)
                if not clean:
                    continue
                cur.execute(clean)
                if cur.description:
                    rows = cur.fetchall()
        json_rows = [[self._json_safe_value(value) for value in row] for row in rows]
        if not rows:
            return False, "Verification SQL returned no rows", json_rows
        for row in rows:
            for value in row:
                if not self._is_zero(value):
                    return False, f"Mismatch found: {self._json_safe_value(row)}", json_rows
        return True, "All Verification Passed", json_rows

    def _split_sql_script(self, script: str) -> list[str]:
        if not script:
            return []
        return [part.strip() for part in re.split(r"^\s*/\s*$", script, flags=re.M) if part.strip()]

    def _clean_sql_statement(self, statement: str) -> str:
        cleaned = self._strip_sql_comments(str(statement or "").strip())
        return re.sub(r"[;/]\s*$", "", cleaned).strip()

    def _strip_sql_comments(self, sql: str) -> str:
        """Remove SQL comments before sending statements to Oracle.

        Oracle raises ORA-01742 when a generated block comment is truncated.
        Comments are not required for execution, so strip
        both complete and dangling comments defensively.
        """
        text = str(sql or "")
        out: list[str] = []
        index = 0
        length = len(text)
        in_single = False
        in_double = False
        while index < length:
            char = text[index]
            next_char = text[index + 1] if index + 1 < length else ""
            if char == "'" and not in_double:
                out.append(char)
                in_single = not in_single
                index += 1
                continue
            if char == '"' and not in_single:
                out.append(char)
                in_double = not in_double
                index += 1
                continue
            if not in_single and not in_double and char == "/" and next_char == "*":
                end = text.find("*/", index + 2)
                if end < 0:
                    break
                index = end + 2
                continue
            if not in_single and not in_double and char == "-" and next_char == "-":
                end = text.find("\n", index + 2)
                if end < 0:
                    break
                index = end + 1
                out.append("\n")
                continue
            out.append(char)
            index += 1
        return "\n".join(line.rstrip() for line in "".join(out).splitlines()).strip()

    def _is_zero(self, value: Any) -> bool:
        value = self._lob_to_str(value)
        if value == "":
            return False
        try:
            return Decimal(str(value).strip()) == Decimal("0")
        except (InvalidOperation, ValueError):
            return str(value).strip() == "0"

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, tuple):
            return [self._json_safe_value(item) for item in value]
        text = self._lob_to_str(value)
        try:
            return int(text)
        except (TypeError, ValueError):
            try:
                return float(text)
            except (TypeError, ValueError):
                return text

    def _attempt_outputs(self, context: dict[str, Any]) -> dict[str, Any]:
        """Collect the key output values recorded for one attempt."""
        return {
            "migration_sql": context.get("migration_sql", ""),
            "verification_sql": context.get("verification_sql", ""),
            "affected_rows": context.get("affected_rows", 0),
            "diff_count": context.get("diff_count", 0),
        }

    def _result(self, job: dict[str, Any], *, ok: bool, status: str, elapsed: int, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the Langflow output payload for the current job."""
        total = int(job.get("total_jobs") or 1)
        index = int(job.get("job_index") or 1)
        should_abort_full_workflow = bool(job.get("full_workflow")) and self._job_name(job) == "migration" and not ok
        return {
            **job,
            "component": "10C_migOneJobExecutor",
            "job_type": "MIG",
            "map_id": job.get("map_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": elapsed,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
            "full_workflow_abort": should_abort_full_workflow,
            "full_workflow_abort_phase": "DB_MIGRATION" if should_abort_full_workflow else "",
            "full_workflow_abort_reason": "DB Migration failed; SQL phases must not start." if should_abort_full_workflow else "",
        }

    def _dependency_status(self, db_config: dict[str, Any], map_id: int, prior_map_id: Any) -> str:
        """Return READY only when the prior migration job has passed."""
        prior = self._to_int(prior_map_id)
        if prior is None or prior <= 0:
            return "READY"
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT STATUS FROM {table} WHERE MAP_ID = :1", [prior])
            row = cur.fetchone()
        if not row:
            return "PENDING"
        status = str(row[0] or "").strip().upper()
        return "READY" if status == "PASS" else (status or "PENDING")

    def _is_dependency_failure_status(self, status: str) -> bool:
        """Return True when a prior job has reached a terminal failure/skip status."""
        value = str(status or "").strip().upper()
        return value.startswith("FAIL-") or value.startswith("SKIP-")

    def _mark_running(self, db_config: dict[str, Any], map_id: int) -> None:
        """Mark a migration job as running in NEXT_MIG_INFO."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = :1,
                       BATCH_CNT = NVL(BATCH_CNT, 0) + 1,
                       UPD_TS = CURRENT_TIMESTAMP
                 WHERE MAP_ID = :2
                """,
                ["RUNNING", map_id],
            )
            conn.commit()

    def _update_job(self, db_config: dict[str, Any], map_id: int, status: str, elapsed: int, retry_count: int) -> None:
        """Persist the current job status, elapsed time, and retry count."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = :1,
                       ELAPSED_SECONDS = :2,
                       RETRY_COUNT = :3,
                       UPD_TS = CURRENT_TIMESTAMP
                 WHERE MAP_ID = :4
                """,
                [status, elapsed, retry_count, map_id],
            )
            conn.commit()

    def _save_generated_sql(self, db_config: dict[str, Any], map_id: int, migration_sql: str, verification_sql: str) -> None:
        """Persist generated MIG_SQL and VERIFY_SQL immediately after generation."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"map_id": map_id}
        if "MIG_SQL" in columns and str(migration_sql or "").strip():
            params["mig_sql"] = migration_sql
            set_clauses.append("MIG_SQL = :mig_sql")
        if "VERIFY_SQL" in columns and str(verification_sql or "").strip():
            params["verify_sql"] = verification_sql
            set_clauses.append("VERIFY_SQL = :verify_sql")
        if "UPD_TS" in columns and set_clauses:
            set_clauses.append("UPD_TS = CURRENT_TIMESTAMP")
        if not set_clauses:
            return
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET {", ".join(set_clauses)}
                 WHERE MAP_ID = :map_id
                """,
                params,
            )
            conn.commit()

    def _insert_mig_log(
        self,
        db_config: dict[str, Any],
        map_id: int,
        log_type: str,
        log_level: str,
        step_name: str,
        status: str,
        message: str,
        retry_count: int,
        generated_sql: str = "",
    ) -> None:
        """Insert one migration execution log row into NEXT_MIG_LOG."""
        table = self._qualify("NEXT_MIG_LOG", db_config.get("system_schema"))
        sequence = self._qualify("MIGRATION_LOG_SEQ", db_config.get("system_schema"))
        column_types = self._table_column_types(db_config, table)
        columns = set(column_types)
        ts_columns = [column for column in ("CREATED_AT",) if column in columns]
        generate_sql_column = ", GENERATE_SQL" if "GENERATE_SQL" in columns else ""
        generate_sql_value = ", :9" if "GENERATE_SQL" in columns else ""
        ts_column_sql = "".join(f", {column}" for column in ts_columns)
        ts_value_sql = "".join(", CURRENT_TIMESTAMP" for _ in ts_columns)
        params = [
            map_id,
            "DB_MIG",
            str(log_type or "")[:20],
            str(log_level or "")[:20],
            str(step_name or "")[:50],
            str(status or "")[:20],
            str(message or "")[:4000],
            retry_count,
        ]
        if "GENERATE_SQL" in columns:
            sql_text = str(generated_sql or "")
            if column_types.get("GENERATE_SQL") not in {"CLOB", "NCLOB"}:
                sql_text = sql_text[:4000]
            params.append(sql_text)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT{generate_sql_column}{ts_column_sql}
                ) VALUES ({sequence}.NEXTVAL, :1, :2, :3, :4, :5, :6, :7, :8{generate_sql_value}{ts_value_sql})
                """,
                params,
            )
            conn.commit()

    def _load_mig_metadata(self, db_config: dict[str, Any], map_id: int | None) -> dict[str, Any]:
        """Load NEXT_MIG_INFO and NEXT_MIG_INFO_DTL metadata for one map id."""
        if map_id is None:
            raise ValueError("FETCH_DDL requires map_id")
        info_table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        detail_table = self._qualify("NEXT_MIG_INFO_DTL", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_TYPE,
                       FR_TABLE,
                       TO_TABLE,
                       TRUNC_YN,
                       CONDITION,
                       MIG_SQL,
                       VERIFY_SQL,
                       USER_EDITED
                  FROM {info_table}
                 WHERE MAP_ID = :1
                """,
                [map_id],
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_MIG_INFO row not found: map_id={map_id}")
            map_type = self._lob_to_str(row[0]) or "TABLE"
            fr_table = self._lob_to_str(row[1])
            to_table = self._lob_to_str(row[2])
            trunc_yn = self._lob_to_str(row[3])
            condition = self._lob_to_str(row[4])
            saved_migration_sql = self._lob_to_str(row[5])
            saved_verification_sql = self._lob_to_str(row[6])
            user_edited = self._lob_to_str(row[7])
            cur.execute(
                f"""
                SELECT MAP_DTL,
                       FR_COL,
                       TO_COL
                  FROM {detail_table}
                 WHERE MAP_ID = :1
                 ORDER BY MAP_DTL
                """,
                [map_id],
            )
            details = [
                {
                    "map_dtl": item[0],
                    "fr_col": self._lob_to_str(item[1]),
                    "to_col": self._lob_to_str(item[2]),
                }
                for item in cur.fetchall()
            ]
        return {
            "map_type": map_type,
            "fr_table": fr_table,
            "raw_to_table": to_table,
            "to_table": self._qualify_to_table(to_table, db_config),
            "trunc_yn": trunc_yn,
            "condition": condition,
            "saved_migration_sql": saved_migration_sql,
            "saved_verification_sql": saved_verification_sql,
            "user_edited": user_edited,
            "mapping_details": details,
            "source_ddl": self._source_ddl_for_prompt(db_config, map_type, fr_table),
            "target_ddl": self._fetch_table_columns(db_config, self._qualify_to_table(to_table, db_config)) if self._looks_like_table(to_table) else [],
        }

    def _source_ddl_for_prompt(self, db_config: dict[str, Any], map_type: str, fr_table: str) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
        """Return source DDL in the same shape as src for simple and COMPLEX mappings."""
        source_tables = self._source_tables_for_ddl(map_type, fr_table)
        if not source_tables:
            return []
        if str(map_type or "").strip().upper() != "COMPLEX":
            source_table = self._qualify_fr_table(source_tables[0], db_config)
            return self._fetch_table_columns(db_config, source_table) if self._looks_like_table(source_table) else []

        source_ddl: dict[str, list[dict[str, Any]]] = {}
        for table_name in source_tables:
            source_table = self._qualify_fr_table(table_name, db_config)
            rows = self._fetch_table_columns(db_config, source_table) if self._looks_like_table(source_table) else []
            if rows:
                source_ddl[source_table] = rows
        return source_ddl

    def _source_tables_for_ddl(self, map_type: str, fr_table: str) -> list[str]:
        """Return source physical tables used for DDL lookup."""
        text = str(fr_table or "").strip()
        if not text:
            return []
        if str(map_type or "").strip().upper() == "COMPLEX":
            return self._extract_query_table_names(text)
        return [text]

    def _extract_query_table_names(self, sql_text: str) -> list[str]:
        """Extract physical table names from a COMPLEX FR_TABLE SQL expression."""
        text = re.sub(r"/\*.*?\*/", " ", sql_text or "", flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", " ", text)
        tables: list[str] = []
        seen: set[str] = set()
        for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+([A-Z_][A-Z0-9_$#]*(?:\.[A-Z_][A-Z0-9_$#]*)?)",
            text,
            flags=re.IGNORECASE,
        ):
            table_name = match.group(1).strip()
            if table_name.upper() in {"SELECT", "WITH"}:
                continue
            key = table_name.upper()
            if key not in seen:
                seen.add(key)
                tables.append(table_name)
        return tables

    def _fetch_table_columns(self, db_config: dict[str, Any], table: str) -> list[dict[str, Any]]:
        """Read Oracle column metadata for a source or target table."""
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, NULLABLE
                  FROM ALL_TAB_COLUMNS
                 WHERE OWNER = :1
                   AND TABLE_NAME = :2
                 ORDER BY COLUMN_ID
            """
            params = [owner, table_name]
        else:
            sql = """
                SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, NULLABLE
                  FROM USER_TAB_COLUMNS
                 WHERE TABLE_NAME = :1
                 ORDER BY COLUMN_ID
            """
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [
                {
                    "column_name": self._lob_to_str(row[0]),
                    "data_type": self._lob_to_str(row[1]),
                    "data_length": row[2],
                    "data_precision": row[3],
                    "data_scale": row[4],
                    "nullable": self._lob_to_str(row[5]),
                }
                for row in cur.fetchall()
            ]

    def _mapped_columns(self, details: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Return only complete source-to-target column mappings."""
        columns: list[dict[str, str]] = []
        for item in details:
            fr_col = str(item.get("fr_col") or "").strip()
            to_col = str(item.get("to_col") or "").strip()
            if fr_col and to_col:
                columns.append({"fr_col": fr_col, "to_col": to_col})
        return columns

    def _looks_like_table(self, value: Any) -> bool:
        """Return True when a value is a plain Oracle table identifier."""
        text = str(value or "").strip()
        if not text:
            return False
        if re.search(r"\bSELECT\b|\bWITH\b|\s", text, flags=re.I):
            return False
        parts = text.split(".")
        return all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", part.strip()) for part in parts)

    def _lob_to_str(self, value: Any) -> str:
        """Convert Oracle LOB and nullable values to strings."""
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        """Return the upper-case column names available on a table."""
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2"
            params = [owner, table_name]
        else:
            sql = "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1"
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper() for row in cur.fetchall()}

    def _table_column_types(self, db_config: dict[str, Any], table: str) -> dict[str, str]:
        """Return available upper-case column names and Oracle data types."""
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = "SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2"
            params = [owner, table_name]
        else:
            sql = "SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1"
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper(): str(row[1]).upper() for row in cur.fetchall()}

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        """Open and close an Oracle database connection."""
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(db_config.get("db_username") or "").strip(),
            password=str(db_config.get("db_password") or ""),
            dsn=dsn,
        )
        with conn.cursor() as cur:
            cur.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
            cur.execute("ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF'")
            target_schema = str(db_config.get("target_schema") or "").strip().upper()
            if target_schema:
                cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {self._clean_identifier(target_schema)}")
        try:
            yield conn
        finally:
            conn.close()

    def _db_config(self, job: dict[str, Any]) -> dict[str, Any]:
        """Extract the Oracle connection settings from the job payload."""
        item_config = dict(job.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
            "source_schema": str(getattr(self, "source_schema", "") or item_config.get("source_schema") or os.getenv("ORACLE_SCHEMA_SRC") or "").strip(),
            "target_schema": str(getattr(self, "target_schema", "") or item_config.get("target_schema") or os.getenv("ORACLE_SCHEMA_TGT") or "").strip(),
        }

    def _llm_config(self, job: dict[str, Any]) -> dict[str, Any]:
        """Extract LLM settings from Langflow inputs, falling back to the job payload."""
        item_config = dict(job.get("llm_config") or {})
        return {
            "llm_base_url": str(getattr(self, "llm_base_url", "") or item_config.get("llm_base_url") or "").strip(),
            "llm_api_key": self._secret_to_str(getattr(self, "llm_api_key", None)) or str(item_config.get("llm_api_key") or "").strip(),
            "llm_provider": str(getattr(self, "llm_provider", "") or item_config.get("llm_provider") or "").strip(),
            "llm_model": str(getattr(self, "llm_model", "") or item_config.get("llm_model") or "").strip(),
            "llm_fallback_models": str(getattr(self, "llm_fallback_models", "") or item_config.get("llm_fallback_models") or "").strip(),
            "llm_max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None) or item_config.get("llm_max_tokens"), 4096),
            "llm_timeout_seconds": self._positive_int(getattr(self, "llm_timeout_seconds", None) or item_config.get("llm_timeout_seconds"), 900),
        }

    def _qualify(self, table_name: str, schema: Any) -> str:
        """Return a validated schema-qualified Oracle table name."""
        value = str(table_name or "").strip().upper()
        if "." in value:
            return value
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if clean_schema:
            clean_schema = self._clean_identifier(clean_schema)
            return f"{clean_schema}.{clean_table}"
        return clean_table

    def _qualify_fr_table(self, table_name: str, db_config: dict[str, Any]) -> str:
        """Return source schema-qualified physical table name."""
        return self._qualify_domain_table(table_name, db_config.get("source_schema"))

    def _qualify_to_table(self, table_name: str, db_config: dict[str, Any]) -> str:
        """Return target schema-qualified physical table name."""
        return self._qualify_domain_table(table_name, db_config.get("target_schema"))

    def _qualify_domain_table(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip()
        if not value or "." in value or not self._looks_like_table(value):
            return value
        return self._qualify(value, schema)

    def _qualify_source_tables_in_sql(self, sql_text: str, db_config: dict[str, Any]) -> str:
        schema = str(db_config.get("source_schema") or "").strip().upper()
        if not schema:
            return sql_text
        clean_schema = self._clean_identifier(schema)

        def replace(match: re.Match[str]) -> str:
            keyword = match.group(1)
            table_name = match.group(2)
            if "." in table_name or not self._looks_like_table(table_name):
                return match.group(0)
            return f"{keyword} {clean_schema}.{self._clean_identifier(table_name)}"

        return re.sub(
            r"\b(FROM|JOIN)\s+([A-Z_][A-Z0-9_$#]*)\b",
            replace,
            sql_text,
            flags=re.I,
        )

    def _clean_identifier(self, value: str) -> str:
        """Validate and normalize an Oracle identifier."""
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        """Split an optional owner-qualified table identifier."""
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        """Parse a Langflow Data, Message, dict, or JSON string payload."""
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("job_item must be a JSON object")
        return parsed

    def _positive_int(self, value: Any, default: int) -> int:
        """Convert a value to a positive int, or return default."""
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _secret_to_str(self, value: Any) -> str:
        """Convert a Langflow secret value into plain text."""
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _to_int(self, value: Any) -> int | None:
        """Convert a value to int, returning None for invalid input."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None



MIGRATION_PROMPT_TEMPLATE: dict[str, str] = {
    "system_anthropic": "Generate SQL using Oracle 19c syntax. Return only one valid JSON object with ddl_sql, migration_sql, and verification_sql keys. Do not end SQL values with semicolons.",
    "system_openai": "Generate SQL using Oracle 19c syntax. Return only one valid JSON object with ddl_sql, migration_sql, and verification_sql keys. Do not end SQL values with semicolons.",
    "main_prompt": """
You are an Oracle data migration SQL specialist.
Generate or fix Oracle 19c SQL using only the provided mapping rules and DDL information.
The target table already exists, so ddl_sql may be an empty string unless a safe DDL is explicitly needed.

[Non-negotiable rules]
1. Zero hallucination:
   - Do not use tables or columns that are not present in the mapping rules or DDL information.
2. Type safety:
   - When comparing or converting NUMBER, VARCHAR2, DATE, or TIMESTAMP values, use explicit CAST, TO_NUMBER, TO_DATE, or TO_TIMESTAMP as needed.
3. Oracle 19c compatibility:
   - Keep aliases short, preferably 1-5 characters.
   - Keep every alias within Oracle's 30 byte identifier limit.
   - Do not use non-Oracle syntax such as LIMIT.
4. Schema qualification:
   - Use the exact schema-qualified Source table and Target table values provided below.
   - Do not remove schema prefixes from physical AS-IS or TO-BE tables.
   - Do not add schema prefixes to DUAL, CTE names, inline view aliases, table aliases, or subquery aliases.
5. Output:
   - Return JSON only.
   - Required keys: ddl_sql, migration_sql, verification_sql.
   - Do not include markdown, comments, explanations, or trailing semicolons inside SQL values.

{ddl_info_block}
[Mapping rules]
- Source table: {from_table}
- Target table: {to_table}
- Column mappings:
{mapping_info}

[Migration SQL requirements]
- Prefer this shape unless retry guidance says otherwise:
  INSERT INTO {to_table} (target_columns...)
  SELECT source_expressions...
  FROM {from_table} S
  [WHERE condition]
- Source filter condition: {condition}
- If condition is blank, omit the WHERE clause.
- If condition is present, apply the same source scope in migration_sql and verification_sql.
- Target columns and expressions must follow the target DDL and mapping rules.

{verification_instruction}

[JSON shape]
{{
  "ddl_sql": "",
  "migration_sql": "INSERT INTO ... SELECT ...",
  "verification_sql": "SELECT ..."
}}
""",
    "verification_append": """
[Verification SQL requirements - append mode]
- The target table already contains rows inserted by earlier jobs.
- Do not compare the full target table count with the source count.
- Verify only rows inserted by this job by filtering the target side with EXISTS against the current source scope.
- Use one SELECT statement without UNION ALL.
- Use the provided DDL to identify data types.
- Exclude LOB/LONG columns from all verification column-count comparisons: CLOB, NCLOB, BLOB, LONG, LONG RAW.
- Do not use LOB/LONG columns in COUNT(column), DISTINCT, GROUP BY, ORDER BY, MINUS, JOIN keys, equality predicates, or value comparisons.
- Recommended shape:
  SELECT ABS(S.TOT - T.TOT) AS DIFF_TOT,
         ABS(S.C1 - T.C1) AS DIFF_C1,
         ABS(S.C2 - T.C2) AS DIFF_C2
  FROM (SELECT COUNT(*) TOT,
               COUNT(source_non_lob_col1) C1,
               COUNT(source_non_lob_col2) C2
        FROM {from_table}
        [WHERE CONDITION]) S,
       (SELECT COUNT(*) TOT,
               COUNT(target_non_lob_col1) C1,
               COUNT(target_non_lob_col2) C2
        FROM {to_table} T2
        WHERE EXISTS (
            SELECT 1
            FROM {from_table} SRC
            WHERE T2.target_key = SRC.source_key
            [AND CONDITION]
        )) T
- Choose EXISTS keys from mapping rules and DDL. Prefer primary/unique keys or a stable non-LOB source discriminator.
- The verification passes only when every DIFF_* column in the single result row is 0.""",
    "verification_regular": """
[Verification SQL requirements]
- Use one SELECT statement without UNION ALL.
- Compare total row count and mapped non-null column counts between source and target.
- Use the provided DDL to identify data types.
- Exclude LOB/LONG columns from all verification column-count comparisons: CLOB, NCLOB, BLOB, LONG, LONG RAW.
- Do not use LOB/LONG columns in COUNT(column), DISTINCT, GROUP BY, ORDER BY, MINUS, JOIN keys, equality predicates, or value comparisons.
- Recommended shape:
  SELECT ABS(S.TOT - T.TOT) AS DIFF_TOT,
         ABS(S.C1 - T.C1) AS DIFF_C1,
         ABS(S.C2 - T.C2) AS DIFF_C2
  FROM (SELECT COUNT(*) TOT,
               COUNT(source_non_lob_col1) C1,
               COUNT(source_non_lob_col2) C2
        FROM {from_table}
        [WHERE CONDITION]) S,
       (SELECT COUNT(*) TOT,
               COUNT(target_non_lob_col1) C1,
               COUNT(target_non_lob_col2) C2
        FROM {to_table}) T
- The verification passes only when every DIFF_* column in the single result row is 0.""",
    "error_suffix": """

[Previous execution failure]
- Failed SQL: {last_sql}
- Error: {last_error}
Analyze the error and regenerate corrected SQL.""",
    "append_mode_suffix": """

[Append mode migration_sql note]
- Target table '{to_table}' already exists and may contain rows inserted by previous jobs.
- Preserve existing rows.
- Add only this job's source rows.
- ddl_sql may be an empty string.""",
    "dup_key_suffix": """

[ORA-00001 duplicate key retry guidance - MERGE is forbidden]
- The previous INSERT INTO failed because of a primary/unique key duplicate.
- Do not use MERGE, MERGE INTO, UPDATE, or UPSERT-style SQL in DB Migration.
- Keep migration_sql as INSERT INTO ... SELECT ... only.
- Fix the duplicate by narrowing the source scope, applying the provided condition consistently, or removing duplicate source rows inside the SELECT.
- If duplicate source rows are possible, use a deterministic ROW_NUMBER() filter or SELECT DISTINCT in the source subquery before INSERT.
- Do not skip required target columns and do not reference unmapped columns.
- ddl_sql may be an empty string.""",
}
