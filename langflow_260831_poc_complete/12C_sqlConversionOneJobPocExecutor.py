from __future__ import annotations

import logging
import json
import os
import re
import time
from contextlib import contextmanager
from typing import Any
import urllib.request

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


CONVERSION_PASS = "PASS-CONVERSION"
FAIL_TOBE = "FAIL-TOBE"
FAIL_BIND = "FAIL-BIND"
FAIL_TEST = "FAIL-TEST"
SQL_LENGTH_SHORT_MAX = 5000
RAG_SEARCH = "SEARCH"
RAG_GENERAL = "GENERAL"


class _PromptValues(dict):
    # Keep unknown prompt placeholders visible instead of raising KeyError.
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


SQL_PROMPT_TEMPLATES: dict[str, str] = {
    "BIND_TUNED_SQL": """
You are an Oracle/MyBatis SQL pre-tuning specialist.

[Goal]
Rewrite the AS-IS SQL only when it is too long or hard to use for bind/test generation.
Keep the same result set and keep all MyBatis bind names and dynamic tags.

[Current FROM SQL]
{current_from_sql}

[SQL_TUNING GENERAL RAG]
{universal_tuning_rules}

[SQL_TUNING SEARCH RAG EXAMPLES]
{tuning_examples_text}

[Last Error]
{last_error}

[Rules]
- Return only one executable Oracle/MyBatis SQL template.
- Do not include explanations, markdown, structured wrapper objects, PL/SQL blocks, or a trailing semicolon.
- Do not change business meaning, filters, joins, aliases, or bind parameter names unless needed to make validation possible.
""".strip(),
    "TOBE_SQL": """
You are an Oracle/MyBatis SQL migration generator.

[Goal]
Convert FROM SQL into one Oracle 19c TO-BE SQL template using the mapping rules and SQL_CONVERSION RAG.

[FROM SQL]
{from_sql}

[Mapping And RAG Context]
{mapping_schema_text}

[Target Schema]
{target_schema}

[Correct SQL Hints]
{correct_sql_hint_text}

[Last Error]
{last_error}

[Rules]
- Generate or fix every SQL statement for Oracle 19c syntax.
- Mapping rules are the primary source of table and column mapping truth.
- If no mapping rule exists for a source table or column, keep that original name unchanged.
- Every physical TO-BE table must use target_schema.TABLE_NAME format.
- Do not add target_schema to DUAL, CTE names, inline view aliases, table aliases, or subquery aliases.
- Preserve MyBatis bind markers and dynamic tags whenever possible.
- Return only one executable Oracle/MyBatis SQL template.
- Do not include explanations, markdown, structured wrapper objects, PL/SQL blocks, multiple SQL statements, or a trailing semicolon.
""".strip(),
    "BIND_SQL": """
You are an Oracle bind candidate SQL generator.

[Goal]
Generate one executable Oracle SELECT statement that returns candidate values for MyBatis bind parameters.

[FROM SQL]
{from_sql}

[FROM Schema]
{from_schema}

[AS-IS Source Filter Conditions]
{asis_source_filter_conditions}

[Correct SQL Hints]
{correct_sql_hint_text}

[Last Error]
{last_error}

[Rules]
- Return only one executable Oracle SELECT statement.
- Physical AS-IS tables must use from_schema.TABLE_NAME format.
- Do not add schema to DUAL, CTE names, inline view aliases, table aliases, or subquery aliases.
- The final Bind SQL must not contain MyBatis XML tags or unresolved bind markers.
- Output column aliases must exactly match bind parameter names and should be double quoted.
- If no bind parameter is needed, return SELECT 1 AS "NO_BIND" FROM DUAL.
- Do not include explanations, markdown, structured wrapper objects, PL/SQL blocks, multiple SQL statements, or a trailing semicolon.
""".strip(),
    "TEST_SQL": """
You are an Oracle SQL conversion validation query generator.

[Goal]
Generate one executable Oracle SELECT statement that compares FROM SQL and TO-BE SQL row counts for each bind case.

[FROM SQL]
{from_sql}

[TO-BE SQL]
{tobe_sql}

[FROM Schema]
{from_schema}

[TO-BE Schema]
{tobe_schema}

[Bind Set]
{bind_set_text}

[Correct SQL Hints]
{correct_sql_hint_text}

[Last Error]
{last_error}

[Rules]
- Return only one executable Oracle SELECT statement.
- Final columns must be CASE_NO, FROM_COUNT, TO_COUNT.
- Use UNION ALL when multiple bind cases exist.
- Replace MyBatis bind markers and dynamic tags using the provided bind cases.
- Remove ORDER BY when it is not needed for row count validation.
- Do not include explanations, markdown, structured wrapper objects, PL/SQL blocks, multiple SQL statements, or a trailing semicolon.
""".strip(),
}


