from __future__ import annotations

import logging
import json
import os
import re
import time
import urllib.request
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


MIGRATION_PROMPT_TEMPLATE: dict[str, str] = {
    "system_anthropic": "Oracle 19c 문법으로 SQL을 생성하십시오. migration_sql과 verification_sql key를 가진 유효한 JSON object 하나만 반환하고 SQL 값 끝에는 세미콜론을 붙이지 마십시오. verification_sql의 첫 출력 컬럼은 반드시 DECODE(모든 COUNT 차이의 ABS 합계, 0, 'Y', 'N') AS SUCCESS_YN이어야 하며, 그 뒤에 부호 있는 DIFF_TOT와 DIFF_C1...을 출력하십시오. SUCCESS_YN 없는 SQL은 허용되지 않습니다.",
    "system_openai": "Oracle 19c 문법으로 SQL을 생성하십시오. migration_sql과 verification_sql key를 가진 유효한 JSON object 하나만 반환하고 SQL 값 끝에는 세미콜론을 붙이지 마십시오. verification_sql의 첫 출력 컬럼은 반드시 DECODE(모든 COUNT 차이의 ABS 합계, 0, 'Y', 'N') AS SUCCESS_YN이어야 하며, 그 뒤에 부호 있는 DIFF_TOT와 DIFF_C1...을 출력하십시오. SUCCESS_YN 없는 SQL은 허용되지 않습니다.",
    "main_prompt": """
당신은 Oracle 데이터 마이그레이션 SQL 전문가입니다.
제공된 mapping rule과 DDL 정보만 사용하여 Oracle 19c migration SQL과 verification SQL을 생성하거나 수정하십시오.

[절대 규칙]
1. Hallucination 금지:
   - mapping rule 또는 DDL 정보에 없는 테이블과 컬럼을 사용하지 마십시오.
2. 타입 안정성:
   - NUMBER, VARCHAR2, DATE, TIMESTAMP 값을 비교하거나 변환할 때 필요한 경우 CAST, TO_NUMBER, TO_DATE, TO_TIMESTAMP를 명시적으로 사용하십시오.
3. Oracle 19c 호환성:
   - alias는 짧게 작성하고 가능하면 1-5자 범위로 유지하십시오.
   - 모든 alias는 Oracle 30 byte identifier 제한을 넘지 않게 하십시오.
   - LIMIT 같은 비 Oracle 문법을 사용하지 마십시오.
4. Schema 규칙:
   - 아래에 제공된 schema-qualified Source table과 Target table 값을 그대로 사용하십시오.
   - source_schema가 제공되면 Source/from 물리 테이블은 source_schema.TABLE_NAME 형식으로 qualify되어야 합니다.
   - target_schema가 제공되면 Target/to 물리 테이블은 target_schema.TABLE_NAME 형식으로 qualify되어야 합니다.
   - schema 값이 비어 있으면 물리 테이블에 schema prefix를 임의로 붙이지 마십시오.
   - 물리 AS-IS 또는 TO-BE 테이블의 schema prefix를 제거하지 마십시오.
   - DUAL, CTE 이름, inline view alias, table alias, subquery alias에는 schema prefix를 붙이지 마십시오.
5. 출력:
   - JSON만 반환하십시오.
   - 필수 key는 migration_sql, verification_sql입니다.
   - SQL 값 내부에 markdown, 주석, 설명, 끝 세미콜론을 포함하지 마십시오.
6. 최종 공백/들여쓰기 정리는 17C에서 처리합니다.

{ddl_info_block}
[Mapping rules]
- Source table: {from_table}
- Target table: {to_table}
- Column mappings:
{mapping_info}

[Correct SQL examples]
아래 예시는 참고용으로만 사용하십시오. migration_sql에는 검색된 MIG_SQL 패턴을, verification_sql에는 검색된 VERIFY_SQL 패턴을 참고할 수 있습니다.
검색된 예시가 이전 DIFF-only 형식이어도 verification_sql은 반드시 아래 SUCCESS_YN + DIFF_* 형식으로 생성하십시오.
{correct_sql_hints}

[Migration SQL requirements]
- 권장 형태: MIG_SQL.
- retry guidance가 다른 형태를 요구하지 않는 한 다음 형태를 우선하십시오:
  INSERT INTO {to_table} (target_columns...)
  SELECT source_expressions...
  FROM {from_table} S
  [WHERE condition]
- Source filter condition: {condition}
- condition이 비어 있으면 WHERE 절을 생략하십시오.
- condition이 있으면 migration_sql과 verification_sql에 동일한 source scope로 적용하십시오.
- Target columns와 expressions는 반드시 target DDL과 mapping rules를 따라야 합니다.

{verification_instruction}

[Verification SQL 구체 예시 - regular mode]
아래는 MEM_ID → ID 매핑과 예시에 표시된 원본 조건이 실제 mapping rule에 주어진 경우의 형태입니다.
테이블·컬럼·조건을 복사하지 말고 제공된 metadata와 실제 migration_sql 범위로 대체하십시오.
append mode에서는 아래 전체 target 집계를 사용하지 말고 앞서 지정한 target EXISTS 범위 제한을 적용하십시오.
SELECT DECODE(ABS(S.TOT - T.TOT) + ABS(S.C1 - T.C1), 0, 'Y', 'N') AS SUCCESS_YN,
       (S.TOT - T.TOT) AS DIFF_TOT,
       (S.C1 - T.C1) AS DIFF_C1
FROM (
    SELECT COUNT(*) TOT, COUNT(S.MEM_ID) C1
    FROM ASIS.TABLE1 S
    WHERE EXISTS (
        SELECT 1
        FROM ASIS.TABLE2 V
        JOIN ASIS.TABLE1 U ON U.ID = V.ID AND U.DEL_YN = 'Y'
    )
) S,
(
    SELECT COUNT(*) TOT, COUNT(T.ID) C1
    FROM TOBE.TABLE3 T
) T
위 예시의 EXISTS는 바깥 S와 상관되지 않은 조건입니다. 실제 mapping rule의 의도를 그대로 유지하십시오.
행별 상관 필터가 실제로 제공된 경우에만 그 연결 조건을 사용하고 임의의 키를 추가하지 마십시오.
비교할 컬럼이 늘어나면 COUNT(...) C2...와 DIFF_C2...를 추가하고 DECODE의 ABS 합계에도 모두 포함하십시오.

[Output constraint]
- migration_sql 또는 verification_sql 끝에 세미콜론(;)을 붙이지 마십시오.
- verification_sql의 첫 출력은 반드시 DECODE(...) AS SUCCESS_YN이어야 합니다. DIFF_*만 출력하지 마십시오.

[JSON shape]
{{
  "migration_sql": "INSERT INTO ... SELECT ...",
  "verification_sql": "SELECT ..."
}}
""",
    "verification_append": """
[Verification SQL requirements - append mode]
- Exclude the four standard audit fields (registered/created timestamp, registered/created by, modified/updated timestamp, modified/updated by) from every COUNT(column) comparison. Infer their physical names from the supplied DDL.
- target table에는 이전 job이 insert한 row가 이미 있을 수 있습니다.
- 전체 target table count를 source count와 비교하지 마십시오.
- current source scope에 대한 EXISTS 조건으로 target side를 필터링하여 이 job이 insert한 row만 검증하십시오.
- UNION ALL 없이 SELECT 문 하나만 사용하십시오.
- 제공된 DDL로 data type을 판단하십시오.
- CLOB, NCLOB, BLOB, LONG, LONG RAW 같은 LOB/LONG 컬럼은 모든 verification column-count 비교에서 제외하십시오.
- LOB/LONG 컬럼은 COUNT(column), DISTINCT, GROUP BY, ORDER BY, MINUS, JOIN key, equality predicate, value comparison에 사용하지 마십시오.
- 단일 집계 결과 row를 반환하고 SUCCESS_YN, DIFF_TOT, 매핑별 DIFF_C1... 컬럼만 출력하십시오.
- SUCCESS_YN은 전체 행 수와 모든 컬럼 COUNT 차이의 ABS 합계가 0이면 'Y', 아니면 'N'입니다. 서로 다른 차이가 상쇄되지 않게 하십시오.
- DIFF_*는 ABS 없이 source count - target count로 출력하여 차이의 방향을 보존하십시오.
- C1, C2...는 같은 매핑의 source/target COUNT를 짝지으십시오. 제외할 컬럼만 있으면 TOT만 비교하십시오.
- Source 집계의 필터·JOIN·EXISTS 범위는 실제 migration_sql과 같아야 합니다. EXISTS의 상관 여부와 연결 키를 임의로 추가하거나 변경하지 마십시오.
- 필수 출력 형태 (테이블·컬럼·조건은 실제 metadata에 맞춰 변경):
  SELECT DECODE(ABS(S.TOT - T.TOT) + ABS(S.C1 - T.C1) + ABS(S.C2 - T.C2), 0, 'Y', 'N') AS SUCCESS_YN,
         (S.TOT - T.TOT) AS DIFF_TOT,
         (S.C1 - T.C1) AS DIFF_C1,
         (S.C2 - T.C2) AS DIFF_C2
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
- EXISTS key는 mapping rules와 DDL에서 선택하십시오. primary/unique key 또는 안정적인 non-LOB source discriminator를 우선하십시오.
- 단일 결과 row의 SUCCESS_YN='Y'이고 모든 DIFF_* 컬럼이 0일 때만 verification이 통과합니다.
- EXISTS 문법과 집계 inline view의 괄호를 정확히 닫으십시오. COUNT(*)와 COUNT(column)을 사용하십시오.""",
    "verification_regular": """
[Verification SQL requirements]
- Exclude the four standard audit fields (registered/created timestamp, registered/created by, modified/updated timestamp, modified/updated by) from every COUNT(column) comparison. Infer their physical names from the supplied DDL.
- UNION ALL 없이 SELECT 문 하나만 사용하십시오.
- source와 target 사이의 total row count 및 mapped non-null column count를 비교하십시오.
- 제공된 DDL로 data type을 판단하십시오.
- CLOB, NCLOB, BLOB, LONG, LONG RAW 같은 LOB/LONG 컬럼은 모든 verification column-count 비교에서 제외하십시오.
- LOB/LONG 컬럼은 COUNT(column), DISTINCT, GROUP BY, ORDER BY, MINUS, JOIN key, equality predicate, value comparison에 사용하지 마십시오.
- 단일 집계 결과 row를 반환하고 SUCCESS_YN, DIFF_TOT, 매핑별 DIFF_C1... 컬럼만 출력하십시오.
- SUCCESS_YN은 전체 행 수와 모든 컬럼 COUNT 차이의 ABS 합계가 0이면 'Y', 아니면 'N'입니다. 서로 다른 차이가 상쇄되지 않게 하십시오.
- DIFF_*는 ABS 없이 source count - target count로 출력하여 차이의 방향을 보존하십시오.
- C1, C2...는 같은 매핑의 source/target COUNT를 짝지으십시오. 제외할 컬럼만 있으면 TOT만 비교하십시오.
- Source 집계의 필터·JOIN·EXISTS 범위는 실제 migration_sql과 같아야 합니다. EXISTS의 상관 여부와 연결 키를 임의로 추가하거나 변경하지 마십시오.
- 필수 출력 형태 (테이블·컬럼·조건은 실제 metadata에 맞춰 변경):
  SELECT DECODE(ABS(S.TOT - T.TOT) + ABS(S.C1 - T.C1) + ABS(S.C2 - T.C2), 0, 'Y', 'N') AS SUCCESS_YN,
         (S.TOT - T.TOT) AS DIFF_TOT,
         (S.C1 - T.C1) AS DIFF_C1,
         (S.C2 - T.C2) AS DIFF_C2
  FROM (SELECT COUNT(*) TOT,
               COUNT(source_non_lob_col1) C1,
               COUNT(source_non_lob_col2) C2
        FROM {from_table}
        [WHERE CONDITION]) S,
       (SELECT COUNT(*) TOT,
               COUNT(target_non_lob_col1) C1,
               COUNT(target_non_lob_col2) C2
        FROM {to_table}) T
- 단일 결과 row의 SUCCESS_YN='Y'이고 모든 DIFF_* 컬럼이 0일 때만 verification이 통과합니다.
- EXISTS 문법과 집계 inline view의 괄호를 정확히 닫으십시오. COUNT(*)와 COUNT(column)을 사용하십시오.""",
    "error_suffix": """

[Previous execution failure]
- Failed SQL: {last_sql}
- Error: {last_error}
오류 원인을 분석하고 수정된 SQL을 재생성하십시오.""",
    "append_mode_suffix": """

[Append mode migration_sql note]
- Target table '{to_table}'은 이미 존재하며 이전 job이 insert한 row를 포함할 수 있습니다.
- 기존 row를 보존하십시오.
- 이 job의 source row만 추가하십시오.
""",
    "dup_key_suffix": """

[ORA-00001 duplicate key retry guidance - MERGE 금지]
- 이전 INSERT INTO가 primary/unique key 중복으로 실패했습니다.
- DB Migration에서는 MERGE, MERGE INTO, UPDATE, UPSERT-style SQL을 사용하지 마십시오.
- migration_sql은 INSERT INTO ... SELECT ... 형태로 유지하십시오.
- source scope를 좁히거나, 제공된 condition을 일관되게 적용하거나, SELECT 내부에서 중복 source row를 제거해 duplicate 문제를 해결하십시오.
- duplicate source row 가능성이 있으면 INSERT 전 source subquery에서 deterministic ROW_NUMBER() filter 또는 SELECT DISTINCT를 사용하십시오.
- 필수 target column을 누락하지 말고 unmapped column을 참조하지 마십시오.
""",
}


