from __future__ import annotations

import json
import random
import re
import time
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType10CMigOneJobPocExecutor(Component):
    display_name = "10C MIG One Job POC Executor"
    description = "Runs one DB Migration POC job with real DB status/log updates and internal retry."
    name = "NewType10CMigOneJobPocExecutor"
    icon = "DatabaseZap"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        """Run one migration job and return the final job result payload."""
        started = time.perf_counter()
        job = self._parse_payload(getattr(self, "job_item", ""))
        if not self._should_run_migration(job):
            result = self._pass_through(job, started, "10C skipped because job_name is not migration.")
            self.status = result
            return Data(data=result)
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
                    self._insert_log(
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
                return Data(data=result)

            self._mark_running(db_config, map_id)
            base_context = {"job": job, "map_id": map_id, "attempt": 1}
            fetch_step = self._node_fetch_ddl(base_context)
            if fetch_step.get("status") != "PASS":
                raise ValueError(fetch_step.get("message") or "FETCH_DDL failed")
            self._insert_log(
                db_config,
                map_id,
                "POC_STEP",
                "INFO",
                "FETCH_DDL",
                "PASS",
                fetch_step.get("message") or "FETCH_DDL completed",
                0,
                "",
            )
            pipeline_context = {**base_context, **(fetch_step.get("outputs") or {})}
            graph_result = self._run_poc_graph(pipeline_context, db_config, max_retry)
            attempts = list(graph_result.get("attempts") or [])
            final_status = str(graph_result.get("final_status") or "FAIL-TEST")
            final_ok = final_status == "PASS"
            retry_count = max(0, int(graph_result.get("db_attempts") or 1) - 1)
            message = str(graph_result.get("message") or "")

            elapsed = int(time.perf_counter() - started)
            if final_ok:
                self._update_job(db_config, map_id, "PASS", elapsed, retry_count)
                self._insert_log(
                    db_config,
                    map_id,
                    "POC_FINAL",
                    "INFO",
                    "VERIFY",
                    "PASS",
                    message,
                    retry_count,
                    graph_result.get("verification_sql", ""),
                )
            else:
                self._update_job(db_config, map_id, final_status, elapsed, retry_count)
                self._insert_log(
                    db_config,
                    map_id,
                    "POC_FINAL",
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
                    "next_node": "12C_sqlConversionOneJobPocExecutor" if job.get("full_workflow") else "10D_migIterationDashboard",
                }
            )
            self.status = result
            return Data(data=result)
        except Exception as exc:
            elapsed = int(time.perf_counter() - started)
            result = self._result(job, ok=False, status="FAIL-INSERT", elapsed=elapsed, attempts=attempts)
            result.update({"error_type": "SYSTEM_ERROR", "error": str(exc), "message": f"POC executor error: {exc}"})
            self.status = result
            return Data(data=result)

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
            "component": "10C_migOneJobPocExecutor",
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

    def _run_poc_graph(self, context: dict[str, Any], db_config: dict[str, Any], max_retry: int) -> dict[str, Any]:
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
            self._insert_log(
                db_config,
                int(state["map_id"]),
                "POC_RETRY",
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
            f"[POC] map_id={state.get('map_id')} attempt={state.get('db_attempts')} migration pipeline passed"
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
        """Insert a POC step log for the current graph node result."""
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
        self._insert_log(db_config, map_id, "POC_STEP", log_level, stage, status, message, retry_count, stage_sql)

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
        if str(step.get("status") or "").startswith("FAIL"):
            return "; route=retry,next=GENERATE_SQL"
        if step.get("status") == "PASS":
            return "; route=finalize"
        return ""

    # ------------------------------------------------------------------
    # Future development area: replace these POC stubs with real nodes.
    # - _node_generate_sql: connect to the LLM and generate MIG_SQL / VERIFY_SQL.
    # - _node_execute_sql: run TRUNCATE and INSERT against the target database.
    # - _node_verify: run verification SQL and classify validation failures.
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
            "message": "[REAL] migration mapping and DDL metadata loaded",
            "outputs": {
                **metadata,
            },
        }

    def _node_generate_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """POC stub for LLM-based migration and verification SQL generation."""
        job = context["job"]
        rng = self._rng(context, "GENERATE_SQL")
        if rng.random() < self._fail_probability(context["attempt"], "GENERATE_SQL"):
            return {
                "stage": "GENERATE_SQL",
                "status": "FAIL-INSERT",
                "message": "[POC] migration SQL generation failed",
                "outputs": {
                    "migration_sql": context.get("migration_sql", ""),
                    "verification_sql": context.get("verification_sql", ""),
                },
            }
        map_id = job.get("map_id")
        target_table = context.get("to_table") or job.get("to_table") or "POC_TARGET"
        source_table = context.get("fr_table") or job.get("fr_table") or "POC_SOURCE"
        verify_only = context.get("failure_status") == "FAIL-TEST"
        migration_sql = context.get("migration_sql") or self._build_poc_migration_sql(context, source_table, target_table, map_id)
        verification_sql = f"SELECT 0 AS DIFF_TOT FROM DUAL /* POC verify map_id={map_id} attempt={context.get('attempt')} */"
        return {
            "stage": "GENERATE_SQL",
            "status": "PASS",
            "message": "[POC] verification SQL regenerated" if verify_only else "[POC] migration and verification SQL generated",
            "outputs": {"migration_sql": migration_sql, "verification_sql": verification_sql},
        }

    def _node_execute_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """POC stub for target truncate and migration INSERT execution."""
        if str(context.get("trunc_yn") or "").strip().upper() == "Y":
            truncate_rng = self._rng(context, "TRUNCATE")
            if truncate_rng.random() < self._fail_probability(context["attempt"], "TRUNCATE"):
                return {
                    "stage": "TRUNCATE",
                    "status": "FAIL-TRUNCATE",
                    "message": "[POC] target truncate failed",
                    "outputs": {"affected_rows": 0},
                }
        rng = self._rng(context, "EXECUTE_SQL")
        if rng.random() < self._fail_probability(context["attempt"], "EXECUTE_SQL"):
            return {
                "stage": "EXECUTE_SQL",
                "status": "FAIL-INSERT",
                "message": "[POC] migration SQL execution failed",
                "outputs": {"affected_rows": 0},
            }
        return {
            "stage": "EXECUTE_SQL",
            "status": "PASS",
            "message": "[POC] migration SQL executed",
            "outputs": {"affected_rows": self._rng(context, "ROWS").randint(1, 500)},
        }

    def _node_verify(self, context: dict[str, Any]) -> dict[str, Any]:
        """POC stub for verification SQL execution and result validation."""
        rng = self._rng(context, "VERIFY")
        if rng.random() < self._fail_probability(context["attempt"], "VERIFY"):
            return {
                "stage": "VERIFY",
                "status": "FAIL-TEST",
                "message": "[POC] verification SQL returned differences",
                "outputs": {"diff_count": rng.randint(1, 20)},
            }
        return {
            "stage": "VERIFY",
            "status": "PASS",
            "message": "[POC] verification SQL passed",
            "outputs": {"diff_count": 0},
        }

    def _rng(self, context: dict[str, Any], node_name: str) -> random.Random:
        """Create deterministic pseudo-randomness for repeatable POC behavior."""
        job = context["job"]
        seed = f"MIG:{job.get('map_id')}:{job.get('job_index')}:{context.get('attempt')}:{node_name}"
        return random.Random(seed)

    def _fail_probability(self, attempt: int, node_name: str) -> float:
        """Return the POC failure probability for a node and attempt number."""
        base = {
            "TRUNCATE": 0.20,
            "GENERATE_SQL": 0.25,
            "EXECUTE_SQL": 0.35,
            "VERIFY": 0.30,
        }.get(node_name, 0.0)
        return max(0.05, base - ((attempt - 1) * 0.15))

    def _build_poc_migration_sql(self, context: dict[str, Any], source_table: str, target_table: str, map_id: Any) -> str:
        """Build a simple deterministic INSERT SQL for the POC executor."""
        mapped_columns = self._mapped_columns(context.get("mapping_details") or [])
        if mapped_columns:
            to_cols = ", ".join(item["to_col"] for item in mapped_columns)
            fr_cols = ", ".join(item["fr_col"] for item in mapped_columns)
            return f"INSERT INTO {target_table} ({to_cols}) SELECT {fr_cols} FROM {source_table} /* POC map_id={map_id} */"
        return f"INSERT INTO {target_table} SELECT * FROM {source_table} /* POC map_id={map_id} */"

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
        return {
            **job,
            "component": "10C_migOneJobPocExecutor",
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
        return value == "FAIL" or value.startswith("FAIL-") or value.startswith("SKIP-")

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

    def _insert_log(
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
        columns = self._table_columns(db_config, table)
        ts_columns = [column for column in ("CREATED_AT", "UPD_TS") if column in columns]
        generate_sql_column = ", GENERATE_SQL" if "GENERATE_SQL" in columns else ""
        generate_sql_value = ", :9" if "GENERATE_SQL" in columns else ""
        ts_column_sql = "".join(f", {column}" for column in ts_columns)
        ts_value_sql = "".join(", CURRENT_TIMESTAMP" for _ in ts_columns)
        params = [map_id, "DB_MIG_POC", log_type, log_level, step_name, status, str(message)[:4000], retry_count]
        if "GENERATE_SQL" in columns:
            params.append(str(generated_sql or "")[:4000])
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
            "to_table": to_table,
            "trunc_yn": trunc_yn,
            "condition": condition,
            "saved_migration_sql": saved_migration_sql,
            "saved_verification_sql": saved_verification_sql,
            "user_edited": user_edited,
            "mapping_details": details,
            "source_ddl": self._fetch_table_columns(db_config, fr_table) if self._looks_like_table(fr_table) else [],
            "target_ddl": self._fetch_table_columns(db_config, to_table) if self._looks_like_table(to_table) else [],
        }

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

    def _to_int(self, value: Any) -> int | None:
        """Convert a value to int, returning None for invalid input."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
