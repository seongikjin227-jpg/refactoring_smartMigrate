from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


CONVERSION_SUCCESS_STATUSES = {"PASS", "PASS-CONVERSION"}
TUNING_PASS = "PASS-TUNING"
FAIL_TUNED = "FAIL-TUNED"
FAIL_TEST = "FAIL-TEST"
RAG_SEARCH = "SEARCH"
RAG_GENERAL = "GENERAL"

SQL_OUTPUT_FORMATTING_GUIDE = "\nReturn SQL only; final whitespace formatting is handled by 17C."


class _PromptValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


SQL_PROMPT_TEMPLATES: dict[str, str] = {
    "TUNE_TOBE_SQL": """
You are an Oracle/MyBatis SQL tuning specialist.

[Goal]
Improve the current TO-BE SQL only when the supplied SQL_TUNING RAG rules clearly apply.
Keep the same business result, filters, joins, aliases, bind names, and MyBatis dynamic tags.

[Current TO-BE SQL]
{current_tobe_sql}

[SQL_TUNING GENERAL RAG]
{universal_tuning_rules}

[SQL_TUNING SEARCH RAG EXAMPLES]
{tuning_examples_text}

[Last Error]
{last_error}

[Output]
Return only valid JSON with keys tuned_sql and tuned_result.

[Rules]
- If no rule should be applied, return the original SQL and set tuned_result to "NO TUNING".
- Do not change the query result.
- Do not add comments, markdown fences, explanations, PL/SQL blocks, multiple SQL statements, or a trailing semicolon.
""".strip(),
    "TUNED_TEST_SQL": """
You are an Oracle SQL validation query generator.

[Goal]
Create one executable Oracle SELECT that compares row counts between the baseline TO_SQL and TUNED_TO_SQL for the same bind cases.

[Baseline TO_SQL]
{baseline_tobe_sql}

[TUNED_TO_SQL]
{tuned_sql}

[Target Schema]
{tobe_schema}

[Bind Set JSON]
{bind_set_json}

[Last Error]
{last_error}

[Rules]
- Return only one executable Oracle SQL statement.
- The SQL must return CASE_NO, FROM_COUNT, TO_COUNT columns.
- FROM_COUNT must count rows from baseline TO_SQL.
- TO_COUNT must count rows from TUNED_TO_SQL.
- Use the supplied bind values as literals in each case.
- If there are no bind values, generate one case with CASE_NO = 1.
- Do not include markdown, explanations, comments, wrappers, or a trailing semicolon.
""".strip(),
}