FAIL_TEST2 = "FAIL-TEST2"
# The migration selectors use ``RETRY_COUNT < 2``.  A failure before the
# LangGraph retry loop (for example a Milvus/RAG connection exception while
# loading metadata) must therefore be persisted with this value, otherwise it
# is selected forever without consuming a retry.
AUTO_SELECTION_RETRY_LIMIT = 2


class NewType10CMigOneJobPocExecutor3(Component):

    display_name = "10C MIG One Job Executor 3 (PK Verify)"
    description = "Runs one DB Migration job with count verification followed by primary-key-based column comparison."
    name = "NewType10CMigOneJobPocExecutor3"
    icon = "DatabaseZap"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        StrInput(name="source_schema", display_name="Source Schema", required=False),
        StrInput(name="target_schema", display_name="Target Schema", required=False),
        HandleInput(
            name="llm",
            display_name="Language Model",
            info="Required. Connect the official OpenAI-compatible Chat Model output. 10C does not use direct HTTP LLM settings in this test branch.",
            input_types=["LanguageModel"],
        ),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=False),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=30, required=False),
        StrInput(name="milvus_uri", display_name="Milvus URI", required=False),
        StrInput(name="milvus_username", display_name="Milvus Username", required=False),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=False),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=False),
        StrInput(name="correct_sql_migration_collection_name", display_name="Correct SQL Migration Collection", value="SM_CORRECT_SQL_MIGRATION", required=False),
        IntInput(name="correct_sql_top_k", display_name="Correct SQL Top K", value=1, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    # ##############################
    # 진입점
    # ##############################

    # Langflow output에서 migration 작업 한 건을 검증하고 전체 DB Migration 실행 흐름을 시작한다.
    def run_job(self) -> Data:
        """migration 작업 한 건을 실행하고 최종 결과 payload를 반환한다."""
        logger = logging.getLogger("smartmigrate.workflow")
        logger.info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "START", 0]})
        try:
            started = time.perf_counter()
            job = self._parse_payload(getattr(self, "job_item", ""))
            if self._job_name(job) != "migration":
                result = self._pass_through(job, started, "10C skipped because job_name is not migration.")
                self.status = result
                __log_result = Data(data=result)
                logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", 0]})
                return __log_result
            map_id = self._to_int(job.get("map_id"))
            if map_id is None:
                raise ValueError("MIG job item requires map_id")
            max_retry = max(0, int(job.get("max_retry") if job.get("max_retry") is not None else (getattr(self, "max_retry", None) or 2)))
            db_config = self._db_config(job)
            current_status = self._load_mig_status(db_config, map_id)
            # Holds the failure stage appropriate for an unexpected system
            # error.  Graph nodes update this as business stages complete.
            status_tracker = {"failure_status": current_status if current_status.startswith("FAIL") else "FAIL-INSERT"}
            if current_status == "PASS":
                elapsed = int(time.perf_counter() - started)
                message = f"MAP_ID={map_id} is already PASS; migration execution skipped."
                attempts = [{"attempt": 0, "stage": "CHECK_CURRENT_STATUS", "status": "PASS", "reason": message}]
                logger.info(
                    message,
                    extra={"workflow_log": [map_id, "DB_MIGRATION", "JOB_SKIP", "INFO", "CHECK_CURRENT_STATUS", "PASS", 0, message]},
                )
                result = self._result(job, ok=True, status="PASS", elapsed=elapsed, attempts=attempts)
                result.update({"already_pass": True, "db_status_updated": False, "message": message})
                self.status = result
                __log_result = Data(data=result)
                logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", 0]})
                return __log_result
            attempts: list[dict[str, Any]] = []
            graph_result: dict[str, Any] = {}
            final_status = "FAIL-INSERT"
            retry_count = 0
            message = ""

            try:
                # 1. 대상 DDL/데이터를 건드리기 전에 선행 migration 의존성을 확인한다.
                dep_status = self._dependency_status(db_config, map_id, job.get("prior_map_id"))
                if dep_status != "READY":
                    elapsed = int(time.perf_counter() - started)
                    if self._is_dependency_failure_status(dep_status):
                        preserve_failed_status = current_status.startswith("FAIL")
                        status = current_status if preserve_failed_status else ""
                        retry_count = 3
                        self._update_job(
                            db_config,
                            map_id,
                            status,
                            elapsed,
                            retry_count,
                            preserve_status=not preserve_failed_status,
                        )
                        logger.warning(
                            f"prior_map_id={job.get('prior_map_id')} status={dep_status}; "
                            f"preserved_status={status}; retry disabled={preserve_failed_status}",
                            extra={"workflow_log": [map_id, "DB_MIGRATION", "JOB_SKIP", "WARN", "DEP_CHECK", status, retry_count]},
                        )
                        result = self._result(job, ok=False, status=status, elapsed=elapsed, attempts=attempts)
                        result.update({"skipped": True, "db_status_updated": True, "retry_disabled": preserve_failed_status, "retry_count": retry_count})
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
                    logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", 0]})
                    return __log_result

                # 2. 작업을 RUNNING으로 바꾸고 프롬프트 생성에 필요한 DDL/매핑 metadata를 읽는다.
                self._mark_running(db_config, map_id, current_status)
                base_context = {
                    "job": job,
                    "map_id": map_id,
                    "attempt": 1,
                    "llm": self._required_llm(),
                    "failure_status": current_status if current_status in {"FAIL-TEST", FAIL_TEST2} else "",
                    "status_tracker": status_tracker,
                }
                fetch_step = self._node_fetch_ddl(base_context)
                if fetch_step.get("status") != "PASS":
                    raise ValueError(fetch_step.get("message") or "FETCH_DDL failed")
                logger.info(
                    fetch_step.get("message") or "FETCH_DDL completed",
                    extra={"workflow_log": [map_id, "DB_MIGRATION", "FETCH_DDL", "INFO", "FETCH_DDL", "PASS", 0]},
                )

                # 3. GENERATE_SQL -> EXECUTE_SQL -> VERIFY 순서의 재시도 graph를 실행한다.
                pipeline_context = {
                    **base_context,
                    **(fetch_step.get("outputs") or {}),
                    # FAIL-TEST means INSERT was already completed.  The saved
                    # MIG_SQL is retained solely because the persisted stage
                    # says to resume at verification, never because USER_EDITED.
                    "failure_status": current_status if current_status in {"FAIL-TEST", FAIL_TEST2} else "",
                    "migration_sql": (fetch_step.get("outputs") or {}).get("saved_migration_sql", "") if current_status in {"FAIL-TEST", FAIL_TEST2} else "",
                    "verification_sql": (fetch_step.get("outputs") or {}).get("saved_verification_sql", "") if current_status == FAIL_TEST2 else "",
                    # VERIFY2_SQL is the generated read-only PK comparison
                    # statement. It is retained for diagnostics and resume,
                    # although this executor deterministically rebuilds it from
                    # MIG_SQL/VERIFY_SQL before each execution.
                    "verification2_sql": (fetch_step.get("outputs") or {}).get("saved_verification2_sql", "") if current_status == FAIL_TEST2 else "",
                }
                graph_result = self._run_migration_graph(pipeline_context, db_config, max_retry)
                attempts = list(graph_result.get("attempts") or [])
                final_status = str(graph_result.get("final_status") or "FAIL-TEST")
                final_ok = final_status == "PASS"
                retry_count = max(0, int(graph_result.get("db_attempts") or 1) - 1)
                message = str(graph_result.get("message") or "")

                elapsed = int(time.perf_counter() - started)
                if final_ok:
                    # 4. 최종 PASS는 상태 결과만 남긴다. 생성 SQL 전문은
                    # GENERATE_SQL 단계 로그와 NEXT_MIG_INFO의 SQL 컬럼에 이미
                    # 보관되므로 성공 로그에 중복 저장하지 않는다.
                    self._update_job(db_config, map_id, "PASS", elapsed, retry_count)
                    logger.info(
                        message,
                        extra={"workflow_log": [map_id, "DB_MIGRATION", "MIGRATION_SUCCESS", "INFO", "FINAL", "PASS", retry_count, ""]},
                    )
                else:
                    # 4. 재시도를 모두 소진하면 최종 실패 상태와 실패 SQL을 함께 저장한다.
                    self._update_job(db_config, map_id, final_status, elapsed, retry_count)
                    logger.error(
                        message,
                        extra={"workflow_log": [map_id, "DB_MIGRATION", "JOB_FAIL", "ERROR", "FINAL", final_status, retry_count, graph_result.get("stage_sql", "")]},
                    )

                elapsed = int(time.perf_counter() - started)
                result = self._result(job, ok=final_ok, status="PASS" if final_ok else final_status, elapsed=elapsed, attempts=attempts)
                result.update(
                    {
                        "retry_count": retry_count,
                        "message": message,
                        "migration_sql": graph_result.get("current_migration_sql", ""),
                        "verification_sql": graph_result.get("current_v_sql", ""),
                        "verification2_sql": graph_result.get("record_projection_sql", "") or graph_result.get("saved_verification2_sql", ""),
                        "record_projection_sql": graph_result.get("record_projection_sql", ""),
                        "record_verify_result": graph_result.get("record_verify_result", {}),
                        "record_verify_detail": graph_result.get("record_verify_detail", ""),
                        "generated_sql_list": self._generated_sql_list(
                            job.get("generated_sql_list"),
                            map_id,
                            graph_result.get("current_migration_sql", ""),
                            graph_result.get("current_v_sql", ""),
                            graph_result.get("generated_sql_columns") or [],
                        ),
                        "llm_model": graph_result.get("llm_model", ""),
                        "generated_sql_saved": bool(graph_result.get("generated_sql_saved")),
                        "next_node": "12C_sqlConversionOneJobPocExecutor" if job.get("full_workflow") else "10D_migIterationDashboard",
                    }
                )
                self.status = result
                __log_result = Data(data=result)
                logger.info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "INFO", "RUN_JOB", "END", 0]})
                return __log_result
            except Exception as exc:
                elapsed = int(time.perf_counter() - started)
                error_message = message or str(exc)
                stage_sql = graph_result.get("stage_sql", "") if isinstance(graph_result, dict) else ""
                terminal_retry_count = AUTO_SELECTION_RETRY_LIMIT
                error_status = str(status_tracker.get("failure_status") or "FAIL-INSERT")
                try:
                    self._update_job(db_config, map_id, error_status, elapsed, terminal_retry_count)
                    logger.error(
                        error_message,
                        extra={"workflow_log": [map_id, "DB_MIGRATION", "JOB_FAIL", "ERROR", "FINAL", final_status, terminal_retry_count, stage_sql]},
                    )
                except Exception:
                    logger.warning(
                        "DB status update failed while recording migration executor failure; original error is preserved.",
                        extra={"workflow_log": [map_id, "DB_MIGRATION", "JOB_FAIL", "WARN", "FINAL", final_status, terminal_retry_count, str(exc)]},
                        exc_info=True,
                    )
                result = self._result(job, ok=False, status=error_status, elapsed=elapsed, attempts=attempts)
                result.update({"retry_count": terminal_retry_count, "error_type": "SYSTEM_ERROR", "error": str(exc), "message": f"migration executor error: {exc}"})
                self.status = result
                __log_result = Data(data=result)
                logger.error("error run_job", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "ERROR", "RUN_JOB", "ERROR", 0]})
                return __log_result
        except Exception as exc:
            logger.error(f"error run_job: {exc}", extra={"workflow_log": [0, "WORKFLOW", "10C_MIG_EXEC", "ERROR", "RUN_JOB", "ERROR", 0]})
            raise

    # ##############################
    # 런타임 실행 흐름
    # ##############################

    # loop payload의 route/job_name 값을 10C가 처리할 migration 작업명으로 정규화한다.
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

    # 현재 item이 10C 담당이 아닐 때 원본 payload를 유지한 채 다음 노드로 넘긴다. 12C도 같은 패턴을 사용한다.
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

    # ##############################
    # LangGraph 노드
    # ##############################

    # LangGraph 첫 단계에서 대상 row, DDL, 매핑 정보를 읽어 프롬프트 입력 context를 만든다.
    def _node_fetch_ddl(self, context: dict[str, Any]) -> dict[str, Any]:
        """SQL 생성을 위한 매핑 metadata와 source/target DDL을 로드한다."""
        map_id = self._to_int(context.get("map_id"))
        db_config = self._db_config(context["job"])
        metadata = self._load_mig_metadata(db_config, map_id)
        if context.get("failure_status") == FAIL_TEST2:
            # Record-only retries do not call the LLM, so avoid an unrelated
            # embedding/RAG request before resuming record verification.
            metadata["correct_sql_hints"] = ""
        else:
            correct_sql_kind = "VERIFY_SQL" if context.get("failure_status") == "FAIL-TEST" else "MIG_SQL"
            metadata["correct_sql_hints"] = self._migration_correct_sql_hints(metadata, map_id, correct_sql_kind)
        return {
            "stage": "FETCH_DDL",
            "status": "PASS",
            "message": (
                "migration mapping and DDL metadata loaded; "
                f"system_schema={db_config.get('system_schema') or ''}, "
                f"source_schema={db_config.get('source_schema') or ''}, "
                f"target_schema={db_config.get('target_schema') or ''}"
            ),
            "outputs": {
                **metadata,
            },
        }

    # 현재 attempt의 오류 맥락과 metadata를 바탕으로 MIG_SQL/VERIFY_SQL을 생성한다.
    def _node_generate_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """설정된 LLM으로 migration SQL과 verification SQL을 생성한다."""
        job = context["job"]
        try:
            verify_only = context.get("failure_status") == "FAIL-TEST"
            migration_sql, verification_sql, used_model = self._generate_migration_sqls(context, verify_only=verify_only)
            if verify_only:
                migration_sql = str(context.get("current_migration_sql") or context.get("migration_sql") or context.get("saved_migration_sql") or "").strip()
            migration_sql = self._clean_sql_statement(migration_sql)
            verification_sql = self._clean_sql_statement(verification_sql)
            if not migration_sql:
                raise ValueError("LLM response did not include migration_sql")
            if not verification_sql:
                raise ValueError("LLM response did not include verification_sql")
            return {
                "stage": "GENERATE_SQL",
                "status": "PASS",
                "message": f"SQL generated by LLM model={used_model}",
                "outputs": {
                    "migration_sql": migration_sql,
                    "verification_sql": verification_sql,
                    "generated_sql_columns": ["VERIFY_SQL"] if verify_only else ["MIG_SQL", "VERIFY_SQL"],
                    "llm_model": used_model,
                    "generated_sql_saved": False,
                },
            }
        except Exception as exc:
            return {
                "stage": "GENERATE_SQL",
                # The failure status is the stage being retried, rather than
                # the status with which this executor invocation began.
                "status": "FAIL-TEST" if verify_only else "FAIL-INSERT",
                "message": f"migration SQL generation failed: {exc}",
                "outputs": {
                    "migration_sql": context.get("current_migration_sql") or context.get("migration_sql", ""),
                    "verification_sql": context.get("current_v_sql") or context.get("verification_sql", ""),
                },
            }

    # 생성된 MIG_SQL을 Oracle에 실행하고 실행 건수 또는 오류를 state에 기록한다.
    def _node_execute_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        """Oracle target truncate와 migration SQL 실행을 수행한다."""
        db_config = dict(context.get("db_config") or {})
        target_table = str(context.get("to_table") or "").strip()
        try:
            if str(context.get("trunc_yn") or "").strip().upper() == "Y":
                self._truncate_table(db_config, target_table)
            affected_rows = self._execute_sql_script(db_config, str(context.get("current_migration_sql") or context.get("migration_sql") or ""))
            message = "migration SQL executed"
            if affected_rows == 0:
                message = "migration SQL executed; affected_rows=0 treated as PASS"
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

    # VERIFY_SQL을 실행해 migration 결과가 기대 조건을 만족하는지 판단한다.
    def _node_verify(self, context: dict[str, Any]) -> dict[str, Any]:
        """SUCCESS_YN='Y'와 모든 DIFF_* 값이 0인지 검증한다."""
        db_config = dict(context.get("db_config") or {})
        try:
            ok, message, rows = self._execute_verification(db_config, str(context.get("current_v_sql") or context.get("verification_sql") or ""))
            if not ok:
                return {
                    "stage": "VERIFY_COUNT",
                    "status": "FAIL-TEST",
                    "message": message,
                    "outputs": {"verification_rows": rows},
                }
            return {
                "stage": "VERIFY_COUNT",
                "status": "PASS",
                "message": message,
                "outputs": {"verification_rows": rows, "diff_count": 0},
            }
        except Exception as exc:
            return {
                "stage": "VERIFY_COUNT",
                "status": "FAIL-TEST",
                "message": str(exc),
                "outputs": {"diff_count": -1},
            }

    def _node_verify_records(self, context: dict[str, Any]) -> dict[str, Any]:
        """Compare count-verified rows by target PK without row-order matching."""
        migration_sql = str(
            context.get("saved_migration_sql")
            or context.get("current_migration_sql")
            or context.get("migration_sql")
            or ""
        )
        comparison_sql = ""
        try:
            comparison_sql, compare_columns, pk_columns = self._build_pk_compare_sql(
                migration_sql,
                str(context.get("current_v_sql") or context.get("verification_sql") or ""),
                context,
            )
            self._save_verify2_sql(
                dict(context.get("db_config") or {}),
                int(context["map_id"]),
                comparison_sql,
            )
            result = self._execute_pk_comparison(
                dict(context.get("db_config") or {}),
                comparison_sql,
            )
            result["compared_columns"] = compare_columns
            result["pk_columns"] = pk_columns
            detail_json = json.dumps(result, ensure_ascii=False, default=str)
            return {
                "stage": "VERIFY_RECORDS",
                "status": "PASS" if result["ok"] else FAIL_TEST2,
                "message": result["summary"],
                "outputs": {
                    "record_projection_sql": comparison_sql,
                    "pk_compare_sql": comparison_sql,
                    "saved_verification2_sql": comparison_sql,
                    "record_verify_result": result,
                    "record_verify_detail": detail_json,
                },
            }
        except Exception as exc:
            return {
                "stage": "VERIFY_RECORDS",
                "status": FAIL_TEST2,
                "message": f"PK verification failed: {exc}",
                "outputs": {
                    "record_projection_sql": comparison_sql,
                    "pk_compare_sql": comparison_sql,
                    "saved_verification2_sql": comparison_sql,
                    "record_verify_detail": f"PK verification failed: {exc}",
                },
            }

    def _build_pk_compare_sql(self, migration_sql: str, verification_sql: str, context: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        """Keep Executor2 concat comparison and replace only row-order pairing with PK pairing."""
        _, target_count_sql = self._extract_count_verify_datasets(verification_sql)
        compare_columns = self._count_verify_target_columns(target_count_sql)
        if not compare_columns:
            raise ValueError("PK_VERIFY could not find target COUNT(column) expressions in VERIFY_SQL")
        pk_columns = self._target_pk_columns_for_compare(context)
        migration_expressions = self._migration_target_expressions(migration_sql)
        required_columns = list(dict.fromkeys([*pk_columns, *compare_columns]))
        missing = [column for column in required_columns if column not in migration_expressions]
        if missing:
            raise ValueError(f"PK_VERIFY target columns are absent from MIG_SQL INSERT list: {missing}")

        type_by_column = {
            self._clean_identifier(str(item.get("column_name") or "")): str(item.get("data_type") or "").upper()
            for item in context.get("target_ddl") or []
            if str(item.get("column_name") or "").strip()
        }
        nullable_by_column = {
            self._clean_identifier(str(item.get("column_name") or "")): str(item.get("nullable") or "Y").strip().upper() != "N"
            for item in context.get("target_ddl") or []
            if str(item.get("column_name") or "").strip()
        }
        ddl_missing = [column for column in compare_columns if column not in type_by_column]
        if ddl_missing:
            raise ValueError(f"PK_VERIFY target DDL types are unavailable: {ddl_missing}")

        target_from_clause = self._select_from_clause(target_count_sql)
        target_alias = self._target_from_alias(target_from_clause)
        source_from_clause = self._migration_source_from_clause(migration_sql)
        source_scope = self._format_from_scope(source_from_clause, "    ")
        target_scope = self._format_from_scope(target_from_clause, "    ")
        asis_pk_projection = ",\n               ".join(
            f"{migration_expressions[column]} AS {column}" for column in pk_columns
        )
        tobe_pk_projection = ",\n               ".join(
            f"{target_alias + '.' if target_alias else ''}{column} AS {column}" for column in pk_columns
        )
        asis_concat = self._row_concat_sql(
            [(column, migration_expressions[column], type_by_column[column], nullable_by_column[column]) for column in compare_columns],
            continuation_indent="        ",
        )
        tobe_concat = self._row_concat_sql(
            [
                (
                    column,
                    f"{target_alias}.{column}" if target_alias else column,
                    type_by_column[column],
                    nullable_by_column[column],
                )
                for column in compare_columns
            ],
            continuation_indent="        ",
        )
        pk_join = "\n    AND ".join(
            f"A.{column} = T.{column}" for column in pk_columns
        )
        sql = f"""SELECT
    COUNT(CASE WHEN A.ROW_CONCAT = T.ROW_CONCAT THEN 1 END) AS MATCH_CNT,
    COUNT(CASE WHEN A.ROW_CONCAT <> T.ROW_CONCAT OR T.ROW_CONCAT IS NULL THEN 1 END) AS MISMATCH_CNT
FROM (
    SELECT
        {asis_pk_projection},
        {asis_concat}
        AS ROW_CONCAT
    {source_scope}
) A
LEFT JOIN (
    SELECT
        {tobe_pk_projection},
        {tobe_concat}
        AS ROW_CONCAT
    {target_scope}
) T
    ON {pk_join}"""
        return sql, compare_columns, pk_columns

    def _row_concat_sql(self, columns: list[tuple[str, str, str, bool]], *, continuation_indent: str) -> str:
        parts = [
            self._row_concat_value_sql(expression, data_type, is_nullable)
            for _, expression, data_type, is_nullable in columns
        ]
        return f" || '|' ||\n{continuation_indent}".join(parts)

    def _format_from_scope(self, from_clause: str, indent: str) -> str:
        """Align the outer FROM/WHERE keywords of an extracted source scope."""
        scope = str(from_clause or "").strip()
        where_index = self._top_level_keyword(scope, "WHERE")
        if where_index < 0:
            return scope
        return f"{scope[:where_index].rstrip()}\n{indent}WHERE{scope[where_index + len('WHERE'):] }"

    def _row_concat_value_sql(self, expression: str, data_type: str, is_nullable: bool) -> str:
        value_sql = self._normalized_compare_value_sql(expression, data_type)
        # A target NOT NULL constraint guarantees both values after a successful
        # INSERT, so the NULL sentinel is unnecessary.  Retain the sentinel for
        # nullable columns to distinguish NULL from an empty concatenation part.
        if not is_nullable:
            return value_sql
        return f"NVL({value_sql} || '', '<NULL>')"

    def _normalized_compare_value_sql(self, expression: str, data_type: str) -> str:
        """Normalize only types whose implicit concat representation is unstable."""
        raw_expression = str(expression or "").strip()
        # Bare column references need no grouping parentheses. Keep grouping
        # only for compound expressions so concatenation precedence is stable.
        simple_column = re.fullmatch(
            r'(?:(?:[A-Za-z_][A-Za-z0-9_$#]*|"[^"]+")\.)?(?:[A-Za-z_][A-Za-z0-9_$#]*|"[^"]+")',
            raw_expression,
        )
        expr = raw_expression if simple_column else f"({raw_expression})"
        normalized_type = re.sub(r"\s+", " ", str(data_type or "").upper()).strip()
        if normalized_type == "DATE":
            # Oracle's implicit DATE-to-character conversion follows the
            # session NLS_DATE_FORMAT and can omit the time portion.  Both
            # sides must use an explicit, identical representation because
            # ROW_CONCAT is the actual value-comparison key in Executor3.
            return f"TO_CHAR(CAST({expr} AS DATE), 'YYYY-MM-DD HH24:MI:SS')"
        if normalized_type.startswith("RAW"):
            return f"RAWTOHEX(CAST({expr} AS RAW(2000)))"
        return expr

    def _extract_count_verify_datasets(self, verification_sql: str) -> tuple[str, str]:
        """Extract the two inline SELECTs from ``FROM (SELECT ...) S, (SELECT ...) T``."""
        sql = self._clean_sql_statement(verification_sql)
        from_index = self._top_level_keyword(sql, "FROM")
        if from_index < 0:
            raise ValueError("RECORD_VERIFY VERIFY_SQL has no outer FROM clause")
        position = from_index + len("FROM")

        def read_inline_select(start: int) -> tuple[str, int]:
            while start < len(sql) and sql[start].isspace():
                start += 1
            if start >= len(sql) or sql[start] != "(":
                raise ValueError("RECORD_VERIFY requires VERIFY_SQL shape FROM (SELECT ...) S, (SELECT ...) T")
            end = self._matching_parenthesis(sql, start)
            dataset = sql[start + 1:end].strip()
            if not dataset.upper().startswith("SELECT"):
                raise ValueError("RECORD_VERIFY inline VERIFY_SQL dataset must be a SELECT")
            return dataset, end + 1

        source_sql, position = read_inline_select(position)
        while position < len(sql) and (sql[position].isspace() or sql[position].isalnum() or sql[position] in "_$#\""):
            position += 1
        while position < len(sql) and sql[position].isspace():
            position += 1
        if position >= len(sql) or sql[position] != ",":
            raise ValueError("RECORD_VERIFY requires comma-separated S and T datasets in VERIFY_SQL")
        target_sql, _ = read_inline_select(position + 1)
        return source_sql, target_sql

    def _count_verify_target_columns(self, target_count_sql: str) -> list[str]:
        """Return target columns from the T-side COUNT(column) list, excluding COUNT(*)."""
        from_index = self._top_level_keyword(target_count_sql, "FROM")
        if from_index < 0:
            raise ValueError("RECORD_VERIFY target VERIFY dataset has no FROM clause")
        columns: list[str] = []
        for expression in self._split_sql_list(target_count_sql[len("SELECT"):from_index]):
            match = re.match(r"^\s*COUNT\s*\(\s*(.*?)\s*\)", expression, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            count_expression = match.group(1).strip()
            if count_expression == "*":
                continue
            simple_column = re.fullmatch(r"(?:(?:[A-Za-z_][A-Za-z0-9_$#]*|\"[^\"]+\")\.)?(?:[A-Za-z_][A-Za-z0-9_$#]*|\"[^\"]+\")", count_expression)
            if not simple_column:
                raise ValueError(f"RECORD_VERIFY target COUNT expression must be a target column: {count_expression}")
            column = self._clean_identifier(count_expression.rsplit(".", 1)[-1].strip().strip('"'))
            if column not in columns:
                columns.append(column)
        return columns

    def _migration_target_expressions(self, migration_sql: str) -> dict[str, str]:
        """Map each INSERT target column to its same-position SELECT expression."""
        target_columns, expressions, _ = self._migration_select_parts(migration_sql)
        return {
            column: self._strip_select_alias(expression)
            for column, expression in zip(target_columns, expressions, strict=True)
        }

    def _migration_source_from_clause(self, migration_sql: str) -> str:
        """Return the MIG_SQL source FROM/WHERE scope used by ASIS_ROWS."""
        _, _, from_clause = self._migration_select_parts(migration_sql)
        return from_clause

    def _migration_select_parts(self, migration_sql: str) -> tuple[list[str], list[str], str]:
        """Parse INSERT targets, same-position SELECT expressions, and its FROM scope."""
        sql = self._clean_sql_statement(migration_sql)
        # Some valid generated statements begin with a WITH clause before the
        # INSERT.  Locate the single INSERT instead of requiring byte zero.
        match = re.search(r"\bINSERT\s+INTO\s+[^\s(]+\s*\(", sql, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            preview = re.sub(r"\s+", " ", sql[:240]).strip()
            raise ValueError(
                "RECORD_VERIFY supports INSERT INTO target(columns) SELECT ... migrations only; "
                f"MIG_SQL starts with: {preview or '(empty)'}"
            )
        open_index = match.end() - 1
        close_index = self._matching_parenthesis(sql, open_index)
        target_columns = [self._clean_identifier(item.strip().strip('"')) for item in self._split_sql_list(sql[open_index + 1:close_index])]
        select_sql = sql[close_index + 1:].strip()
        select_index = 0 if select_sql.upper().startswith("SELECT ") else self._top_level_keyword(select_sql, "SELECT")
        if select_index < 0:
            preview = re.sub(r"\s+", " ", sql[:240]).strip()
            raise ValueError(f"RECORD_VERIFY requires INSERT INTO target(columns) SELECT ... syntax; MIG_SQL starts with: {preview}")
        from_index = self._top_level_keyword(select_sql, "FROM")
        if from_index < 0 or from_index < select_index:
            raise ValueError("RECORD_VERIFY could not find FROM in migration SELECT")
        expressions = self._split_sql_list(select_sql[select_index + len("SELECT"):from_index])
        if len(expressions) != len(target_columns):
            raise ValueError(f"RECORD_VERIFY target column/expression count mismatch: {len(target_columns)} != {len(expressions)}")
        return target_columns, expressions, select_sql[from_index:].strip()

    def _strip_select_alias(self, expression: str) -> str:
        """Remove an optional explicit SELECT alias before applying the target-column alias."""
        return re.sub(r"\s+AS\s+(?:\"[^\"]+\"|[A-Z_][A-Z0-9_$#]*)\s*$", "", expression.strip(), flags=re.IGNORECASE)

    def _select_from_clause(self, select_sql: str) -> str:
        from_index = self._top_level_keyword(select_sql, "FROM")
        if from_index < 0:
            raise ValueError("RECORD_VERIFY dataset SELECT has no FROM clause")
        return select_sql[from_index:].strip()

    def _target_from_alias(self, target_from_clause: str) -> str:
        """Return the first TO-BE table alias actually declared after FROM.

        The PK comparison SQL must never invent ``T2``.  If the Verify T-side is
        ``FROM TO_EMP T2`` this returns ``T2`` and the projection uses
        ``T2.EMP_NO``.  If it is ``FROM TO_EMP`` it returns an empty string and
        the projection uses ``EMP_NO`` as well.
        """
        match = re.match(r"^\s*FROM\s+", target_from_clause, flags=re.IGNORECASE)
        if not match:
            return ""
        remainder = target_from_clause[match.end():].lstrip()
        if not remainder:
            return ""

        # Consume one table reference (schema.table or a parenthesised inline
        # view), then inspect the immediately following token as its alias.
        if remainder.startswith("("):
            try:
                end = self._matching_parenthesis(remainder, 0)
            except ValueError:
                return ""
            remainder = remainder[end + 1:].lstrip()
        else:
            table_match = re.match(r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)(?:\.(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*))*', remainder)
            if not table_match:
                return ""
            remainder = remainder[table_match.end():].lstrip()

        if remainder.upper().startswith("AS "):
            remainder = remainder[3:].lstrip()
        alias_match = re.match(r'("[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)', remainder)
        if not alias_match:
            return ""
        alias = alias_match.group(1)
        if alias.upper() in {"WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "ON", "GROUP", "ORDER", "HAVING", "CONNECT", "START", "UNION", "MINUS", "FETCH"}:
            return ""
        return alias

    def _target_pk_columns_for_compare(self, context: dict[str, Any]) -> list[str]:
        columns = [self._clean_identifier(str(column)) for column in context.get("target_pk_columns") or [] if str(column).strip()]
        if not columns:
            raise ValueError("PK_VERIFY requires target primary-key columns; target table has no usable PK")
        return columns

    def _execute_pk_comparison(
        self,
        db_config: dict[str, Any],
        comparison_sql: str,
    ) -> dict[str, Any]:
        """Run the aggregate PK comparison and pass only when mismatch is zero."""
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(comparison_sql)
            db_row = cur.fetchone()
        if not db_row:
            raise ValueError("PK comparison returned no aggregate result row")
        match_count = int(db_row[0] or 0)
        mismatch_count = int(db_row[1] or 0)
        return {
            "ok": mismatch_count == 0,
            "comparison_sql": comparison_sql,
            "match_count": match_count,
            "mismatch_count": mismatch_count,
            "summary": f"MATCH_CNT={match_count}, MISMATCH_CNT={mismatch_count}",
        }

    def _record_log_value(self, value: Any) -> Any:
        value = value.read() if hasattr(value, "read") else value
        if isinstance(value, bytes):
            return {
                "type": "BLOB",
                "bytes": len(value),
                "preview_bytes": min(len(value), 4000),
                "truncated": len(value) > 4000,
                "preview_hex": value[:4000].hex(),
            }
        if isinstance(value, str) and len(value) > 4000:
            return {"type": "CLOB", "length": len(value), "truncated": True, "preview": value[:4000]}
        return self._json_safe_value(value)

    def _json_dump(self, value: Any) -> str:
        """Render record verification values as readable multi-line JSON."""
        return json.dumps(value, ensure_ascii=False, default=str, indent=2)

    def _matching_parenthesis(self, text: str, open_index: int) -> int:
        depth = 0
        for index, char in enumerate(text[open_index:], start=open_index):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        raise ValueError("Unclosed INSERT target column list")

    def _split_sql_list(self, text: str) -> list[str]:
        values, start, depth, quoted = [], 0, 0, False
        for index, char in enumerate(text):
            if char == "'":
                quoted = not quoted
            elif not quoted and char == "(":
                depth += 1
            elif not quoted and char == ")":
                depth -= 1
            elif not quoted and depth == 0 and char == ",":
                values.append(text[start:index])
                start = index + 1
        values.append(text[start:])
        return [value for value in values if value.strip()]

    def _top_level_keyword(self, text: str, keyword: str) -> int:
        depth, quoted = 0, False
        upper = text.upper()
        for index, char in enumerate(text):
            if char == "'":
                quoted = not quoted
            elif not quoted and char == "(":
                depth += 1
            elif not quoted and char == ")":
                depth -= 1
            elif not quoted and depth == 0 and upper.startswith(keyword, index) and (index == 0 or not upper[index - 1].isalnum()) and (index + len(keyword) == len(text) or not upper[index + len(keyword)].isalnum()):
                return index
        return -1

    # ##############################
    # LangGraph 재시도 callback
    # ##############################

    # migration 재시도 graph를 구성하고 GENERATE/EXECUTE/VERIFY 상태 전이를 실행한다.
    def _run_migration_graph(self, context: dict[str, Any], db_config: dict[str, Any], max_retry: int) -> dict[str, Any]:
        """migration 작업 한 건의 retry graph를 구성하고 실행한다."""
        from langgraph.graph import END, StateGraph

        # callback은 LangGraph 연결용이고, 실제 작업은 위쪽 _node_* 메서드에서 수행한다.
        def generate_node(state: dict[str, Any]) -> dict[str, Any]:
            step = self._node_generate_sql(state)
            return self._apply_step(state, step)

        # migration graph에서 MIG_SQL을 실행하고 실패 시 retry 판단에 필요한 state를 남긴다.
        def execute_node(state: dict[str, Any]) -> dict[str, Any]:
            step = self._node_execute_sql(state)
            next_state = self._apply_step(state, step)
            if step.get("status") == "PASS":
                next_state.update({"status": "EXECUTED", "error_type": "", "failure_status": ""})
            return next_state

        # migration graph에서 VERIFY_SQL을 실행하고 검증 결과를 state에 반영한다.
        def verify_node(state: dict[str, Any]) -> dict[str, Any]:
            step = self._node_verify(state)
            return self._apply_step(state, step)

        def verify_records_node(state: dict[str, Any]) -> dict[str, Any]:
            step = self._node_verify_records(state)
            return self._apply_step(state, step)

        # 실패한 시도 정보를 저장하고 다음 재시도 state를 준비한다.
        def retry_prepare_node(state: dict[str, Any]) -> dict[str, Any]:
            attempt = self._attempt_result_from_state(state, ok=False)
            retry_count = int(state.get("db_attempts") or 1)
            self._update_job(db_config, int(state["map_id"]), f"RUNNING-{attempt['status']}", 0, retry_count)
            logging.getLogger("smartmigrate.workflow").warning(
                attempt["message"],
                extra={
                    "workflow_log": [
                        int(state["map_id"]),
                        "DB_MIGRATION",
                        "ROW_ERROR",
                        "WARN",
                        attempt["failed_stage"],
                        attempt["status"],
                        retry_count,
                        self._stage_sql_from_state(state, attempt["status"]),
                    ]
                },
            )
            return {
                **state,
                "attempts": [*(state.get("attempts") or []), attempt],
                "current_steps": [],
                "db_attempts": retry_count + 1,
                "attempt": retry_count + 1,
                "error_type": "",
                "status": "",
                "failure_status": attempt["status"],
            }

        # graph 최종 상태를 DB와 Langflow payload에 반영하는 종료 노드다.
        def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
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

        # 현재 state와 남은 retry 횟수를 기준으로 다음 노드를 결정한다.
        def should_continue(state: dict[str, Any]) -> str:
            if state.get("status") == "PASS":
                return "finalize"
            if state.get("error_type") == "BIZ_RETRY":
                return "retry_prepare" if int(state.get("db_attempts") or 1) < int(state.get("max_attempts") or 1) else "finalize"
            if state.get("status") == "EXECUTED":
                return "verify"
            if state.get("status") == "COUNT_VERIFIED":
                return "verify_records"
            if not state.get("current_migration_sql"):
                return "generate"
            return "execute"

        # retry 준비 후 실패 단계에 정확히 맞는 node로 돌아간다.
        def after_retry_prepare(state: dict[str, Any]) -> str:
            if state.get("failure_status") == FAIL_TEST2:
                return "verify_records"
            return "execute" if state.get("failure_status") == "FAIL-TRUNCATE" else "generate"

        workflow = StateGraph(dict)
        workflow.add_node("generate", generate_node)
        workflow.add_node("execute", execute_node)
        workflow.add_node("verify", verify_node)
        workflow.add_node("verify_records", verify_records_node)
        workflow.add_node("retry_prepare", retry_prepare_node)
        workflow.add_node("finalize", finalize_node)
        # FAIL-TEST2 means INSERT and count verification already passed.  It
        # must resume only the last record-comparison stage.
        workflow.set_conditional_entry_point(
            lambda state: "verify_records" if state.get("failure_status") == FAIL_TEST2 else "generate",
            {"generate": "generate", "verify_records": "verify_records"},
        )
        workflow.add_conditional_edges("generate", should_continue, {"execute": "execute", "verify": "verify", "verify_records": "verify_records", "retry_prepare": "retry_prepare", "finalize": "finalize", "generate": "generate"})
        workflow.add_conditional_edges("execute", should_continue, {"verify": "verify", "verify_records": "verify_records", "retry_prepare": "retry_prepare", "finalize": "finalize", "generate": "generate", "execute": "execute"})
        workflow.add_conditional_edges("verify", should_continue, {"verify_records": "verify_records", "finalize": "finalize", "retry_prepare": "retry_prepare", "generate": "generate"})
        workflow.add_conditional_edges("verify_records", should_continue, {"finalize": "finalize", "retry_prepare": "retry_prepare", "generate": "generate"})
        workflow.add_conditional_edges("retry_prepare", after_retry_prepare, {"generate": "generate", "execute": "execute", "verify_records": "verify_records"})
        workflow.add_edge("finalize", END)
        graph = workflow.compile()
        initial_state = {
            **context,
            "db_attempts": 1,
            "db_config": db_config,
            # max_retry는 최초 실행 이후의 재시도 횟수이므로 전체 시도 횟수는 max_retry + 1이다.
            "max_attempts": max_retry + 1,
            "current_steps": [],
            "attempts": [],
            "current_migration_sql": context.get("migration_sql") or context.get("saved_migration_sql", ""),
            "current_v_sql": context.get("verification_sql", ""),
            "pk_compare_sql": context.get("verification2_sql") or context.get("saved_verification2_sql", ""),
            "last_error": "",
            "last_sql": "",
            "error_type": "",
            "failure_status": str(context.get("failure_status") or ""),
            "status": "",
        }
        return dict(graph.invoke(initial_state))

    # 각 graph node 결과를 공통 state 구조에 병합한다. 12C도 유사한 state 누적 패턴을 쓴다.
    def _apply_step(self, state: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
        """Merge a graph step and retain the last completed business stage."""
        outputs = dict(step.get("outputs") or {})
        step_status = str(step.get("status") or "")
        step_stage = str(step.get("stage") or "")
        status_tracker = state.get("status_tracker")
        if isinstance(status_tracker, dict):
            if step_status.startswith("FAIL"):
                status_tracker["failure_status"] = step_status
            elif step_stage == "EXECUTE_SQL":
                # INSERT completed; later unexpected errors belong to count VERIFY.
                status_tracker["failure_status"] = "FAIL-TEST"
            elif step_stage == "VERIFY_COUNT":
                # Count verification completed; only record verification remains.
                status_tracker["failure_status"] = FAIL_TEST2
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
                next_state["generated_sql_columns"] = list(
                    dict.fromkeys([*(state.get("generated_sql_columns") or []), *(outputs.get("generated_sql_columns") or [])])
                )
                self._save_generated_sql(
                    self._db_config(state.get("job") or {}),
                    int(state["map_id"]),
                    next_state.get("current_migration_sql", ""),
                    next_state.get("current_v_sql", ""),
                )
                # The DB save succeeded.  Record verification always uses this
                # persisted value, including when this same run proceeds
                # immediately from generation to verification.
                next_state["saved_migration_sql"] = next_state.get("current_migration_sql", "")
                next_state["saved_verification_sql"] = next_state.get("current_v_sql", "")
                next_state["generated_sql_saved"] = True
            if step.get("stage") == "VERIFY_COUNT":
                next_state["status"] = "COUNT_VERIFIED"
            elif step.get("stage") == "VERIFY_RECORDS":
                next_state["status"] = "PASS"
            elif step.get("stage") == "GENERATE_SQL" and state.get("failure_status") == "FAIL-TEST":
                next_state["status"] = "EXECUTED"
            self._log_step(next_state, step)
            return next_state

        status = str(step.get("status") or "FAIL-INSERT")
        next_state.update(
            {
                "status": "",
                "error_type": "BIZ_RETRY",
                "failure_status": status,
                "last_error": str(step.get("message") or ""),
                "last_sql": str(next_state.get("record_projection_sql") or next_state.get("current_v_sql") or "") if status == FAIL_TEST2 else (str(next_state.get("current_v_sql") or "") if status == "FAIL-TEST" else str(next_state.get("current_migration_sql") or next_state.get("migration_sql") or "")),
                "current_migration_sql": outputs.get("migration_sql") or state.get("current_migration_sql") or "",
                "current_v_sql": outputs.get("verification_sql") or state.get("current_v_sql") or "",
            }
        )
        self._log_step(next_state, step)
        return next_state

    # ##############################
    # SQL 프롬프트 생성
    # ##############################

    # DDL/매핑/RAG 힌트/재시도 오류를 합쳐 LLM이 migration SQL 쌍을 만들도록 호출한다.
    def _generate_migration_sqls(self, context: dict[str, Any], *, verify_only: bool) -> tuple[str, str, str]:
        prompt_template = MIGRATION_PROMPT_TEMPLATE
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
            correct_sql_hints=str(context.get("correct_sql_hints") or "- (no matching user-edited migration SQL)"),
        )
        last_error = str(context.get("last_error") or "").strip()
        last_sql = str(context.get("last_sql") or "").strip()
        is_verify_retry = context.get("failure_status") == "FAIL-TEST"
        if verify_only:
            # 검증 SQL만 실패한 재시도에서는 MIG_SQL은 유지하고 VERIFY_SQL만 다시 생성한다.
            # 사용자가 MIG_SQL만 보정한 경우에도 같은 기준으로 VERIFY_SQL만 생성한다.
            mode_title = "Verification retry mode" if is_verify_retry else "Verification-only mode"
            existing_mig_sql = str(context.get('current_migration_sql') or context.get('migration_sql') or '')
            prompt += (
                f"\n\n[{mode_title}]\n"
                "- Existing migration_sql을 JSON response의 migration_sql 값으로 그대로 반환하십시오.\n"
                "- Verification SQL만 재생성하십시오.\n"
                f"- Existing migration_sql:\n{existing_mig_sql}\n"
            )
        if last_error:
            prompt += prompt_template["error_suffix"].format(last_sql=last_sql, last_error=last_error)
            if "ORA-00001" in last_error:
                prompt += prompt_template["dup_key_suffix"].format(to_table=to_table, from_table=from_table)
        if is_append:
            prompt += prompt_template["append_mode_suffix"].format(to_table=to_table)
        prompt += (
            "\n\n[Final verification output check]\n"
            "- JSON 반환 전 verification_sql의 바깥 SELECT 첫 컬럼이 DECODE(...) AS SUCCESS_YN인지 확인하십시오.\n"
            "- DECODE의 ABS 합계에 TOT와 모든 비교 C1...의 차이가 포함되어야 합니다. 합계 0은 'Y', 그 외는 'N'입니다.\n"
            "- 뒤따르는 DIFF_TOT와 DIFF_C1...은 ABS 없이 S 값 - T 값이어야 합니다.\n"
            "- Correct SQL 예시나 재시도 SQL에 SUCCESS_YN이 없어도 그대로 따르지 말고 필수 컬럼을 포함해 반환하십시오.\n"
        )
        attempt = int(context.get("db_attempts") or context.get("attempt") or 1)
        mode = "VERIFY_RETRY" if is_verify_retry else ("VERIFY_ONLY" if verify_only else ("APPEND" if is_append else "REGULAR"))
        logging.getLogger("smartmigrate.workflow").info(
            f"attempt={attempt} stage=PROMPT_BUILD status=PASS mode={mode}; final LLM prompt assembled",
            extra={"workflow_log": [context.get("map_id") or 0, "DB_MIGRATION", "PROMPT_BUILD", "INFO", "PROMPT_BUILD", "PASS", max(0, attempt - 1), prompt]},
        )

        content, used_model = self._call_llm_json(
            system_anthropic=str(prompt_template.get("system_anthropic") or prompt_template.get("system_openai") or ""),
            system_openai=str(prompt_template.get("system_openai") or ""),
            prompt=prompt,
            llm=context.get("llm"),
        )
        result = self._extract_json_object(content)
        return (
            self._merge_sql_value(result.get("migration_sql", "")),
            self._merge_sql_value(result.get("verification_sql", "")),
            used_model,
        )

    # FR_TABLE 값이 물리 테이블인지 복합 SELECT인지 판단해 프롬프트용 source 표현으로 만든다.
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

    # Milvus에서 이전에 확정된 migration Correct SQL 예시를 찾아 현재 프롬프트 힌트로 만든다.
    def _migration_correct_sql_hints(self, metadata: dict[str, Any], map_id: int, correct_sql_kind: str) -> str:
        """현재 생성 단계와 같은 kind의 migration Correct SQL 예시만 조회한다."""
        fr_table = str(metadata.get("fr_table") or "").strip()
        to_table = str(metadata.get("raw_to_table") or metadata.get("to_table") or "").strip()
        condition = str(metadata.get("condition") or "").strip()
        map_type = str(metadata.get("map_type") or "TABLE").strip()
        mapping_info = self._mapping_info(metadata.get("mapping_details") or [])
        query_text = "\n".join(
            (
                f"MAP_TYPE: {map_type}",
                f"FR_TABLE: {fr_table}",
                f"TO_TABLE: {to_table}",
                f"CONDITION: {condition}",
                f"COLUMN_MAPPINGS:\n{mapping_info}",
            )
        )
        vector = self._embed_rag_text(query_text)
        rows = self._migration_milvus_client().search(
            collection_name=self._migration_rag_config()["collection"],
            data=[vector],
            anns_field="dense_vector",
            filter=f'is_active == true and correct_sql_kind == "{correct_sql_kind}" and {"mig_sql" if correct_sql_kind == "MIG_SQL" else "verify_sql"} != ""',
            limit=self._positive_int(getattr(self, "correct_sql_top_k", None), 1),
            output_fields=["map_id", "correct_sql_kind", "fr_table", "to_table", "condition", "mig_sql", "verify_sql", "user_edited", "status"],
            search_params={"metric_type": "COSINE"},
        )
        lines: list[str] = []
        for hit in (rows[0] if rows else []):
            entity = self._milvus_entity(hit)
            score = self._milvus_score(hit)
            reference_map_id = str(entity.get("map_id") or "-")
            lines.extend((
                f"- REFERENCE_MAP_ID={reference_map_id} | SCORE={round(score, 6)} | FR_TABLE={entity.get('fr_table') or ''} | TO_TABLE={entity.get('to_table') or ''}",
                f"  CONDITION: {entity.get('condition') or ''}",
                f"  {correct_sql_kind}: {entity.get('mig_sql') if correct_sql_kind == 'MIG_SQL' else entity.get('verify_sql') or ''}",
            ))
            logging.getLogger("smartmigrate.workflow").info(
                "Migration Correct SQL hint loaded",
                extra={"workflow_log": [map_id, "DB_MIGRATION", "CORRECT_SQL_HINT", "INFO", "LOAD_MIGRATION_HINT", "PASS", 0, f"collection={self._migration_rag_config()['collection']}, reference_map_id={reference_map_id}, score={round(score, 6)}"]},
            )
        return "\n".join(lines) if lines else f"- (no matching user-edited {correct_sql_kind})"

    # migration Correct SQL 검색에 필요한 embedding/Milvus 설정을 모은다. 12C의 RAG 설정 헬퍼와 구조가 같다.
    def _migration_rag_config(self) -> dict[str, str | int]:
        return {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or ""),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None), 30),
            "collection": self._clean_collection_name(getattr(self, "correct_sql_migration_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_MIGRATION_COLLECTION") or "SM_CORRECT_SQL_MIGRATION"),
        }

    # Correct SQL 힌트 검색용 query text를 embedding vector로 변환한다. 12C도 embedding 기반 검색을 사용한다.
    def _embed_rag_text(self, text: str) -> list[float]:
        config = self._migration_rag_config()
        base_url = str(config["base_url"]).rstrip("/")
        if not base_url:
            raise ValueError("RAG Embedding Base URL is not configured")
        endpoint = base_url if base_url.endswith("/embeddings") else (f"{base_url}/embeddings" if base_url.endswith("/v1") else f"{base_url}/v1/embeddings")
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"
        request = urllib.request.Request(endpoint, data=json.dumps({"model": config["model"], "input": [text]}).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=int(config["timeout"])) as response:
            body = json.loads(response.read().decode("utf-8"))
        values = (body.get("data") or [{}])[0].get("embedding") if isinstance(body, dict) else None
        if not isinstance(values, list):
            raise ValueError("invalid embedding response")
        return [float(value) for value in values]

    # migration Correct SQL collection에 접근할 Milvus client를 생성한다.
    def _migration_milvus_client(self) -> Any:
        from pymilvus import MilvusClient
        config = self._migration_rag_config()
        uri = str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip()
        username = str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip()
        password = self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or "")
        db_name = str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip()
        if not all((uri, username, password, db_name, config["collection"])):
            raise ValueError("Milvus migration Correct SQL settings are incomplete")
        return MilvusClient(uri=uri, user=username, password=password, db_name=db_name, timeout=10)

    # Milvus hit 객체에서 payload/entity dict를 추출한다. 12C/15C의 Milvus hit 처리와 같은 계열이다.
    def _milvus_entity(self, hit: Any) -> dict[str, Any]:
        if isinstance(hit, dict):
            return dict(hit.get("entity") or hit.get("fields") or hit)
        return dict(getattr(hit, "entity", None) or getattr(hit, "fields", None) or {})

    # Milvus hit의 distance/score 값을 float로 통일한다. 12C/15C도 같은 점수 정규화를 사용한다.
    def _milvus_score(self, hit: Any) -> float:
        value = hit.get("distance", hit.get("score", 0.0)) if isinstance(hit, dict) else getattr(hit, "distance", getattr(hit, "score", 0.0))
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # Milvus collection 이름을 공백 없는 유효 문자열로 정리한다. 12C도 동일한 목적의 헬퍼가 있다.
    def _clean_collection_name(self, value: Any) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
            raise ValueError(f"Invalid Milvus collection name: {clean}")
        return clean

    # 컬럼 매핑 목록을 migration 프롬프트에 넣을 사람이 읽기 쉬운 텍스트로 바꾼다.
    def _mapping_info(self, details: list[dict[str, Any]]) -> str:
        lines = []
        for item in details:
            fr_col = str(item.get("fr_col") or "").strip()
            to_col = str(item.get("to_col") or "").strip()
            if fr_col and to_col:
                lines.append(f"  - {fr_col} -> {to_col}")
        return "\n".join(lines) if lines else "  (no column mappings found)"

    # source/target DDL 정보를 한 프롬프트 블록으로 묶어 LLM이 구조 차이를 볼 수 있게 한다.
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

    # Oracle metadata row를 컬럼명/타입/nullable 형식의 DDL 요약 텍스트로 렌더링한다.
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

    # 같은 target table의 PASS 이력이 있는지 확인해 최초 적재인지 append 검증인지 판단한다.
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

    # ##############################
    # Workflow 로그 기록
    # ##############################

    # graph node 실행 결과를 workflow 로그에 남긴다.
    def _log_step(self, state: dict[str, Any], step: dict[str, Any]) -> None:
        """graph step 하나를 workflow logger를 통해 NEXT_MIG_LOG에 남긴다."""
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
        if stage == "VERIFY_RECORDS":
            log_type = "VERIFY_RECORDS"
        elif stage == "VERIFY_COUNT":
            log_type = "VERIFY_SQL"
        elif status.startswith("FAIL-"):
            log_type = "ROW_ERROR"
        elif stage in {"FETCH_DDL", "EXECUTE_SQL", "TRUNCATE", "PROMPT_BUILD"}:
            log_type = stage
        else:
            log_type = "GENERATE_SQL"
        logging.getLogger("smartmigrate.workflow").log(
            logging.WARNING if log_level == "WARN" else logging.INFO,
            message,
            extra={"workflow_log": [map_id, "DB_MIGRATION", log_type, log_level, stage, status, retry_count, stage_sql]},
        )

    # 실패/성공 단계별로 로그에 함께 저장할 SQL 본문을 고른다.
    def _log_sql_for_step(self, state: dict[str, Any], step: dict[str, Any]) -> str:
        """로그를 남기는 step에 맞는 SQL 본문을 선택한다."""
        stage = str(step.get("stage") or "")
        if stage == "VERIFY_COUNT":
            return str(state.get("current_v_sql") or "")
        if stage == "VERIFY_RECORDS":
            return self._record_verify_log_body(state)
        if stage == "GENERATE_SQL" and state.get("status") == "EXECUTED":
            return str(state.get("current_v_sql") or "")
        return self._stage_sql_from_state(state, str(step.get("status") or ""))

    def _record_verify_log_body(self, state: dict[str, Any]) -> str:
        """Render the PK comparison SQL and aggregate result counts only."""
        comparison_sql = str(state.get("record_projection_sql") or "").strip()
        result = dict(state.get("record_verify_result") or {})
        parts = [
            "[PK_COMPARE_SQL]",
            comparison_sql or "(comparison SQL unavailable)",
            "",
            "[PK_COMPARE_SUMMARY]",
            f"pk_columns={result.get('pk_columns') or []}",
            f"compared_columns={result.get('compared_columns') or []}",
            f"MATCH_CNT={result.get('match_count') or 0}",
            f"MISMATCH_CNT={result.get('mismatch_count') or 0}",
            f"result={result.get('summary') or ''}",
        ]
        if not result:
            detail = str(state.get("record_verify_detail") or "").strip()
            if detail:
                parts.extend(["", "[RECORD_VERIFY_ERROR_DETAIL]", detail])
        return "\n".join(parts)

    # retry router가 왜 다음 경로를 선택했는지 status 메시지로 설명한다.
    def _route_note(self, state: dict[str, Any], step: dict[str, Any]) -> str:
        """step 결과 이후 선택된 graph route를 설명한다."""
        if step.get("stage") == "GENERATE_SQL" and state.get("status") == "EXECUTED":
            return "; route=verify_retry_generate_only,next=VERIFY"
        if step.get("stage") == "GENERATE_SQL" and step.get("status") == "PASS":
            return "; route=normal,next=EXECUTE_SQL"
        if step.get("stage") == "EXECUTE_SQL" and step.get("status") == "PASS":
            return "; route=normal,next=VERIFY"
        if step.get("status") == "FAIL-TEST":
            return "; route=retry,next=GENERATE_SQL_VERIFY_ONLY"
        if step.get("status") == FAIL_TEST2:
            return "; route=retry,next=VERIFY_RECORDS_ONLY"
        if step.get("status") == "FAIL-TRUNCATE":
            return "; route=retry,next=EXECUTE_SQL"
        if str(step.get("status") or "").startswith("FAIL-"):
            return "; route=retry,next=GENERATE_SQL"
        if step.get("status") == "PASS":
            return "; route=finalize"
        return ""

    # 현재 state를 attempt history에 저장할 표준 dict로 변환한다. 12C도 attempt 누적 구조를 사용한다.
    def _attempt_result_from_state(self, state: dict[str, Any], *, ok: bool) -> dict[str, Any]:
        """현재 graph state에서 외부에 노출할 attempt 기록을 만든다."""
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

    # 실패한 단계에 맞춰 NEXT_MIG_INFO에 저장할 최종 status를 결정한다.
    def _failure_status_from_state(self, state: dict[str, Any]) -> str:
        """graph state에 맞는 최종 업무 실패 status를 결정한다."""
        explicit = str(state.get("failure_status") or "").strip()
        if explicit:
            return explicit
        if state.get("status") == "EXECUTED":
            return "FAIL-TEST"
        steps = list(state.get("current_steps") or [])
        failed_step = next((step for step in reversed(steps) if step.get("status") != "PASS"), None)
        return str((failed_step or {}).get("status") or "FAIL-INSERT")

    # 최종 실패 단계와 연결된 SQL을 찾아 로그/결과 payload에 싣는다.
    def _stage_sql_from_state(self, state: dict[str, Any], failure_status: str | None = None) -> str:
        """실패 status와 가장 관련 있는 SQL 본문을 반환한다."""
        status = failure_status or self._failure_status_from_state(state)
        if status == FAIL_TEST2:
            return str(state.get("record_projection_sql") or "")
        if status == "FAIL-TEST":
            return str(state.get("current_v_sql") or "")
        return str(state.get("current_migration_sql") or state.get("last_sql") or "")

    # ##############################
    # DB Migration 상태 저장
    # ##############################

    # 실행 직전 NEXT_MIG_INFO의 현재 STATUS를 조회해 이미 완료된 job인지 확인한다.
    def _load_mig_status(self, db_config: dict[str, Any], map_id: int) -> str:
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT STATUS FROM {table} WHERE MAP_ID = :1", [map_id])
            row = cur.fetchone()
        return self._lob_to_str(row[0]).strip().upper() if row else ""

    # PRIOR_MAP_ID가 있으면 선행 migration 상태를 확인해 실행 가능 여부를 반환한다.
    def _dependency_status(self, db_config: dict[str, Any], map_id: int, prior_map_id: Any) -> str:
        """선행 migration 작업이 PASS일 때만 READY를 반환한다."""
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

    # 선행 작업 status가 후속 작업을 SKIP 처리해야 하는 실패 계열인지 판단한다.
    def _is_dependency_failure_status(self, status: str) -> bool:
        """선행 작업이 종료성 실패/skip 상태인지 판단한다."""
        value = str(status or "").strip().upper()
        return value.startswith("FAIL-") or value.startswith("SKIP-")

    # migration row를 RUNNING으로 표시하고 실행 시작 상태를 DB에 반영한다.
    def _mark_running(self, db_config: dict[str, Any], map_id: int, prior_status: str = "") -> None:
        """Mark migration running without discarding a persisted FAIL stage."""
        normalized_prior = str(prior_status or "").strip().upper()
        running_status = f"RUNNING-{normalized_prior}" if normalized_prior.startswith("FAIL") else "RUNNING"
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
                [running_status, map_id],
            )
            conn.commit()

    # migration 최종 상태, 소요 시간, retry count를 NEXT_MIG_INFO에 저장한다.
    def _update_job(self, db_config: dict[str, Any], map_id: int, status: str, elapsed: int, retry_count: int, *, preserve_status: bool = False) -> None:
        """현재 작업 status, elapsed time, retry count를 저장한다."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            if preserve_status:
                cur.execute(
                    f"""
                    UPDATE {table}
                       SET ELAPSED_SECONDS = :1,
                           RETRY_COUNT = :2,
                           UPD_TS = CURRENT_TIMESTAMP
                     WHERE MAP_ID = :3
                    """,
                    [elapsed, retry_count, map_id],
                )
                conn.commit()
                return
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

    # 생성된 MIG_SQL/VERIFY_SQL을 NEXT_MIG_INFO에 저장한다.
    def _save_generated_sql(self, db_config: dict[str, Any], map_id: int, migration_sql: str, verification_sql: str) -> None:
        """생성 직후 MIG_SQL과 VERIFY_SQL을 저장한다."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        set_clauses: list[str] = []
        params: dict[str, Any] = {"map_id": map_id}
        if str(migration_sql or "").strip():
            params["mig_sql"] = migration_sql
            set_clauses.append("MIG_SQL = :mig_sql")
        if str(verification_sql or "").strip():
            params["verify_sql"] = verification_sql
            set_clauses.append("VERIFY_SQL = :verify_sql")
        if set_clauses:
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

    # 2차 PK 비교용 읽기 전용 SELECT를 NEXT_MIG_INFO에 저장한다.
    def _save_verify2_sql(self, db_config: dict[str, Any], map_id: int, verification2_sql: str) -> None:
        """Store the generated PK comparison SELECT in VERIFY2_SQL."""
        sql = str(verification2_sql or "").strip()
        if not sql:
            return
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET VERIFY2_SQL = :verify2_sql,
                       UPD_TS = CURRENT_TIMESTAMP
                 WHERE MAP_ID = :map_id
                """,
                {"verify2_sql": sql, "map_id": map_id},
            )
            conn.commit()

    # MAP_ID 기준으로 migration row와 관련 컬럼 매핑/DDL 입력 데이터를 로드한다.
    def _load_mig_metadata(self, db_config: dict[str, Any], map_id: int | None) -> dict[str, Any]:
        """MAP_ID 한 건의 NEXT_MIG_INFO와 NEXT_MIG_INFO_DTL metadata를 로드한다."""
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
                       USER_EDITED,
                       VERIFY2_SQL
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
            saved_verification2_sql = self._lob_to_str(row[8])
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
            "saved_verification2_sql": saved_verification2_sql,
            "mapping_details": details,
            "source_ddl": self._source_ddl_for_prompt(db_config, map_type, fr_table),
            "target_ddl": self._fetch_table_columns(db_config, self._qualify_to_table(to_table, db_config)) if self._looks_like_table(to_table) else [],
            "target_pk_columns": self._fetch_primary_key_columns(db_config, self._qualify_to_table(to_table, db_config)) if self._looks_like_table(to_table) else [],
        }

    # FR_TABLE 또는 복합 SELECT에서 source DDL prompt 재료를 수집한다.
    def _source_ddl_for_prompt(self, db_config: dict[str, Any], map_type: str, fr_table: str) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
        """simple/COMPLEX 매핑에 맞춰 source DDL을 prompt 입력 구조로 반환한다."""
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

    # 복합 SQL에서 DDL 조회가 필요한 물리 source table 목록을 추출한다.
    def _source_tables_for_ddl(self, map_type: str, fr_table: str) -> list[str]:
        """DDL 조회에 사용할 source 물리 테이블 목록을 반환한다."""
        text = str(fr_table or "").strip()
        if not text:
            return []
        if str(map_type or "").strip().upper() == "COMPLEX":
            return self._extract_query_table_names(text)
        return [text]

    # SELECT/WITH 문에서 FROM/JOIN 뒤의 테이블명을 프롬프트 DDL 조회용으로 뽑는다.
    def _extract_query_table_names(self, sql_text: str) -> list[str]:
        """COMPLEX FR_TABLE SQL 표현식에서 물리 테이블명을 추출한다."""
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

    # Oracle metadata에서 실제 테이블 컬럼 정보를 조회해 LLM prompt DDL로 사용한다.
    def _fetch_table_columns(self, db_config: dict[str, Any], table: str) -> list[dict[str, Any]]:
        """source 또는 target table의 Oracle 컬럼 metadata를 조회한다."""
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

    def _fetch_primary_key_columns(self, db_config: dict[str, Any], table: str) -> list[str]:
        """Load target PK columns from Oracle metadata for deterministic record matching."""
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = """
                SELECT CC.COLUMN_NAME
                  FROM ALL_CONSTRAINTS C
                  JOIN ALL_CONS_COLUMNS CC
                    ON CC.OWNER = C.OWNER
                   AND CC.CONSTRAINT_NAME = C.CONSTRAINT_NAME
                 WHERE C.OWNER = :1
                   AND C.TABLE_NAME = :2
                   AND C.CONSTRAINT_TYPE = 'P'
                 ORDER BY CC.POSITION
            """
            params = [owner, table_name]
        else:
            sql = """
                SELECT CC.COLUMN_NAME
                  FROM USER_CONSTRAINTS C
                  JOIN USER_CONS_COLUMNS CC
                    ON CC.CONSTRAINT_NAME = C.CONSTRAINT_NAME
                 WHERE C.TABLE_NAME = :1
                   AND C.CONSTRAINT_TYPE = 'P'
                 ORDER BY CC.POSITION
            """
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [self._lob_to_str(row[0]).strip().upper() for row in cur.fetchall() if self._lob_to_str(row[0]).strip()]

    # ##############################
    # SQL 실행
    # ##############################

    # migration 실행 전 target table을 비우기 위한 TRUNCATE를 수행한다.
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

    # 여러 문장으로 된 MIG_SQL script를 분리해 순서대로 실행한다.
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

    # VERIFY_SQL 결과의 컬럼명으로 SUCCESS_YN과 DIFF_*를 검증한다.
    def _execute_verification(self, db_config: dict[str, Any], sql_script: str) -> tuple[bool, str, list[list[Any]]]:
        statements = self._split_sql_script(sql_script)
        if not statements:
            return False, "No verification SQL provided", []
        if len(statements) != 1:
            return False, "Verification SQL must contain a single aggregate SELECT", []
        clean = self._clean_sql_statement(statements[0])
        if not clean:
            return False, "No verification SQL provided", []
        rows: list[Any] = []
        columns: list[str] = []
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(clean)
            if cur.description:
                columns = [str(column[0]).strip().upper() for column in cur.description]
                rows = cur.fetchall()
        json_rows = [[self._json_safe_value(value) for value in row] for row in rows]
        if not rows:
            return False, "Verification SQL returned no rows", json_rows
        if len(rows) != 1:
            return False, "Verification SQL must return exactly one aggregate row", json_rows
        if len(set(columns)) != len(columns):
            return False, f"Duplicate verification result columns: {columns}", json_rows
        if "SUCCESS_YN" not in columns or "DIFF_TOT" not in columns:
            return False, f"Verification SQL requires SUCCESS_YN and DIFF_TOT; received {columns}", json_rows
        if any(name != "SUCCESS_YN" and not name.startswith("DIFF_") for name in columns):
            return False, f"Verification SQL only supports SUCCESS_YN and DIFF_* columns; received {columns}", json_rows
        row = rows[0]
        if len(row) != len(columns):
            return False, "Verification result column/value count mismatch", json_rows
        values = dict(zip(columns, row))
        success = str(self._lob_to_str(values["SUCCESS_YN"])).strip().upper()
        if success != "Y":
            return False, f"Verification SUCCESS_YN must be Y; received {self._json_safe_value(values['SUCCESS_YN'])}", json_rows
        for name, value in values.items():
            if name.startswith("DIFF_") and not self._is_zero(value):
                return False, f"Verification mismatch: {name}={self._json_safe_value(value)}", json_rows
        return True, "All Verification Passed", json_rows

    # 세미콜론과 PL/SQL 블록 경계를 고려해 SQL script를 실행 단위로 나눈다.
    def _split_sql_script(self, script: str) -> list[str]:
        if not script:
            return []
        return [part.strip() for part in re.split(r"^\s*/\s*$", script, flags=re.M) if part.strip()]

    # 실행 전 SQL 문장의 wrapper/불필요한 구분자를 제거한다.
    def _clean_sql_statement(self, statement: str) -> str:
        cleaned = self._strip_sql_comments(str(statement or "").strip())
        return re.sub(r"[;/]\s*$", "", cleaned).strip()

    # SQL 실행/분리 전에 주석을 제거해 parser 오판을 줄인다. 12C의 SQL 정리 계열과 목적이 같다.
    def _strip_sql_comments(self, sql: str) -> str:
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

    # 검증 결과 값이 숫자 0인지 안전하게 판단한다.
    def _is_zero(self, value: Any) -> bool:
        value = self._lob_to_str(value)
        if value == "":
            return False
        try:
            return Decimal(str(value).strip()) == Decimal("0")
        except (InvalidOperation, ValueError):
            return str(value).strip() == "0"

    # ##############################
    # LLM 호출
    # ##############################

    # 설정된 provider/model 후보로 LLM을 호출하고 JSON 응답을 반환한다.
    def _call_llm_json(self, *, system_anthropic: str, system_openai: str, prompt: str, llm: Any) -> tuple[str, str]:
        """Call only the Langflow-connected LanguageModel; no HTTP fallback exists."""
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("10C requires a connected LanguageModel with an invoke() method")
        from langchain_core.messages import HumanMessage, SystemMessage

        response = llm.invoke(
            [
                SystemMessage(content=system_openai or system_anthropic),
                HumanMessage(content=prompt),
            ]
        )
        text = self._language_model_response_text(response)
        model_name = self._language_model_name(llm)
        if not text:
            raise ValueError(f"Connected LanguageModel returned empty content. model={model_name}")
        return text, model_name

    def _required_llm(self) -> Any:
        llm = getattr(self, "llm", None)
        if llm is None:
            raise ValueError("Connect an official OpenAI-compatible Chat Model to 10C Language Model input.")
        return llm

    def _language_model_name(self, llm: Any) -> str:
        return str(
            getattr(llm, "model_name", None)
            or getattr(llm, "model", None)
            or getattr(llm, "deployment_name", None)
            or type(llm).__name__
        ).strip()

    def _language_model_response_text(self, response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text") is not None:
                    parts.append(str(item["text"]))
            return "".join(parts).strip()
        return str(content or "").strip()

    # LLM 응답에서 JSON 객체 본문만 찾아 dict로 파싱한다. 12C도 같은 패턴을 사용한다.
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

    # LLM이 문자열/list/dict로 돌려준 SQL 값을 하나의 SQL 텍스트로 합친다.
    def _merge_sql_value(self, value: Any) -> str:
        if isinstance(value, list):
            return "\n/\n".join(str(item.get("sql") if isinstance(item, dict) else item) for item in value)
        return str(value or "").strip()

    # ##############################
    # 결과 payload 구성
    # ##############################

    # Langflow/DataFrame payload에 넣을 수 있도록 값을 JSON 안전 형태로 바꾼다.
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

    # 현재 graph context에서 결과 payload에 노출할 SQL 산출물만 모은다.
    def _attempt_outputs(self, context: dict[str, Any]) -> dict[str, Any]:
        """attempt 한 번에 기록할 주요 output 값을 모은다."""
        return {
            "migration_sql": context.get("migration_sql", ""),
            "verification_sql": context.get("verification_sql", ""),
            "affected_rows": context.get("affected_rows", 0),
            "diff_count": context.get("diff_count", 0),
        }

    # migration 결과에 포함할 generated_sqls 목록을 표준 구조로 만든다.
    def _generated_sql_list(self, existing: Any, map_id: Any, migration_sql: Any, verification_sql: Any, generated_columns: list[str]) -> list[dict[str, Any]]:
        # This list is an attempt-scoped handoff to 17C, not a cumulative
        # history.  In particular, FAIL-TEST2 must not reformat MIG_SQL or
        # VERIFY_SQL generated during an older attempt.
        result: list[dict[str, Any]] = []
        for column, value in (("MIG_SQL", migration_sql), ("VERIFY_SQL", verification_sql)):
            if column in {str(item).upper() for item in generated_columns} and str(value or "").strip():
                result.append(
                    {
                        "table": "NEXT_MIG_INFO",
                        "key_column": "MAP_ID",
                        "key_value": int(map_id),
                        "column": column,
                        "source_component": self.name,
                    }
                )
        return self._dedupe_generated_sql_list(result)

    # 같은 stage/name의 generated SQL 항목이 중복되지 않게 정리한다. 12C에도 같은 목적의 헬퍼가 있다.
    def _dedupe_generated_sql_list(self, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in values:
            key = (
                str(item.get("table") or "").upper(),
                str(item.get("key_column") or "").upper(),
                str(item.get("key_value") or ""),
                str(item.get("column") or "").upper(),
            )
            if key in seen or not key[-1]:
                continue
            seen.add(key)
            result.append(item)
        return result

    # 10C 실행 결과를 dashboard/후속 노드가 읽는 표준 payload로 만든다.
    def _result(self, job: dict[str, Any], *, ok: bool, status: str, elapsed: int, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        """현재 작업의 Langflow output payload를 만든다."""
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

    # ##############################
    # 설정 및 입력 파싱
    # ##############################

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다. 12C도 같은 입력 정규화를 사용한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        """Langflow Data, Message, dict, JSON 문자열 payload를 dict로 파싱한다."""
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

    # 숫자 입력을 양의 정수로 변환하고 실패하면 기본값을 사용한다. 12C도 같은 유틸을 둔다.
    def _positive_int(self, value: Any, default: int) -> int:
        """값을 양의 정수로 변환하고 실패하면 기본값을 반환한다."""
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    # Langflow Secret 입력을 일반 문자열로 꺼낸다. 12C/15C/17C도 같은 처리 흐름을 쓴다.
    def _secret_to_str(self, value: Any) -> str:
        """Langflow Secret 값을 일반 문자열로 변환한다."""
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    # MAP_ID처럼 정수여야 하는 값을 int 또는 None으로 변환한다.
    def _to_int(self, value: Any) -> int | None:
        """값을 int로 변환하고 유효하지 않으면 None을 반환한다."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # payload와 Langflow 입력에서 Oracle 접속/schema 설정을 모은다.
    def _db_config(self, job: dict[str, Any]) -> dict[str, Any]:
        """job payload에서 Oracle 접속 설정을 추출한다."""
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

    # ##############################
    # DB 및 identifier helper
    # ##############################

    # 값이 Oracle 물리 테이블명 형태인지 간단히 판단한다.
    def _looks_like_table(self, value: Any) -> bool:
        """값이 단순 Oracle table identifier이면 True를 반환한다."""
        text = str(value or "").strip()
        if not text:
            return False
        if re.search(r"\bSELECT\b|\bWITH\b|\s", text, flags=re.I):
            return False
        parts = text.split(".")
        return all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", part.strip()) for part in parts)

    # Oracle LOB 값을 연결 종료 전에 문자열로 읽는다. 12C/15C/17C도 같은 이유로 사용한다.
    # Oracle identifier로 안전한 문자만 허용하고 대문자 형태로 반환한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # Source table에는 source_schema를 붙인다. 이미 schema-qualified이면 그대로 둔다.
    def _qualify_fr_table(self, table_name: str, db_config: dict[str, Any]) -> str:
        return self._qualify_runtime_table(table_name, db_config.get("source_schema"))

    # Target table에는 target_schema를 붙인다. 이미 schema-qualified이면 그대로 둔다.
    def _qualify_to_table(self, table_name: str, db_config: dict[str, Any]) -> str:
        return self._qualify_runtime_table(table_name, db_config.get("target_schema"))

    def _qualify_runtime_table(self, table_name: str, schema: Any) -> str:
        table = str(table_name or "").strip()
        if not table or "." in table:
            return table
        clean_table = self._clean_identifier(table)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    # COMPLEX FR_TABLE SQL에서 FROM/JOIN 뒤의 물리 table에 source_schema를 붙인다.
    def _qualify_source_tables_in_sql(self, sql_text: str, db_config: dict[str, Any]) -> str:
        source_schema = str(db_config.get("source_schema") or "").strip().upper()
        if not source_schema:
            return str(sql_text or "")

        def replace(match: re.Match[str]) -> str:
            prefix = match.group(1)
            table_name = match.group(2)
            if "." in table_name:
                return match.group(0)
            return f"{prefix}{self._clean_identifier(source_schema)}.{self._clean_identifier(table_name)}"

        return re.sub(
            r"(\b(?:FROM|JOIN)\s+)([A-Z_][A-Z0-9_$#]*)",
            replace,
            str(sql_text or ""),
            flags=re.IGNORECASE,
        )

    # OWNER.TABLE 형태를 Oracle metadata 조회용 owner/table_name으로 분리한다.
    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return self._clean_identifier(owner), self._clean_identifier(name)
        return None, self._clean_identifier(value)

    def _lob_to_str(self, value: Any) -> str:
        """Oracle LOB 및 nullable 값을 문자열로 변환한다."""
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    # Oracle metadata에서 컬럼명 set을 조회한다. 저장 우회가 아니라 실행/DDL 판단용이다.
    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        """테이블의 컬럼명을 대문자 set으로 반환한다."""
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

    # Oracle metadata에서 컬럼 타입을 조회해 CLOB 처리나 비교식을 결정한다.
    def _table_column_types(self, db_config: dict[str, Any], table: str) -> dict[str, str]:
        """테이블의 대문자 컬럼명과 Oracle data type을 반환한다."""
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
    # Oracle 연결을 열고 사용 후 정리하는 context manager다. 12C 계열 실행기와 같은 패턴이다.
    def _connect(self, db_config: dict[str, Any]):
        """Oracle DB 연결을 열고 닫는다."""
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

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str, schema: Any) -> str:
        """검증된 schema-qualified Oracle table 이름을 반환한다."""
        value = str(table_name or "").strip().upper()
        if "." in value:
            return value
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if not clean_schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        clean_schema = self._clean_identifier(clean_schema)
        return f"{clean_schema}.{clean_table}"
