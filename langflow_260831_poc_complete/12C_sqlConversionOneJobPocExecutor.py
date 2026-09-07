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
TUNED_FR_SQL_PRETUNING_MIN_LENGTH_DEFAULT = 8000
RAG_SEARCH = "SEARCH"
RAG_GENERAL = "GENERAL"

SQL_OUTPUT_FORMATTING_GUIDE = "\nSQL만 반환하십시오. 최종 공백/들여쓰기 정리는 17C에서 처리합니다."


class _PromptValues(dict):
    # Keep unknown prompt placeholders visible instead of raising KeyError.
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


SQL_PROMPT_TEMPLATES: dict[str, str] = {
    "TUNED_FR_SQL": """
당신은 Oracle/MyBatis FROM SQL 사전 튜닝 전문가입니다.

[목표]
TO_SQL, BIND_SQL, TEST_SQL 생성 전에 FROM SQL의 의미와 MyBatis 구조를 보존하면서 안전하게 단순화 또는 튜닝하십시오.

[현재 FROM SQL]
{current_from_sql}

[SQL_TUNING GENERAL RAG]
{universal_tuning_rules}

[SQL_TUNING SEARCH RAG 예시]
{tuning_examples_text}

[이전 오류]
{last_error}

[규칙]
- Oracle/MyBatis SQL 템플릿 하나만 반환하십시오.
- 설명, markdown, 구조화 wrapper object, PL/SQL block, 여러 SQL 문, SQL 끝 세미콜론을 출력하지 마십시오.
- SQL 의미, 테이블명, 컬럼명, alias, join 의미, MyBatis 동적 태그, #{{param}}/${{param}} bind parameter를 보존하십시오.
- 결과 무결성을 보장하는 전제하에 불필요한 중첩, 중복 join, 과다한 inline view, 비효율적 조건을 제거하여 쿼리 구조를 최적화하고 단순화하십시오.
- universal_tuning_rules는 NEXT_MIG_RAG_INFO의 SQL_TUNING GENERAL 필수 규칙입니다. 현재 SQL에 관련되면 우선 적용하십시오.
- searched tuning RAG 예시는 현재 FROM SQL에 관련될 때만 참고하십시오.
- 튜닝 규칙을 적용하더라도 원본 결과 set, 필터 의도, 집계 의도, join 의도, bind parameter 의미가 바뀌면 안 됩니다.
- 적용할 튜닝이 없으면 현재 FROM SQL을 그대로 반환하십시오.
- last_error가 있으면 원본 결과를 보존하는 최소 수정으로 오류를 우선 해결하십시오.
""".strip(),
    "TOBE_SQL": """
당신은 Oracle/MyBatis TO-BE SQL 변환 생성기입니다.

[목표]
FROM SQL의 결과 의미를 보존하면서 매핑 규칙, SQL_CONVERSION RAG, 타겟 스키마에 맞는 Oracle 19c TO-BE SQL 템플릿 하나를 생성하십시오.

[FROM SQL]
{from_sql}

[Mapping 및 RAG context]
{mapping_schema_text}

[Target Schema]
{target_schema}

[Correct SQL 힌트]
{correct_sql_hint_text}

[이전 오류]
{last_error}

[규칙]
- 모든 SQL은 Oracle 19c 문법에 맞게 생성하거나 수정하십시오.
- FR_TABLE이나 FR_COL 이름을 타겟명처럼 참조하지 말고, 변환 대상은 오직 타겟 구조와 매핑 규칙을 기준으로 작성하십시오.
- mapping rules는 테이블명과 컬럼명 변경의 우선 기준입니다.
- 소스 테이블이나 컬럼이 매핑룰과 매칭되지 않아도 원본 테이블명 또는 컬럼명을 그대로 유지하십시오. 이는 명칭이 바뀌지 않은 것으로 판단합니다.
- 가능한 한 원본 쿼리 구조, 필터 의도, 집계 의도, 조인 의도, alias, MyBatis 동적 태그, bind parameter 이름을 유지하십시오.
- MyBatis 바인딩 파라미터 태그 #{{param}}, ${{param}}는 제거하거나 값으로 치환하지 마십시오. 기존 parameter 이름과 marker 형식을 유지하십시오.
- 기존 MyBatis 동적 태그 <if>, <choose>, <when>, <otherwise>, <where>, <trim>, <foreach> 구조는 유지하고, 매핑룰상 필요한 테이블명, 컬럼명, 별칭, SQL 표현식만 변경하십시오.
- TO-BE SQL에서 실제 타겟 물리 테이블은 반드시 target_schema.TABLE_NAME 형식으로 작성하십시오.
- FROM SQL에 있던 모든 물리 테이블의 기존 스키마명은 제거하고 target_schema.TABLE_NAME 형식으로 교체하십시오.
- DUAL, CTE 이름, inline view alias, subquery alias, table alias에는 schema를 붙이지 마십시오.
- 동적 분기를 여러 SQL 문으로 분리하지 마십시오. 기존 MyBatis 동적 태그 구조 안에 그대로 유지하십시오.
- SQL_CONVERSION RAG 예시는 변환 패턴 힌트로만 사용하십시오. 매핑 규칙, 현재 FROM SQL, target_schema, last_error와 충돌하면 현재 입력을 우선하십시오.
- Correct SQL 힌트는 참고용으로만 사용하고, 현재 입력과 충돌하면 현재 입력을 우선하십시오.
- 실행 가능한 Oracle/MyBatis SQL 템플릿 하나만 반환하십시오.
- 설명, markdown, 구조화 wrapper object, PL/SQL block, 여러 SQL 문, SQL 끝 세미콜론을 출력하지 마십시오.
""".strip(),
    "BIND_SQL": """
당신은 Oracle Bind SQL 후보 생성기입니다.

[목표]
FROM SQL에서 MyBatis bind parameter 값을 검증용으로 추출할 수 있는, 바로 실행 가능한 Oracle SELECT 문 하나를 생성하십시오.

[FROM SQL]
{from_sql}

[FROM Schema]
{from_schema}

[AS-IS Source Filter Conditions]
{asis_source_filter_conditions}

[Correct SQL 힌트]
{correct_sql_hint_text}

[이전 오류]
{last_error}

[규칙]
- Bind parameter는 반드시 FROM SQL로부터 생성하십시오.
- Oracle 19c에서 바로 실행 가능한 SELECT 문 하나만 반환하십시오.
- 생성된 Bind SQL에는 MyBatis 태그와 bind parameter가 없어야 합니다.
- 설명, markdown, 구조화 wrapper object, PL/SQL block, 여러 SQL 문, SQL 끝 세미콜론을 출력하지 마십시오.
- 출력 컬럼은 동일 SELECT 절에 모두 출력되어야 합니다.
- 각 output column alias는 MyBatis parameter 이름과 정확히 일치해야 하며 반드시 double quote로 감싸십시오.
- 가능하면 SELECT DISTINCT를 사용해 중복 후보를 줄이고, main SQL의 ORDER BY는 제거하십시오.
- bind parameter가 전혀 없다고 판단되면 정확히 SELECT 1 AS "no bind" FROM DUAL 을 반환하십시오.
- SELECT list에서 조건을 억지로 구현하거나 parameter 값을 계산, 합성하지 마십시오.
- 다음 형태는 금지합니다: SELECT "id" AS "id", SELECT #{{id}} AS "id", <foreach> 태그가 남은 SQL.
- 원본 SQL에 ID_CD = #{{id}}가 있으면 #{{id}}를 출력하는 SQL이 아니라 id와 비교되는 대상 컬럼 ID_CD를 찾아 SELECT ID_CD AS "id" 형태로 출력하십시오.
- 각 output column은 실제 FROM 절 테이블의 컬럼 또는 해당 컬럼에서 유도된 원본 alias를 기반으로 작성하십시오.
- 실제 대상 컬럼이 있으면 임의 literal 값을 절대 넣지 마십시오.
- bind parameter를 사용하는 컬럼이나 테이블이 alias라면 해당 alias의 원본 컬럼이나 테이블을 기반으로 값을 반환하십시오.
- output 컬럼의 테이블과 inner join 관계는 누락하지 마십시오.
- 동적 태그 밖의 모든 필수 parameter를 추출했는지 확인하십시오.
- 원본 SQL의 논리적 구조상 값이 반드시 존재해야 하는 필수 parameter는 해당 컬럼이 NULL인 행을 제외하도록 WHERE 절에 IS NOT NULL 조건을 추가하십시오.
- parameter와 관련된 테이블 간 join 조건과 filtering 조건은 유지하십시오.
- asis_source_filter_conditions는 현재 source SQL에 매칭된 mapping rule의 NEXT_MIG_INFO.CONDITION 값입니다. 관련 있는 동일 FR_TABLE/source scope 후보 row source에 적용하십시오.
- FROM schema는 모든 물리 테이블에 붙이십시오. FROM SQL에 이미 schema가 붙어 있는 물리 테이블도 기존 schema를 제거하고 from_schema.TABLE_NAME 형식으로 다시 붙이십시오.
- CTE 이름, inline view alias, subquery alias, table alias, DUAL에는 schema를 붙이지 마십시오.
- <foreach> 태그만 포함된 block 또는 SYSDATE만 포함된 MyBatis 조건문 block은 선처리하여 제거하십시오.
- SYSDATE, CURRENT_DATE, SYSTIMESTAMP, CURRENT_TIMESTAMP, TRUNC(SYSDATE), ADD_MONTHS(SYSDATE, ...), LAST_DAY(SYSDATE)가 포함된 block은 선처리하여 제거하십시오.
- 최종 Bind SQL에는 <if>, <choose>, <when>, <otherwise>, <where>, <trim>, <foreach> 태그가 남으면 안 됩니다.
- 최종 Bind SQL에는 #{{param}}, ${{param}}, :param, ?, {{{{param}}}} 같은 미해결 parameter 표현이 남으면 안 됩니다.
- <foreach> 태그 안에 있는 bind parameter는 추출하지 마십시오.
- <choose>, <when>, <otherwise>는 첫 번째 <when> branch를 사용하는 것을 기본으로 하십시오.
- #month parameter 관련 block은 parameter로 선정하지 마십시오.
- ROWNUM 함수를 활용하여 #{{firstRow}}, #{{lastRow}} 등의 parameter를 입력받는 경우는 예: SELECT '1' AS "firstRow", '100' AS "lastRow" FROM DUAL 처럼 임의 숫자 문자열을 출력할 수 있습니다.
- Correct SQL 힌트는 bind 추출 방식과 row source 구성 방식 참고용으로만 사용하고, 현재 FROM SQL과 맞지 않는 테이블/컬럼/조건은 그대로 복사하지 마십시오.
- last_error가 있으면 이전 오류를 우선 해결하십시오.
""".strip(),
    "TEST_SQL": """
당신은 Oracle SQL Conversion 검증 쿼리 생성기입니다.

[목표]
각 bind case별로 FROM SQL과 TO-BE SQL의 row count를 비교하는 실행 가능한 Oracle SELECT 문 하나를 생성하십시오.

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

[Correct SQL 힌트]
{correct_sql_hint_text}

[이전 오류]
{last_error}

[규칙]
- Oracle 19c에서 실행 가능한 SELECT 문 하나만 반환하십시오.
- 설명, markdown, 구조화 wrapper object, PL/SQL block, 여러 SQL 문, SQL 끝 세미콜론을 출력하지 마십시오.
- 최종 SQL에는 MyBatis 바인딩 파라미터 태그와 동적 태그가 남으면 안 됩니다.
- 최종 컬럼은 CASE_NO, FROM_COUNT, TO_COUNT만 포함하십시오.
- 각 bind case마다 하나의 validation row를 만들고 여러 bind case는 UNION ALL로 연결하십시오.
- 각 case는 SELECT <case_no> AS CASE_NO, (<source_count_query>) AS FROM_COUNT, (<target_count_query>) AS TO_COUNT FROM DUAL 형태를 따르십시오.
- UNION ALL로 연결되는 각 SELECT block은 반드시 FROM DUAL로 끝나야 합니다.
- FROM SQL은 from_schema, TO-BE SQL은 tobe_schema를 사용하십시오.
- FROM SQL의 물리 테이블은 기존 schema가 있더라도 제거하고 from_schema.TABLE_NAME 형식으로 다시 붙이십시오.
- TO-BE SQL의 물리 테이블은 기존 schema가 있더라도 제거하고 tobe_schema.TABLE_NAME 형식으로 다시 붙이십시오.
- CTE 이름, inline view alias, subquery alias, table alias, DUAL에는 schema를 붙이지 마십시오.
- FROM SQL과 TO-BE SQL의 의미를 최대한 보존하고 SELECT COUNT(*) FROM (<sql>) alias 형태로 감싸십시오.
- 이미 SELECT COUNT(*), SELECT COUNT(1), SELECT COUNT(column)처럼 단일 count 값을 반환하는 검증용 count query라면 다시 SELECT COUNT(*) FROM (<sql>)로 감싸지 마십시오.
- FROM SQL, TO-BE SQL에 있는 <choose> 태그, <foreach> 태그, SYSDATE가 포함된 block은 선처리하여 제거하십시오.
- bind set의 bind case 값을 사용해 FROM SQL, TO-BE SQL에 있는 MyBatis 바인딩 파라미터 태그를 Oracle literal로 치환하십시오.
- bind case 값을 기준으로 <if> 태그 조건을 평가하고 비활성화된 동적 태그 block은 제거하십시오.
- <if test="param != null"> 형태의 조건에서 bind case 값이 NULL이면 해당 <if> block 전체를 비활성으로 처리하고 최종 SQL에서 제거하십시오.
- <where>, <trim> 제거 후 WHERE/AND/OR 문법을 정리하십시오.
- <choose>, <when>, <otherwise>는 첫 번째 <when> branch를 사용하는 것을 기본으로 하십시오.
- bind parameter 자리에 숫자 데이터를 대입할 때는 작은따옴표로 감싸서 문자열 literal 형태로 대입하십시오.
- 최종 SQL에는 #{{param}}, ${{param}}, :param, ?, {{{{param}}}} 같은 미해결 parameter 표현이 남으면 안 됩니다.
- ORDER BY는 검증 SQL에서 제거하십시오. subquery, inline view, CTE 내부 ORDER BY도 제거하십시오.
- WITH 절에 정의된 CTE 이름은 물리 테이블이 아닙니다.
- #month 등 날짜 관련 parameter는 test SQL에서 제거하십시오.
- 생성된 검증 SQL문의 괄호 짝이 모두 올바르게 닫혔는지 검증하십시오.
- Correct SQL 힌트는 validation SQL 구성 방식과 count 비교 패턴 참고용으로만 사용하고, 현재 입력과 맞지 않는 테이블/컬럼/조건은 그대로 복사하지 마십시오.
- last_error가 있으면 이전 오류를 우선 해결하십시오.
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
        StrInput(name="milvus_uri", display_name="Milvus URI", required=False),
        StrInput(name="milvus_username", display_name="Milvus Username", required=False),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=False),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=False),
        StrInput(name="rag_collection_name", display_name="RAG Collection Name", value="SM_RAG_RULES", required=False),
        StrInput(name="correct_sql_collection_name", display_name="Correct SQL Collection Name", value="SM_CORRECT_SQL_CONVERSION", required=False),
        IntInput(name="rag_top_k", display_name="MIG RAG Top K", value=3, required=False),
        IntInput(name="correct_sql_top_k", display_name="Correct SQL Top K", value=1, required=False),
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
                self._mark_running_status(db_config, str(job["row_id"]), "STATUS_CONVERSION", "RUNNING", "SQL conversion started")

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
        source_tables = self._source_tables(target_table)
        sql_conversion_general_rules = self._load_rag_general_rules(db_config, "SQL_CONVERSION", source_tables, map_id)
        sql_conversion_examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_CONVERSION", source_sql, source_tables, map_id)
        correct_sql_hints = {
            **self._correct_sql_hints_text(db_config, source_sql, job.get("sql_id"), job.get("space_nm"), map_id, 0, tag_kind)
        }
        self._log_rag_context(map_id, "SQL_CONVERSION", sql_conversion_general_rules, sql_conversion_examples, 0)
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
            "source_tables": source_tables, "sql_conversion_general_rules": sql_conversion_general_rules,
            "sql_conversion_examples": sql_conversion_examples, "correct_sql_hints": correct_sql_hints,
            "max_retry": self._max_retry(), "attempt_no": 1, "retry_count": 0,
            "last_status": FAIL_TOBE, "last_message": "SQL conversion failed.",
            "to_sql": str(job.get("to_sql") or "").strip(),
            "bind_sql": str(job.get("bind_sql") or "").strip(),
            "bind_set": str(job.get("bind_set") or "") or None,
            "test_sql": str(job.get("test_sql") or "").strip(),
            # Capture the user-owned SQL before this run writes any generated values.
            "initial_user_edited_columns": self._initial_user_edited_columns(job),
            "tuned_fr_sql": str(job.get("tuned_fr_sql") or "").strip() or None,
            "sql_length": self._sql_length_kind(source_sql),
            "resume_stage": self._initial_resume_stage(job, tag_kind, str(job.get("to_sql") or "").strip(), str(job.get("bind_sql") or "").strip()),
            "status": "RUNNING",
        }
        final_state = self._run_conversion_graph(initial_state)
        result = final_state.get("result")
        if isinstance(result, dict):
            return result
        return self._finish_failure(payload, job, db_config, started, final_state.get("last_status") or FAIL_TOBE, final_state.get("last_message") or "SQL conversion failed", final_state.get("attempts") or [], partial_values=final_state)

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
            allow_pre_tuning = int(state["attempt_no"]) >= int(state["max_retry"]) and state.get("resume_stage") == "GENERATE_TOBE_SQL"
            before_tuned = state.get("tuned_fr_sql")
            try:
                source_for_conversion, tuned_fr_sql, sql_length = self._prepare_conversion_source(
                    state["job"], state["db_config"], state["llm_config"], state["rag_config"],
                    state["source_sql"], state["target_table"], state["map_id"], allow_generate=allow_pre_tuning,
                )
                next_state = {**state, "source_for_conversion": source_for_conversion, "tuned_fr_sql": tuned_fr_sql, "sql_length": sql_length, "node_failed": False}
                if allow_pre_tuning and tuned_fr_sql and not before_tuned and state.get("resume_stage") == "GENERATE_TOBE_SQL":
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
                reason = self._stage_reuse_reason(state, "TOBE_SQL")
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "REUSE_TOBE_SQL", "status": CONVERSION_PASS, "reason": reason})
                logger.info(f"TOBE_SQL reused ({reason})", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TOBE_SQL", "INFO", "REUSE_TOBE_SQL", "SUCCESS", state["retry_count"], f"{reason}\n\n{state.get('to_sql') or ''}"]})
                state["node_failed"] = False
                return state
            try:
                tobe_reuse_stage = "USE_USER_EDITED_TO_SQL" if str(state["job"].get("user_edited") or "").strip().upper() == "Y" and str(state["job"].get("to_sql") or "").strip() else "GENERATE_TOBE_SQL"
                to_sql = self._generate_tobe_sql(
                    state["job"], state["db_config"], state["llm_config"], state["rag_config"],
                    state["source_for_conversion"], state["mapping_rules"], state["target_table"], state.get("retry_context") or "", state["retry_count"],
                    state.get("sql_conversion_general_rules") or [], state.get("sql_conversion_examples") or [],
                    str((state.get("correct_sql_hints") or {}).get("TO_SQL") or "- (empty)"),
                )
                state["to_sql"] = to_sql
                state["node_failed"] = False
                state["last_status"] = ""
                state["last_message"] = ""
                attempt_entry = {"attempt": state["attempt_no"], "stage": tobe_reuse_stage, "status": CONVERSION_PASS, "sql_length": len(to_sql)}
                if tobe_reuse_stage == "USE_USER_EDITED_TO_SQL":
                    attempt_entry["reason"] = "USER_EDITED=Y; TO_SQL is not null"
                state["attempts"].append(attempt_entry)
                tobe_log_sql = f"USER_EDITED=Y; TO_SQL is not null\n\n{to_sql}" if tobe_reuse_stage == "USE_USER_EDITED_TO_SQL" else to_sql
                logger.info("TOBE_SQL completed", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "TOBE_SQL", "INFO", tobe_reuse_stage, "SUCCESS", state["retry_count"], tobe_log_sql]})
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
                reason = self._stage_reuse_reason(state, "BIND_SQL")
                state["attempts"].append({"attempt": state["attempt_no"], "stage": "REUSE_BIND_SQL", "status": CONVERSION_PASS, "reason": reason})
                logger.info(f"BIND_SQL reused ({reason})", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "BIND_SQL", "INFO", "REUSE_BIND_SQL", "SUCCESS", state["retry_count"], f"{reason}\n\n{state.get('bind_sql') or ''}"]})
                state["node_failed"] = False
                return state
            try:
                bind_reuse_stage = "USE_USER_EDITED_BIND_SQL" if str(state["job"].get("user_edited") or "").strip().upper() == "Y" and str(state["job"].get("bind_sql") or "").strip() else "GENERATE_BIND_SQL"
                bind_sql, bind_set = self._generate_bind_payload(
                    state["job"], state["db_config"], state["llm_config"], state["source_for_conversion"],
                    state["to_sql"], state["mapping_rules"], state.get("retry_context") or "", state["retry_count"],
                    str((state.get("correct_sql_hints") or {}).get("BIND_SQL") or "- (empty)"),
                )
                state.update({"bind_sql": bind_sql, "bind_set": bind_set, "resume_stage": "GENERATE_TEST_SQL", "last_status": "", "last_message": "", "node_failed": False})
                attempt_entry = {"attempt": state["attempt_no"], "stage": bind_reuse_stage, "status": CONVERSION_PASS}
                if bind_reuse_stage == "USE_USER_EDITED_BIND_SQL":
                    attempt_entry["reason"] = "USER_EDITED=Y; BIND_SQL is not null"
                state["attempts"].append(attempt_entry)
                bind_log_sql = f"USER_EDITED=Y; BIND_SQL is not null\n\n{bind_sql}" if bind_reuse_stage == "USE_USER_EDITED_BIND_SQL" else bind_sql
                logger.info("BIND_SQL completed", extra={"workflow_log": [state["map_id"], "SQL_CONVERSION", "BIND_SQL", "INFO", bind_reuse_stage, "SUCCESS", state["retry_count"], bind_log_sql]})
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
                test_reuse_stage = "USE_USER_EDITED_TEST_SQL" if str(state["job"].get("user_edited") or "").strip().upper() == "Y" and str(state["job"].get("test_sql") or "").strip() else "GENERATE_TEST_SQL"
                test_sql = self._generate_test_sql(
                    state["job"], state["db_config"], state["llm_config"], state["source_sql"],
                    state["to_sql"], state.get("bind_set"), state.get("retry_context") or "", state["retry_count"],
                    str((state.get("correct_sql_hints") or {}).get("TEST_SQL") or "- (empty)"),
                )
                state["test_sql"] = test_sql
                self._update_row(state["db_config"], state["job"]["row_id"], {"TEST_SQL": test_sql})
                test_rows = self._execute_test_query(state["db_config"], test_sql)
                self._evaluate_test_rows(test_rows)
                state.update({"test_sql": test_sql, "status": CONVERSION_PASS, "node_failed": False})
                attempt_entry = {"attempt": state["attempt_no"], "stage": test_reuse_stage, "status": CONVERSION_PASS}
                if test_reuse_stage == "USE_USER_EDITED_TEST_SQL":
                    attempt_entry["reason"] = "USER_EDITED=Y; TEST_SQL is not null"
                state["attempts"].append(attempt_entry)
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
            user_edited = str(state["job"].get("user_edited") or "").strip().upper() == "Y"
            running_status = f"RUNNING-{state.get('last_status') or FAIL_TOBE}"
            self._mark_running_status(
                state["db_config"],
                str(state["job"]["row_id"]),
                "STATUS_CONVERSION",
                running_status,
                state.get("last_message") or "",
                next_attempt - 1,
            )
            next_state = {
                **state,
                "attempt_no": next_attempt,
                "retry_count": next_attempt - 1,
                "retry_context": f"RETRY_CONTEXT: attempt={next_attempt}/{state['max_retry']}; FINAL_RETRY_MODE={final_retry_mode}; last_error={state.get('last_message') or ''}",
                "resume_stage": state.get("resume_stage") or "GENERATE_TOBE_SQL",
                "status": "RUNNING",
                "node_failed": False,
            }
            if user_edited:
                next_state = self._reset_unprotected_user_edited_sql(next_state)
            return next_state

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

    def _initial_user_edited_columns(self, job: dict[str, Any]) -> set[str]:
        if str(job.get("user_edited") or "").strip().upper() != "Y":
            return set()
        return {
            column
            for column, key in (("TO_SQL", "to_sql"), ("BIND_SQL", "bind_sql"), ("TEST_SQL", "test_sql"))
            if str(job.get(key) or "").strip()
        }

    def _reset_unprotected_user_edited_sql(self, state: dict[str, Any]) -> dict[str, Any]:
        protected = set(state.get("initial_user_edited_columns") or [])
        if "TO_SQL" not in protected:
            return {**state, "to_sql": "", "bind_sql": "", "bind_set": None, "test_sql": "", "resume_stage": "GENERATE_TOBE_SQL"}
        if state.get("tag_kind") == "SELECT" and "BIND_SQL" not in protected:
            return {**state, "bind_sql": "", "bind_set": None, "test_sql": "", "resume_stage": "GENERATE_BIND_SQL"}
        if state.get("tag_kind") == "SELECT" and "TEST_SQL" not in protected:
            return {**state, "test_sql": "", "resume_stage": "GENERATE_TEST_SQL"}
        return state

    # Choose the source SQL used by TO_SQL generation and optionally create TUNED_FR_SQL.
    def _prepare_conversion_source(self, job: dict[str, Any], db_config: dict[str, Any], llm_config: dict[str, Any], rag_config: dict[str, Any], source_sql: str, target_table: str, map_id: str, allow_generate: bool = True) -> tuple[str, str | None, str]:
        """Use saved TUNED_FR_SQL or generate it for long source SQL before conversion."""
        saved_tuned_fr_sql = str(job.get("tuned_fr_sql") or "").strip()
        if saved_tuned_fr_sql:
            return saved_tuned_fr_sql, saved_tuned_fr_sql, self._sql_length_kind(source_sql)

        pretuning_enabled = str(os.getenv("TUNED_FR_SQL_PRETUNING_ENABLED", "false")).strip().lower() == "true"
        pretuning_min_length = self._tuned_fr_sql_pretuning_min_length()
        sql_length = self._sql_length_kind(source_sql, pretuning_min_length)
        if not allow_generate or not pretuning_enabled or len(source_sql) < pretuning_min_length:
            return source_sql, None, sql_length

        # Long SQL pre-tuning uses SQL_TUNING RAG only on the final graph attempt.
        # GENERAL rules are loaded as direct guidance. SEARCH rules are ranked by Milvus dense_vector
        # inside _retrieve_rag_examples(), then serialized into the embedded TUNED_FR_SQL prompt.
        source_tables = self._source_tables(target_table)
        tuning_rules = self._load_rag_general_rules(db_config, "SQL_TUNING", source_tables, map_id)
        tuning_examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_TUNING", source_sql, source_tables, map_id)
        self._log_rag_context(map_id, "SQL_TUNING", tuning_rules, tuning_examples, 0)
        prompt = self._build_prompt(
            "TUNED_FR_SQL",
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
    def _generate_tobe_sql(
        self,
        job: dict[str, Any],
        db_config: dict[str, Any],
        llm_config: dict[str, Any],
        rag_config: dict[str, Any],
        source_sql: str,
        mapping_rules: list[dict[str, str]],
        target_table: str,
        last_error: str,
        retry_count: int,
        general_rules: list[dict[str, Any]] | None = None,
        examples: list[dict[str, Any]] | None = None,
        correct_sql_hint_text: str | None = None,
    ) -> str:
        map_id = f"{job.get('sql_id')} / {job.get('space_nm')}"[:100]
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("to_sql") or "").strip():
            reason = "USER_EDITED=Y; TO_SQL is not null"
            logging.getLogger("smartmigrate.workflow").info(
                f"USER_EDITED TO_SQL reused ({reason})",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "TOBE_SQL", "INFO", "USE_USER_EDITED_TO_SQL", "SUCCESS", retry_count, f"{reason}\n\n{str(job['to_sql'])}"]},
            )
            return str(job["to_sql"])
        if str(job.get("to_sql") or "").strip():
            logging.getLogger("smartmigrate.workflow").info(
                "Existing TO_SQL ignored because USER_EDITED is not Y",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "TOBE_SQL", "INFO", "IGNORE_EXISTING_TO_SQL", "START", retry_count, str(job.get("to_sql") or "")]},
            )

        # TO_SQL prompt context is assembled in this order:
        # migration table/column mapping rules, SQL_CONVERSION GENERAL RAG guidance,
        # and SQL_CONVERSION SEARCH examples ranked by vector similarity per SQL block.
        if general_rules is None or examples is None:
            source_tables = self._source_tables(target_table)
            general_rules = self._load_rag_general_rules(db_config, "SQL_CONVERSION", source_tables, map_id)
            examples = self._retrieve_rag_examples(db_config, rag_config, "SQL_CONVERSION", source_sql, source_tables, map_id)
            self._log_rag_context(map_id, "SQL_CONVERSION", general_rules, examples, retry_count)
        correct_sql_hint_text = correct_sql_hint_text if correct_sql_hint_text is not None else self._correct_sql_hint_text(db_config, source_sql, job.get("sql_id"), job.get("space_nm"), map_id, retry_count, "TO_SQL", job.get("tag_kind"))
        prompt = self._build_prompt(
            "TOBE_SQL",
            from_sql=source_sql,
            mapping_schema_text=self._mapping_prompt_text(mapping_rules, general_rules, examples, db_config),
            target_schema=db_config["target_schema"],
            correct_sql_hint_text=correct_sql_hint_text,
            last_error=last_error or "None",
        )
        self._log_prompt(map_id, "TOBE_SQL_PROMPT", prompt, retry_count)
        sql, _ = self._call_llm_text(prompt, llm_config)
        sql = self._clean_generated_sql(sql)
        if not sql:
            raise ValueError("TO_SQL generation returned empty SQL")
        self._increment_rag_hits(db_config, examples)
        return sql

    # Generate executable BIND_SQL and convert its result rows into BIND_SET JSON.
    def _generate_bind_payload(
        self,
        job: dict[str, Any],
        db_config: dict[str, Any],
        llm_config: dict[str, Any],
        source_sql: str,
        to_sql: str,
        mapping_rules: list[dict[str, str]],
        last_error: str,
        retry_count: int,
        correct_sql_hint_text: str | None = None,
    ) -> tuple[str, str | None]:
        map_id = f"{job.get('sql_id')} / {job.get('space_nm')}"[:100]
        bind_param_names = self._bind_names(source_sql) or self._bind_names(to_sql)
        logging.getLogger("smartmigrate.workflow").info(
            "Bind parameters extracted",
            extra={"workflow_log": [map_id, "SQL_CONVERSION", "BIND_PARAM", "INFO", "EXTRACT_BIND_PARAM", "PASS", retry_count, ", ".join(bind_param_names) if bind_param_names else "NO_BIND"]},
        )
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("bind_sql") or "").strip():
            bind_sql = str(job["bind_sql"])
            reason = "USER_EDITED=Y; BIND_SQL is not null"
            logging.getLogger("smartmigrate.workflow").info(
                f"USER_EDITED BIND_SQL reused ({reason})",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "BIND_SQL", "INFO", "USE_USER_EDITED_BIND_SQL", "SUCCESS", retry_count, f"{reason}\n\n{bind_sql}"]},
            )
        else:
            # Existing logic checks both source SQL and generated TO_SQL. If neither contains MyBatis
            # parameters or dynamic tags, bind execution is skipped and TEST_SQL receives [{}].
            if not bind_param_names:
                logging.getLogger("smartmigrate.workflow").info(
                    "BIND_SQL skipped because no bind parameter exists",
                    extra={"workflow_log": [map_id, "SQL_CONVERSION", "BIND_SQL", "INFO", "SKIP_BIND_SQL", "PASS", retry_count, "NO_BIND"]},
                )
                return "", None
            prompt = self._build_prompt(
                "BIND_SQL",
                from_sql=source_sql,
                from_schema=db_config["source_schema"],
                asis_source_filter_conditions=self._source_filter_prompt_text(mapping_rules),
                correct_sql_hint_text=correct_sql_hint_text if correct_sql_hint_text is not None else self._correct_sql_hint_text(db_config, source_sql, job.get("sql_id"), job.get("space_nm"), map_id, retry_count, "BIND_SQL", job.get("tag_kind")),
                last_error=last_error or "None",
            )
            if "FINAL_RETRY_MODE=ON" in str(last_error or "").upper():
                # Final retry keeps the normal bind prompt and appends stronger recovery rules.
                prompt += (
                    "\n\n[최종 재시도 모드]\n"
                    "- 이전 Bind SQL 실행 오류를 우선 해결하십시오.\n"
                    "- MyBatis 동적 태그 조건을 복잡하게 재구성하지 말고, 위험한 동적 block은 제거하십시오.\n"
                    "- 필요한 경우 join과 row source를 단순화하되 parameter 대상 컬럼과 inner join 관계는 보존하십시오.\n"
                    "- Oracle에서 바로 실행 가능한 보수적인 bind 후보 query 하나만 반환하십시오.\n"
                )
            self._log_prompt(map_id, "BIND_SQL_PROMPT", prompt, retry_count)
            bind_sql, _ = self._call_llm_text(prompt, llm_config)
            bind_sql = self._clean_generated_sql(bind_sql)
            if not bind_sql:
                raise ValueError("BIND_SQL generation returned empty SQL")
        bind_rows = self._execute_binding_query(db_config, bind_sql)
        logging.getLogger("smartmigrate.workflow").info(
            "BIND_SQL executed",
            extra={"workflow_log": [map_id, "SQL_CONVERSION", "BIND_SQL", "INFO", "EXECUTE_BIND_SQL", "PASS", retry_count, bind_sql]},
        )
        bind_sets = self._build_bind_sets(bind_rows)
        bind_set = json.dumps(bind_sets, ensure_ascii=False, default=str)
        logging.getLogger("smartmigrate.workflow").info(
            "BIND_SET built",
            extra={"workflow_log": [map_id, "SQL_CONVERSION", "BIND_SET", "INFO", "BUILD_BIND_SET", "PASS", retry_count, bind_set]},
        )
        return bind_sql, bind_set

    # Generate validation TEST_SQL that compares FROM SQL and TO_SQL row counts.
    def _generate_test_sql(
        self,
        job: dict[str, Any],
        db_config: dict[str, Any],
        llm_config: dict[str, Any],
        source_sql: str,
        to_sql: str,
        bind_set: str | None,
        last_error: str,
        retry_count: int,
        correct_sql_hint_text: str | None = None,
    ) -> str:
        map_id = f"{job.get('sql_id')} / {job.get('space_nm')}"[:100]
        if str(job.get("user_edited") or "").strip().upper() == "Y" and str(job.get("test_sql") or "").strip():
            reason = "USER_EDITED=Y; TEST_SQL is not null"
            logging.getLogger("smartmigrate.workflow").info(
                f"USER_EDITED TEST_SQL reused ({reason})",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "TEST_SQL", "INFO", "USE_USER_EDITED_TEST_SQL", "SUCCESS", retry_count, f"{reason}\n\n{str(job['test_sql'])}"]},
            )
            return str(job["test_sql"])
        # TEST_SQL compares the original AS-IS SQL with TO_SQL using bind cases from BIND_SET.
        prompt = self._build_prompt(
            "TEST_SQL",
            from_sql=source_sql,
            tobe_sql=to_sql,
            from_schema=db_config["source_schema"],
            tobe_schema=db_config["target_schema"],
            bind_set_text=self._bind_set_prompt_text(bind_set),
            correct_sql_hint_text=correct_sql_hint_text if correct_sql_hint_text is not None else self._correct_sql_hint_text(db_config, source_sql, job.get("sql_id"), job.get("space_nm"), map_id, retry_count, "TEST_SQL", job.get("tag_kind")),
            last_error=last_error or "None",
        )
        if retry_count >= self._configured_retry_limit():
            # Final retry keeps the normal test prompt and appends stricter validation recovery rules.
            prompt += (
                "\n\n[최종 재시도 모드]\n"
                "- 이전 TEST_SQL 실행 오류 또는 count mismatch 오류를 우선 해결하십시오.\n"
                "- 비교 형태는 CASE_NO, FROM_COUNT, TO_COUNT로 유지하십시오.\n"
                "- MyBatis 동적 branch가 모호하면 첫 번째 <when> branch를 기본으로 사용하고, 위험한 <foreach> 또는 SYSDATE block은 제거하십시오.\n"
                "- 최종 SQL에는 MyBatis bind marker와 동적 태그가 남으면 안 됩니다.\n"
            )
        self._log_prompt(map_id, "TEST_SQL_PROMPT", prompt, retry_count)
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
            "generated_sql_list": self._generated_sql_list(payload, job, extra),
            "db_status_updated": bool(job.get("row_id")),
        }

    def _generated_sql_list(self, payload: dict[str, Any], job: dict[str, Any], extra: dict[str, Any]) -> list[dict[str, Any]]:
        result = [dict(item) for item in payload.get("generated_sql_list") or [] if isinstance(item, dict)]
        row_id = job.get("row_id") or payload.get("row_id")
        for key, column in (
            ("tuned_fr_sql", "TUNED_FR_SQL"),
            ("to_sql", "TO_SQL"),
            ("bind_sql", "BIND_SQL"),
            ("test_sql", "TEST_SQL"),
        ):
            if str(extra.get(key) or "").strip():
                result.append(
                    {
                        "table": "NEXT_SQL_INFO",
                        "row_id": row_id,
                        "column": column,
                        "source_component": "12C_sqlConversionOneJobPocExecutor",
                    }
                )
        return self._dedupe_generated_sql_list(result)

    def _dedupe_generated_sql_list(self, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in values:
            key = (
                str(item.get("table") or "").upper(),
                str(item.get("row_id") or ""),
                str(item.get("key_value") or ""),
                str(item.get("column") or "").upper(),
            )
            if key in seen or not key[-1]:
                continue
            seen.add(key)
            result.append(item)
        return result

    # Map final failure status to the stage name that should be shown in logs.
    def _failure_stage(self, status: str) -> str:
        """Return the conversion stage represented by a failure status."""
        if status == FAIL_BIND:
            return "GENERATE_BIND_SQL"
        if status == FAIL_TEST:
            return "GENERATE_TEST_SQL"
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

    def _stage_reuse_reason(self, state: dict[str, Any], sql_name: str) -> str:
        """Explain why an already generated SQL was reused in this attempt."""
        resume_stage = str(state.get("resume_stage") or "").strip() or "UNKNOWN"
        last_status = str(state.get("last_status") or "").strip()
        user_edited = str((state.get("job") or {}).get("user_edited") or "").strip().upper()
        state_key = "to_sql" if sql_name == "TOBE_SQL" else "bind_sql"
        has_sql = bool(str(state.get(state_key) or "").strip())
        parts = [f"resume_stage={resume_stage}"]
        if last_status:
            parts.append(f"because {last_status}")
        if user_edited == "Y":
            parts.append("USER_EDITED=Y")
        parts.append(f"{sql_name} is {'not null' if has_sql else 'null'}")
        if resume_stage == "GENERATE_TEST_SQL":
            parts.append("next_stage=GENERATE_TEST_SQL")
        return "; ".join(parts)

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

    def _mark_running_status(self, db_config: dict[str, Any], row_id: str, status_column: str, status: str, message: str, retry_count: int = 0) -> None:
        """Persist a running SQL status while retry is still active."""
        self._update_row(
            db_config,
            row_id,
            {
                status_column: status,
                "LOG": f"RUNNING stage=SQL_CONVERSION status={status} message={message}",
                "RETRY_COUNT": retry_count,
            },
        )

    # Pick EDIT_FR_SQL first and fall back to original FR_SQL.
    def _source_sql(self, job: dict[str, Any]) -> str:
        """Return EDIT_FR_SQL first, otherwise FR_SQL."""
        edited = str(job.get("edit_fr_sql") or "").strip()
        return edited if edited else str(job.get("fr_sql") or "")

    # Classify SQL length using the same threshold that gates TUNED_FR_SQL pre-tuning.
    def _sql_length_kind(self, sql_text: str, threshold: int | None = None) -> str:
        """Classify runtime SQL length using TUNED_FR_SQL_PRETUNING_MIN_LENGTH."""
        limit = threshold if threshold is not None else self._tuned_fr_sql_pretuning_min_length()
        return "LONG" if len(str(sql_text or "")) >= limit else "SHORT"

    def _tuned_fr_sql_pretuning_min_length(self) -> int:
        return self._positive_int(os.getenv("TUNED_FR_SQL_PRETUNING_MIN_LENGTH"), TUNED_FR_SQL_PRETUNING_MIN_LENGTH_DEFAULT)

    # Extract MyBatis bind parameter names and dynamic tag variables.
    def _bind_names(self, sql_text: str) -> list[str]:
        """Extract MyBatis bind names while ignoring foreach-only parameters."""
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
    def _load_rag_general_rules(self, db_config: dict[str, Any], category: str, source_tables: set[str], map_id: str) -> list[dict[str, Any]]:
        try:
            return self._load_rag_rules(db_config, category, RAG_GENERAL, source_tables, map_id)
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"RAG GENERAL rule load skipped: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "RAG_RETRIEVE", "WARN", "RAG_GENERAL", "SKIP", 0]},
            )
            return []

    # -------------------------------------------------------------------------
    # Milvus RAG SEARCH retrieval
    # -------------------------------------------------------------------------
    # 12C no longer builds an in-memory FAISS index from all Oracle RAG rows.
    # Runtime retrieval is:
    # 1. take the current SQL text that 12C is processing,
    # 2. normalize it to a stable SQL shape,
    # 3. call the embedding API once per SQL/block,
    # 4. send that query vector to Milvus,
    # 5. search against SM_RAG_RULES.dense_vector with COSINE metric.
    #
    # The dense_vector values in SM_RAG_RULES are created by 00B from
    # NEXT_MIG_RAG_INFO.SOURCE_SQL. Therefore this search means:
    # "current FROM SQL/block" vs "stored RAG SOURCE_SQL examples".
    #
    # Returned guidance_text/source_sql/target_sql are prompt metadata. They are
    # not used for distance math here; only dense_vector is used for similarity.
    def _retrieve_rag_examples(self, db_config: dict[str, Any], rag_config: dict[str, Any], category: str, sql_text: str, source_tables: set[str], map_id: str) -> list[dict[str, Any]]:
        if category == "SQL_TUNING":
            blocks = self._split_sql_blocks(sql_text)
            blocks = [block for block in blocks if block["block_type"] == "SUBQUERY"] + [block for block in blocks if block["block_type"] != "SUBQUERY"]
        else:
            blocks = [{"block_id": "FULL_SQL", "block_type": "FULL", "sql": str(sql_text or ""), "normalized_sql": self._normalize_sql_shape(sql_text)}] if str(sql_text or "").strip() else []
        if not blocks:
            logging.getLogger("smartmigrate.workflow").info(
                f"RAG SEARCH skipped category={category}",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "RAG_RETRIEVE", "INFO", f"{category}_SEARCH", "SKIP", 0, "blocks=0"]},
            )
            return []
        client = self._milvus_client()
        config = self._milvus_config()
        output_fields = ["rag_id", "category", "rule_type", "source_tables", "guidance_text", "source_sql", "target_sql"]
        filter_expr = f'category == "{category}" and rule_type == "{RAG_SEARCH}" and is_active == true'
        top_k = self._positive_int(getattr(self, "rag_top_k", None), 3)
        fetch_k = max(top_k * 5, top_k)
        matches_by_block: list[list[tuple[dict[str, Any], float]]] = []
        try:
            # Embed the current SQL/block once, then search that query vector
            # against Milvus dense_vector. This is a remote Milvus vector search,
            # not a local FAISS search or an Oracle full-table embedding pass.
            vectors = self._embed_texts([block["normalized_sql"] for block in blocks], rag_config)
            method = "milvus_dense_vector"
            search_result = client.search(
                collection_name=config["rag_collection"],
                data=vectors,
                anns_field="dense_vector",
                filter=filter_expr,
                limit=fetch_k,
                output_fields=output_fields,
                # Milvus calculates vector similarity with COSINE distance.
                # This matches the dense embedding use case better than lexical
                # equality and replaces the old local-vector-search approach.
                search_params={"metric_type": "COSINE"},
            )
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
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"Milvus RAG search skipped: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [map_id, "SQL_CONVERSION", "RAG_RETRIEVE", "WARN", "RAG_SEARCH", "SKIP", 0]},
            )
            return []
        if category != "SQL_TUNING":
            top_matches = sorted([item for matches in matches_by_block for item in matches], key=lambda item: item[1], reverse=True)[:top_k]
            matches_by_block = [top_matches]
        match_summary = self._rag_match_summary(blocks, matches_by_block)
        payloads = [
            {
                "block_id": block["block_id"], "block_type": block["block_type"], "source_sql": block["sql"], "search_method": method,
                "top_rule_matches": [{key: value for key, value in rule.items() if key not in {"normalized_source_sql", "embedding_vector"}} | {"score": round(score, 6)} for rule, score in matches],
            }
            for block, matches in zip(blocks, matches_by_block)
        ]
        match_count = sum(len(block["top_rule_matches"]) for block in payloads)
        search_mode = f"block_top{top_k}" if category == "SQL_TUNING" else f"full_sql_top{top_k}"
        return payloads

    # Load GENERAL RAG guidance from Milvus with scalar filters only.
    # GENERAL rules are not similarity-ranked; they are selected by category,
    # rule_type, is_active, and source table applicability.
    def _load_rag_rules(self, db_config: dict[str, Any], category: str, rule_type: str, source_tables: set[str], map_id: str) -> list[dict[str, Any]]:
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
        return result

    def _log_rag_context(self, map_id: str, category: str, general_rules: list[dict[str, Any]], examples: list[dict[str, Any]], retry_count: int) -> None:
        match_count = sum(len(block.get("top_rule_matches") or []) for block in examples)
        general_ids = ",".join(str(rule.get("rule_id") or "") for rule in general_rules[:20])
        search_ids = ",".join(
            str(match.get("rule_id") or "")
            for block in examples
            for match in (block.get("top_rule_matches") or [])[:20]
            if match.get("rule_id")
        )
        logging.getLogger("smartmigrate.workflow").info(
            f"RAG context loaded category={category}",
            extra={
                "workflow_log": [
                    map_id,
                    "SQL_CONVERSION",
                    "RAG_CONTEXT",
                    "INFO",
                    f"{category}_RAG_CONTEXT",
                    "PASS",
                    retry_count,
                    f"general_rows={len(general_rules)}, search_blocks={len(examples)}, search_matches={match_count}, general_rag_ids={general_ids}, search_rag_ids={search_ids}",
                ]
            },
        )

    # Serialize mapping rules and RAG examples into the TO_SQL prompt context.
    def _mapping_prompt_text(self, mapping_rules: list[dict[str, str]], general_rules: list[dict[str, Any]], examples: list[dict[str, Any]], db_config: dict[str, Any]) -> str:
        # This text is inserted into the embedded TOBE_SQL prompt as mapping_schema_text.
        # Group by FR_TABLE/TO_TABLE so a COMPLEX source query is printed once and column mappings stay readable.
        source_schema, target_schema = db_config["source_schema"], db_config["target_schema"]
        grouped: dict[tuple[str, str, str, str, str], set[tuple[str, str]]] = {}
        for rule in mapping_rules:
            map_type = str(rule.get("map_type") or "").strip().upper() or "SIMPLE"
            fr_table = str(rule.get("fr_table") or "").strip()
            to_table = self._qualify_mapping_table(rule.get("to_table") or "", target_schema)
            description = str(rule.get("description") or "").strip()
            condition = str(rule.get("condition") or "").strip()
            if map_type == "COMPLEX":
                while fr_table.endswith(";"):
                    fr_table = fr_table[:-1].rstrip()
                from_expr = f"(\n{fr_table}\n) SRC"
            else:
                from_expr = self._qualify_mapping_table(fr_table, source_schema)
            grouped.setdefault((map_type, from_expr, to_table, description, condition), set()).add((str(rule.get("fr_col") or "").strip(), str(rule.get("to_col") or "").strip()))

        lines = ["[MIGRATION_MAPPING_RULES]"]
        for map_type, from_expr, to_table, description, condition in sorted(grouped):
            source_key = "FR_TABLE_QUERY" if map_type == "COMPLEX" else "FR_TABLE"
            lines.append(f"- MAP_TYPE={map_type} | {source_key}={from_expr} | TO_TABLE={to_table}")
            if map_type == "COMPLEX":
                lines.append("  - Use the FR_TABLE_QUERY as one inline view and reference mapped FR_COL values from alias SRC.")
            if description:
                lines.append(f"  - DESCRIPTION={description}")
            if condition:
                lines.append(f"  - CONDITION={condition}")
            for fr_col, to_col in sorted(grouped[(map_type, from_expr, to_table, description, condition)]):
                lines.append(f"  - FR_COL={fr_col} -> TO_COL={to_col}")
        lines.extend(["", "[SQL_CONVERSION_GENERAL_RAG_GUIDANCE]"])
        for rule in general_rules:
            lines.append(f"- RAG_ID={rule['rule_id']} | SOURCE_TABLES={','.join(rule['source_tables']) or 'ALL'}")
            lines.extend(f"  - {guide}" for guide in rule["guidance"])
        lines.extend(["", "[SQL_CONVERSION_SEARCH_RAG_TOP_3_BY_FULL_SQL]", self._serialize_conversion_examples(examples)])
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

    # Call the embedding endpoint for Milvus dense_vector search queries.
    def _embed_texts(self, texts: list[str], rag_config: dict[str, Any]) -> list[list[float]]:
        endpoint = str(rag_config["rag_embed_base_url"]).strip().rstrip("/")
        if not endpoint:
            raise ValueError("RAG_EMBED_BASE_URL is required for Milvus retrieval")
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

    # Build a compact block -> RAG_ID(score) summary for DB logs.
    def _rag_match_summary(self, blocks: list[dict[str, str]], matches_by_block: list[list[tuple[dict[str, Any], float]]]) -> str:
        parts: list[str] = []
        for block, matches in zip(blocks, matches_by_block):
            if not matches:
                parts.append(f"{block.get('block_id')}:none")
                continue
            matched = ",".join(f"{rule.get('rule_id')}:{round(float(score), 4)}" for rule, score in matches[:5])
            parts.append(f"{block.get('block_id')}:{matched}")
        return "; ".join(parts)[:3500]

    # Store the exact SQL pair used for each vector-search match.  The message
    # keeps RAG_ID searchable; the SQL CLOB keeps both full comparison inputs.
    def _log_rag_comparisons(self, map_id: str, category: str, blocks: list[dict[str, str]], matches_by_block: list[list[tuple[dict[str, Any], float]]]) -> None:
        logger = logging.getLogger("smartmigrate.workflow")
        for block, matches in zip(blocks, matches_by_block):
            for rule, score in matches:
                rule_id = str(rule.get("rule_id") or "-")
                comparison_sql = "\n".join(
                    [
                        "[작업 대상 SQL]",
                        str(block.get("sql") or ""),
                        "",
                        "[검색된 참고 SQL]",
                        str(rule.get("source_sql") or ""),
                    ]
                )
                logger.info(
                    f"RAG comparison category={category}, block={block.get('block_id')}, RAG_ID={rule_id}, score={round(float(score), 6)}",
                    extra={"workflow_log": [map_id, "SQL_CONVERSION", "RAG_COMPARE", "INFO", f"{category}_COMPARE", "PASS", 0, comparison_sql]},
                )

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

    # Render SQL_TUNING SEARCH matches for the final-attempt TUNED_FR_SQL prompt.
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

    # -------------------------------------------------------------------------
    # Milvus Correct SQL hint retrieval
    # -------------------------------------------------------------------------
    # This searches SM_CORRECT_SQL_CONVERSION, which 00B builds from NEXT_SQL_INFO.
    # dense_vector is generated from EDIT_FR_SQL first, otherwise FR_SQL.
    #
    # Runtime meaning:
    # "current FROM SQL" vs "previously corrected FROM SQL".
    #
    # If a similar user-edited PASS row exists, its TO_SQL/BIND_SQL/TEST_SQL is
    # injected as a hint into the corresponding generation prompt.
    def _correct_sql_hints_text(self, db_config: dict[str, Any], source_sql: str, current_sql_id: str | None, current_space_nm: str | None, map_id: str, retry_count: int, tag_kind: Any = "") -> dict[str, str]:
        hints = {column: "- (empty)" for column in ("TO_SQL", "BIND_SQL", "TEST_SQL")}
        config = self._milvus_config()
        try:
            # Correct SQL hint compares the current FROM SQL embedding with
            # SM_CORRECT_SQL_CONVERSION.dense_vector once, then reuses the same
            # ranked hits for TO_SQL/BIND_SQL/TEST_SQL hints.
            query_vector = self._embed_texts([self._normalize_sql_shape(source_sql)], self._rag_config())[0]
            filter_expr = 'user_edited == "Y" and is_active == true'
            tag_kind_value = str(tag_kind or "").strip().upper()
            if tag_kind_value:
                filter_expr += f' and tag_kind == {self._milvus_string(tag_kind_value)}'
            top_k = self._positive_int(getattr(self, "correct_sql_top_k", None), 1)
            rows = self._milvus_client().search(
                collection_name=config["correct_sql_collection"],
                data=[query_vector],
                anns_field="dense_vector",
                filter=filter_expr,
                limit=max(top_k * 10, 10),
                output_fields=["space_nm", "sql_id", "source_sql", "to_sql", "bind_sql", "test_sql", "status_conversion", "user_edited", "tag_kind"],
                search_params={"metric_type": "COSINE"},
            )
            hits = rows[0] if rows else []
            hint_fields = {"TO_SQL": "to_sql", "BIND_SQL": "bind_sql", "TEST_SQL": "test_sql"}
            selected_counts = {column: 0 for column in hint_fields}
            for hit in hits:
                entity = self._milvus_entity(hit)
                if self._status(entity.get("status_conversion")) not in {"PASS", CONVERSION_PASS}:
                    continue
                score = self._milvus_score(hit)
                for column, field in hint_fields.items():
                    if selected_counts[column] >= top_k:
                        continue
                    hint_sql = str(entity.get(field) or "").strip()
                    if not hint_sql:
                        continue
                    if hints[column] == "- (empty)":
                        hints[column] = self._format_correct_sql_hint(column, score, entity, hint_sql)
                    else:
                        hints[column] += "\n" + self._format_correct_sql_hint(column, score, entity, hint_sql)
                    selected_counts[column] += 1
                if all(count >= top_k for count in selected_counts.values()):
                    break
        except Exception:
            hints = {column: "- (empty)" for column in ("TO_SQL", "BIND_SQL", "TEST_SQL")}
        loaded = [column for column, text in hints.items() if str(text or "").strip() != "- (empty)"]
        logging.getLogger("smartmigrate.workflow").info(
            "Correct SQL hints loaded",
            extra={
                "workflow_log": [
                    map_id,
                    "SQL_CONVERSION",
                    "CORRECT_SQL_HINT",
                    "INFO",
                    "LOAD_SQL_HINTS",
                    "PASS" if loaded else "SKIP",
                    retry_count,
                    f"loaded={','.join(loaded) if loaded else 'none'}",
                ]
            },
        )
        return hints

    def _correct_sql_hint_text(self, db_config: dict[str, Any], source_sql: str, current_sql_id: str | None, current_space_nm: str | None, map_id: str, retry_count: int, hint_column: str, tag_kind: Any = "") -> str:
        hint_column = str(hint_column or "").strip().upper()
        if hint_column not in {"TO_SQL", "BIND_SQL", "TEST_SQL"}:
            return "- (empty)"
        return self._correct_sql_hints_text(db_config, source_sql, current_sql_id, current_space_nm, map_id, retry_count, tag_kind).get(hint_column, "- (empty)")

    def _format_correct_sql_hint(self, hint_column: str, score: float, hint: dict[str, Any], hint_sql: str) -> str:
        lines = [
            f"- SCORE={round(score, 6)} | METHOD=milvus_dense_vector | SPACE_NM={hint.get('space_nm') or ''} | SQL_ID={hint.get('sql_id') or ''}",
            f"  FROM_SQL: {hint.get('source_sql') or ''}",
            f"  {hint_column}: {hint_sql}",
        ]
        return "\n".join(lines)

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
        return SQL_PROMPT_TEMPLATES[template_name].format_map(_PromptValues(values)) + SQL_OUTPUT_FORMATTING_GUIDE

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
                        system="Oracle/MyBatis SQL만 생성하십시오.", messages=[{"role": "user", "content": prompt}],
                    )
                    content = "".join(str(getattr(item, "text", "")) for item in response.content).strip()
                elif not base_url:
                    from openai import OpenAI

                    response = OpenAI(api_key=api_key, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).chat.completions.create(
                        model=candidate, temperature=0, max_tokens=self._positive_int(config.get("llm_max_tokens"), 4096),
                        messages=[{"role": "system", "content": "Oracle/MyBatis SQL만 생성하십시오."}, {"role": "user", "content": prompt}],
                    )
                    content = str(response.choices[0].message.content or "").strip()
                else:
                    root = base_url.rstrip("/")
                    url = root if root.endswith("/chat/completions") else f"{root}/chat/completions"
                    request = urllib.request.Request(
                        url,
                        data=json.dumps({"model": candidate, "messages": [{"role": "system", "content": "Oracle/MyBatis SQL만 생성하십시오."}, {"role": "user", "content": prompt}], "temperature": 0, "max_tokens": self._positive_int(config.get("llm_max_tokens"), 4096)}).encode("utf-8"),
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
            if {key.lower() for key in bind_case} == {"no_bind"} or {key.lower() for key in bind_case} == {"no bind"}:
                return [{}]
            signature = json.dumps(bind_case, ensure_ascii=False, default=str, sort_keys=True)
            if bind_case and signature not in seen:
                selected.append(bind_case)
                seen.add(signature)
            if len(selected) == 3:
                break
        return selected or [{}]

    def _bind_set_prompt_text(self, bind_set: Any) -> str:
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
        return json.dumps(parsed[:3], ensure_ascii=False, default=str)

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

    def _milvus_config(self) -> dict[str, Any]:
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "rag_collection": self._clean_collection_name(getattr(self, "rag_collection_name", "") or os.getenv("MILVUS_RAG_COLLECTION") or "SM_RAG_RULES"),
            "correct_sql_collection": self._clean_collection_name(getattr(self, "correct_sql_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_CONVERSION_COLLECTION") or "SM_CORRECT_SQL_CONVERSION"),
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

    def _status(self, value: Any) -> str:
        return str(value or "").strip().upper()

    def _milvus_string(self, value: Any) -> str:
        return json.dumps(str(value or ""), ensure_ascii=False)

    def _clean_collection_name(self, value: Any) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
            raise ValueError(f"Invalid Milvus collection name: {clean}")
        return clean

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