class NewType15CSqlTuningOneJobPocExecutor(Component):
    display_name = "15C SQL Tuning One Job Executor"
    description = "Runs one SQL Tuning job with LangGraph retry, RAG retrieval, LLM tuning, and validation."
    name = "NewType15CSqlTuningOneJobPocExecutor"
    icon = "WandSparkles"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        IntInput(name="tuning_iterations", display_name="Tuning Iterations", value=1, required=False),
        StrInput(name="llm_base_url", display_name="LLM Base URL", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_provider", display_name="LLM Provider", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="GLM-5.1", required=False),
        StrInput(name="llm_fallback_models", display_name="LLM Fallback Models", value="GLM-5.1,Qwen3.6-35B-A3B,Kimi-K2.5", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=8192, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=900, required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embed Base URL", required=False),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embed API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embed Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embed Timeout Seconds", value=60, required=False),
        StrInput(name="milvus_uri", display_name="Milvus URI", required=False),
        StrInput(name="milvus_username", display_name="Milvus Username", required=False),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=False),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=False),
        StrInput(name="rag_collection_name", display_name="RAG Collection Name", value="SM_RAG_RULES", required=False),
        IntInput(name="rag_top_k", display_name="MIG RAG Top K", value=3, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "START", 0]})
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        db_config: dict[str, Any] = {}
        job: dict[str, Any] = {}
        try:
            payload = self._parse_payload(getattr(self, "job_item", ""))
            self._payload_max_retry = payload.get("max_retry") if isinstance(payload, dict) else None
            if not self._should_run_tuning(payload):
                result = self._component_pass_through(payload, started, "15C skipped because job_name is not conversion or tuning.")
                self.status = result
                return Data(data=result)

            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            job = self._load_sql_job(db_config, payload)
            merged = {**job, **payload}
            conversion_status = self._status(merged.get("conversion_status") or merged.get("status_conversion") or job.get("status_conversion"))
            if not self._is_conversion_pass(conversion_status):
                result = self._pass_through(
                    payload=merged,
                    job=job,
                    started=started,
                    status=conversion_status or self._status(merged.get("status")) or "NOT-RUN",
                    message=f"SQL tuning passed through without DB update because conversion status is {conversion_status or 'NULL'}.",
                )
                self.status = result
                return Data(data=result)

            self._increment_batch_count(db_config, str(job["row_id"]))
            self._mark_running_status(db_config, str(job["row_id"]), "RUNNING", "SQL tuning started")
            result = self._run_tuning(merged, job, db_config, started)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = self._finish_failure(payload, job, db_config, started, FAIL_TUNED, str(exc))
            self.status = result
            return Data(data=result)
        finally:
            logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "15C_SQL_TUNE", "INFO", "RUN_JOB", "END", 0]})

    def _run_tuning(self, payload: dict[str, Any], job: dict[str, Any], db_config: dict[str, Any], started: float) -> dict[str, Any]:
        to_sql = str(payload.get("to_sql") or job.get("to_sql") or "").strip()
        if not to_sql:
            return self._finish_failure(payload, job, db_config, started, FAIL_TUNED, "TO_SQL is empty")
        state = {
            "payload": payload,
            "job": job,
            "db_config": db_config,
            "llm_config": self._llm_config(payload),
            "rag_config": self._rag_config(payload),
            "started": started,
            "attempt_no": 1,
            "retry_count": 0,
            "max_retry": self._max_retry(),
            "retry_context": str(payload.get("retry_context") or "None"),
            "to_sql": to_sql,
            "tuned_sql": "",
            "tuned_result": "",
            "tag_kind": str(payload.get("tag_kind") or job.get("tag_kind") or "").strip().upper(),
            "target_table": str(payload.get("target_table") or job.get("target_table") or "").strip(),
            "bind_set": payload.get("bind_set") or job.get("bind_set"),
            "attempts": [],
            "last_status": "",
            "last_message": "",
            "status": "RUNNING",
            "node_failed": False,
            "tuning_guides": [],
            "matched_rule_ids": [],
        }
        final_state = self._run_tuning_graph(state)
        result = final_state.get("result")
        if isinstance(result, dict):
            return result
        return self._finish_failure(
            payload,
            job,
            db_config,
            started,
            final_state.get("last_status") or FAIL_TUNED,
            final_state.get("last_message") or "SQL tuning failed",
            final_state.get("attempts") or [],
            partial_values={"TUNED_TO_SQL": final_state.get("tuned_sql"), "TUNED_RESULT": final_state.get("tuned_result")},
            tuning_guides=final_state.get("tuning_guides") or [],
        )

    def _run_tuning_graph(self, context: dict[str, Any]) -> dict[str, Any]:
        from langgraph.graph import END, StateGraph

        logger = logging.getLogger("smartmigrate.workflow")

        def load_rules_node(state: dict[str, Any]) -> dict[str, Any]:
            current_sql = str(state.get("tuned_sql") or state.get("to_sql") or "").strip()
            map_id = self._map_id(state["job"])
            try:
                general_rules, tuning_examples, source_tables = self._retrieve_tuning_context(state["db_config"], state["rag_config"], current_sql, state.get("target_table") or "", map_id, state["retry_count"])
                state["general_rules"] = general_rules
                state["tuning_guides"] = tuning_examples
                state["source_tables"] = sorted(source_tables)
                state["matched_rule_ids"] = sorted({match["rule_id"] for block in tuning_examples for match in block.get("top_rule_matches", []) if match.get("rule_id")})
                self._update_block_rag_content(state["db_config"], state["job"]["row_id"], tuning_examples)
                state["node_failed"] = False
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "LOAD_TUNING_RULES", "status": "PASS", "general_rules": len(general_rules), "matched_rule_ids": state["matched_rule_ids"]})
                return state
            except Exception as exc:
                state["last_status"], state["last_message"] = FAIL_TUNED, str(exc)
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "LOAD_TUNING_RULES", "status": FAIL_TUNED, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [map_id, "SQL_TUNING", "RAG_RETRIEVE", "ERROR", "LOAD_TUNING_RULES", FAIL_TUNED, state["retry_count"]]})
                return state

        def apply_tuning_node(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("node_failed"):
                return state
            map_id = self._map_id(state["job"])
            base_sql = str(state.get("to_sql") or "").strip()
            final_sql = base_sql
            tuned_result = "NO TUNING"
            all_examples = list(state.get("tuning_guides") or [])
            try:
                for iteration in range(1, self._tuning_iterations() + 1):
                    if iteration > 1:
                        general_rules, tuning_examples, _ = self._retrieve_tuning_context(state["db_config"], state["rag_config"], final_sql, state.get("target_table") or "", map_id, state["retry_count"])
                        state["general_rules"] = general_rules
                        state["tuning_guides"] = tuning_examples
                        all_examples.extend(tuning_examples)
                    general_rules = list(state.get("general_rules") or [])
                    tuning_examples = list(state.get("tuning_guides") or [])
                    has_search_matches = any(block.get("top_rule_matches") for block in tuning_examples)
                    if not general_rules and not has_search_matches:
                        tuned_result = "NO TUNING"
                        break
                    candidate_sql, candidate_result = self._generate_tuned_sql(state["job"], state["llm_config"], final_sql, general_rules, tuning_examples, state.get("retry_context") or "None", state["retry_count"])
                    candidate_sql = self._clean_generated_sql(candidate_sql)
                    if not candidate_sql:
                        raise ValueError("TUNED_TO_SQL generation returned empty SQL")
                    tuned_result = candidate_result or "TUNING APPLIED"
                    matched_ids = sorted({match["rule_id"] for block in tuning_examples for match in block.get("top_rule_matches", []) if match.get("rule_id")})
                    state["matched_rule_ids"] = sorted(set(state.get("matched_rule_ids") or []) | set(matched_ids))
                    state["attempts"].append({"attempt": state["attempt_no"], "stage": "APPLY_TUNING_RULES", "status": TUNING_PASS, "iteration": iteration, "result": tuned_result, "matched_rule_ids": matched_ids})
                    logger.info("TUNED_TO_SQL generated", extra={"workflow_log": [map_id, "SQL_TUNING", "TUNED_TO_SQL", "INFO", "APPLY_TUNING_RULES", "SUCCESS", state["retry_count"], candidate_sql]})
                    if self._normalize_compare_sql(candidate_sql) == self._normalize_compare_sql(final_sql):
                        tuned_result = "NO TUNING" if "NO TUNING" in tuned_result.upper() else tuned_result
                        break
                    final_sql = candidate_sql

                state["tuned_sql"] = final_sql
                state["tuned_result"] = tuned_result
                state["tuning_guides"] = all_examples
                state["node_failed"] = False
                self._update_row(state["db_config"], state["job"]["row_id"], {"TUNED_TO_SQL": final_sql, "TUNED_RESULT": tuned_result})
                if not any(item.get("stage") == "APPLY_TUNING_RULES" and item.get("attempt") == state["attempt_no"] for item in state["attempts"]):
                    state["attempts"].append({"attempt": state["attempt_no"], "stage": "APPLY_TUNING_RULES", "status": TUNING_PASS, "result": tuned_result})
                return state
            except Exception as exc:
                state["last_status"], state["last_message"] = FAIL_TUNED, str(exc)
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "APPLY_TUNING_RULES", "status": FAIL_TUNED, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [map_id, "SQL_TUNING", "TUNED_TO_SQL", "ERROR", "APPLY_TUNING_RULES", FAIL_TUNED, state["retry_count"]]})
                return state

        def validate_tuned_node(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("node_failed"):
                return state
            map_id = self._map_id(state["job"])
            tuned_sql = str(state.get("tuned_sql") or "").strip()
            to_sql = str(state.get("to_sql") or "").strip()
            if state.get("tag_kind") != "SELECT":
                state["status"] = TUNING_PASS
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "SKIP_TUNED_VALIDATION", "status": TUNING_PASS, "reason": f"TAG_KIND:{state.get('tag_kind') or 'UNKNOWN'}"})
                return state
            if self._normalize_compare_sql(tuned_sql) == self._normalize_compare_sql(to_sql) or self._is_no_tuning_result(state.get("tuned_result")):
                state["status"] = TUNING_PASS
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "SKIP_TUNED_VALIDATION", "status": TUNING_PASS, "reason": "NO_TUNING"})
                return state
            try:
                test_sql = self._generate_tuned_test_sql(state["job"], state["db_config"], state["llm_config"], to_sql, tuned_sql, state.get("bind_set"), state.get("retry_context") or "None", state["retry_count"])
                rows = self._execute_test_query(state["db_config"], test_sql)
                self._evaluate_test_rows(rows)
                state["status"] = TUNING_PASS
                state["node_failed"] = False
                state["tuned_test_sql"] = test_sql
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "VALIDATE_TUNED_SQL", "status": TUNING_PASS, "rows": len(rows)})
                logger.info("TUNED_TEST_SQL validated", extra={"workflow_log": [map_id, "SQL_TUNING", "TUNED_TEST_SQL", "INFO", "VALIDATE_TUNED_SQL", "PASS", state["retry_count"], test_sql]})
                return state
            except Exception as exc:
                state["last_status"], state["last_message"] = FAIL_TEST, str(exc)
                state["node_failed"] = True
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "VALIDATE_TUNED_SQL", "status": FAIL_TEST, "reason": str(exc)})
                logger.error(str(exc), extra={"workflow_log": [map_id, "SQL_TUNING", "TUNED_TEST_SQL", "ERROR", "VALIDATE_TUNED_SQL", FAIL_TEST, state["retry_count"], state.get("tuned_test_sql") or ""]})
                return state

        def retry_prepare_node(state: dict[str, Any]) -> dict[str, Any]:
            next_attempt = int(state["attempt_no"]) + 1
            running_status = f"RUNNING-{state.get('last_status') or FAIL_TUNED}"
            self._mark_running_status(state["db_config"], str(state["job"]["row_id"]), running_status, state.get("last_message") or "", next_attempt - 1)
            return {**state, "attempt_no": next_attempt, "retry_count": next_attempt - 1, "retry_context": f"RETRY_CONTEXT: attempt={next_attempt}/{state['max_retry']}; last_error={state.get('last_message') or ''}", "status": "RUNNING", "node_failed": False, "tuned_sql": "", "tuned_result": ""}

        def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
            if state.get("status") == TUNING_PASS:
                final_log = f"FINAL SUCCESS stage=SQL_TUNING status={TUNING_PASS} job={state['job'].get('space_nm')}.{state['job'].get('sql_id')} result={state.get('tuned_result') or ''}"
                self._update_row(state["db_config"], state["job"]["row_id"], {"TUNED_TO_SQL": state.get("tuned_sql") or state.get("to_sql"), "TUNED_RESULT": state.get("tuned_result") or "NO TUNING", "STATUS_TUNING": TUNING_PASS, "LOG": final_log, "RETRY_COUNT": state["retry_count"]})
                self._increment_rag_hits(state["db_config"], state.get("tuning_guides") or [])
                state["result"] = self._result(
                    payload=state["payload"],
                    job=state["job"],
                    ok=True,
                    status=TUNING_PASS,
                    elapsed=time.perf_counter() - state["started"],
                    attempts=state["attempts"],
                    message="SQL tuning completed.",
                    extra={
                        "status_tuning": TUNING_PASS,
                        "tuning_status": TUNING_PASS,
                        "tuned_to_sql": state.get("tuned_sql") or state.get("to_sql"),
                        "tuned_result": state.get("tuned_result") or "NO TUNING",
                        "tuning_guides": state.get("tuning_guides") or [],
                        "matched_rule_ids": sorted(set(state.get("matched_rule_ids") or [])),
                        "tag_kind": state.get("tag_kind"),
                        "next_node": "17C_sqlFormattingOneJobPocExecutor",
                    },
                )
                return state
            state["result"] = self._finish_failure(
                state["payload"],
                state["job"],
                state["db_config"],
                state["started"],
                state.get("last_status") or FAIL_TUNED,
                state.get("last_message") or "SQL tuning failed.",
                state.get("attempts") or [],
                partial_values={"TUNED_TO_SQL": state.get("tuned_sql"), "TUNED_RESULT": state.get("tuned_result")},
                tuning_guides=state.get("tuning_guides") or [],
            )
            return state

        def route_after_stage(state: dict[str, Any]) -> str:
            if state.get("status") == TUNING_PASS:
                return "finalize"
            if state.get("node_failed") and int(state.get("attempt_no") or 1) < int(state.get("max_retry") or 1):
                return "retry_prepare"
            if state.get("node_failed"):
                return "finalize"
            return "validate_tuned"

        workflow = StateGraph(dict)
        workflow.add_node("load_rules", load_rules_node)
        workflow.add_node("apply_tuning", apply_tuning_node)
        workflow.add_node("validate_tuned", validate_tuned_node)
        workflow.add_node("retry_prepare", retry_prepare_node)
        workflow.add_node("finalize", finalize_node)
        workflow.set_entry_point("load_rules")
        workflow.add_conditional_edges("load_rules", lambda state: route_after_stage(state) if state.get("node_failed") else "apply_tuning", {"apply_tuning": "apply_tuning", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_conditional_edges("apply_tuning", route_after_stage, {"validate_tuned": "validate_tuned", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_conditional_edges("validate_tuned", route_after_stage, {"validate_tuned": "validate_tuned", "retry_prepare": "retry_prepare", "finalize": "finalize"})
        workflow.add_edge("retry_prepare", "load_rules")
        workflow.add_edge("finalize", END)
        return workflow.compile().invoke(context)

    def _retrieve_tuning_context(self, db_config: dict[str, Any], rag_config: dict[str, Any], sql_text: str, target_table: str, map_id: str, retry_count: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
        source_tables = self._source_tables(target_table)
        general_rules = self._load_rag_rules(db_config, "SQL_TUNING", RAG_GENERAL, source_tables, map_id)
        tuning_examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_TUNING", sql_text, source_tables, map_id, retry_count)
        return general_rules, tuning_examples, source_tables

    # -------------------------------------------------------------------------
    # Milvus RAG SEARCH retrieval for 15C tuning
    # -------------------------------------------------------------------------
    # 15C no longer loads all RAG rows and builds a local FAISS index.
    # Runtime retrieval is:
    # 1. split the current TO_SQL into MAIN/SUBQUERY blocks,
    # 2. normalize each block,
    # 3. embed each block through the embedding API,
    # 4. search SM_RAG_RULES.dense_vector in Milvus,
    # 5. use search_params metric_type=COSINE for vector similarity.
    #
    # The stored SM_RAG_RULES.dense_vector values are generated by 00B from
    # NEXT_MIG_RAG_INFO.SOURCE_SQL. For tuning rules, SOURCE_SQL means the
    # "before tuning" SQL example, and TARGET_SQL/guidance_text are returned as
    # metadata for the tuning prompt.
    def _retrieve_rag_examples(self, db_config: dict[str, Any], rag_config: dict[str, Any], category: str, sql_text: str, source_tables: set[str], map_id: str, retry_count: int) -> list[dict[str, Any]]:
        blocks = self._split_sql_blocks(sql_text)
        if not blocks:
            return []
        ordered_blocks = [block for block in blocks if block["block_type"] == "SUBQUERY"]
        ordered_blocks.extend(block for block in blocks if block["block_type"] != "SUBQUERY")
        top_k = self._positive_int(getattr(self, "rag_top_k", None), 3)
        fetch_k = max(top_k * 5, top_k)
        try:
            # Embed each current TO_SQL block once, then search those query
            # vectors against Milvus dense_vector. This is remote Milvus COSINE
            # vector search, not local FAISS and not Oracle-side vector math.
            client = self._milvus_client()
            config = self._milvus_config()
            vectors = self._embed_texts([block["normalized_sql"] for block in ordered_blocks], rag_config)
            search_result = client.search(
                collection_name=config["rag_collection"],
                data=vectors,
                anns_field="dense_vector",
                filter=f'category == "{category}" and rule_type == "{RAG_SEARCH}" and is_active == true',
                limit=fetch_k,
                output_fields=["rag_id", "category", "rule_type", "source_tables", "guidance_text", "source_sql", "target_sql"],
                # Milvus calculates dense embedding similarity with COSINE here.
                # Higher score means the current SQL block is closer to a stored
                # tuning example's SOURCE_SQL.
                search_params={"metric_type": "COSINE"},
            )
            matches_by_block = []
            for hits in search_result:
                matches = []
                for hit in hits:
                    rule = self._milvus_rag_entity(hit)
                    if not self._source_tables_match(rule.get("source_tables") or [], source_tables):
                        continue
                    matches.append((rule, self._milvus_score(hit)))
                    if len(matches) >= top_k:
                        break
                matches_by_block.append(matches)
            method = "milvus_dense_vector"
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"Milvus RAG search skipped: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [map_id, "SQL_TUNING", "RAG_RETRIEVE", "WARN", "RAG_SEARCH", "SKIP", retry_count]},
            )
            return []
        payloads = [
            {
                "block_id": block["block_id"],
                "block_type": block["block_type"],
                "source_sql": block["sql"],
                "search_method": method,
                "top_rule_matches": [{key: value for key, value in rule.items() if key not in {"normalized_source_sql", "embedding_vector"}} | {"score": round(score, 6)} for rule, score in matches],
            }
            for block, matches in zip(ordered_blocks, matches_by_block)
        ]
        match_count = sum(len(block["top_rule_matches"]) for block in payloads)
        logging.getLogger("smartmigrate.workflow").info(
            "RAG SEARCH completed category=SQL_TUNING",
            extra={"workflow_log": [map_id, "SQL_TUNING", "RAG_RETRIEVE", "INFO", "SQL_TUNING_SEARCH", "PASS", retry_count, f"collection={self._milvus_config()['rag_collection']}, method={method}, blocks={len(ordered_blocks)}, matches={match_count}, threshold=none, matched={self._rag_match_summary(ordered_blocks, matches_by_block)}"]},
        )
        return payloads

    def _load_rag_rules(self, db_config: dict[str, Any], category: str, rule_type: str, source_tables: set[str], map_id: str) -> list[dict[str, Any]]:
        # GENERAL tuning guidance is loaded by scalar Milvus query. It is not
        # ranked by vector distance; SEARCH examples above are the vector path.
        config = self._milvus_config()
        rows = self._milvus_client().query(
            collection_name=config["rag_collection"],
            filter=f'category == "{category}" and rule_type == "{rule_type}" and is_active == true',
            output_fields=["rag_id", "category", "rule_type", "source_tables", "guidance_text", "source_sql", "target_sql"],
            limit=1000,
        )
        result = []
        for row in rows or []:
            rule = self._milvus_rag_entity({"entity": row})
            if self._source_tables_match(rule.get("source_tables") or [], source_tables):
                result.append(rule)
        logging.getLogger("smartmigrate.workflow").info(f"RAG {rule_type} loaded category={category}", extra={"workflow_log": [map_id, "SQL_TUNING", "RAG_LOAD", "INFO", f"{category}_{rule_type}", "PASS", 0, f"collection={config['rag_collection']}, rows={len(result)}, rag_ids={','.join(rule.get('rule_id') or '' for rule in result[:20])}"]})
        return result

    def _generate_tuned_sql(self, job: dict[str, Any], llm_config: dict[str, Any], current_sql: str, general_rules: list[dict[str, Any]], tuning_examples: list[dict[str, Any]], last_error: str, retry_count: int) -> tuple[str, str]:
        prompt = self._build_prompt("TUNE_TOBE_SQL", current_tobe_sql=current_sql, universal_tuning_rules=self._serialize_general_rules(general_rules), tuning_examples_text=self._serialize_tuning_examples(tuning_examples), last_error=last_error or "None")
        map_id = self._map_id(job)
        self._log_prompt(map_id, "TUNE_TOBE_SQL_PROMPT", prompt, retry_count)
        raw, _ = self._call_llm_text(prompt, llm_config, system="You tune Oracle/MyBatis SQL without changing semantics.")
        return self._parse_tuning_response(raw)

    def _generate_tuned_test_sql(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], to_sql: str, tuned_sql: str, bind_set: Any, last_error: str, retry_count: int) -> str:
        prompt = self._build_prompt("TUNED_TEST_SQL", baseline_tobe_sql=to_sql, tuned_sql=tuned_sql, tobe_schema=str(db_config.get("target_schema") or os.getenv("ORACLE_SCHEMA_TGT") or "UNKNOWN").strip().upper(), bind_set_json=self._load_bind_sets_json(bind_set), last_error=last_error or "None")
        map_id = self._map_id(job)
        self._log_prompt(map_id, "TUNED_TEST_SQL_PROMPT", prompt, retry_count)
        raw, _ = self._call_llm_text(prompt, llm_config, system="You generate Oracle SQL validation queries.")
        test_sql = self._clean_generated_sql(raw)
        if not test_sql:
            raise ValueError("TUNED_TEST_SQL generation returned empty SQL")
        return test_sql

    def _parse_tuning_response(self, raw: str) -> tuple[str, str]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json|sql)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None
        if isinstance(parsed, dict):
            tuned_sql = str(parsed.get("tuned_sql") or parsed.get("sql") or parsed.get("TUNED_TO_SQL") or "").strip()
            tuned_result = str(parsed.get("tuned_result") or parsed.get("result") or "").strip() or "TUNING APPLIED"
            return tuned_sql, tuned_result
        return self._clean_generated_sql(text), "TUNING APPLIED"

    def _finish_failure(self, payload: dict[str, Any], job: dict[str, Any], db_config: dict[str, Any], started: float, status: str, message: str, attempts: list[dict[str, Any]] | None = None, partial_values: dict[str, Any] | None = None, tuning_guides: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        failure_attempts = attempts or [{"attempt": 1, "stage": self._failure_stage(status), "status": status, "reason": message}]
        if db_config and job.get("row_id"):
            update_values = {key: value for key, value in (partial_values or {}).items() if value not in (None, "")}
            update_values.update({"STATUS_TUNING": status, "TUNED_RESULT": str((partial_values or {}).get("TUNED_RESULT") or message)[:4000], "LOG": f"FINAL FAILURE stage=SQL_TUNING status={status} error={message}", "RETRY_COUNT": self._configured_retry_limit()})
            self._update_row(db_config, str(job["row_id"]), update_values)
            logging.getLogger("smartmigrate.workflow").error(message, extra={"workflow_log": [self._map_id(job), "SQL_TUNING", "SQL_TUNING", "ERROR", self._failure_stage(status), status, max(0, len(failure_attempts) - 1), update_values.get("TUNED_TO_SQL") or ""]})
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=failure_attempts,
            message=message,
            extra={"status_tuning": status, "tuning_status": status, "tuned_to_sql": (partial_values or {}).get("TUNED_TO_SQL"), "tuned_result": (partial_values or {}).get("TUNED_RESULT") or message, "tuning_guides": list(tuning_guides or []), "next_node": self._dashboard_node(payload)},
        )

    def _pass_through(self, *, payload: dict[str, Any], job: dict[str, Any], started: float, status: str, message: str) -> dict[str, Any]:
        return self._result(payload=payload, job=job, ok=False, status=status, elapsed=time.perf_counter() - started, attempts=[], message=message, extra={"tuning_skipped": True, "next_node": self._dashboard_node(payload)})

    def _component_pass_through(self, payload: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        result = {**payload, "component": "15C_sqlTuningOneJobPocExecutor", "ok": bool(payload.get("ok", True)), "status": payload.get("status") or "PASS-THROUGH", "elapsed_seconds": round(elapsed, 3), "attempt_count": int(payload.get("attempt_count") or 0), "attempts": list(payload.get("attempts") or []), "job_index": index, "total_jobs": total, "completed_count": index, "remaining_count": max(total - index, 0), "stages": dict(payload.get("stages") or {}), "component_pass_through": True, "pass_through_component": "15C", "message": payload.get("message") or message, "next_node": "17C_sqlFormattingOneJobPocExecutor"}
        history = list(result.get("history") or [])
        history.append({"step": "15C_pass_through", "message": message})
        result["history"] = history
        return result

    def _result(self, *, payload: dict[str, Any], job: dict[str, Any], ok: bool, status: str, elapsed: float, attempts: list[dict[str, Any]], message: str, extra: dict[str, Any]) -> dict[str, Any]:
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        completed = min(index, total)
        stages = dict(payload.get("stages") or {})
        if not extra.get("tuning_skipped"):
            stages["tuning"] = {"ok": ok, "status": status, "message": message, "attempts": attempts, "tuned_result": extra.get("tuned_result"), "matched_rule_ids": extra.get("matched_rule_ids") or []}
        return {
            **payload,
            **extra,
            "component": "15C_sqlTuningOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_TUNING",
            "job_type": "SQL",
            "row_id": job.get("row_id") or payload.get("row_id"),
            "space_nm": job.get("space_nm") or payload.get("space_nm"),
            "sql_id": job.get("sql_id") or payload.get("sql_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "generated_sql_list": self._generated_sql_list(payload, job, extra),
            "db_status_updated": bool(job.get("row_id")) and not extra.get("tuning_skipped"),
        }

    def _generated_sql_list(self, payload: dict[str, Any], job: dict[str, Any], extra: dict[str, Any]) -> list[dict[str, Any]]:
        result = [dict(item) for item in payload.get("generated_sql_list") or [] if isinstance(item, dict)]
        row_id = job.get("row_id") or payload.get("row_id")
        if str(extra.get("tuned_to_sql") or "").strip():
            result.append({"table": "NEXT_SQL_INFO", "row_id": row_id, "column": "TUNED_TO_SQL", "source_component": "15C_sqlTuningOneJobPocExecutor"})
        return self._dedupe_generated_sql_list(result)

    def _dedupe_generated_sql_list(self, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in values:
            key = (str(item.get("table") or "").upper(), str(item.get("row_id") or ""), str(item.get("key_value") or ""), str(item.get("column") or "").upper())
            if key in seen or not key[-1]:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _load_sql_job(self, db_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        aliases = [
            ("TAG_KIND", "tag_kind", "VARCHAR2(100)"),
            ("SPACE_NM", "space_nm", "VARCHAR2(4000)"),
            ("SQL_ID", "sql_id", "VARCHAR2(4000)"),
            ("TO_SQL", "to_sql", "CLOB"),
            ("TUNED_TO_SQL", "tuned_to_sql", "CLOB"),
            ("STATUS_CONVERSION", "status_conversion", "VARCHAR2(100)"),
            ("STATUS_TUNING", "status_tuning", "VARCHAR2(100)"),
            ("BIND_SET", "bind_set", "CLOB"),
            ("TARGET_TABLE", "target_table", "VARCHAR2(4000)"),
            ("TUNED_RESULT", "tuned_result", "VARCHAR2(4000)"),
            ("FORMATTED_SQL", "formatted_sql", "CLOB"),
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
                raise ValueError("SQL tuning item requires row_id or space_nm+sql_id")
            where_sql = "TO_CHAR(SPACE_NM) = :space_nm AND TO_CHAR(SQL_ID) = :sql_id"
            params = {"space_nm": space_nm, "sql_id": sql_id}
        order_expr = "UPD_TS NULLS FIRST" if "UPD_TS" in columns else "ROWID"
        query = f"SELECT {select_sql} FROM {table} WHERE {where_sql} ORDER BY {order_expr}"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_SQL_INFO row not found: space_nm={payload.get('space_nm')}, sql_id={payload.get('sql_id')}")
            keys = ["row_id", *[alias for _, alias, _ in aliases]]
            loaded = {key: self._lob_to_str(row[index]) for index, key in enumerate(keys)}
        return {**payload, **loaded}

    def _update_row(self, db_config: dict[str, Any], row_id: str, values: dict[str, Any]) -> None:
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
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {', '.join(set_clauses)} WHERE ROWID = CHARTOROWID(:rid)", params)
            conn.commit()

    def _update_block_rag_content(self, db_config: dict[str, Any], row_id: str, tuning_examples: list[dict[str, Any]]) -> None:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        if "BLOCK_RAG_CONTENT" in self._table_columns(db_config, table):
            self._update_row(db_config, row_id, {"BLOCK_RAG_CONTENT": self._serialize_tuning_examples(tuning_examples)})

    def _increment_batch_count(self, db_config: dict[str, Any], row_id: str) -> None:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if "BATCH_CNT" not in columns:
            return
        set_clause = "BATCH_CNT = NVL(BATCH_CNT, 0) + 1"
        if "UPD_TS" in columns:
            set_clause += ", UPD_TS = CURRENT_TIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE ROWID = CHARTOROWID(:1)", [row_id])
            conn.commit()

    def _mark_running_status(self, db_config: dict[str, Any], row_id: str, status: str, message: str, retry_count: int = 0) -> None:
        self._update_row(db_config, row_id, {"STATUS_TUNING": status, "LOG": f"RUNNING stage=SQL_TUNING status={status} message={message}", "RETRY_COUNT": retry_count})

    def _increment_rag_hits(self, db_config: dict[str, Any], examples: list[dict[str, Any]]) -> None:
        rule_ids = sorted({match["rule_id"] for block in examples for match in block.get("top_rule_matches", []) if match.get("rule_id")})
        if not rule_ids:
            return
        table = self._qualify(os.getenv("RAG_INFO_TABLE", "NEXT_MIG_RAG_INFO"), db_config.get("system_schema"))
        try:
            columns = self._table_columns(db_config, table)
            if "HIT_CNT" not in columns:
                return
            set_clause = "HIT_CNT = NVL(HIT_CNT, 0) + 1"
            if "UPDATED_AT" in columns:
                set_clause += ", UPDATED_AT = SYSTIMESTAMP"
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.executemany(f"UPDATE {table} SET {set_clause} WHERE TO_CHAR(RAG_ID) = :rule_id AND UPPER(TRIM(RULE_TYPE)) = 'SEARCH'", [{"rule_id": rule_id} for rule_id in rule_ids])
                conn.commit()
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(f"RAG HIT_CNT update skipped: {type(exc).__name__}: {exc}", extra={"workflow_log": [0, "SQL_TUNING", "RAG_HIT", "WARN", "HIT_CNT", "SKIP", 0]})

    def _build_prompt(self, template_name: str, **values: str) -> str:
        return SQL_PROMPT_TEMPLATES[template_name].format_map(_PromptValues(values)) + SQL_OUTPUT_FORMATTING_GUIDE

    def _log_prompt(self, map_id: str, step_name: str, prompt: str, retry_count: int) -> None:
        logging.getLogger("smartmigrate.workflow").info(f"{step_name} assembled", extra={"workflow_log": [map_id, "SQL_TUNING", "PROMPT_BUILD", "INFO", step_name, "PASS", retry_count, prompt]})

    def _call_llm_text(self, prompt: str, config: dict[str, Any], system: str = "You generate Oracle/MyBatis SQL.") -> tuple[str, str]:
        api_key = str(config.get("llm_api_key") or os.getenv("LLM_API_KEY") or os.getenv("OPEN_API_KEY") or "").strip()
        base_url = str(config.get("llm_base_url") or os.getenv("LLM_BASE_URL") or "").strip()
        model = str(config.get("llm_model") or os.getenv("LLM_MODEL") or "GLM-5.1").strip()
        if not api_key:
            raise ValueError("LLM API key is required for SQL tuning")
        provider = str(config.get("llm_provider") or os.getenv("LLM_PROVIDER") or "").strip().lower()
        if not provider:
            provider = "anthropic" if "anthropic" in base_url.lower() or model.lower().startswith("claude") else "openai"
        candidates = list(dict.fromkeys([model, *[item.strip() for item in str(config.get("llm_fallback_models") or os.getenv("LLM_FALLBACK_MODELS") or "").split(",") if item.strip()]]))
        for index, candidate in enumerate(candidates):
            try:
                if provider == "anthropic":
                    from anthropic import Anthropic

                    response = Anthropic(api_key=api_key, base_url=(base_url or "https://api.anthropic.com").rstrip("/"), timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).messages.create(model=candidate, max_tokens=self._positive_int(config.get("llm_max_tokens"), 8192), temperature=0, system=system, messages=[{"role": "user", "content": prompt}])
                    content = "".join(str(getattr(item, "text", "")) for item in response.content).strip()
                elif not base_url:
                    from openai import OpenAI

                    response = OpenAI(api_key=api_key, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).chat.completions.create(model=candidate, temperature=0, max_tokens=self._positive_int(config.get("llm_max_tokens"), 8192), messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
                    content = str(response.choices[0].message.content or "").strip()
                else:
                    root = base_url.rstrip("/")
                    url = root if root.endswith("/chat/completions") else f"{root}/chat/completions"
                    request = urllib.request.Request(url, data=json.dumps({"model": candidate, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0, "max_tokens": self._positive_int(config.get("llm_max_tokens"), 8192)}).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
                    with urllib.request.urlopen(request, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    content = str((((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
                if content:
                    return content, candidate
                raise ValueError("LLM returned empty message content")
            except Exception:
                if index == len(candidates) - 1:
                    raise
        raise ValueError("LLM call failed")

    def _embed_texts(self, texts: list[str], rag_config: dict[str, Any]) -> list[list[float]]:
        endpoint = str(rag_config.get("rag_embed_base_url") or os.getenv("RAG_EMBED_BASE_URL") or "").strip().rstrip("/")
        if not endpoint:
            raise ValueError("RAG_EMBED_BASE_URL is required for vector retrieval")
        if not endpoint.endswith("/embeddings"):
            endpoint = f"{endpoint}/embeddings" if endpoint.endswith("/v1") else f"{endpoint}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        api_key = str(rag_config.get("rag_embed_api_key") or os.getenv("RAG_EMBED_API_KEY") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint, data=json.dumps({"model": rag_config.get("rag_embed_model") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3", "input": texts}).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self._positive_int(rag_config.get("rag_embed_timeout_seconds"), 60)) as response:
            body = json.loads(response.read().decode("utf-8"))
        if isinstance(body.get("data"), list):
            vectors = [[float(value) for value in item["embedding"]] for item in body["data"] if isinstance(item, dict) and isinstance(item.get("embedding"), list)]
        elif isinstance(body.get("embeddings"), list):
            vectors = [[float(value) for value in item] for item in body["embeddings"] if isinstance(item, list)]
        elif isinstance(body.get("embedding"), list):
            vectors = [[float(value) for value in body["embedding"]]]
        else:
            vectors = []
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match request count")
        return vectors

    def _execute_test_query(self, db_config: dict[str, Any], sql: str) -> list[dict[str, Any]]:
        clean_sql = self._runtime_sql(sql, "EXECUTE_TUNED_TEST_SQL")
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(clean_sql)
            columns = [item[0] for item in cur.description] if cur.description else []
            return [{column: self._lob_to_str(value) for column, value in zip(columns, row)} for row in cur.fetchall()]

    def _runtime_sql(self, sql: str, stage: str) -> str:
        clean_sql = str(sql or "").strip().rstrip(";").strip()
        if not clean_sql:
            raise ValueError(f"{stage} SQL is empty")
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

    def _evaluate_test_rows(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            raise ValueError("TUNED_TEST_SQL returned no rows")
        for row in rows:
            values = {str(key).lower(): value for key, value in row.items()}
            if not {"case_no", "from_count", "to_count"}.issubset(values):
                raise ValueError("TUNED_TEST_SQL must return CASE_NO, FROM_COUNT, TO_COUNT columns")
            try:
                from_count, to_count = int(values["from_count"]), int(values["to_count"])
            except (TypeError, ValueError) as exc:
                raise ValueError("TUNED_TEST_SQL count columns must be numeric") from exc
            if (from_count == 0 and to_count == 0) or from_count != to_count:
                raise ValueError(f"TUNED_TEST_SQL row count mismatch: {row}")
        return "PASS"

    def _load_bind_sets_json(self, bind_set: Any) -> str:
        if isinstance(bind_set, str):
            try:
                parsed = json.loads(bind_set or "[]")
            except Exception:
                parsed = []
        else:
            parsed = bind_set
        if not isinstance(parsed, list):
            parsed = []
        if not parsed:
            parsed = [{}]
        return json.dumps(parsed, ensure_ascii=False, default=str)

    def _serialize_general_rules(self, rules: list[dict[str, Any]]) -> str:
        lines = []
        for rule in rules:
            lines.append(f"- RAG_ID={rule.get('rule_id')} SOURCE_TABLES={','.join(rule.get('source_tables') or []) or 'ALL'}")
            lines.extend(f"  GUIDANCE: {guide}" for guide in rule.get("guidance") or [])
        return "\n".join(lines) if lines else "- (empty)"

    def _serialize_tuning_examples(self, examples: list[dict[str, Any]]) -> str:
        lines = []
        for block in examples:
            for match in block.get("top_rule_matches", []):
                lines.append(f"- BLOCK={block.get('block_id')} SCORE={match.get('score')} RULE_ID={match.get('rule_id')}")
                if match.get("guidance"):
                    lines.extend(f"  GUIDANCE: {guide}" for guide in match["guidance"])
                lines.append(f"  BAD_SQL: {match.get('source_sql') or ''}")
                lines.append(f"  TUNED_SQL: {match.get('target_sql') or ''}")
        return "\n".join(lines) if lines else "- (empty)"

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
        return [{"block_id": "MAIN_SQL", "block_type": "MAIN", "sql": main_sql, "normalized_sql": self._normalize_sql_shape(main_sql)}, *[{"block_id": placeholder, "block_type": "SUBQUERY", "sql": inner, "normalized_sql": self._normalize_sql_shape(inner)} for _, _, placeholder, inner in replacements]]

    def _normalize_sql_shape(self, sql_text: str) -> str:
        text = re.sub(r"/\*.*?\*/|--[^\n]*", " ", str(sql_text or ""), flags=re.S)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip().upper()

    def _normalize_compare_sql(self, sql_text: str) -> str:
        return re.sub(r"\s+", " ", self._clean_generated_sql(sql_text)).strip().upper()

    def _clean_generated_sql(self, value: str) -> str:
        sql = str(value or "").strip()
        code_block = re.search(r"```(?:sql)?\s*(.*?)```", sql, flags=re.I | re.S)
        if code_block:
            sql = code_block.group(1).strip()
        starts = [match for pattern in (r"\b(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|WITH)\b", r"<\s*(?:script|select|insert|update|delete|if|choose|when|otherwise|where|trim|foreach)\b") if (match := re.search(pattern, sql, flags=re.I))]
        if starts:
            sql = sql[min(starts, key=lambda item: item.start()).start():].strip()
        while True:
            wrapper = re.match(r"^<\s*(script|select|insert|update|delete)\b[^>]*>", sql, flags=re.I | re.S)
            if not wrapper:
                break
            tag = wrapper.group(1)
            sql = re.sub(rf"</\s*{re.escape(tag)}\s*>\s*$", "", sql[wrapper.end():].strip(), flags=re.I).strip()
        return sql.rstrip(";").strip()

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

    def _rule_embedding_text(self, rule: dict[str, Any]) -> str:
        return "\n".join([str(rule.get("normalized_source_sql") or ""), str(rule.get("source_sql") or "")]).strip()

    def _lexical_similarity(self, left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[A-Z_]+|\d+", left.upper()))
        right_tokens = set(re.findall(r"[A-Z_]+|\d+", right.upper()))
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0

    def _rag_match_summary(self, blocks: list[dict[str, str]], matches_by_block: list[list[tuple[dict[str, Any], float]]]) -> str:
        parts: list[str] = []
        for block, matches in zip(blocks, matches_by_block):
            if not matches:
                parts.append(f"{block.get('block_id')}:none")
                continue
            matched = ",".join(f"{rule.get('rule_id')}:{round(float(score), 4)}" for rule, score in matches[:5])
            parts.append(f"{block.get('block_id')}:{matched}")
        return "; ".join(parts)[:3500]

    def _should_run_tuning(self, payload: dict[str, Any]) -> bool:
        return self._job_name(payload) in {"conversion", "tuning"}

    def _job_name(self, payload: dict[str, Any]) -> str:
        value = str(payload.get("job_name") or "").strip().lower()
        if value:
            return value
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()
        return {"MIG": "migration", "SQL_CONVERSION": "conversion", "SQL_TUNING": "tuning", "SQL_FORMATTING": "formatting"}.get(route, "")

    def _status(self, value: Any) -> str:
        return str(value or "").strip().upper()

    def _is_conversion_pass(self, value: Any) -> bool:
        return self._status(value) in CONVERSION_SUCCESS_STATUSES

    def _is_no_tuning_result(self, value: Any) -> bool:
        return "NO TUNING" in str(value or "").upper()

    def _dashboard_node(self, payload: dict[str, Any]) -> str:
        if payload.get("full_workflow"):
            return "17C_sqlFormattingOneJobPocExecutor"
        route = str(payload.get("job_route") or "").upper()
        if route == "SQL_CONVERSION":
            return "12D_sqlConversionIterationDashboard"
        return "15D_sqlTuningIterationDashboard"

    def _failure_stage(self, status: str) -> str:
        return "VALIDATE_TUNED_SQL" if status == FAIL_TEST else "APPLY_TUNING_RULES"

    def _map_id(self, job: dict[str, Any]) -> str:
        return f"{job.get('sql_id') or ''} / {job.get('space_nm') or ''}"[:100]

    def _max_retry(self) -> int:
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(1, min(11, int(getattr(self, "_payload_max_retry") or 0) + 1))
        return max(1, min(11, int(getattr(self, "max_retry", None) or 2) + 1))

    def _configured_retry_limit(self) -> int:
        if getattr(self, "_payload_max_retry", None) is not None:
            return max(0, min(10, int(getattr(self, "_payload_max_retry") or 0)))
        return max(0, min(10, int(getattr(self, "max_retry", None) or 2)))

    def _tuning_iterations(self) -> int:
        return max(1, min(5, int(getattr(self, "tuning_iterations", None) or 1)))

    def _select_expr(self, columns: set[str], column: str, alias: str, data_type: str) -> str:
        if column in columns:
            return f"{column} AS {alias}"
        if data_type.upper() == "CLOB":
            return f"TO_CLOB(NULL) AS {alias}"
        return f"CAST(NULL AS {data_type}) AS {alias}"

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
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
        import oracledb

        dsn = oracledb.makedsn(str(db_config.get("db_host") or "").strip(), int(db_config.get("db_port") or 1521), service_name=str(db_config.get("db_service_name") or "").strip())
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

    def _llm_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("llm_config") or {})
        return {
            "llm_base_url": str(getattr(self, "llm_base_url", "") or item_config.get("llm_base_url") or "").strip(),
            "llm_api_key": self._secret_to_str(getattr(self, "llm_api_key", None)) or str(item_config.get("llm_api_key") or "").strip(),
            "llm_provider": str(getattr(self, "llm_provider", "") or item_config.get("llm_provider") or "").strip(),
            "llm_model": str(getattr(self, "llm_model", "") or item_config.get("llm_model") or "").strip(),
            "llm_fallback_models": str(getattr(self, "llm_fallback_models", "") or item_config.get("llm_fallback_models") or "").strip(),
            "llm_max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None) or item_config.get("llm_max_tokens"), 8192),
            "llm_timeout_seconds": self._positive_int(getattr(self, "llm_timeout_seconds", None) or item_config.get("llm_timeout_seconds"), 900),
        }

    def _rag_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("rag_config") or {})
        return {
            "rag_embed_base_url": str(getattr(self, "rag_embed_base_url", "") or item_config.get("rag_embed_base_url") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "rag_embed_api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(item_config.get("rag_embed_api_key") or os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "rag_embed_model": str(getattr(self, "rag_embed_model", "") or item_config.get("rag_embed_model") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "rag_embed_timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or item_config.get("rag_embed_timeout_seconds") or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }

    def _milvus_config(self) -> dict[str, Any]:
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "rag_collection": self._clean_collection_name(getattr(self, "rag_collection_name", "") or os.getenv("MILVUS_RAG_COLLECTION") or "SM_RAG_RULES"),
        }

    def _milvus_client(self) -> Any:
        # Milvus 2.6.5 SDK connection. The URI is passed exactly as entered in
        # Langflow/env; do not split host/port or rewrite it before calling SDK.
        from pymilvus import MilvusClient

        config = self._milvus_config()
        missing = [key for key in ("uri", "username", "password", "db_name") if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing Milvus config: {', '.join(missing)}")
        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    def _milvus_rag_entity(self, hit: Any) -> dict[str, Any]:
        entity = self._milvus_entity(hit)
        source_tables = self._source_tables(entity.get("source_tables") or "")
        return {
            "rule_id": str(entity.get("rag_id") or "").strip(),
            "category": str(entity.get("category") or "").strip(),
            "rule_type": str(entity.get("rule_type") or "").strip(),
            "source_tables": sorted(source_tables),
            "guidance": [line.strip() for line in str(entity.get("guidance_text") or "").splitlines() if line.strip()],
            "source_sql": str(entity.get("source_sql") or "").strip(),
            "target_sql": str(entity.get("target_sql") or "").strip(),
            "normalized_source_sql": self._normalize_sql_shape(entity.get("source_sql") or ""),
        }

    def _milvus_entity(self, hit: Any) -> dict[str, Any]:
        if isinstance(hit, dict):
            entity = hit.get("entity") or hit.get("fields") or hit
            return dict(entity) if isinstance(entity, dict) else {}
        entity = getattr(hit, "entity", None) or getattr(hit, "fields", None)
        if isinstance(entity, dict):
            return dict(entity)
        if hasattr(hit, "to_dict"):
            data = hit.to_dict()
            entity = data.get("entity") or data.get("fields") or data
            return dict(entity) if isinstance(entity, dict) else {}
        return {}

    def _milvus_score(self, hit: Any) -> float:
        if isinstance(hit, dict):
            value = hit.get("distance", hit.get("score", 0.0))
        else:
            value = getattr(hit, "distance", getattr(hit, "score", 0.0))
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _source_tables_match(self, rule_tables: list[str] | set[str], source_tables: set[str]) -> bool:
        rule_set = set(rule_tables)
        return not rule_set or not source_tables or bool(rule_set & source_tables)

    def _clean_collection_name(self, value: Any) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
            raise ValueError(f"Invalid Milvus collection name: {clean}")
        return clean

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
            "target_schema": str(item_config.get("target_schema") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"15C SQL Tuning is not connected to database settings: missing {', '.join(missing)}")

    def _qualify(self, table_name: str, schema: Any) -> str:
        clean_table = self._clean_identifier(table_name)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

    def _lob_to_str(self, value: Any) -> str:
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
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