class NewType12CSqlConversionOneJobPocExecutor(Component):

    display_name = "12C SQL Conversion One Job Executor"
    description = "Runs one SQL Conversion job with mapping rules, RAG retrieval, bind validation, and DB status updates."
    name = "NewType12CSqlConversionOneJobPocExecutor"
    icon = "FileCode"

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
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=False),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=30, required=False),
    ]

    outputs = [
        Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"]),
    ]

    # ##############################
    # Entry point
    # ##############################

    # Langflow output method: validate inputs, load one SQL job, and start the conversion graph.
    def run_job(self) -> Data:
        logger = logging.getLogger("smartmigrate.workflow")
        logger.info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "12C_SQL_CONV", "INFO", "RUN_JOB", "START", 0]})
        try:
            started = time.perf_counter()

            # ##############################
            # Preflight checks
            # ##############################
            # This section only validates route, DB settings, prerequisites, and target row state.
            # It does not generate SQL and only prepares the one NEXT_SQL_INFO row that will be processed.
            # The actual SQL conversion work starts at _run_conversion().
            payload = self._parse_payload(getattr(self, "job_item", ""))
            self._payload_max_retry = payload.get("max_retry") if isinstance(payload, dict) else None
            if self._job_name(payload) != "conversion":
                result = self._pass_through(payload, started, "12C skipped because job_name is not conversion.")
                self.status = result
                __log_result = Data(data=result)
                logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "12C_SQL_CONV", "INFO", "RUN_JOB", "END", 0]})
                return __log_result
            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            job: dict[str, Any] = {}
            try:
                prereq = self._migration_prerequisite_status(db_config)
                if prereq.get("blocked"):
                    result = self._prerequisite_blocked(payload, started, prereq)
                    self.status = result
                    __log_result = Data(data=result)
                    logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "12C_SQL_CONV", "INFO", "RUN_JOB", "END", 0]})
                    return __log_result
                job = self._load_sql_job(db_config, payload)
                self._increment_batch_count(db_config, str(job["row_id"]))

                # ##############################
                # Actual conversion execution
                # ##############################
                # From here, the component runs the implemented SQL conversion flow:
                # source SQL preparation, RAG retrieval, prompt assembly, LLM calls, bind/test SQL, and DB status update.
                # _run_conversion() builds the LangGraph state and invokes the graph.
                result = self._run_conversion(payload, job, db_config, started)
            except Exception as exc:
                result = self._finish_failure(payload, job, db_config, started, FAIL_TOBE, str(exc))
            self.status = result
            __log_result = Data(data=result)
            logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "12C_SQL_CONV", "INFO", "RUN_JOB", "END", 0]})
            return __log_result
        except Exception as exc:
            logger.error(f"error run_job: {exc}", extra={"workflow_log": [0, "WORKFLOW", "12C_SQL_CONV", "ERROR", "RUN_JOB", "ERROR", 0]})
            raise

    # Build the output payload when DB Migration is not complete enough to run SQL Conversion.
    def _prerequisite_blocked(self, payload: dict[str, Any], started: float, prereq: dict[str, Any]) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        message = (
            "DB Migration 선행 작업이 남아 있어 SQL Conversion을 실행하지 않았습니다. "
            f"pending={prereq.get('pending_count', 0)}, fail={prereq.get('fail_count', 0)}"
        )
        return {
            **payload,
            "component": "12C_sqlConversionOneJobPocExecutor",
            "ok": False,
            "status": "PREREQUISITE_REQUIRED",
            "error_type": "DB_MIGRATION_PREREQUISITE_REQUIRED",
            "message": message,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": 0,
            "attempts": [],
            "job_index": index,
            "total_jobs": total,
            "completed_count": max(index - 1, 0),
            "remaining_count": max(total - index + 1, 0),
            "workflow_blocked": True,
            "full_workflow_abort": bool(payload.get("full_workflow")),
            "full_workflow_abort_phase": "DB_MIGRATION",
            "full_workflow_abort_reason": message,
            "db_status_updated": False,
            "next_node": "12D_sqlConversionIterationDashboard",
        }

    # Resolve the current loop item route into the local job name used by 12C.
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

    # Return the payload unchanged when this component is not responsible for the current job.
    def _pass_through(self, payload: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        result = {
            **payload,
            "component": "12C_sqlConversionOneJobPocExecutor",
            "ok": bool(payload.get("ok", True)),
            "status": payload.get("status") or "PASS-THROUGH",
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": int(payload.get("attempt_count") or 0),
            "attempts": list(payload.get("attempts") or []),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
            "stages": dict(payload.get("stages") or {}),
            "component_pass_through": True,
            "pass_through_component": "12C",
            "message": payload.get("message") or message,
            "next_node": "15C_sqlTuningOneJobPocExecutor",
        }
        history = list(result.get("history") or [])
        history.append({"step": "12C_pass_through", "message": message})
        result["history"] = history
        return result

    # Prepare conversion state and invoke the LangGraph workflow.
    def _run_conversion(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Run TO-BE generation, bind extraction, and SELECT validation for one SQL job."""
        # ##############################
        # Conversion input setup
        # ##############################
        # Source SQL priority follows the existing flow: EDIT_FR_SQL first, then FR_SQL.
        # TARGET_TABLE scopes both migration mapping rules and SQL_CONVERSION/SQL_TUNING RAG rules.
        source_sql = self._source_sql(job)
        if not source_sql.strip():
            return self._finish_failure(payload, job, db_config, started, FAIL_TOBE, "FR_SQL/EDIT_FR_SQL is empty")
        target_table = str(job.get("target_table") or "").strip()
        if not target_table:
            return self._finish_failure(
                payload,
                job,
                db_config,
                started,
                FAIL_TOBE,
                "TARGET_TABLE is empty. Cannot retrieve mapping rules for SQL conversion.",
            )

        map_id = f"{job.get('sql_id') or ''} / {job.get('space_nm') or ''}"[:100]
        tag_kind = str(job.get("tag_kind") or "").strip().upper()
        attempts: list[dict[str, Any]] = []
        llm_config = self._llm_config(payload)
        rag_config = self._rag_config()
        mapping_rules = self._load_mapping_rules(db_config, target_table)
        logger = logging.getLogger("smartmigrate.workflow")

        # ##############################
        # LangGraph execution
        # ##############################
        # The graph owns retry routing. Each node updates state, and route callbacks decide
        # whether the next node should continue, retry, or finalize.
        initial_state = {
            "payload": payload, "job": job, "db_config": db_config, "started": started,
            "source_sql": source_sql, "source_for_conversion": source_sql, "target_table": target_table,
            "map_id": map_id, "tag_kind": tag_kind, "attempts": attempts,
            "llm_config": llm_config, "rag_config": rag_config, "mapping_rules": mapping_rules,
            "max_retry": self._max_retry(), "attempt_no": 1, "retry_count": 0,
            "last_status": FAIL_TOBE, "last_message": "SQL conversion failed.",
            "to_sql": str(job.get("to_sql") or "").strip(),
            "bind_sql": str(job.get("bind_sql") or "").strip(),
            "bind_set": str(job.get("bind_set") or "") or None,
            "test_sql": str(job.get("test_sql") or "").strip(),
            "tuned_fr_sql": str(job.get("tuned_fr_sql") or "").strip() or None,
            "sql_length": self._sql_length_kind(source_sql),
            "resume_stage": self._initial_resume_stage(job, tag_kind, str(job.get("to_sql") or "").strip(), str(job.get("bind_sql") or "").strip()),
            "status": "RUNNING",
        }
        final_state = self._run_conversion_graph(initial_state)
        result = final_state.get("result")
        if isinstance(result, dict):
            return result
        return self._finish_failure(payload, job, db_config, started, final_state.get("last_status") or FAIL_TOBE, final_state.get("last_message") or "SQL conversion failed", final_state.get("attempts") or [], partial_values=final_state, mark_user_edited=True)

    # ##############################
    # LangGraph retry callbacks
    # ##############################

    # Build LangGraph nodes and route retry/finalize decisions from state.
    def _run_conversion_graph(self, context: dict[str, Any]) -> dict[str, Any]:
        """Build and execute the SQL conversion graph for one NEXT_SQL_INFO row."""
        from langgraph.graph import END, StateGraph

        logger = logging.getLogger("smartmigrate.workflow")

        # Node 1: choose original SQL or final-attempt pre-tuned FROM SQL.
        def prepare_source_node(state: dict[str, Any]) -> dict[str, Any]:
            # Pre-tuning is intentionally delayed until the final attempt.
            # Earlier attempts use the original source SQL so normal conversion gets a chance first.
            allow_pre_tuning = int(state["attempt_no"]) >= int(state["max_retry"])
            before_tuned = state.get("tuned_fr_sql")
            try:
                source_for_conversion, tuned_fr_sql, sql_length = self._prepare_conversion_source(
                    state["job"], state["db_config"], state["llm_config"], state["rag_config"],
                    state["source_sql"], state["target_table"], state["map_id"], allow_generate=allow_pre_tuning,
                )
                next_state = {**state, "source_for_conversion": source_for_conversion, "tuned_fr_sql": tuned_fr_sql, "sql_length": sql_length, "node_failed": False}
                if allow_pre_tuning and tuned_fr_sql and not before_tuned:
                    next_state["resume_stage"] = "GENERATE_TOBE_SQL"
                return next_state
            except Exception as exc:
                state["last_status"], state["last_message"], state["resume_stage"] = FAIL_TOBE, str(exc), "TUNE_FR_SQL"
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "TUNE_FR_SQL", "status": FAIL_TOBE, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TUNED_FR_SQL", "ERROR", "TUNE_FR_SQL", FAIL_TOBE, state["retry_count"]]})
                return state

        # Node 2: generate or reuse TO_SQL and persist it to NEXT_SQL_INFO.
        def generate_tobe_node(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("resume_stage") != "GENERATE_TOBE_SQL" and state.get("to_sql"):
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "REUSE_TOBE_SQL", "status": CONVERSION_PASS, "reason": f"resume_from={state.get('resume_stage')}"})
                state["node_failed"] = False
                return state
            try:
                to_sql = self._generate_tobe_sql(
                    state["job"], state["db_config"], state["llm_config"], state["rag_config"],
                    state["source_for_conversion"], state["mapping_rules"], state["target_table"], state.get("retry_context") or "", state["retry_count"],
                )
                state["to_sql"] = to_sql
                state["node_failed"] = False
                state["last_status"] = ""
                state["last_message"] = ""
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "GENERATE_TOBE_SQL", "status": CONVERSION_PASS, "sql_length": len(to_sql)})
                logger.info("TOBE_SQL generated", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TOBE_SQL", "INFO", "GENERATE_TOBE_SQL", "SUCCESS", state["retry_count"], to_sql]})
                update_values = {"TO_SQL": to_sql}
                if state.get("tuned_fr_sql"):
                    update_values["TUNED_FR_SQL"] = state["tuned_fr_sql"]
                self._update_row(state["db_config"], state["job"]["row_id"], update_values)
                state["resume_stage"] = "GENERATE_BIND_SQL" if state["tag_kind"] == "SELECT" else "SKIP_TEST_FOR_NON_SELECT"
                return state
            except Exception as exc:
                state["last_status"], state["last_message"], state["resume_stage"] = FAIL_TOBE, str(exc), "GENERATE_TOBE_SQL"
                state["to_sql"] = ""
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "GENERATE_TOBE_SQL", "status": FAIL_TOBE, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TOBE_SQL", "ERROR", "GENERATE_TOBE_SQL", FAIL_TOBE, state["retry_count"]]})
                return state

        # Node 3: for SELECT jobs, build bind candidate SQL and BIND_SET.
        def generate_bind_node(state: dict[str, Any]) -> dict[str, Any]:
            if state["tag_kind"] != "SELECT":
                state["node_failed"] = False
                state.update({"bind_sql": "", "bind_set": None, "test_sql": "", "status": CONVERSION_PASS})
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "SKIP_TEST_FOR_NON_SELECT", "status": CONVERSION_PASS, "tag_kind": state["tag_kind"] or "UNKNOWN"})
                return state
            if state.get("resume_stage") == "GENERATE_TEST_SQL":
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "REUSE_BIND_SQL", "status": CONVERSION_PASS, "reason": "resume_from=GENERATE_TEST_SQL"})
                state["node_failed"] = False
                return state
            try:
                bind_sql, bind_set = self._generate_bind_payload(
                    state["job"], state["db_config"], state["llm_config"], state["source_for_conversion"],
                    state["to_sql"], state["mapping_rules"], state.get("retry_context") or "", state["retry_count"],
                )
                state.update({"bind_sql": bind_sql, "bind_set": bind_set, "resume_stage": "GENERATE_TEST_SQL", "last_status": "", "last_message": "", "node_failed": False})
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "GENERATE_BIND_SQL", "status": CONVERSION_PASS})
                logger.info("BIND_SQL generated", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "BIND_SQL", "INFO", "GENERATE_BIND_SQL", "SUCCESS", state["retry_count"], bind_sql]})
                self._update_row(state["db_config"], state["job"]["row_id"], {"BIND_SQL": bind_sql, "BIND_SET": bind_set})
                return state
            except Exception as exc:
                state["last_status"], state["last_message"], state["resume_stage"] = FAIL_BIND, str(exc), "GENERATE_BIND_SQL"
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "GENERATE_BIND_SQL", "status": FAIL_BIND, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "BIND_SQL", "ERROR", "GENERATE_BIND_SQL", FAIL_BIND, state["retry_count"], state.get("bind_sql") or ""]})
                return state

        # Node 4: for SELECT jobs, generate and execute row-count validation SQL.
        def generate_test_node(state: dict[str, Any]) -> dict[str, Any]:
            if state["tag_kind"] != "SELECT":
                state["node_failed"] = False
                return state
            try:
                test_sql = self._generate_test_sql(
                    state["job"], state["db_config"], state["llm_config"], state["source_sql"],
                    state["to_sql"], state.get("bind_set"), state.get("retry_context") or "", state["retry_count"],
                )
                state["test_sql"] = test_sql
                self._update_row(state["db_config"], state["job"]["row_id"], {"TEST_SQL": test_sql})
                test_rows = self._execute_test_query(state["db_config"], test_sql)
                self._evaluate_test_rows(test_rows)
                state.update({"test_sql": test_sql, "status": CONVERSION_PASS, "node_failed": False})
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "GENERATE_TEST_SQL", "status": CONVERSION_PASS})
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "VALIDATE_TEST_SQL", "status": CONVERSION_PASS, "rows": len(test_rows)})
                logger.info("TEST_SQL validated", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TEST_SQL", "INFO", "VALIDATE_TEST_SQL", "PASS", state["retry_count"], test_sql]})
                return state
            except Exception as exc:
                state["last_status"], state["last_message"], state["resume_stage"] = FAIL_TEST, str(exc), "GENERATE_TEST_SQL"
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "VALIDATE_TEST_SQL", "status": FAIL_TEST, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TEST_SQL", "ERROR", "VALIDATE_TEST_SQL", FAIL_TEST, state["retry_count"], state.get("test_sql") or ""]})
                return state

        # Retry node: advance attempt counters and carry the previous error into the next prompt.
        def retry_prepare_node(state: dict[str, Any]) -> dict[str, Any]:
            next_attempt = int(state["attempt_no"]) + 1
            final_retry_mode = "ON" if next_attempt >= int(state["max_retry"]) else "OFF"
            return {
                **state,
                "attempt_no": next_attempt,
                "retry_count": next_attempt - 1,
                "retry_context": f"RETRY_CONTEXT: attempt={next_attempt}/{state['max_retry']}; FINAL_RETRY_MODE={final_retry_mode}; last_error={state.get('last_message') or ''}",
                "status": "RUNNING",
                "node_failed": False,
            }

        # Final node: persist the final NEXT_SQL_INFO status and build the Langflow result payload.
        def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("status") == CONVERSION_PASS:
                final_log = f"FINAL SUCCESS stage=SQL_CONVERSION status={CONVERSION_PASS} job={state['job'].get('space_nm')}.{state['job'].get('sql_id')} reason=TAG_KIND:{state['tag_kind'] or 'UNKNOWN'}"
                values = {"TO_SQL": state.get("to_sql"), "BIND_SQL": state.get("bind_sql"), "BIND_SET": state.get("bind_set"), "TEST_SQL": state.get("test_sql"), "STATUS_CONVERSION": CONVERSION_PASS, "LOG": final_log, "RETRY_COUNT": state["retry_count"]}
                if state.get("tuned_fr_sql"):
                    values["TUNED_FR_SQL"] = state["tuned_fr_sql"]
                self._update_row(state["db_config"], state["job"]["row_id"], values)
                state["result"] = self._result(
                    payload=state["payload"], job=state["job"], ok=True, status=CONVERSION_PASS,
                    elapsed=time.perf_counter() - state["started"], attempts=state["attempts"],
                    message="SQL conversion completed. Continuing to tuning.",
                    extra={"status_conversion": CONVERSION_PASS, "conversion_status": CONVERSION_PASS, "to_sql": state.get("to_sql"), "bind_sql": state.get("bind_sql"), "bind_set": state.get("bind_set"), "test_sql": state.get("test_sql"), "tuned_fr_sql": state.get("tuned_fr_sql"), "sql_length": state.get("sql_length"), "tag_kind": state["tag_kind"], "next_node": "15C_sqlTuningOneJobPocExecutor"},
                )
                return state
            state["result"] = self._finish_failure(
                state["payload"], state["job"], state["db_config"], state["started"],
                state.get("last_status") or FAIL_TOBE, state.get("last_message") or "SQL conversion failed.",
                state.get("attempts") or [],
                partial_values={"TO_SQL": state.get("to_sql"), "BIND_SQL": state.get("bind_sql"), "BIND_SET": state.get("bind_set"), "TEST_SQL": state.get("test_sql"), "TUNED_FR_SQL": state.get("tuned_fr_sql")},
                mark_user_edited=True,
            )
            return state

        # Router: continue retries only while a node failed and retry budget remains.
        def route_after_stage(state: dict[str, Any]) -> str:
            if state.get("status") == CONVERSION_PASS:
                return "finalize"
            if state.get("node_failed") and int(state.get("attempt_no") or 1) < int(state.get("max_retry") or 1):
                return "retry_prepare"
            return "finalize"

        workflow = StateGraph(dict)
        workflow.add_node("prepare_source", prepare_source_node)
        workflow.add_node("generate_tobe", generate_tobe_node)
        workflow.add_node("generate_bind", generate_bind_node)
        workflow.add_node("generate_test", generate_test_node)
        workflow.add_node("retry_prepare", retry_prepare_node)
        workflow.add_node("finalize", finalize_node)
        workflow.set_entry_point("prepare_source")
        workflow.add_conditional_edges("prepare_source", lambda state: route_after_stage(state) if state.get("node_failed") else "generate_tobe", {"generate_tobe": "generate_tobe", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_conditional_edges("generate_tobe", lambda state: route_after_stage(state) if state.get("node_failed") else "generate_bind", {"generate_bind": "generate_bind", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_conditional_edges("generate_bind", lambda state: route_after_stage(state) if state.get("node_failed") or state.get("tag_kind") != "SELECT" else "generate_test", {"generate_test": "generate_test", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_conditional_edges("generate_test", route_after_stage, {"retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_edge("retry_prepare", "prepare_source")
        workflow.add_edge("finalize", END)
        return workflow.compile().invoke(context)

    # Choose the source SQL used by TO_SQL generation and optionally create TUNED_FR_SQL.
    def _prepare_conversion_source(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], rag_config: dict[str, Any], source_sql: str, target_table: str, map_id: str, allow_generate: bool = True) -> tuple[str, str | None, str]:
        """Use saved TUNED_FR_SQL or generate it for long source SQL before conversion."""
        saved_tuned_fr_sql = str(job.get("tuned_fr_sql") or "").strip()
        if saved_tuned_fr_sql:
            return saved_tuned_fr_sql, saved_tuned_fr_sql, self._sql_length_kind(source_sql)

        sql_length = self._sql_length_kind(source_sql)
        pretuning_enabled = str(os.getenv("BIND_SQL_PRETUNING_ENABLED", "false")).strip().lower() == "true"
        pretuning_min_length = self._positive_int(os.getenv("BIND_SQL_PRETUNING_MIN_LENGTH"), 8000)
        if not allow_generate or not pretuning_enabled or (sql_length != "LONG" and len(source_sql) < pretuning_min_length):
            return source_sql, None, sql_length

        # Long SQL pre-tuning uses SQL_TUNING RAG only on the final graph attempt.
        # GENERAL rules are loaded as direct guidance. SEARCH rules are ranked by FAISS vector search
        # inside _retrieve_rag_examples(), then serialized into the embedded BIND_TUNED_SQL prompt.
        source_tables = self._source_tables(target_table)
        tuning_rules = self._load_rag_general_rules(db_config, "SQL_TUNING", source_tables)
        tuning_examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_TUNING", source_sql, source_tables)
        prompt = self._build_prompt(
            "BIND_TUNED_SQL",
            current_from_sql=source_sql,
            universal_tuning_rules=self._serialize_general_rules(tuning_rules),
            tuning_examples_text=self._serialize_tuning_examples(tuning_examples),
            last_error="None",
        )
        self._log_prompt(map_id, "TUNE_FR_SQL_PROMPT", prompt, 0)
        tuned_fr_sql, _ = self._call_llm_text(prompt, llm_config)
        tuned_fr_sql = self._clean_generated_sql(tuned_fr_sql)
        if not tuned_fr_sql:
            raise ValueError("TUNED_FR_SQL generation returned empty SQL")
        self._update_row(db_config, job["row_id"], {"TUNED_FR_SQL": tuned_fr_sql})
        self._increment_rag_hits(db_config, tuning_examples)
        logging.getLogger("smartmigrate.workflow").info(
            "TUNED_FR_SQL generated",
            extra={"workflow_log": [map_id, "SQL_CONVERSION", "TUNED_FR_SQL", "INFO", "TUNE_FR_SQL", "SUCCESS", 0, tuned_fr_sql]},
        )
        return tuned_fr_sql, tuned_fr_sql, sql_length

    # Generate TO_SQL from mapping rules, SQL_CONVERSION RAG, and retry context.
    def _generate_tobe_sql(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], rag_config: dict[str, Any], source_sql: str, mapping_rules: list[dict[str, str]], target_table: str, last_error: str, retry_count: int) -> str:
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("to_sql") or "").strip():
            return str(job["to_sql"])

        # TO_SQL prompt context is assembled in this order:
        # migration table/column mapping rules, SQL_CONVERSION GENERAL RAG guidance,
        # and SQL_CONVERSION SEARCH examples ranked by vector similarity per SQL block.
        source_tables = self._source_tables(target_table)
        general_rules = self._load_rag_general_rules(db_config, "SQL_CONVERSION", source_tables)
        examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_CONVERSION", source_sql, source_tables)
        prompt = self._build_prompt(
            "TOBE_SQL",
            from_sql=source_sql,
            mapping_schema_text=self._mapping_prompt_text(mapping_rules, general_rules, examples, db_config),
            target_schema=db_config["target_schema"],
            correct_sql_hint_text="- (empty)",
            last_error=last_error or "None",
        )
        self._log_prompt(f"{job.get('sql_id')} / {job.get('space_nm')}"[:100], "TOBE_SQL_PROMPT", prompt, retry_count)
        sql, _ = self._call_llm_text(prompt, llm_config)
        sql = self._clean_generated_sql(sql)
        if not sql:
            raise ValueError("TO_SQL generation returned empty SQL")
        self._increment_rag_hits(db_config, examples)
        return sql

    # Generate executable BIND_SQL and convert its result rows into BIND_SET JSON.
    def _generate_bind_payload(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], source_sql: str, to_sql: str, mapping_rules: list[dict[str, str]], last_error: str, retry_count: int) -> tuple[str, str | None]:
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("bind_sql") or "").strip():
            bind_sql = str(job["bind_sql"])
        else:
            # Existing logic checks both source SQL and generated TO_SQL. If neither contains MyBatis
            # parameters or dynamic tags, bind execution is skipped and TEST_SQL receives [{}].
            if not (self._bind_names(source_sql) or self._bind_names(to_sql)):
                return "", None
            prompt = self._build_prompt(
                "BIND_SQL",
                from_sql=source_sql,
                from_schema=db_config["source_schema"],
                asis_source_filter_conditions=self._source_filter_prompt_text(mapping_rules),
                correct_sql_hint_text="- (empty)",
                last_error=last_error or "None",
            )
            if "FINAL_RETRY_MODE=ON" in str(last_error or "").upper():
                # Final retry keeps the normal bind prompt and appends stronger recovery rules.
                prompt += (
                    "\n\n[Final retry mode]\n"
                    "- Prioritize fixing the previous bind SQL execution error.\n"
                    "- Simplify joins and row sources if needed.\n"
                    "- Return a conservative bind candidate query that can execute in Oracle.\n"
                )
            self._log_prompt(f"{job.get('sql_id')} / {job.get('space_nm')}"[:100], "BIND_SQL_PROMPT", prompt, retry_count)
            bind_sql, _ = self._call_llm_text(prompt, llm_config)
            bind_sql = self._clean_generated_sql(bind_sql)
            if not bind_sql:
                raise ValueError("BIND_SQL generation returned empty SQL")
        bind_sets = self._build_bind_sets(self._execute_binding_query(db_config, bind_sql))
        return bind_sql, json.dumps(bind_sets, ensure_ascii=False, default=str)

    # Generate validation TEST_SQL that compares FROM SQL and TO_SQL row counts.
    def _generate_test_sql(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], source_sql: str, to_sql: str, bind_set: str | None, last_error: str, retry_count: int) -> str:
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("test_sql") or "").strip():
            return str(job["test_sql"])
        # TEST_SQL compares the original AS-IS SQL with TO_SQL using bind cases from BIND_SET.
        prompt = self._build_prompt(
            "TEST_SQL",
            from_sql=source_sql,
            tobe_sql=to_sql,
            from_schema=db_config["source_schema"],
            tobe_schema=db_config["target_schema"],
            bind_set_text=bind_set or "- no bind case",
            correct_sql_hint_text="- (empty)",
            last_error=last_error or "None",
        )
        if retry_count >= self._configured_retry_limit():
            # Final retry keeps the normal test prompt and appends stricter validation recovery rules.
            prompt += (
                "\n\n[Final retry mode]\n"
                "- Prioritize fixing the previous TEST_SQL execution or count mismatch error.\n"
                "- Keep the comparison shape: CASE_NO, FROM_COUNT, TO_COUNT.\n"
                "- If a dynamic MyBatis branch is ambiguous, choose the safest branch for the provided bind case.\n"
            )
        self._log_prompt(f"{job.get('sql_id')} / {job.get('space_nm')}"[:100], "TEST_SQL_PROMPT", prompt, retry_count)
        test_sql, _ = self._call_llm_text(prompt, llm_config)
        test_sql = self._clean_generated_sql(test_sql)
        if not test_sql:
            raise ValueError("TEST_SQL generation returned empty SQL")
        return test_sql

    # Persist failure state to NEXT_SQL_INFO and return the standard failure payload.
    def _finish_failure(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        db_config: dict[str, Any],
        started: float,
        status: str,
        message: str,
        attempts: list[dict[str, Any]] | None = None,
        partial_values: dict[str, Any] | None = None,
        mark_user_edited: bool = False,
    ) -> dict[str, Any]:
        """Persist a SQL conversion failure using the source status values."""
        failure_attempts = attempts or [{"attempt": 1, "stage": self._failure_stage(status), "status": status, "reason": message}]
        if job.get("row_id"):
            update_values = {
                key: value
                for key, value in (partial_values or {}).items()
                if value not in (None, "")
            }
            update_values.update(
                {
                    "STATUS_CONVERSION": status,
                    "LOG": f"FINAL FAILURE stage=SQL_CONVERSION status={status} error={message}",
                    "RETRY_COUNT": self._configured_retry_limit(),
                }
            )
            if mark_user_edited:
                update_values["USER_EDITED"] = "Y"
            self._update_row(
                db_config,
                str(job["row_id"]),
                update_values,
            )
            retry_count = self._retry_count(failure_attempts)
            logging.getLogger("smartmigrate.workflow").error(
                message,
                extra={
                    "workflow_log": [
                        f"{job.get('sql_id') or ''} / {job.get('space_nm') or ''}"[:100],
                        "SQL_CONVERSION",
                        "SQL_CONVERSION",
                        "ERROR",
                        self._failure_stage(status),
                        status,
                        retry_count,
                        (partial_values or {}).get("TO_SQL") or "",
                    ]
                },
            )
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=failure_attempts,
            message=message,
            extra={
                "status_conversion": status,
                "conversion_status": status,
                "to_sql": (partial_values or {}).get("TO_SQL"),
                "bind_sql": (partial_values or {}).get("BIND_SQL"),
                "bind_set": (partial_values or {}).get("BIND_SET"),
                "test_sql": (partial_values or {}).get("TEST_SQL"),
                "next_node": "15C_sqlTuningOneJobPocExecutor" if payload.get("full_workflow") else "12D_sqlConversionIterationDashboard",
            },
        )

    # Build the standard Langflow result payload passed to the next component.
    def _result(
        self,
        *,
        payload: dict[str, Any],
        job: dict[str, Any],
        ok: bool,
        status: str,
        elapsed: float,
        attempts: list[dict[str, Any]],
        message: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the standard Loop result payload."""
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        completed = min(index, total)
        stages = dict(payload.get("stages") or {})
        stages["conversion"] = {"ok": ok, "status": status, "message": message, "attempts": attempts}
        return {
            **payload,
            **extra,
            "component": "12C_sqlConversionOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_CONVERSION",
            "job_type": "SQL",
            "row_id": job.get("row_id") or payload.get("row_id"),
            "space_nm": job.get("space_nm") or payload.get("space_nm"),
            "sql_id": job.get("sql_id") or payload.get("sql_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "retry_count": self._retry_count(attempts),
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "db_status_updated": bool(job.get("row_id")),
        }

    # Map final failure status to the stage name that should be shown in logs.
    def _failure_stage(self, status: str) -> str:
        """Return the conversion stage represented by a failure status."""
        if status == FAIL_BIND:
            return "GENERATE_BIND_SQL"
        if status == FAIL_TEST:
            return "VALIDATE_TEST_SQL"
        return "GENERATE_TOBE_SQL"

    # Decide where to resume when a user-edited failed row already has partial SQL.
    def _initial_resume_stage(self, job: dict[str, Any], tag_kind: str, to_sql: str, bind_sql: str) -> str:
        """Resume user-corrected failed SQL rows from the next useful stage."""
        if str(job.get("user_edited") or "").strip().upper() != "Y":
            return "GENERATE_TOBE_SQL"
        if not str(to_sql or "").strip():
            return "GENERATE_TOBE_SQL"

        status = str(job.get("status_conversion") or "").strip().upper()
        if str(tag_kind or "").strip().upper() != "SELECT":
            return "SKIP_TEST_FOR_NON_SELECT"
        if status == FAIL_TEST and str(bind_sql or "").strip():
            return "GENERATE_TEST_SQL"
        if status in {FAIL_TOBE, FAIL_BIND, FAIL_TEST} or status.startswith("FAIL-"):
            return "GENERATE_BIND_SQL"
        return "GENERATE_TOBE_SQL"

    # Derive retry count from recorded attempt history.
    def _retry_count(self, attempts: list[dict[str, Any]]) -> int:
        """Return retries from attempt history."""
        max_attempt = 1
        for attempt in attempts:
            try:
                max_attempt = max(max_attempt, int(attempt.get("attempt") or 1))
            except (TypeError, ValueError):
                continue
        return max(max_attempt - 1, 0)

    # Load one NEXT_SQL_INFO row by ROWID or by SPACE_NM and SQL_ID.
    def _load_sql_job(self, db_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Load one NEXT_SQL_INFO row by ROWID or by SPACE_NM + SQL_ID."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        aliases = [
            ("TAG_KIND", "tag_kind", "VARCHAR2(100)"),
            ("SPACE_NM", "space_nm", "VARCHAR2(4000)"),
            ("SQL_ID", "sql_id", "VARCHAR2(4000)"),
            ("FR_SQL", "fr_sql", "CLOB"),
            ("TARGET_TABLE", "target_table", "VARCHAR2(4000)"),
            ("EDIT_FR_SQL", "edit_fr_sql", "CLOB"),
            ("TO_SQL", "to_sql", "CLOB"),
            ("TUNED_TO_SQL", "tuned_to_sql", "CLOB"),
            ("STATUS_TUNING", "status_tuning", "VARCHAR2(100)"),
            ("BIND_SQL", "bind_sql", "CLOB"),
            ("BIND_SET", "bind_set", "CLOB"),
            ("TEST_SQL", "test_sql", "CLOB"),
            ("STATUS_CONVERSION", "status_conversion", "VARCHAR2(100)"),
            ("LOG", "log", "VARCHAR2(4000)"),
            ("TUNED_FR_SQL", "tuned_fr_sql", "CLOB"),
            ("USER_EDITED", "user_edited", "VARCHAR2(1)"),
            ("FORMATTED_SQL", "formatted_sql", "CLOB"),
            ("TUNED_RESULT", "tuned_result", "VARCHAR2(4000)"),
            ("PRIORITY", "priority", "NUMBER"),
            ("RETRY_COUNT", "retry_count", "NUMBER"),
        ]
        select_sql = ",\n               ".join(["ROWIDTOCHAR(ROWID) AS row_id", *[self._select_expr(columns, col, alias, data_type) for col, alias, data_type in aliases]])
        row_id = str(payload.get("row_id") or "").strip()
        if row_id:
            where_sql = "ROWID = CHARTOROWID(:rid)"
            params = {"rid": row_id}
        else:
            space_nm = str(payload.get("space_nm") or "").strip()
            sql_id = str(payload.get("sql_id") or "").strip()
            if not space_nm or not sql_id:
                raise ValueError("SQL job item requires row_id or space_nm+sql_id")
            where_sql = "TO_CHAR(SPACE_NM) = :space_nm AND TO_CHAR(SQL_ID) = :sql_id"
            params = {"space_nm": space_nm, "sql_id": sql_id}
        query = f"""
            SELECT {select_sql}
              FROM {table}
             WHERE {where_sql}
             ORDER BY UPD_TS NULLS FIRST
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_SQL_INFO row not found: space_nm={payload.get('space_nm')}, sql_id={payload.get('sql_id')}")
            keys = ["row_id", *[alias for _, alias, _ in aliases]]
            loaded = {key: self._lob_to_str(row[index]) for index, key in enumerate(keys)}
        return {**payload, **loaded}

    # Update generated SQL/status columns that exist in NEXT_SQL_INFO.
    def _update_row(self, db_config: dict[str, Any], row_id: str, values: dict[str, Any]) -> None:
        """Update only columns that exist in NEXT_SQL_INFO."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"rid": row_id}
        for index, (column, value) in enumerate(values.items(), start=1):
            if column not in columns:
                continue
            name = f"p{index}"
            set_clauses.append(f"{column} = :{name}")
            params[name] = value
        if "UPD_TS" in columns:
            set_clauses.append("UPD_TS = CURRENT_TIMESTAMP")
        if not set_clauses:
            return
        query = f"""
            UPDATE {table}
               SET {", ".join(set_clauses)}
             WHERE ROWID = CHARTOROWID(:rid)
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()

    # Increment BATCH_CNT when this SQL conversion row starts execution.
    def _increment_batch_count(self, db_config: dict[str, Any], row_id: str) -> None:
        """Increment NEXT_SQL_INFO.BATCH_CNT when a SQL conversion job starts."""
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if "BATCH_CNT" not in columns:
            return
        set_clause = "BATCH_CNT = NVL(BATCH_CNT, 0) + 1"
        if "UPD_TS" in columns:
            set_clause += ", UPD_TS = CURRENT_TIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET {set_clause}
                 WHERE ROWID = CHARTOROWID(:1)
                """,
                [row_id],
            )
            conn.commit()

    # Pick EDIT_FR_SQL first and fall back to original FR_SQL.
    def _source_sql(self, job: dict[str, Any]) -> str:
        """Return EDIT_FR_SQL first, otherwise FR_SQL."""
        edited = str(job.get("edit_fr_sql") or "").strip()
        return edited if edited else str(job.get("fr_sql") or "")

    # Classify SQL length for final-attempt pre-tuning decisions.
    def _sql_length_kind(self, sql_text: str) -> str:
        """Classify runtime SQL length using the as-is 5000 character threshold."""
        return "LONG" if len(str(sql_text or "")) > SQL_LENGTH_SHORT_MAX else "SHORT"

    # Extract MyBatis bind parameter names and dynamic tag variables.
    def _bind_names(self, sql_text: str) -> list[str]:
        """Extract MyBatis bind names, including foreach collections and dynamic conditions."""
        names: list[str] = []
        seen: set[str] = set()

        # Append one normalized bind name while preserving first-seen order.
        def add(token: str) -> None:
            name = re.split(r"[,\s?:=!><+\-*/()\[]", str(token or "").strip(), maxsplit=1)[0].split(".")[-1]
            if name and name not in seen:
                names.append(name)
                seen.add(name)

        sql_without_foreach = str(sql_text or "")
        for match in re.finditer(r"<foreach\b([^>]*)>.*?</\s*foreach\s*>", sql_without_foreach, flags=re.I | re.S):
            collection = re.search(r"\bcollection\s*=\s*['\"]([^'\"]+)['\"]", match.group(1) or "", flags=re.I)
            if collection:
                add(collection.group(1))
            sql_without_foreach = sql_without_foreach.replace(match.group(0), " ")
        for match in re.finditer(r"[#$]\{\s*([^}]+?)\s*\}", sql_without_foreach):
            add(match.group(1))
        for match in re.finditer(r"<(?:if|when)\b[^>]*\btest\s*=\s*['\"]([^'\"]+)['\"][^>]*>", sql_without_foreach, flags=re.I | re.S):
            condition = re.sub(r"'[^']*'|\"[^\"]*\"", " ", match.group(1))
            for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_.]*)\b", condition):
                if name.lower() not in {"and", "or", "not", "null", "true", "false", "eq", "ne", "gt", "ge", "lt", "le", "empty", "instanceof", "new", "in"}:
                    add(name)
        return names

    # ##############################
    # Mapping rules and RAG retrieval
    # ##############################

    # Load PASS migration mapping rules scoped to the current SQL target table.
    def _load_mapping_rules(self, db_config: dict[str, Any], target_table: str) -> list[dict[str, str]]:
        map_table = self._qualify(os.getenv("MAPPING_RULE_TABLE", "NEXT_MIG_INFO"), db_config.get("system_schema"))
        detail_table = self._qualify(os.getenv("MAPPING_RULE_DETAIL_TABLE", "NEXT_MIG_INFO_DTL"), db_config.get("system_schema"))
        columns = self._table_columns(db_config, map_table)
        description_expr = "M.DESCRIPTION" if "DESCRIPTION" in columns else "CAST(NULL AS VARCHAR2(4000))"
        condition_expr = "M.CONDITION" if "CONDITION" in columns else "CAST(NULL AS VARCHAR2(4000))"
        query = f"""
            SELECT M.MAP_TYPE, M.FR_TABLE, D.FR_COL, M.TO_TABLE, D.TO_COL,
                   {description_expr}, {condition_expr}
              FROM {map_table} M
              JOIN {detail_table} D ON M.MAP_ID = D.MAP_ID
             WHERE UPPER(TRIM(M.STATUS)) = 'PASS'
             ORDER BY M.MAP_ID, D.MAP_DTL
        """
        target_tables = self._source_tables(target_table)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query)
            rules = [
                {
                    "map_type": self._lob_to_str(row[0]).strip().upper(), "fr_table": self._lob_to_str(row[1]).strip(),
                    "fr_col": self._lob_to_str(row[2]).strip(), "to_table": self._lob_to_str(row[3]).strip(),
                    "to_col": self._lob_to_str(row[4]).strip(), "description": self._lob_to_str(row[5]).strip(),
                    "condition": self._lob_to_str(row[6]).strip(),
                }
                for row in cur.fetchall()
            ]
        if not target_tables:
            return rules
        return [rule for rule in rules if self._table_matches(rule["fr_table"], target_tables)]

    # Load GENERAL RAG guidance and skip it if the RAG table is not ready.
    def _load_rag_general_rules(self, db_config: dict[str, Any], category: str, source_tables: set[str]) -> list[dict[str, Any]]:
        try:
            return self._load_rag_rules(db_config, category, RAG_GENERAL, source_tables)
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"RAG GENERAL rule load skipped: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [0, "SQL_CONVERSION", "RAG_RETRIEVE", "WARN", "RAG_GENERAL", "SKIP", 0]},
            )
            return []

    # Retrieve SEARCH RAG examples using FAISS vector search with token fallback.
    def _retrieve_rag_examples(self, db_config: dict[str, Any], rag_config: dict[str, Any], category: str, sql_text: str, source_tables: set[str]) -> list[dict[str, Any]]:
        # This is the vector search entry point for RAG SEARCH rules.
        # It loads NEXT_MIG_RAG_INFO rows, splits the current SQL into blocks,
        # embeds rule SQL and job SQL together, then ranks candidate rules per block with FAISS.
        # If FAISS, numpy, or the embedding API is unavailable, the same candidates are ranked by token overlap.
        rules = self._load_rag_rules(db_config, category, RAG_SEARCH, source_tables)
        blocks = self._split_sql_blocks(sql_text)
        if not rules or not blocks:
            return []
        blocks = [block for block in blocks if block["block_type"] == "SUBQUERY"] + [block for block in blocks if block["block_type"] != "SUBQUERY"]
        try:
            import faiss
            import numpy as np

            vectors = self._embed_texts([self._rule_embedding_text(rule) for rule in rules] + [block["normalized_sql"] for block in blocks], rag_config)
            rule_vectors = np.asarray(vectors[: len(rules)], dtype="float32")
            block_vectors = np.asarray(vectors[len(rules) :], dtype="float32")
            faiss.normalize_L2(rule_vectors)
            faiss.normalize_L2(block_vectors)
            index = faiss.IndexFlatIP(rule_vectors.shape[1])
            index.add(rule_vectors)
            top_k = self._positive_int(os.getenv("TOBE_SQL_TUNING_TOP_K"), 3)
            scores, indexes = index.search(block_vectors, min(top_k, len(rules)))
            method = "faiss_vector"
            matches_by_block = [
                [(rules[int(rule_index)], float(score)) for score, rule_index in zip(scores[i], indexes[i]) if rule_index >= 0]
                for i in range(len(blocks))
            ]
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"RAG vector search fallback to token search: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [0, "SQL_CONVERSION", "RAG_RETRIEVE", "WARN", "RAG_SEARCH", "FALLBACK", 0]},
            )
            method = "token_fallback"
            matches_by_block = [
                sorted(((rule, self._lexical_similarity(block["normalized_sql"], rule["normalized_source_sql"])) for rule in rules), key=lambda item: item[1], reverse=True)[: self._positive_int(os.getenv("TOBE_SQL_TUNING_TOP_K"), 3)]
                for block in blocks
            ]
        return [
            {
                "block_id": block["block_id"], "block_type": block["block_type"], "source_sql": block["sql"], "search_method": method,
                "top_rule_matches": [{key: value for key, value in rule.items() if key != "normalized_source_sql"} | {"score": round(score, 6)} for rule, score in matches],
            }
            for block, matches in zip(blocks, matches_by_block)
        ]

    # Load raw RAG rows from NEXT_MIG_RAG_INFO for GENERAL or SEARCH use.
    def _load_rag_rules(self, db_config: dict[str, Any], category: str, rule_type: str, source_tables: set[str]) -> list[dict[str, Any]]:
        # GENERAL rows become direct prompt guidance. SEARCH rows become vector-search candidates.
        # SOURCE_TABLES scopes rules to the current SQL target table, so unrelated domains stay out of the prompt.
        table = self._qualify(os.getenv("RAG_INFO_TABLE", "NEXT_MIG_RAG_INFO"), db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if not {"RAG_ID", "CATEGORY", "RULE_TYPE", "USE_YN"}.issubset(columns):
            raise ValueError("NEXT_MIG_RAG_INFO requires RAG_ID, CATEGORY, RULE_TYPE, and USE_YN columns")
        guidance_expr = "GUIDANCE_TEXT" if "GUIDANCE_TEXT" in columns else "CAST(NULL AS VARCHAR2(4000))"
        source_sql_expr = "SOURCE_SQL" if "SOURCE_SQL" in columns else "TO_CLOB(NULL)"
        target_sql_expr = "TARGET_SQL" if "TARGET_SQL" in columns else "TO_CLOB(NULL)"
        source_tables_expr = "SOURCE_TABLES" if "SOURCE_TABLES" in columns else "CAST(NULL AS VARCHAR2(4000))"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT RAG_ID, {source_tables_expr}, {guidance_expr}, {source_sql_expr}, {target_sql_expr}
                      FROM {table}
                     WHERE UPPER(TRIM(CATEGORY)) = :category
                       AND UPPER(TRIM(RULE_TYPE)) = :rule_type
                       AND UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                     ORDER BY CREATED_AT ASC""",
                {"category": category, "rule_type": rule_type},
            )
            result = []
            for row in cur.fetchall():
                rule_tables = self._source_tables(self._lob_to_str(row[1]))
                if rule_tables and not (rule_tables & source_tables):
                    continue
                source_sql = self._lob_to_str(row[3]).strip()
                if rule_type == RAG_SEARCH and not source_sql:
                    continue
                result.append({
                    "rule_id": self._lob_to_str(row[0]).strip(), "source_tables": sorted(rule_tables),
                    "guidance": [line.strip() for line in self._lob_to_str(row[2]).splitlines() if line.strip()],
                    "source_sql": source_sql, "target_sql": self._lob_to_str(row[4]).strip(),
                    "normalized_source_sql": self._normalize_sql_shape(source_sql),
                })
        return result

    # Serialize mapping rules and RAG examples into the TO_SQL prompt context.
    def _mapping_prompt_text(self, mapping_rules: list[dict[str, str]], general_rules: list[dict[str, Any]], examples: list[dict[str, Any]], db_config: dict[str, Any]) -> str:
        # This text is inserted into the embedded TOBE_SQL prompt as mapping_schema_text.
        # Keeping each section explicit makes the final prompt readable in NEXT_MIG_LOG.GENERATE_SQL.
        source_schema, target_schema = db_config["source_schema"], db_config["target_schema"]
        simple = sorted({(rule["fr_table"], rule["fr_col"], rule["to_table"], rule["to_col"]) for rule in mapping_rules if rule["map_type"] != "COMPLEX"})
        complex_rules = sorted({(rule["fr_table"], rule["to_table"]) for rule in mapping_rules if rule["map_type"] == "COMPLEX"})
        lines = ["[MIGRATION_MAPPING_RULES]"]
        lines.extend(f"- FR_TABLE={self._qualify_mapping_table(fr_table, source_schema)} | FR_COL={fr_col} | TO_TABLE={self._qualify_mapping_table(to_table, target_schema)} | TO_COL={to_col}" for fr_table, fr_col, to_table, to_col in simple)
        lines.extend(["", "[COMPLEX_TABLE_MAPPING_RULES]"])
        lines.extend(f"- FR_TABLE={fr_table} | TO_TABLE={self._qualify_mapping_table(to_table, target_schema)}" for fr_table, to_table in complex_rules)
        lines.extend(["", "[SQL_CONVERSION_GENERAL_RAG_GUIDANCE]"])
        for rule in general_rules:
            lines.append(f"- RAG_ID={rule['rule_id']} | SOURCE_TABLES={','.join(rule['source_tables']) or 'ALL'}")
            lines.extend(f"  - {guide}" for guide in rule["guidance"])
        lines.extend(["", "[SQL_CONVERSION_SEARCH_RAG_TOP_K_BY_SQL_BLOCK]", self._serialize_conversion_examples(examples)])
        return "\n".join(lines)

    # Serialize AS-IS source filter conditions for BIND_SQL generation.
    def _source_filter_prompt_text(self, mapping_rules: list[dict[str, str]]) -> str:
        lines = ["[ASIS_SOURCE_FILTER_CONDITIONS]"]
        conditions = sorted({(rule["fr_table"], rule["condition"]) for rule in mapping_rules if rule["condition"]})
        lines.extend(f"- FR_TABLE={table} | CONDITION={condition}" for table, condition in conditions)
        return "\n".join(lines) if conditions else "[ASIS_SOURCE_FILTER_CONDITIONS]\n- (empty)"

    # Normalize comma, whitespace, or list-like table values into uppercase table names.
    def _source_tables(self, value: str) -> set[str]:
        text = str(value or "").strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    text = ",".join(str(item) for item in parsed)
            except json.JSONDecodeError:
                pass
        return {token.split(".")[-1].strip().strip('"').upper() for token in re.split(r"[,;|\s]+", text) if token.strip()}

    # Check whether a mapping table token belongs to the current target table set.
    def _table_matches(self, table_name: str, candidates: set[str]) -> bool:
        normalized = str(table_name or "").upper()
        return any(re.search(rf"(?<![A-Z0-9_$#]){re.escape(table)}(?![A-Z0-9_$#])", normalized) for table in candidates)

    # Add schema to a physical mapping table name when it is not already qualified.
    def _qualify_mapping_table(self, table_name: str, schema: str) -> str:
        table = str(table_name or "").strip()
        if not table or "." in table:
            return table
        return f"{schema}.{table}"

    # Split SQL into MAIN_SQL and SUBQUERY blocks so RAG can search each block separately.
    def _split_sql_blocks(self, sql_text: str) -> list[dict[str, str]]:
        source = str(sql_text or "").strip().rstrip(";").strip()
        if not source:
            return []
        replacements: list[tuple[int, int, str, str]] = []
        stack: list[int] = []
        quoted = False
        for index, char in enumerate(source):
            if char == "'":
                quoted = not quoted
            elif not quoted and char == "(":
                stack.append(index)
            elif not quoted and char == ")" and stack:
                start = stack.pop()
                inner = source[start + 1:index].strip()
                if re.match(r"^SELECT\b", inner, flags=re.I):
                    replacements.append((start, index + 1, f"SUBQUERY_{len(replacements) + 1}", inner))
        main_sql = source
        for start, end, placeholder, _ in reversed(replacements):
            main_sql = f"{main_sql[:start]}({placeholder}){main_sql[end:]}"
        return [
            {"block_id": "MAIN_SQL", "block_type": "MAIN", "sql": main_sql, "normalized_sql": self._normalize_sql_shape(main_sql)},
            *[{"block_id": placeholder, "block_type": "SUBQUERY", "sql": inner, "normalized_sql": self._normalize_sql_shape(inner)} for _, _, placeholder, inner in replacements],
        ]

    # Normalize SQL text before embedding or lexical similarity scoring.
    def _normalize_sql_shape(self, sql_text: str) -> str:
        text = re.sub(r"/\*.*?\*/|--[^\n]*", " ", str(sql_text or ""), flags=re.S)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip().upper()

    # Call the embedding endpoint used by FAISS vector search.
    def _embed_texts(self, texts: list[str], rag_config: dict[str, Any]) -> list[list[float]]:
        endpoint = str(rag_config["rag_embed_base_url"]).strip().rstrip("/")
        if not endpoint:
            raise ValueError("RAG_EMBED_BASE_URL is required for FAISS retrieval")
        if not endpoint.endswith("/embeddings"):
            endpoint = f"{endpoint}/embeddings" if endpoint.endswith("/v1") else f"{endpoint}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        api_key = str(rag_config["rag_embed_api_key"]).strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"model": rag_config["rag_embed_model"], "input": texts}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=rag_config["rag_embed_timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        vectors: list[list[float]] = []
        if isinstance(body.get("data"), list):
            vectors = [[float(value) for value in item["embedding"]] for item in body["data"] if isinstance(item, dict) and isinstance(item.get("embedding"), list)]
        elif isinstance(body.get("embeddings"), list):
            vectors = [[float(value) for value in item] for item in body["embeddings"] if isinstance(item, list)]
        elif isinstance(body.get("embedding"), list):
            vectors = [[float(value) for value in body["embedding"]]]
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match request count")
        return vectors

    # Build the text embedded for each RAG rule.
    def _rule_embedding_text(self, rule: dict[str, Any]) -> str:
        return "\n".join([str(rule.get("normalized_source_sql") or ""), str(rule.get("source_sql") or "")]).strip()

    # Score two normalized SQL strings when vector search cannot run.
    def _lexical_similarity(self, left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[A-Z_]+|\d+", left.upper()))
        right_tokens = set(re.findall(r"[A-Z_]+|\d+", right.upper()))
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0

    # Render SQL_CONVERSION SEARCH matches for the TO_SQL prompt.
    def _serialize_conversion_examples(self, examples: list[dict[str, Any]]) -> str:
        lines = []
        for block in examples:
            for match in block["top_rule_matches"]:
                lines.append(f"- BLOCK={block['block_id']} SCORE={match.get('score')} RULE_ID={match.get('rule_id')}")
                if match.get("guidance"):
                    lines.extend(f"  GUIDANCE: {guide}" for guide in match["guidance"])
                lines.append(f"  SOURCE_SQL: {match.get('source_sql') or ''}")
                lines.append(f"  TARGET_SQL: {match.get('target_sql') or ''}")
        return "\n".join(lines) if lines else "- (empty)"

    # Render SQL_TUNING SEARCH matches for the final-attempt pre-tuning prompt.
    def _serialize_tuning_examples(self, examples: list[dict[str, Any]]) -> str:
        lines = []
        for block in examples:
            for match in block["top_rule_matches"]:
                lines.append(f"- BLOCK={block['block_id']} SCORE={match.get('score')} RULE_ID={match.get('rule_id')}")
                if match.get("guidance"):
                    lines.extend(f"  GUIDANCE: {guide}" for guide in match["guidance"])
                lines.append(f"  BAD_SQL: {match.get('source_sql') or ''}")
                lines.append(f"  TUNED_SQL: {match.get('target_sql') or ''}")
        return "\n".join(lines) if lines else "- (empty)"

    # Render GENERAL RAG guidance lines for embedded prompts.
    def _serialize_general_rules(self, rules: list[dict[str, Any]]) -> str:
        lines = []
        for rule in rules:
            lines.append(f"- RAG_ID={rule.get('rule_id')} SOURCE_TABLES={','.join(rule.get('source_tables') or []) or 'ALL'}")
            lines.extend(f"  GUIDANCE: {guide}" for guide in rule.get("guidance") or [])
        return "\n".join(lines) if lines else "- (empty)"

    # Increment SEARCH RAG HIT_CNT after a prompt uses retrieved examples.
    def _increment_rag_hits(self, db_config: dict[str, Any], examples: list[dict[str, Any]]) -> None:
        rule_ids = sorted({match["rule_id"] for block in examples for match in block["top_rule_matches"] if match.get("rule_id")})
        if not rule_ids:
            return
        table = self._qualify(os.getenv("RAG_INFO_TABLE", "NEXT_MIG_RAG_INFO"), db_config.get("system_schema"))
        try:
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.executemany(
                    f"UPDATE {table} SET HIT_CNT = NVL(HIT_CNT, 0) + 1, UPDATED_AT = SYSTIMESTAMP WHERE TO_CHAR(RAG_ID) = :rule_id AND UPPER(TRIM(RULE_TYPE)) = 'SEARCH'",
                    [{"rule_id": rule_id} for rule_id in rule_ids],
                )
                conn.commit()
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"RAG HIT_CNT update skipped: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [0, "SQL_CONVERSION", "RAG_HIT", "WARN", "HIT_CNT", "SKIP", 0]},
            )

    # ##############################
    # Prompt generation and LLM client
    # ##############################

    # Render an embedded prompt template without reading external prompt files.
    def _build_prompt(self, template_name: str, **values: str) -> str:
        return SQL_PROMPT_TEMPLATES[template_name].format_map(_PromptValues(values))

    # Store the final LLM prompt text through the SmartMigrate workflow logger.
    def _log_prompt(self, map_id: str, step_name: str, prompt: str, retry_count: int) -> None:
        logging.getLogger("smartmigrate.workflow").info(
            f"{step_name} assembled", extra={"workflow_log": [map_id, "SQL_CONVERSION", "PROMPT_BUILD", "INFO", step_name, "PASS", retry_count, prompt]}
        )

    # Call the configured LLM with fallback models and return raw text.
    def _call_llm_text(self, prompt: str, config: dict[str, Any]) -> tuple[str, str]:
        api_key = str(config.get("llm_api_key") or os.getenv("LLM_API_KEY") or os.getenv("OPEN_API_KEY") or "").strip()
        base_url = str(config.get("llm_base_url") or os.getenv("LLM_BASE_URL") or "").strip()
        model = str(config.get("llm_model") or os.getenv("LLM_MODEL") or "GLM-5.1").strip()
        if not api_key:
            raise ValueError("LLM API key is required for SQL conversion")
        provider = str(config.get("llm_provider") or os.getenv("LLM_PROVIDER") or "").strip().lower()
        if not provider:
            provider = "anthropic" if "anthropic" in base_url.lower() or model.lower().startswith("claude") else "openai"
        if provider not in {"openai", "anthropic"}:
            raise ValueError("LLM provider must be openai or anthropic")
        candidates = [model, *[item.strip() for item in str(config.get("llm_fallback_models") or os.getenv("LLM_FALLBACK_MODELS") or "").split(",") if item.strip()]]
        candidate_models = list(dict.fromkeys(candidates))
        for index, candidate in enumerate(candidate_models):
            try:
                if provider == "anthropic":
                    from anthropic import Anthropic

                    response = Anthropic(api_key=api_key, base_url=(base_url or "https://api.anthropic.com").rstrip("/"), timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).messages.create(
                        model=candidate, max_tokens=self._positive_int(config.get("llm_max_tokens"), 4096), temperature=0,
                        system="You generate Oracle/MyBatis SQL.", messages=[{"role": "user", "content": prompt}],
                    )
                    content = "".join(str(getattr(item, "text", "")) for item in response.content).strip()
                elif not base_url:
                    from openai import OpenAI

                    response = OpenAI(api_key=api_key, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).chat.completions.create(
                        model=candidate, temperature=0, max_tokens=self._positive_int(config.get("llm_max_tokens"), 4096),
                        messages=[{"role": "system", "content": "You generate Oracle/MyBatis SQL."}, {"role": "user", "content": prompt}],
                    )
                    content = str(response.choices[0].message.content or "").strip()
                else:
                    root = base_url.rstrip("/")
                    url = root if root.endswith("/chat/completions") else f"{root}/chat/completions"
                    request = urllib.request.Request(
                        url,
                        data=json.dumps({"model": candidate, "messages": [{"role": "system", "content": "You generate Oracle/MyBatis SQL."}, {"role": "user", "content": prompt}], "temperature": 0, "max_tokens": self._positive_int(config.get("llm_max_tokens"), 4096)}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    content = str((((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
                if content:
                    return content, candidate
                raise ValueError("LLM returned empty message content")
            except Exception:
                if index == len(candidate_models) - 1:
                    raise
        raise ValueError("LLM call failed")

    # Remove markdown, wrappers, and trailing terminators from generated SQL.
    def _clean_generated_sql(self, value: str) -> str:
        # LLM responses sometimes include markdown fences, short explanations, or <script>/<select> wrappers.
        # Runtime execution and DB storage should keep only the executable Oracle/MyBatis SQL body.
        sql = str(value or "").strip()
        code_block = re.search(r"```(?:sql)?\s*(.*?)```", sql, flags=re.I | re.S)
        if code_block:
            sql = code_block.group(1).strip()
        starts = [
            match for pattern in (
                r"\b(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|WITH)\b",
                r"<\s*(?:script|select|insert|update|delete|if|choose|when|otherwise|where|trim|foreach)\b",
            )
            if (match := re.search(pattern, sql, flags=re.I))
        ]
        if starts:
            sql = sql[min(starts, key=lambda item: item.start()).start():].strip()
        while True:
            wrapper = re.match(r"^<\s*(script|select|insert|update|delete)\b[^>]*>", sql, flags=re.I | re.S)
            if not wrapper:
                break
            tag = wrapper.group(1)
            sql = re.sub(rf"</\s*{re.escape(tag)}\s*>\s*$", "", sql[wrapper.end():].strip(), flags=re.I).strip()
        return sql.rstrip(";").strip()

    # ##############################
    # Bind and test SQL execution
    # ##############################

    # Execute BIND_SQL and return raw candidate rows.
    def _execute_binding_query(self, db_config: dict[str, Any], sql: str) -> list[dict[str, Any]]:
        clean_sql = self._runtime_sql(sql, "EXECUTE_BIND_SQL")
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(clean_sql)
            columns = [item[0] for item in cur.description] if cur.description else []
            return [{column: self._lob_to_str(value) for column, value in zip(columns, row)} for row in cur.fetchmany(50)]

    # Execute TEST_SQL and return validation rows.
    def _execute_test_query(self, db_config: dict[str, Any], sql: str) -> list[dict[str, Any]]:
        clean_sql = self._runtime_sql(sql, "EXECUTE_TEST_SQL")
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(clean_sql)
            columns = [item[0] for item in cur.description] if cur.description else []
            return [{column: self._lob_to_str(value) for column, value in zip(columns, row)} for row in cur.fetchall()]

    # Prepare generated SQL for direct Oracle execution.
    def _runtime_sql(self, sql: str, stage: str) -> str:
        clean_sql = str(sql or "").strip().rstrip(";").strip()
        if not clean_sql:
            raise ValueError(f"{stage} SQL is empty")
        if stage in {"EXECUTE_BIND_SQL", "EXECUTE_TEST_SQL"}:
            limit_match = re.search(r"\s+LIMIT\s+(\d+)\s*$", clean_sql, flags=re.I)
            fetch_match = re.search(r"\s+FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY\s*$", clean_sql, flags=re.I)
            if limit_match:
                limit = int(limit_match.group(1))
                inner = re.sub(r"\s+LIMIT\s+\d+\s*$", "", clean_sql, flags=re.I).strip()
                clean_sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {limit}"
            elif fetch_match:
                limit = int(fetch_match.group(1))
                inner = re.sub(r"\s+FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$", "", clean_sql, flags=re.I).strip()
                clean_sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {limit}"
        if any(token in clean_sql.lower() for token in ("<if", "<choose", "<when", "<otherwise", "<where", "<trim", "#{", "${")):
            raise ValueError(f"{stage} SQL contains unresolved MyBatis tags or bind markers")
        return clean_sql

    # Convert BIND_SQL result rows into up to three unique bind cases.
    def _build_bind_sets(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            bind_case = {str(key).strip().strip('"'): value for key, value in row.items() if str(key).strip()}
            if set(bind_case) == {"NO_BIND"}:
                return [{}]
            signature = json.dumps(bind_case, ensure_ascii=False, default=str, sort_keys=True)
            if bind_case and signature not in seen:
                selected.append(bind_case)
                seen.add(signature)
            if len(selected) == 3:
                break
        return selected or [{}]

    # Validate TEST_SQL output columns and row-count equality.
    def _evaluate_test_rows(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            raise ValueError("TEST_SQL returned no rows")
        for row in rows:
            values = {str(key).lower(): value for key, value in row.items()}
            if not {"case_no", "from_count", "to_count"}.issubset(values):
                raise ValueError("TEST_SQL must return CASE_NO, FROM_COUNT, TO_COUNT columns")
            try:
                from_count, to_count = int(values["from_count"]), int(values["to_count"])
            except (TypeError, ValueError) as exc:
                raise ValueError("TEST_SQL count columns must be numeric") from exc
            if from_count == 0 and to_count == 0 or from_count != to_count:
                raise ValueError(f"TEST_SQL row count mismatch: {row}")
        return "PASS"

    # Convert configured retry count into total graph attempts.
    def _max_retry(self) -> int:
        """Return bounded total attempts for the conversion loop."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(1, min(11, int(getattr(self, "_payload_max_retry") or 0) + 1))
        return max(1, min(11, int(getattr(self, "max_retry", None) or 2) + 1))

    # Return the retry count configured by Langflow or the loop payload.
    def _configured_retry_limit(self) -> int:
        """Return the configured retry limit, not including the first attempt."""
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(0, min(10, int(getattr(self, "_payload_max_retry") or 0)))
        return max(0, min(10, int(getattr(self, "max_retry", None) or 2)))

    # Build a SELECT expression that tolerates optional NEXT_SQL_INFO columns.
    def _select_expr(self, columns: set[str], column: str, alias: str, data_type: str) -> str:
        """Return a safe SELECT expression for optional NEXT_SQL_INFO columns."""
        if column in columns:
            return f"{column} AS {alias}"
        if data_type.upper() == "CLOB":
            return f"TO_CLOB(NULL) AS {alias}"
        return f"CAST(NULL AS {data_type}) AS {alias}"

    # Read table metadata from Oracle for optional-column handling.
    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        """Return available upper-case column names for a table."""
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

    # Check DB Migration completion before SQL Conversion starts.
    def _migration_prerequisite_status(self, db_config: dict[str, Any]) -> dict[str, Any]:
        """Block SQL Conversion while any active DB Migration row is pending or failed."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        user_edited_expr = "USER_EDITED" if "USER_EDITED" in columns else "'N'"
        status_expr = "STATUS" if "STATUS" in columns else "NULL"
        use_expr = "USE_YN" if "USE_YN" in columns else "'Y'"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                       SUM(CASE WHEN {status_expr} IS NULL THEN 1 ELSE 0 END) AS PENDING_COUNT,
                       SUM(CASE WHEN UPPER(TRIM(NVL({status_expr}, ''))) LIKE 'FAIL-%' THEN 1 ELSE 0 END) AS FAIL_COUNT,
                       SUM(
                           CASE
                               WHEN UPPER(TRIM(NVL({user_edited_expr}, 'N'))) = 'Y'
                                AND UPPER(TRIM(NVL({status_expr}, ''))) LIKE 'FAIL-%'
                               THEN 1 ELSE 0
                           END
                       ) AS USER_EDITED_FAIL_COUNT
                  FROM {table}
                 WHERE UPPER(TRIM(NVL({use_expr}, 'N'))) = 'Y'
                """
            )
            row = cur.fetchone() or (0, 0, 0)
        pending_count = self._num(row[0])
        fail_count = self._num(row[1])
        user_edited_fail_count = self._num(row[2])
        return {
            "blocked": pending_count > 0 or fail_count > 0,
            "pending_count": pending_count,
            "fail_count": fail_count,
            "user_edited_fail_count": user_edited_fail_count,
        }

    @contextmanager
    # Open one short-lived Oracle connection for NEXT_SQL_INFO operations.
    def _connect(self, db_config: dict[str, Any]):
        """Open and close an Oracle database connection."""
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

    # Extract Oracle connection and schema settings from the loop payload and inputs.
    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extract Oracle connection settings from the Loop item."""
        item_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
            "source_schema": str(getattr(self, "source_schema", "") or item_config.get("source_schema") or os.getenv("ORACLE_SCHEMA_SRC") or "SFAMIG").strip().upper(),
            "target_schema": str(getattr(self, "target_schema", "") or item_config.get("target_schema") or os.getenv("ORACLE_SCHEMA_TGT") or "SFAADM").strip().upper(),
        }

    # Extract LLM settings from Langflow inputs and payload fallback values.
    def _llm_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("llm_config") or {})
        return {
            "llm_base_url": str(getattr(self, "llm_base_url", "") or item_config.get("llm_base_url") or "").strip(),
            "llm_api_key": self._secret_to_str(getattr(self, "llm_api_key", None)) or str(item_config.get("llm_api_key") or "").strip(),
            "llm_provider": str(getattr(self, "llm_provider", "") or item_config.get("llm_provider") or "").strip(),
            "llm_model": str(getattr(self, "llm_model", "") or item_config.get("llm_model") or "").strip(),
            "llm_fallback_models": str(getattr(self, "llm_fallback_models", "") or item_config.get("llm_fallback_models") or "").strip(),
            "llm_max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None) or item_config.get("llm_max_tokens"), 4096),
            "llm_timeout_seconds": self._positive_int(getattr(self, "llm_timeout_seconds", None) or item_config.get("llm_timeout_seconds"), 900),
        }

    # Extract RAG embedding settings from Langflow inputs or environment variables.
    def _rag_config(self) -> dict[str, Any]:
        return {
            "rag_embed_base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "rag_embed_api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "rag_embed_model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "rag_embed_timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 30),
        }

    # Normalize Langflow secret inputs to plain strings for client libraries.
    def _secret_to_str(self, value: Any) -> str:
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")

    # Parse a positive integer while keeping a simple default fallback.
    def _positive_int(self, value: Any, default: int) -> int:
        try:
            return int(value) if int(value) > 0 else default
        except (TypeError, ValueError):
            return default

    # Fail fast when mandatory Oracle connection fields are missing.
    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        """Fail early when the Loop item does not include database settings."""
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"12C SQL Conversion is not connected to database settings: missing {', '.join(missing)}")

    # Qualify a database table name with the configured system schema.
    def _qualify(self, table_name: str, schema: Any) -> str:
        """Return a validated schema-qualified table name."""
        clean_table = self._clean_identifier(table_name)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    # Keep only safe Oracle identifier characters for dynamic table names.
    def _clean_identifier(self, value: str) -> str:
        """Validate and normalize an Oracle identifier."""
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # Split OWNER.TABLE into metadata lookup parts.
    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        """Split an optional owner-qualified table identifier."""
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

    # Read Oracle LOB values before storing them in payload dictionaries.
    def _lob_to_str(self, value: Any) -> str:
        """Convert Oracle LOB and nullable values to strings."""
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    # Convert Oracle aggregate values to integers.
    def _num(self, value: Any) -> int:
        """Convert nullable DB aggregate values to int."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    # Parse the incoming Langflow job item into a dictionary.
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
