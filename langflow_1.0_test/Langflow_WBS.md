# SmartMigrate Langflow 구현 WBS

## 0. 작성 기준

이 WBS는 `src/smart_migrate`의 기존 Python/LangGraph 기반 로직을 Langflow 기반 Flow, Agent, Custom Component, Tool 구조로 재구현하기 위한 개발 작업 분해 문서이다.

큰 방향은 다음 순서로 진행한다.

1. 먼저 Langflow에서 유지할 실행 틀과 경계를 설계한다.
2. 그 다음 각 틀에 들어갈 기능을 개발한다.
3. 마지막으로 기존 배치/DB/LLM 동작과 동일한지 검증한다.

권장 방향은 "Langflow는 시각적 오케스트레이션과 Agent/Tool 연결 지점으로 사용하고, DB 변경/SQL 실행/retry/state persistence는 deterministic Python Custom Component 안에서 처리"하는 구조이다. 특히 Migration, SQL Conversion처럼 중간 상태와 retry가 많은 로직은 Langflow node를 과하게 쪼개지 않는 것이 안전하다.

## 1. 주요 설계 결정 질문

| No | 결정 항목 | 권장안 | 대안 | 확인 질문 |
| --- | --- | --- | --- | --- |
| D-01 | Supervisor Tool 선택 방식 | Polling은 코드가 먼저 수행하고, LLM은 이미 조회된 job 중 실행할 Agent Tool 1개만 선택 | LLM이 `poll_jobs`까지 Tool Mode로 직접 선택 | 배치 안정성을 우선할 것인가, Agent 자율성을 우선할 것인가? |
| D-02 | Chat Agent Tool 선택 방식 | LLM Tool Mode 사용. 사용자의 자연어 요청을 command JSON으로 변환 | 규칙 기반 분기 처리 | 사용자가 자연어로 요청하는 운영 콘솔 성격을 유지할 것인가? |
| D-03 | Migration/Conversion 내부 단계 분해 | Custom Component 내부에서 end-to-end 처리 | Generate/Execute/Verify를 Langflow node로 분리 | 디버깅 가시성보다 retry/state 안정성을 더 우선해도 되는가? |
| D-04 | 상태 저장 위치 | Oracle 업무 테이블과 log/control table 기준 | Langflow runtime state/edge로 전달 | 재시작, 중복 실행, 장애 복구를 DB 기준으로 할 것인가? |
| D-05 | 기존 `src` 재사용 방식 | Langflow Component가 기존 service/repository를 import할 수 있게 패키지화 | Langflow component 안에 필요한 로직 복사 | Langflow 배포 환경에서 `smart_migrate` package import를 보장할 수 있는가? |
| D-06 | Background Batch 실행 방식 | `NEXT_BATCH_CONTROL` 기반 lock/heartbeat/stop 제어 | Langflow 요청 1회당 cycle 1회 실행 | 배치가 Langflow 요청 종료 후에도 계속 살아 있어야 하는가? |
| D-07 | Tuning/Formatting 실행 시점 | Conversion PASS 후 continuation 또는 별도 job priority | 완전 독립 Agent로만 실행 | 운영상 conversion 완료 즉시 후속 처리가 필요한가? |
| D-08 | LLM 호출 위치 | Langflow `Language Model` 컴포넌트를 기본으로 사용하고, 업무 Component는 prompt payload 생성/응답 파싱/DB 저장을 담당 | 업무 Component 내부에서 직접 LLM API 호출 | Langflow 화면에서 모델/temperature/base_url 등을 교체하고 관찰 가능해야 하는가? |

## 2. 전체 WBS 요약

| WBS | 단계 | 목표 산출물 |
| --- | --- | --- |
| 1 | AS-IS 분석 및 이관 범위 확정 | 기존 로직-Flow 매핑표, 제외/유지 범위 |
| 2 | Langflow 아키텍처 설계 | Agent/Tool/Component 경계, Tool 선택 정책, 상태 저장 정책 |
| 3 | 공통 런타임 기반 설계/개발 | import/package, DB, LLM, prompt, RAG/embedding, repository, 공통 알고리즘 사전 검증 |
| 4 | Chat Agent 틀 개발 | 사용자 요청 라우팅 Agent, command JSON 계약, 공통 Tool |
| 5 | Supervisor Agent 틀 개발 | batch loop, polling, tool routing, control/log 구조 |
| 6 | DB Migration 기능 개발 | migration job 조회, DDL 조회, SQL 생성/실행/검증/retry |
| 7 | SQL Conversion 기능 개발 | TO_SQL, BIND_SQL, TEST_SQL 생성/검증/retry |
| 8 | SQL Tuning 기능 개발 | rule/RAG 조회, 튜닝 SQL 생성, tuned test 검증 |
| 9 | SQL Formatting 기능 개발 | formatted SQL 생성 및 저장 |
| 10 | 운영 보조 Tool 개발 | dashboard, 실패 분석, 재실행, RAG rule 조회 |
| 11 | Flow 조립 및 프롬프트 작성 | Langflow Flow, Agent prompt, Tool descriptions |
| 12 | 테스트 및 검증 | 단위/통합/회귀 테스트, 운영 시나리오 검증 |
| 13 | 배포 및 운영 문서화 | Langflow 배포 절차, 환경 변수, runbook |

## 3. 상세 WBS

### 1. AS-IS 분석 및 이관 범위 확정

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 1.1 | 기존 모듈 책임 정리 | `supervisor`, `db_migration`, `sql_conversion`, `sql_tuning`, `sql_formatting`, `repositories`, `integrations` 책임 정리 | AS-IS 모듈 책임표 | - |
| 1.2 | Job table 및 상태 체계 정리 | `NEXT_MIG_INFO`, `NEXT_SQL_INFO`, log/control table, PASS/FAIL/WAITING/SKIP 상태 정리 | DB 상태 전이표 | 1.1 |
| 1.3 | LLM 호출 지점 정리 | migration prompt, conversion prompt, tuning prompt, formatting prompt 호출 위치 정리 | LLM 호출 매핑표 | 1.1 |
| 1.4 | Langflow 이관 범위 확정 | 기존 `src`를 그대로 import할지, Langflow component 내부로 이식할지 결정 | 이관 범위 결정서 | 1.1 |
| 1.5 | 우선순위 확정 | 1차 범위를 Chat/Supervisor/Migration/Conversion으로 제한할지 결정 | Phase별 scope | 1.4 |

### 2. Langflow 아키텍처 설계

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 2.1 | Flow 구조 설계 | Chat Flow, Supervisor Flow, 업무별 Command Tool Flow 분리 | 전체 Flow 구조도 | 1 |
| 2.2 | Agent와 Tool 책임 분리 | Agent는 routing/command 생성, Tool은 DB/LLM/SQL 실행 담당으로 경계 정의 | Agent/Tool 책임표 | 2.1 |
| 2.3 | Tool 선택 구조 설계 | 분기 처리 방식과 LLM Tool Mode 적용 위치 결정 | Tool routing 정책 | 2.2 |
| 2.4 | Command JSON 계약 설계 | action, job identifier, optional payload, response schema 정의 | command schema 문서 | 2.2 |
| 2.5 | 상태 저장 전략 설계 | runtime state, DB state, log state 구분 | state persistence 정책 | 2.4 |
| 2.6 | 예외/retry 설계 | LLM 오류, DB 오류, SQL 실행 오류, 검증 실패 retry 정책 정의 | retry/error policy | 2.5 |
| 2.7 | 보안 설계 | DB password/API key/prompt 본문이 Agent message에 노출되지 않도록 input boundary 정의 | secret handling 정책 | 2.4 |
| 2.8 | LLM Component 연동 설계 | Langflow Language Model 컴포넌트 입력/출력 구조와 기존 `SqlLlmService` 역할 재배치 | LLM wiring 정책 | 2.2 |
| 2.9 | 공통 Flow shell 분류 | 전체 기능을 조회형/실행형/생성형/검증형/운영형 등 껍데기 패턴으로 분류 | Flow shell catalog | 2.1 |

#### 2.10 공통 Flow Shell 분류

Langflow 전환 시 먼저 해야 할 일은 개별 기능을 바로 구현하는 것이 아니라, 기능들이 어떤 공통 껍데기(shell)를 공유하는지 나누는 것이다. 같은 shell을 쓰는 기능은 입력, 출력, 오류 처리, 화면 표시, 로그 정책을 공통화할 수 있다.

##### 2.10.1 Flow Shell Catalog

| Shell ID | Shell 이름 | 목적 | 기본 Flow 형태 | 출력 디자인 기준 | 대표 기능 |
| --- | --- | --- | --- | --- | --- |
| SH-01 | 단순 DB 조회 shell | table/status/log/rule 등을 조회해서 보여준다 | User/Agent -> Query Tool -> Result Formatter -> Chat/Table Output | 짧은 요약 + markdown table + row count + next action 제안 | pending 조회, status 조회, RAG rule 조회, failure summary |
| SH-02 | 상세 조회 shell | 특정 `map_id`/`row_id`의 상세 정보와 SQL/CLOB을 보여준다 | Identifier Input -> Detail Loader -> Detail Formatter -> Output | summary section + metadata table + SQL code block + log tail | migration detail, SQL job detail, generated SQL preview |
| SH-03 | Command enqueue shell | 사용자의 실행 요청을 DB command queue에 등록한다 | Agent -> Command Builder -> Queue Writer -> Confirmation Output | command id/run target/status를 간결히 표시 | migration 실행 요청, SQL conversion 실행 요청, rerun 요청 |
| SH-04 | 즉시 실행 shell | 특정 job을 현재 Flow에서 바로 실행한다 | Input -> Job Loader -> Executor Tool -> Persist -> Result Output | 실행 단계별 status + 최종 상태 + log id + 실패 원인 | 수동 migration 실행, 수동 conversion 실행 |
| SH-05 | LLM 생성 shell | prompt를 만들고 Language Model을 거쳐 결과를 파싱/저장한다 | Context Builder -> Prompt Builder -> Language Model -> Parser -> Persist | prompt preview option + generated SQL code block + validation status | MIG_SQL, VERIFY_SQL, TO_SQL, BIND_SQL, TEST_SQL 생성 |
| SH-06 | RAG 검색 shell | SQL/context 기반으로 RAG rule/example을 검색한다 | Context Input -> Rule Loader -> Embedding/FAISS or Fallback -> Result Formatter | block별 top-k table + score + guidance + fallback 여부 | tuning rule search, conversion example search |
| SH-07 | 검증/비교 shell | SQL 실행 결과나 count 비교로 PASS/FAIL을 판정한다 | SQL/Input Loader -> SQL Executor -> Evaluator -> Persist/Output | case별 count table + PASS/FAIL badge + retry context | TEST_SQL 실행, tuned SQL 비교, verify SQL 실행 |
| SH-08 | 편집/저장 shell | 사용자가 수정한 SQL/prompt/config를 저장한다 | Form/Text Input -> Validator -> DB Update -> Confirmation Output | 저장 전/후 diff 또는 저장 대상 요약 | save_user_sql, prompt override, reset tuning state |
| SH-09 | Batch control shell | background worker의 start/stop/status를 제어한다 | Control Command -> Lock/Heartbeat Handler -> Control Output | current status + run_id + heartbeat + active job | supervisor start, stop, status |
| SH-10 | Failure analysis shell | 실패 row/log/error/sql을 모아 원인을 요약한다 | Failed Job Loader -> Log Aggregator -> Optional LLM Summary -> Output | error summary + failed stage + SQL/log snippets + rerun action | failure_summary, analyze_failure |
| SH-11 | Self-test/readiness shell | 런타임 의존성과 설정 상태를 점검한다 | Self-test Command -> Check Runner -> Readiness Formatter | PASS/WARN/FAIL/BLOCKED matrix + blocker list | test_connection, test_llm, test_rag, self_test |
| SH-12 | Dashboard shell | 여러 조회 결과를 한 화면 요약으로 보여준다 | Multi Query Tool -> Aggregator -> Dashboard Formatter | KPI summary + grouped tables + stale/running warning | batch dashboard, job dashboard |

##### 2.10.2 Shell별 공통 입출력 계약

| Shell | 공통 입력 | 공통 출력 | 공통 오류 처리 |
| --- | --- | --- | --- |
| SH-01 단순 DB 조회 | filter, limit, sort, status, optional identifier | `summary`, `columns`, `rows`, `row_count`, `next_actions` | query 실패, table 없음, 권한 없음 |
| SH-02 상세 조회 | `map_id` 또는 `row_id`, include_sql, include_logs | `metadata`, `sql_blocks`, `logs`, `status` | identifier 없음, row 없음, CLOB read 실패 |
| SH-03 Command enqueue | action, target id, priority, requested_by | `command_id`, `queued_at`, `target`, `status` | 중복 command, invalid target, queue table 없음 |
| SH-04 즉시 실행 | target id, mode, dry_run, max_attempts | `final_status`, `steps`, `logs`, `saved_fields` | 실행 중 예외, retry 초과, DB write 실패 |
| SH-05 LLM 생성 | prompt context, prompt_name, retry_context, metadata | `prompt_preview`, `raw_output`, `parsed_output`, `saved_fields` | LLM output 파싱 실패, empty response, SQL sanitizer 실패 |
| SH-06 RAG 검색 | sql_text, category, source_tables, top_k | `blocks`, `matches`, `search_method`, `embedding_model` | embedding 실패, FAISS 실패, fallback 결과 없음 |
| SH-07 검증/비교 | SQL, bind_set, baseline/candidate SQL | `cases`, `pass_fail`, `error_summary`, `retry_context` | SQL 실행 실패, count column 누락 |
| SH-08 편집/저장 | target id, field values, validate_only | `updated_fields`, `diff_summary`, `status` | validation 실패, column length 초과 |
| SH-09 Batch control | command, run_id, heartbeat_timeout | `control_status`, `run_id`, `heartbeat_at`, `active_job` | lock 획득 실패, stale worker 충돌 |
| SH-10 Failure analysis | target id 또는 status filter, include_logs | `failure_stage`, `last_error`, `related_sql`, `recommendation` | 로그 없음, 실패 원인 불명 |
| SH-11 Self-test | check_scope, timeout, include_optional | `checks`, `summary`, `blockers` | dependency 없음, secret 누락 |
| SH-12 Dashboard | period, status filter, limit | `kpis`, `groups`, `warnings`, `links` | 일부 query 실패 시 partial result |

##### 2.10.3 출력 디자인 공통 규칙

| 출력 유형 | 디자인 기준 | 적용 Shell |
| --- | --- | --- |
| 요약 문장 | 첫 줄에 현재 상태와 핵심 숫자를 표시한다 | 전체 |
| Markdown table | 10~20행 이내 조회 결과에 사용한다. 긴 CLOB/SQL은 table에 직접 넣지 않는다 | SH-01, SH-06, SH-07, SH-12 |
| Metadata table | job id, status, schema, table, updated_at 같은 고정 필드 표시 | SH-02, SH-04, SH-09 |
| SQL code block | SQL/CLOB은 별도 fenced code block으로 표시하고 table cell에 넣지 않는다 | SH-02, SH-05, SH-07, SH-10 |
| Step list | 실행형 기능은 load/generate/execute/verify/save 단계를 순서대로 표시한다 | SH-04, SH-05, SH-07 |
| PASS/WARN/FAIL matrix | self-test와 readiness report에 사용한다 | SH-11 |
| Next actions | 조회 후 가능한 action을 1~3개만 제안한다 | SH-01, SH-02, SH-10, SH-12 |
| Secret masking | API key/password/DSN credential은 항상 masking한다 | 전체 |

##### 2.10.4 기존 기능별 Shell 매핑

| 기능 | Shell | 기존 모듈/파일 | 비고 |
| --- | --- | --- | --- |
| batch status 조회 | SH-01, SH-09 | `Supervisor_Agent.py`, `SupervisorJobRegistry.py` | control row와 active job 표시 |
| pending migration 조회 | SH-01 | `MigrationJobRepository.py`, `SupervisorJobPolling.py` | markdown table 출력 |
| pending SQL conversion 조회 | SH-01 | `SqlJobRepository.get_pending_jobs` | row_id 중심 출력 |
| pending tuning 조회 | SH-01 | `SqlJobRepository.get_tuning_jobs` | conversion PASS 조건 표시 |
| pending formatting 조회 | SH-01 | `SqlJobRepository.get_formatting_jobs` | FORMATTED_SQL empty 조건 표시 |
| migration 상세 조회 | SH-02 | `MigrationJobRepository.py` | DDL/MIG_SQL/VERIFY_SQL은 code block |
| SQL job 상세 조회 | SH-02 | `SqlJobRepository.get_sql_job_by_row_id` | FR/TO/BIND/TEST/TUNED SQL 분리 |
| migration 실행 queue 등록 | SH-03 | `Chat_Command_Tool.py`, `NEXT_BATCH_COMMAND` | 실제 실행은 Supervisor |
| SQL conversion queue 등록 | SH-03 | `Chat_Command_Tool.py`, `NEXT_BATCH_COMMAND` | row_id 또는 space/sql_id |
| migration 즉시 실행 | SH-04, SH-05, SH-07 | `MigrationGraph.py`, `MigrationExecuteNode.py`, `MigrationVerifyNode.py` | generate/execute/verify 단계 |
| SQL conversion 즉시 실행 | SH-04, SH-05, SH-07 | `SqlConversionCoordinator.py`, `SqlConversionValidateNode.py` | TO/BIND/TEST 단계 |
| TO_SQL 생성 | SH-05, SH-06 | `SqlLlmService.generate_tobe_sql`, `SqlTuningRuleRetrieveNode.py` | Conversion RAG 포함 |
| BIND_SQL 생성 | SH-05, SH-07 | `generate_bind_sql`, `SqlBindCases.py` | bind set 생성 포함 |
| TEST_SQL 생성/검증 | SH-05, SH-07 | `generate_test_sql`, `execute_test_query` | count 비교 |
| TUNED_TO_SQL 생성 | SH-05, SH-06, SH-07 | `tune_tobe_sql`, `SqlTuningWorkflow.py` | RAG + tuned 비교 |
| FORMATTED_SQL 생성 | SH-05 | `SqlFormattingWorkflow.py` | LLM output parser 단순 |
| RAG rule 조회 | SH-06 | `SqlTuningRuleRetrieveNode.py` | FAISS/fallback 표시 |
| save_user_sql | SH-08 | `MigrationGraph.py`, `SqlConversionCoordinator.py` user edited branch | 저장 전 validation 필요 |
| reset/rerun | SH-08, SH-03 | `SqlJobRepository.reset_tuning_state`, command queue | reset 후 enqueue 가능 |
| failure summary | SH-10 | `SqlLogRepository.py`, job repositories | LLM summary는 선택 |
| self_test | SH-11 | 3.10 checklist | readiness report |
| dashboard | SH-12 | dashboard command tool | 여러 조회 결과 집계 |

### 3. 공통 런타임 기반 설계/개발

이 단계는 단순 공통 유틸 개발이 아니라, 기존 `src` 로직이 Langflow runtime 안에서 동일하게 import, 설정, 실행, 검증되는지 먼저 확인하는 사전 리스크 제거 단계이다. 여기서 실패하는 항목은 Migration/Conversion/Tuning/Formatting 기능 개발 전에 해결해야 한다.

#### 3.1 기존 런타임 의존성 매핑

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 환경변수/설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.1.1 | Langflow Python import path 검증 | 전체 `smart_migrate` package | `smart_migrate.*`, `python-dotenv` | `PYTHONPATH`, `.env` 위치, Langflow custom component path | Langflow component에서 `import smart_migrate` 성공 여부 확인 | import path 검증 결과 | 2 |
| 3.1.2 | 의존 패키지 설치 상태 검증 | 전체 runtime | `requests`, `oracledb`, `langchain`, `langgraph`, `langchain_openai`, `langchain_anthropic`, `openai`, `faiss`, `numpy` | `requirements.txt` | Langflow runtime에서 각 패키지 import smoke test | dependency matrix | 3.1.1 |
| 3.1.3 | 환경 변수 로딩 방식 검증 | `config/AppSettings.py`, 각 service의 `load_dotenv` | `dotenv.load_dotenv` | `DB_*`, `ORACLE_SCHEMA*`, `LLM_*`, `RAG_*` | Langflow 실행 프로세스에서 `.env`와 Component input 중 어떤 값이 우선되는지 확인 | config loading 정책 | 3.1.1 |
| 3.1.4 | Component input 표준화 | 모든 Langflow Component | Langflow input field | DB, schema, LLM, prompt, timeout, batch 설정 | 동일 input 이름으로 Chat/Supervisor/Tool에서 재사용 가능한지 확인 | 공통 input spec | 3.1.3 |
| 3.1.5 | Secret 전달 방식 검증 | Chat/Supervisor Agent | Langflow secure input | DB password, LLM API key, RAG API key | secret이 Agent message, command JSON, log에 섞이지 않는지 확인 | secret handling checklist | 3.1.4 |

#### 3.2 Oracle DB 런타임 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 환경변수/설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.2.1 | Oracle client 초기화 검증 | `integrations/oracle/OracleConnection.py` | `oracledb` | `ORACLE_CLIENT_PATH` | Thin/Thick mode에서 접속 가능한지 확인 | Oracle 접속 검증 결과 | 3.1 |
| 3.2.2 | DSN 구성 검증 | `get_connection()` | `oracledb.connect` | `DB_USER`, `DB_PASS`, `DB_HOST`, `DB_PORT`, `DB_SID` | Langflow runtime에서 실제 DB 접속 및 `SELECT 1 FROM DUAL` 실행 | DB smoke test | 3.2.1 |
| 3.2.3 | Schema qualification 검증 | `qualify_table_name`, `qualify_fr_table`, `qualify_to_table` | `re`, `os` | `ORACLE_SCHEMA`, `ORACLE_SCHEMA_SRC`, `ORACLE_SCHEMA_TGT` | system/source/target schema가 정확히 붙는지 확인 | schema qualify 테스트 | 3.2.2 |
| 3.2.4 | 필수 테이블 접근 검증 | repositories 전체 | Oracle catalog query | `NEXT_MIG_INFO`, `NEXT_MIG_INFO_DTL`, `NEXT_SQL_INFO`, `NEXT_MIG_RAG_INFO`, log/control table | 각 테이블 `SELECT COUNT(*)` 또는 column 조회 가능 여부 확인 | table readiness matrix | 3.2.3 |
| 3.2.5 | CLOB read 검증 | `SqlJobRepository._to_text`, RAG service `_to_text` | Oracle LOB object | CLOB 컬럼: `FR_SQL`, `TO_SQL`, `BIND_SQL`, `TEST_SQL`, `MIG_SQL`, `VERIFY_SQL` | 긴 SQL/CLOB이 잘리지 않고 문자열로 변환되는지 확인 | CLOB 변환 테스트 결과 | 3.2.4 |
| 3.2.6 | Transaction/commit/rollback 검증 | repository update 함수 | `oracledb` transaction | job/log table | 테스트 row에 update 후 commit/rollback 동작 확인 | DB write 테스트 결과 | 3.2.4 |
| 3.2.7 | Column compatibility 검증 | `SqlJobRepository._get_available_columns`, `_get_column_data_lengths` | `ALL_TAB_COLUMNS`, `USER_TAB_COLUMNS` | `STATUS_CONVERSION`, `STATUS_TUNING`, `FORMATTED_SQL`, `BLOCK_RAG_CONTENT`, `RETRY_COUNT` | 운영 DB 스키마와 코드가 기대하는 컬럼 차이 확인 | column compatibility report | 3.2.4 |

#### 3.3 LLM 런타임 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 환경변수/설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.3.1 | Langflow Language Model 컴포넌트 연결 검증 | 기존 `SqlLlmService.call_llm_text_api` 대체 wiring | Langflow Language Model node | model/base_url/api_key/temperature/max_tokens는 LLM node input | Prompt Component -> Language Model -> Response Parser 연결이 가능한지 확인 | LLM component wiring 결과 | 3.1 |
| 3.3.2 | LLM 입력 타입 검증 | `PromptLoader.build_prompt_messages` 역할 재배치 | Langflow Message/Text/Data | prompt messages, system/user content | Language Model이 받는 입력이 `Message`인지 `Text`인지 확인하고 adapter 필요 여부 판단 | LLM input type mapping | 3.3.1 |
| 3.3.3 | LLM 출력 타입 검증 | `SqlLlmService._extract_sql_text` 전 단계 | Langflow Message/Text/Data | LLM response object | LLM output에서 content text를 안정적으로 추출할 수 있는지 확인 | LLM output adapter 결과 | 3.3.1 |
| 3.3.4 | 내부 LLM 호출 fallback 범위 결정 | `LlmClient.py`, `SqlLlmService.call_llm_text_api`, `LlmFallback.py` | `openai`, `langchain_openai`, `langchain_anthropic` | `LLM_*`, `LLM_FALLBACK_MODELS` | Langflow LLM node 사용이 불가능한 batch/background 경로에서만 내부 호출을 허용할지 결정 | internal LLM fallback 정책 | 3.3.1 |
| 3.3.5 | SQL 응답 추출/정규화 검증 | `SqlLlmService._extract_sql_text`, `_normalize_oracle_sql` | `re`, `json` | prompt별 응답 format | Language Model output text에서 code block, MyBatis tag, semicolon, SQLPlus `/`, `LIMIT` 변환 테스트 | SQL sanitizer 테스트 | 3.3.3 |
| 3.3.6 | Tuning 응답 JSON 파싱 검증 | `SqlLlmService._extract_tuning_response` | `json`, `re` | `tobe_sql_tuning_prompt.json` | Language Model output text에서 `{tuned_sql,tuned_result}` JSON, code block, label format 모두 파싱되는지 확인 | tuning response parser 테스트 | 3.3.3 |
| 3.3.7 | LLM 호출 로그 책임 재설계 | 기존 `_call_llm_for_job` 내부 log 분리 | `SqlLogRepository.insert_sql_log` | `NEXT_SQL_LOG` | 호출은 LLM node, 로그는 Parser/Persist Component가 맡도록 metadata 전달 가능 여부 확인 | LLM logging redesign | 3.3.1, 3.2.6 |

#### 3.4 Prompt 런타임 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 파일/설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.4.1 | Prompt file 로딩 검증 | `integrations/llm/PromptLoader.py` | `json`, `Path` | `src/smart_migrate/config/prompts/*.json` | UTF-8-SIG JSON 로딩 성공 여부 확인 | prompt loading 결과 | 3.1 |
| 3.4.2 | Prompt 변수 치환 검증 | `render_prompt_template`, `_SafeFormatDict` | Python format | `{from_sql}`, `{mapping_schema_text}`, `{last_error}` 등 | 누락 변수는 `{key}`로 남고 crash가 나지 않는지 확인 | prompt render 테스트 | 3.4.1 |
| 3.4.3 | Prompt message 구조 검증 | `build_prompt_messages` | prompt JSON schema | `system`, `user_instruction`, `inputs`, `rules` | LangChain `SystemMessage/HumanMessage`로 변환 가능한지 확인 | prompt message 테스트 | 3.4.2 |
| 3.4.4 | SQL/CLOB prompt 삽입 검증 | `_render_input_block`, `_detect_block_type` | `json`, `re` | 긴 SQL, JSON RAG context | 긴 SQL과 JSON RAG context가 escape/줄바꿈 손상 없이 들어가는지 확인 | long prompt 테스트 | 3.4.3 |
| 3.4.5 | Prompt별 input 매핑표 작성 | Migration/Conversion/Tuning/Formatting prompt | prompt JSON files | `migration_prompt.json`, `tobe_sql_prompt.json`, `bind_sql_prompt.json`, `test_sql_prompt.json`, `tobe_sql_tuning_prompt.json`, `sql_indent_format_prompt.json` | 각 prompt가 필요한 입력을 기존 job/repository에서 가져올 수 있는지 확인 | prompt input mapping table | 3.4.4 |

#### 3.5 RAG/Embedding 런타임 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 환경변수/설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.5.1 | Tuning/Conversion RAG service import 검증 | `agents/sql_tuning/SqlTuningRuleRetrieveNode.py` | `requests`, `faiss`, `numpy`, `dotenv` | `RAG_INFO_TABLE`, `TOBE_SQL_TUNING_TOP_K` | `tobe_sql_tuning_service` import 및 객체 생성 확인 | RAG import 테스트 | 3.1.2 |
| 3.5.2 | Embedding endpoint 설정 검증 | `TobeSqlTuningService._embedding_endpoint` | `requests` | `RAG_EMBED_BASE_URL`, `RAG_EMBED_API_KEY`, `RAG_EMBED_MODEL=BAAI/bge-m3`, `RAG_EMBED_TIMEOUT_SEC` | `/v1/embeddings` endpoint 자동 보정과 인증 header 확인 | embedding config report | 3.5.1 |
| 3.5.3 | Embedding API smoke test | `TobeSqlTuningService._embed_texts` | `requests` | `RAG_EMBED_MODEL` 기본값 `BAAI/bge-m3` | 샘플 SQL 2개 embedding 호출, vector count/dimension 확인 | embedding smoke test | 3.5.2 |
| 3.5.4 | FAISS vector search 검증 | `_retrieve_by_vector_search` | `faiss-cpu==1.8.0`, `numpy==1.26.4` | `TOBE_SQL_TUNING_TOP_K` | embedding vector normalize, `IndexFlatIP`, top_k 검색 동작 확인 | FAISS 검색 테스트 | 3.5.3 |
| 3.5.5 | Lexical fallback 검증 | `_build_lexical_match_payload`, `_lexical_similarity` | `re` | embedding endpoint 미설정 또는 faiss import 실패 | vector search 실패 시 token fallback으로 결과가 나오는지 확인 | fallback 검색 테스트 | 3.5.1 |
| 3.5.6 | RAG rule table 조회 검증 | `_load_search_rules`, `_load_general_rules` | Oracle connection | `NEXT_MIG_RAG_INFO`, `CATEGORY`, `RULE_TYPE`, `USE_YN`, `SOURCE_TABLES` | `SQL_TUNING`/`SQL_CONVERSION`, `SEARCH`/`GENERAL` rule 조회 가능 여부 확인 | RAG table readiness report | 3.2.4 |
| 3.5.7 | Source table filter 검증 | `_parse_source_tables`, `_source_tables_match` | `re` | job `target_table`, RAG `SOURCE_TABLES` | table명이 schema 포함/미포함일 때 rule filtering이 맞는지 확인 | table filter 테스트 | 3.5.6 |
| 3.5.8 | SQL block split 검증 | `_split_sql_into_blocks`, `_normalize_sql_shape` | `re` | subquery 포함 SQL | MAIN/SUBQUERY 분해, literal/number normalization 확인 | block split 테스트 | 3.5.1 |
| 3.5.9 | RAG hit count update 검증 | `increment_rule_hit_counts` | Oracle update | `HIT_CNT`, `UPDATED_AT` | 성공한 rule id의 hit count 증가 여부 확인 | hit count 테스트 | 3.5.6 |
| 3.5.10 | Correct SQL Hint RAG 상태 판정 | `agents/sql_conversion/CorrectSqlRagService.py` | `requests`, `faiss`, `numpy`, `SqlJobRepository.get_feedback_corpus_rows` | `CORRECT_SQL_HINT_TOP_K`, `CORRECT_SQL_HINT_CORPUS_LIMIT`, `RAG_EMBED_*` | 현재 corpus loader가 빈 리스트를 반환하므로 실제 사용 여부와 구현 필요 여부 확인 | Correct SQL Hint RAG 리스크 판정 | 3.5.1 |
| 3.5.11 | Conversion RAG 연동 검증 | `SqlLlmService.generate_tobe_sql` | `tobe_sql_tuning_service.retrieve_conversion_examples` | `CATEGORY=SQL_CONVERSION` | TO_SQL prompt에 GENERAL/SEARCH RAG context가 들어가는지 확인 | conversion RAG prompt 테스트 | 3.5.6, 3.4.5 |
| 3.5.12 | Tuning RAG 연동 검증 | `SqlLlmService.tune_tobe_sql`, `generate_bind_tuned_sql` | `retrieve_tuning_examples`, `load_universal_tuning_rules` | `CATEGORY=SQL_TUNING` | tuning prompt와 bind pre-tuning prompt에 RAG context가 들어가는지 확인 | tuning RAG prompt 테스트 | 3.5.6, 3.4.5 |

#### 3.6 Repository/상태 저장 런타임 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 테이블/컬럼 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.6.1 | Migration repository 검증 | `MigrationJobRepository`, `MigrationHistoryRepository`, `MappingRuleRepository` | Oracle connection | `NEXT_MIG_INFO`, `NEXT_MIG_INFO_DTL`, mapping rule table | pending/load/update/log query가 운영 DB 스키마와 맞는지 확인 | migration repository report | 3.2.7 |
| 3.6.2 | SQL job repository 검증 | `SqlJobRepository` | Oracle connection | `NEXT_SQL_INFO`, `STATUS_CONVERSION`, `STATUS_TUNING` | pending/conversion/tuning/formatting job 조회 결과 확인 | SQL repository report | 3.2.7 |
| 3.6.3 | SQL log repository 검증 | `SqlLogRepository.insert_sql_log` | Oracle connection | `NEXT_SQL_LOG` | SQL kind별 로그 insert 가능 여부 확인 | SQL log 테스트 | 3.2.6 |
| 3.6.4 | Batch control/log repository 검증 | Supervisor runtime/control 구현 | Oracle connection | `NEXT_BATCH_CONTROL`, `NEXT_BATCH_COMMAND`, `NEXT_BATCH_LOG` | lock, heartbeat, stop, command claim, append log 가능 여부 확인 | batch control 테스트 | 3.2.6 |
| 3.6.5 | 상태명 호환성 검증 | `shared/SqlStatuses.py`, `MigrationTypes.py` | enum/constant | `PASS`, `FAIL-*`, `READY`, `URGENT`, `WAITING`, `SKIP` | 기존 상태값과 Langflow Tool 결과 schema가 1:1 매핑되는지 확인 | status mapping table | 3.6.1, 3.6.2 |
| 3.6.6 | Column length/truncation 검증 | `SqlJobRepository._fit_payload_to_column_limits` | UTF-8 byte truncation | `LOG`, SQL 저장 컬럼 | 긴 한글/SQL 저장 시 byte truncation이 깨지지 않는지 확인 | truncation 테스트 | 3.2.7 |

#### 3.7 업무별 공통 알고리즘 검증

| WBS | Task | 기존 모듈/기능 | 주요 import/패키지 | 주요 설정 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.7.1 | Bind parameter 추출 검증 | `agents/sql_conversion/SqlBindCases.py` | `re`, `json` | MyBatis `#{}`, `${}` | source SQL/TO_SQL에서 bind 이름 추출 정확도 확인 | bind extractor 테스트 | 3.4 |
| 3.7.2 | Bind set 생성 검증 | `build_bind_sets`, `bind_sets_to_json` | `json` | max cases 3 | DB row 결과를 bind case JSON으로 변환하는지 확인 | bind set 테스트 | 3.7.1 |
| 3.7.3 | Test row status 평가 검증 | `SqlConversionValidateNode.evaluate_status_from_test_rows` | Python logic | `FROM_COUNT`, `TO_COUNT`, case_no | PASS/FAIL 판정 기준 확인 | validation status 테스트 | 3.2 |
| 3.7.4 | SQL length/map type 분류 검증 | `classify_sql_length`, `get_sql_map_type` | repository query | `SQL_LENGTH_SHORT_MAX=5000`, mapping rule | SHORT/LONG, SIMPLE/COMPLEX 분류 결과 확인 | classification 테스트 | 3.6.2 |
| 3.7.5 | User-edited 우선순위 검증 | Migration/Conversion generation node | job `USER_EDITED=Y` | 저장된 `MIG_SQL`, `VERIFY_SQL`, `TO_SQL`, `BIND_SQL`, `TEST_SQL` | 사용자 수정 SQL이 LLM 생성보다 우선되는지 확인 | user-edited 정책 테스트 | 3.6 |
| 3.7.6 | Retry context 생성 검증 | `SqlConversionCoordinator._build_retry_prompt_context`, Migration retry logic | Python logic | `max_retries=3`, `FINAL_RETRY_MODE` | attempt별 last_error 문자열이 prompt에 정확히 들어가는지 확인 | retry context 테스트 | 3.4 |

#### 3.8 Langflow Component 공통 골격 개발

| WBS | Task | 기존 모듈/기능 | 주요 내용 | 사전 검증 | 산출물 | 선행 |
| --- | --- | --- | --- | --- | --- | --- |
| 3.8.1 | Custom Component skeleton 확정 | `langflow/components/*.py` | 입력 필드, Tool Mode output, Data output, error result 구조 표준화 | Langflow에서 component 로딩 확인 | component skeleton | 3.1-3.7 |
| 3.8.2 | Command parser 공통화 | Chat/Migration/Conversion/Tuning/Formatting Tool | command JSON parse, action validation, required key validation | invalid JSON/action 누락 테스트 | command parser | 3.8.1 |
| 3.8.3 | Result schema 공통화 | 모든 Command Tool | `ok`, `action`, `status`, `message`, `data`, `error`, `elapsed_seconds` 표준화 | Chat Output에서 읽기 쉬운 형태 확인 | result schema | 3.8.1 |
| 3.8.4 | Runtime self-test action 설계 | 모든 주요 Tool | `self_test`, `test_connection`, `test_llm`, `test_rag`, `test_prompt` action 제공 | Langflow에서 기능 개발 전 리스크 점검 가능 여부 확인 | self-test action spec | 3.8.2 |
| 3.8.5 | 공통 리스크 판정표 작성 | 3장 전체 | 각 검증 항목을 `PASS / WARN / BLOCKED`로 분류 | BLOCKED 항목이 기능 개발 WBS로 넘어가지 않도록 gate 설정 | runtime readiness report | 3.8.4 |

#### 3.9 공통 런타임 단계 완료 기준

| 구분 | 완료 기준 |
| --- | --- |
| Import | Langflow runtime에서 `smart_migrate` package와 필수 dependency가 import된다. |
| DB | Oracle 접속, schema qualification, 필수 테이블/컬럼 조회, CLOB read, 테스트 update/rollback이 검증된다. |
| LLM | Langflow Language Model 컴포넌트 wiring, input/output adapter, fallback 필요 범위, SQL 응답 정규화, 호출 로그 저장 경로가 검증된다. |
| Prompt | 모든 prompt JSON이 로딩되고, 각 prompt input이 기존 job/repository 데이터와 매핑된다. |
| RAG | `NEXT_MIG_RAG_INFO` 조회, `BAAI/bge-m3` 등 embedding model 호출, FAISS vector search, lexical fallback, hit count update가 검증된다. |
| Correct SQL Hint RAG | 현재 `get_feedback_corpus_rows()`가 빈 리스트를 반환하는 상태를 명시하고, 기능 활성화 여부를 결정한다. |
| Gate | `runtime readiness report`에서 `BLOCKED`가 남아 있으면 6~9번 업무 기능 개발을 시작하지 않는다. |

#### 3.10 단위 동작 확인 체크리스트

아래 체크리스트는 3장 검증 작업을 실제로 수행할 때 사용할 최소 단위 확인 항목이다. 각 항목은 `PASS / WARN / FAIL / BLOCKED` 중 하나로 기록하고, `FAIL/BLOCKED`는 원인과 조치 방안을 남긴다.

##### 3.10.1 Import/Dependency 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-IMP-01 | `smart_migrate` package import | Langflow custom component runtime에서 `import smart_migrate` 실행 | ImportError 없음 | 3.1.1 |
| CHK-IMP-02 | Oracle package import | `import oracledb` | import 성공, version 확인 | 3.1.2, 3.2.1 |
| CHK-IMP-03 | LangChain/LangGraph import | `langchain`, `langgraph`, `langchain_openai`, `langchain_anthropic` import | import 성공 | 3.1.2 |
| CHK-IMP-04 | RAG package import | `import faiss`, `import numpy`, `import requests` | import 성공, `faiss.IndexFlatIP` 접근 가능 | 3.1.2, 3.5.1 |
| CHK-IMP-05 | `.env` 로딩 경로 | `AppSettings.py`와 service별 `load_dotenv` 기준 경로 확인 | Langflow 실행 위치와 무관하게 필요한 env 로딩 | 3.1.3 |
| CHK-IMP-06 | Component input override | `.env` 값과 Langflow input 값이 동시에 있을 때 우선순위 확인 | 문서화된 우선순위대로 동작 | 3.1.4 |

##### 3.10.2 Oracle DB 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-DB-01 | Oracle client mode | `ORACLE_CLIENT_PATH` 설정/미설정 각각 확인 | Thin 또는 Thick mode 접속 성공 | 3.2.1 |
| CHK-DB-02 | 기본 접속 | `get_connection()` 후 `SELECT 1 FROM DUAL` 실행 | row 1건 반환 | 3.2.2 |
| CHK-DB-03 | target schema session | `ORACLE_SCHEMA_TGT` 설정 후 `CURRENT_SCHEMA` 확인 | target schema로 session 설정 | 3.2.2 |
| CHK-DB-04 | table qualify | `qualify_table_name`, `qualify_fr_table`, `qualify_to_table` 샘플 호출 | system/source/target schema가 정확히 prefix 처리 | 3.2.3 |
| CHK-DB-05 | 필수 테이블 존재 | `NEXT_MIG_INFO`, `NEXT_MIG_INFO_DTL`, `NEXT_SQL_INFO`, `NEXT_MIG_RAG_INFO` column 조회 | 모든 필수 테이블 접근 가능 | 3.2.4 |
| CHK-DB-06 | batch table 존재 | `NEXT_BATCH_CONTROL`, `NEXT_BATCH_COMMAND`, `NEXT_BATCH_LOG` 조회 | Supervisor 실행 전 필수 table 확인 | 3.2.4, 3.6.4 |
| CHK-DB-07 | CLOB read | 긴 `FR_SQL` 또는 `TO_SQL` row 조회 후 `.read()` 변환 | 원문 길이와 줄바꿈 보존 | 3.2.5 |
| CHK-DB-08 | write/rollback | 테스트 row update 후 rollback | 원복 확인 | 3.2.6 |
| CHK-DB-09 | write/commit | 테스트 log insert 후 commit | log row 조회 가능 | 3.2.6 |
| CHK-DB-10 | column fallback | optional column 없는 환경 가정 또는 catalog 확인 | `_optional_alias_expr`가 NULL alias로 안전 처리 | 3.2.7 |
| CHK-DB-11 | byte truncation | 한글 포함 긴 문자열 저장 payload 생성 | UTF-8 깨짐 없이 byte limit 내 truncation | 3.6.6 |

##### 3.10.3 LLM 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-LLM-01 | Language Model node 연결 | Prompt Builder output을 Language Model input에 연결 | Langflow validation 오류 없음 | 3.3.1 |
| CHK-LLM-02 | LLM input adapter | `list[dict]`, text prompt, Langflow Message 각각 입력 | Language Model이 받을 수 있는 형태로 변환 | 3.3.2 |
| CHK-LLM-03 | LLM output adapter | Language Model output object를 Parser Component에 전달 | content text 추출 가능 | 3.3.3 |
| CHK-LLM-04 | 모델 설정 외부화 | model/base_url/api_key/temperature/max_tokens를 LLM node input으로 설정 | 업무 Component 내부에 모델 설정 중복 없음 | 3.3.1 |
| CHK-LLM-05 | 내부 fallback 필요성 | background Supervisor path에서 LLM node 연결 가능 여부 확인 | 불가능한 path만 내부 호출 fallback 대상으로 분류 | 3.3.4 |
| CHK-LLM-06 | fallback model 순서 | 내부 fallback 사용 시 primary model + `LLM_FALLBACK_MODELS` 후보 생성 | 중복 제거된 후보 순서 확인 | 3.3.4 |
| CHK-LLM-07 | SQL code block 추출 | Language Model output에 ```sql code block 응답 입력 | SQL 본문만 추출 | 3.3.5 |
| CHK-LLM-08 | MyBatis tag 보존 | `<if>`, `<foreach>`로 시작하는 LLM output 입력 | dynamic tag 시작 SQL 허용 | 3.3.5 |
| CHK-LLM-09 | SQLPlus terminator 제거 | `/` 단독 line 포함 LLM output 입력 | `/` line 제거 | 3.3.5 |
| CHK-LLM-10 | `LIMIT` 변환 | `SELECT ... LIMIT 10` LLM output 입력 | `FETCH FIRST 10 ROWS ONLY`로 변환 | 3.3.5 |
| CHK-LLM-11 | tuning JSON 파싱 | `{"tuned_sql":"...","tuned_result":"..."}` LLM output 입력 | tuple `(tuned_sql, tuned_result)` 반환 | 3.3.6 |
| CHK-LLM-12 | LLM log metadata 전달 | Prompt Builder -> Language Model -> Parser/Persist 경로에서 metadata 전달 | prompt_name, model_name, elapsed_seconds 저장 가능 | 3.3.7 |

##### 3.10.4 Prompt 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-PRM-01 | prompt JSON 로딩 | `load_prompt_template()`로 모든 prompt 파일 로딩 | JSON decode 오류 없음 | 3.4.1 |
| CHK-PRM-02 | UTF-8-SIG 처리 | BOM 포함 prompt 파일 로딩 | 정상 파싱 | 3.4.1 |
| CHK-PRM-03 | 변수 치환 | `{from_sql}`, `{last_error}` 포함 prompt render | 전달 값 치환 | 3.4.2 |
| CHK-PRM-04 | 누락 변수 처리 | 일부 변수를 일부러 누락 | crash 없이 `{key}` 유지 | 3.4.2 |
| CHK-PRM-05 | message 구조 | `build_prompt_messages()` 호출 | system/user 2개 message 생성 | 3.4.3 |
| CHK-PRM-06 | SQL block type | SQL input render | fenced block type `sql` 적용 | 3.4.4 |
| CHK-PRM-07 | JSON/RAG block render | RAG examples JSON input render | 줄바꿈/따옴표 손상 없음 | 3.4.4 |
| CHK-PRM-08 | prompt input mapping | prompt별 required input 표 작성 | job/repository/setting source가 모두 매핑 | 3.4.5 |

##### 3.10.5 RAG/Embedding 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-RAG-01 | RAG service singleton | `tobe_sql_tuning_service` import | 객체 생성 성공, env 값 반영 | 3.5.1 |
| CHK-RAG-02 | embedding endpoint 보정 | base URL을 root, `/v1`, `/v1/embeddings`로 입력 | 최종 endpoint가 `/v1/embeddings` | 3.5.2 |
| CHK-RAG-03 | embedding auth header | `RAG_EMBED_API_KEY` 설정/미설정 확인 | 설정 시 Bearer header 포함 | 3.5.2 |
| CHK-RAG-04 | embedding model | 기본 env 미설정 상태 확인 | `BAAI/bge-m3` 사용 | 3.5.2 |
| CHK-RAG-05 | embedding response format A | OpenAI style `{data:[{embedding:[]}]}` mock 또는 실제 호출 | vector list 추출 | 3.5.3 |
| CHK-RAG-06 | embedding response format B | `{embeddings:[[...]]}` 형식 입력 | vector list 추출 | 3.5.3 |
| CHK-RAG-07 | embedding count 검증 | rule text N개 + block text M개 호출 | N+M개 vector 반환 | 3.5.3 |
| CHK-RAG-08 | FAISS normalize | `_retrieve_by_vector_search` 실행 | `faiss.normalize_L2` 후 검색 수행 | 3.5.4 |
| CHK-RAG-09 | FAISS index 생성 | rule vector dimension 확인 후 `IndexFlatIP` 생성 | index add/search 성공 | 3.5.4 |
| CHK-RAG-10 | top_k 제한 | rule 수보다 큰 `TOBE_SQL_TUNING_TOP_K` 설정 | `min(top_k, len(rules))`로 검색 | 3.5.4 |
| CHK-RAG-11 | vector fallback | `RAG_EMBED_BASE_URL` 미설정 상태에서 retrieve 호출 | token fallback 결과 반환 | 3.5.5 |
| CHK-RAG-12 | faiss import 실패 fallback | faiss 미설치 환경 가정 | token fallback 결과 반환 또는 WARN | 3.5.5 |
| CHK-RAG-13 | SEARCH rule 조회 | `CATEGORY=SQL_TUNING`, `RULE_TYPE=SEARCH`, `USE_YN=Y` 조회 | rule list 반환 | 3.5.6 |
| CHK-RAG-14 | GENERAL rule 조회 | `CATEGORY=SQL_TUNING`, `RULE_TYPE=GENERAL` 조회 | guidance list 반환 | 3.5.6 |
| CHK-RAG-15 | Conversion RAG 조회 | `CATEGORY=SQL_CONVERSION` 조회 | TO_SQL prompt용 rule 반환 | 3.5.11 |
| CHK-RAG-16 | source table parsing | `"SCHEMA.TABLE_A, TABLE_B"` 입력 | `TABLE_A`, `TABLE_B` set 반환 | 3.5.7 |
| CHK-RAG-17 | source table filtering | rule table과 job table 교집합 확인 | 관련 rule만 남음 | 3.5.7 |
| CHK-RAG-18 | SQL block split | subquery 포함 SQL 입력 | `MAIN_SQL`, `SUBQUERY_1...` block 생성 | 3.5.8 |
| CHK-RAG-19 | SQL shape normalize | literal/number/comment 포함 SQL 입력 | literal/number/comment 제거 또는 치환 | 3.5.8 |
| CHK-RAG-20 | hit count update | 검색 성공 rule id로 update 실행 | `HIT_CNT` 증가, `UPDATED_AT` 갱신 | 3.5.9 |
| CHK-RAG-21 | Correct SQL corpus | `get_feedback_corpus_rows()` 호출 | 현재 빈 리스트면 WARN/BLOCKED 판정 | 3.5.10 |
| CHK-RAG-22 | RAG prompt serialization | `serialize_tuning_examples_for_prompt()` 호출 | prompt에 넣기 좋은 compact JSON 생성 | 3.5.12 |

##### 3.10.6 Repository/상태 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-REP-01 | SQL pending 조회 | `get_pending_jobs()` 호출 | conversion pending job list 반환 또는 빈 list | 3.6.2 |
| CHK-REP-02 | SQL rowid 조회 | `get_sql_job_by_row_id()` 호출 | CLOB 포함 `SqlInfoJob` 반환 | 3.6.2 |
| CHK-REP-03 | tuning pending 조회 | `get_tuning_jobs()` 호출 | conversion PASS + tuning READY/FAIL job 반환 | 3.6.2 |
| CHK-REP-04 | formatting pending 조회 | `get_formatting_jobs()` 호출 | tuning PASS + formatted empty job 반환 | 3.6.2 |
| CHK-REP-05 | classification update | `update_job_classification()` 호출 | `SQL_LENGTH`, `MAP_TYPE` 저장 | 3.6.2 |
| CHK-REP-06 | cycle result update | `update_cycle_result()` 테스트 row 호출 | TO_SQL/BIND/TEST/status/log 저장 | 3.6.2 |
| CHK-REP-07 | block RAG 저장 | `update_block_rag_content()` 호출 | 컬럼 존재 시 JSON 저장 | 3.6.2 |
| CHK-REP-08 | formatted SQL 저장 | `update_formatted_sql()` 호출 | `FORMATTED_SQL` 저장 또는 컬럼 없음 WARN | 3.6.2 |
| CHK-REP-09 | SQL log 저장 | `insert_sql_log()` 호출 | log row insert | 3.6.3 |
| CHK-REP-10 | batch control lock | control row acquire 시뮬레이션 | 동시 실행 방지 조건 확인 | 3.6.4 |
| CHK-REP-11 | batch heartbeat | heartbeat update 호출 | `HEARTBEAT_AT`, `LOOP_NO`, `LAST_EVENT` 갱신 | 3.6.4 |
| CHK-REP-12 | batch stop | `STOP_REQUESTED_YN=Y` 설정 | 다음 loop에서 stop 처리 가능 | 3.6.4 |

##### 3.10.7 업무 공통 알고리즘 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-ALG-01 | bind token 추출 | `#{userId}`, `${dept.id}` 포함 SQL 입력 | normalized bind name 추출 | 3.7.1 |
| CHK-ALG-02 | bind 중복 제거 | 같은 bind 여러 번 사용 | 중복 없는 bind name list 생성 | 3.7.1 |
| CHK-ALG-03 | bind set JSON | bind query row 3건 입력 | 최대 3개 case JSON 생성 | 3.7.2 |
| CHK-ALG-04 | no-bind 처리 | bind token 없는 SQL 입력 | `bind_set_json_for_test="[{}]"` 정책 확인 | 3.7.2 |
| CHK-ALG-05 | test PASS 판정 | from/to count 동일 row 입력 | PASS 반환 | 3.7.3 |
| CHK-ALG-06 | test FAIL 판정 | from/to count 불일치 row 입력 | FAIL 반환 | 3.7.3 |
| CHK-ALG-07 | SHORT/LONG 분류 | 5000자 이하/초과 SQL 입력 | SHORT/LONG 분류 | 3.7.4 |
| CHK-ALG-08 | map type 조회 | target table 기준 mapping rule 조회 | SIMPLE/COMPLEX 또는 UNKNOWN 처리 | 3.7.4 |
| CHK-ALG-09 | user-edited TO_SQL | `USER_EDITED=Y` + saved `TO_SQL` | LLM 호출 없이 저장 SQL 사용 | 3.7.5 |
| CHK-ALG-10 | user-edited BIND/TEST | saved `BIND_SQL`, `TEST_SQL` 존재 | LLM 호출 없이 저장 SQL 사용 | 3.7.5 |
| CHK-ALG-11 | retry context 일반 | attempt 2/3 실패 context 생성 | `FINAL_RETRY_MODE=OFF` | 3.7.6 |
| CHK-ALG-12 | retry context final | attempt 3/3 실패 context 생성 | `FINAL_RETRY_MODE=ON` | 3.7.6 |

##### 3.10.8 Langflow Component 체크리스트

| Check ID | 확인 대상 | 확인 방법 | 기대 결과 | 관련 WBS |
| --- | --- | --- | --- | --- |
| CHK-LF-01 | component load | Langflow에서 custom component refresh | component가 UI에 노출 | 3.8.1 |
| CHK-LF-02 | Tool Mode output | Agent Tool로 연결 | tool schema가 Agent에 노출 | 3.8.1 |
| CHK-LF-03 | Data output | 직접 실행 후 Data 출력 확인 | result dict 확인 가능 | 3.8.1 |
| CHK-LF-04 | invalid command JSON | 깨진 JSON 입력 | 표준 error result 반환 | 3.8.2 |
| CHK-LF-05 | unsupported action | 정의되지 않은 action 입력 | `ok=false`, action error 반환 | 3.8.2 |
| CHK-LF-06 | required key 누락 | `map_id`/`row_id` 누락 입력 | 누락 field 명시 | 3.8.2 |
| CHK-LF-07 | self_test action | `{"action":"self_test"}` 실행 | DB/LLM/RAG/prompt 상태 요약 | 3.8.4 |
| CHK-LF-08 | secret masking | result/log/message 확인 | password/API key 원문 미노출 | 3.8.4 |

#### 3.11 Langflow Custom Component 공통 함수 후보

아래 항목은 Migration/Conversion/Tuning/Formatting/Dashboard/Supervisor Component에 반복해서 들어갈 가능성이 높은 공통 함수 후보이다. 가능하면 `BaseSmartMigrateComponent` 또는 공통 helper module로 분리하고, Langflow 단일 파일 배포 제약이 있으면 각 component에 동일한 section으로 포함한다.

##### 3.11.0 LLM 분리형 Component 패턴

Langflow에서는 Language Model 컴포넌트가 별도로 존재하므로, 기본 설계는 업무 Component가 LLM API를 직접 호출하지 않는 방식으로 잡는다.

권장 Flow 패턴:

```text
Job Loader / Context Builder
-> Prompt Builder
-> Langflow Language Model
-> Response Parser / Validator
-> DB Persist / Log Writer
```

업무 Component 책임:

| Component 유형 | 책임 | LLM 직접 호출 여부 |
| --- | --- | --- |
| Job Loader / Context Builder | DB에서 job, mapping rule, RAG context, retry context 조회 | 호출하지 않음 |
| Prompt Builder | prompt template render, system/user message 생성 | 호출하지 않음 |
| Language Model | 실제 모델 호출, model/base_url/api_key/temperature 관리 | 호출함 |
| Response Parser / Validator | LLM output에서 SQL/JSON 추출, Oracle SQL 정규화, validation 전처리 | 호출하지 않음 |
| DB Persist / Log Writer | 생성 SQL, status, log, elapsed, prompt metadata 저장 | 호출하지 않음 |

예외적으로 내부 LLM 호출을 허용할 수 있는 경우:

| 예외 경로 | 허용 이유 | 조건 |
| --- | --- | --- |
| Background Supervisor loop | Langflow request/response Flow 밖에서 장시간 실행될 수 있음 | Language Model node를 loop 내부에 주입할 수 없는 경우에만 fallback 사용 |
| 단일 Tool end-to-end MVP | 초기 PoC에서 wiring 복잡도를 줄이기 위함 | 이후 Prompt Builder/Parser 분리형으로 리팩토링 계획을 남김 |
| self-test | Language Model node 연결 전 endpoint smoke test가 필요할 수 있음 | secret masking과 timeout 제한 필수 |

##### 3.11.1 Runtime/Package 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_ensure_packages(packages)` | Langflow runtime에 필요한 패키지 자동 설치 | package spec list, `auto_install_packages` | missing package 설치 또는 설치 불가 오류 반환 | 전체 |
| `_import_or_install(module_name, package_spec)` | import 실패 시 지정 패키지 설치 후 재시도 | module name, pip package | imported module | 전체 |
| `_check_required_imports()` | component 실행 전 dependency readiness 확인 | 없음 또는 package matrix | PASS/WARN/FAIL 리스트 | 전체 |
| `_get_runtime_info()` | Python/Langflow/OS/package version 수집 | 없음 | runtime diagnostics dict | 전체 |
| `_resolve_project_root()` | `src/smart_migrate` 또는 `.env` 기준 root 탐색 | component file path, cwd 후보 | project root path | 전체 |
| `_ensure_python_path(path)` | 기존 `smart_migrate` package import path 보정 | project root/src path | `sys.path` 갱신 | 전체 |
| `_load_env_files(paths, override=False)` | `.env` 후보 경로 로딩 | path list, override flag | loaded file list | 전체 |

##### 3.11.2 Config/Input 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_resolve_config_value(input_value, env_name, default=None, required=False)` | Langflow input과 env 값을 일관되게 병합 | component input, env name | resolved value | 전체 |
| `_build_runtime_config()` | DB/LLM/RAG/schema/prompt 설정을 구조화 | component inputs | config dict/dataclass | 전체 |
| `_validate_required_config(config, keys)` | 필수 설정 누락 검증 | config, required keys | 누락 key list 또는 예외 | 전체 |
| `_normalize_bool(value)` | Langflow text/bool input 정규화 | `"Y"`, `"true"`, `True` 등 | bool | 전체 |
| `_normalize_int(value, default, min_value=None, max_value=None)` | batch size/top_k/timeout 등 숫자 input 정규화 | raw value | int | 전체 |
| `_mask_secret(value)` | secret 표시용 masking | raw secret | masked string | 전체 |
| `_mask_config_for_log(config)` | config log 출력 시 secret 제거 | config dict | masked config dict | 전체 |

##### 3.11.3 DB 연결/쿼리 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_init_oracle_client_once()` | Oracle thick client 중복 초기화 방지 | `ORACLE_CLIENT_PATH` | init 상태 | DB 사용 전체 |
| `_build_oracle_dsn()` | host/port/sid 또는 full dsn 정규화 | DB host/port/sid | dsn string | DB 사용 전체 |
| `_get_db_connection()` | 표준 Oracle connection 생성 | DB config | connection | DB 사용 전체 |
| `_set_current_schema(conn, schema)` | target schema session 설정 | connection, schema | `ALTER SESSION` 실행 | Migration/SQL |
| `_test_db_connection()` | `SELECT 1 FROM DUAL` smoke test | DB config | test result | 전체 self-test |
| `_qualify_table(table_name, schema)` | table명 schema prefix 처리 | table, schema | qualified table | DB 사용 전체 |
| `_safe_identifier(value, kind)` | schema/table identifier injection 방지 | identifier | clean identifier 또는 예외 | DB 사용 전체 |
| `_fetch_one_dict(cursor)` | cursor row를 dict로 변환 | cursor | dict 또는 None | DB 사용 전체 |
| `_fetch_all_dicts(cursor)` | cursor 결과를 list[dict]로 변환 | cursor | list[dict] | DB 사용 전체 |
| `_to_text(value, default="")` | CLOB/bytes/None 안전 문자열 변환 | DB value | string | DB 사용 전체 |
| `_execute_query(sql, params=None)` | SELECT 실행 wrapper | sql, binds | rows | DB 사용 전체 |
| `_execute_update(sql, params=None, commit=True)` | UPDATE/INSERT 실행 wrapper | sql, binds, commit flag | affected rows | DB 사용 전체 |
| `_transaction()` | 여러 DB update를 하나의 transaction으로 묶기 | 없음 | context manager | Migration/Conversion |
| `_get_available_columns(table)` | table column cache 조회 | table | set[column] | Repository 대체/검증 |
| `_get_column_lengths(table)` | varchar byte length cache 조회 | table | dict[column,length] | 저장 함수 전체 |
| `_fit_payload_to_column_limits(table, values)` | 저장 전 byte length 맞춤 | table, values | fitted values | 저장 함수 전체 |

##### 3.11.4 LLM/Embedding 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_build_llm_messages(prompt_payload)` | Langflow Language Model 컴포넌트에 넘길 message 구조 생성 | prompt payload | Message/Text compatible payload | LLM 사용 전체 |
| `_to_langflow_lm_input(messages)` | 기존 `list[dict]` message를 Langflow LLM input 형식으로 변환 | messages | Langflow Message/Text/Data | LLM 사용 전체 |
| `_extract_lm_output_text(lm_output)` | Language Model output에서 content text 추출 | Message/Text/Data/객체 | response text | LLM 사용 전체 |
| `_build_llm_metadata(job, prompt_name, sql_kind)` | LLM 호출 전후 로그에 필요한 metadata 구성 | job, prompt name, kind | metadata dict | LLM 사용 전체 |
| `_parse_sql_lm_output(lm_output)` | LLM output text 추출 후 SQL 본문 파싱 | Language Model output | SQL text | Migration/Conversion |
| `_parse_tuning_lm_output(lm_output)` | LLM output text 추출 후 tuning JSON/text 파싱 | Language Model output | `(tuned_sql, tuned_result)` | Tuning |
| `_extract_sql_text(response_text)` | LLM 응답에서 SQL 본문 추출 | response text | SQL text | Migration/Conversion |
| `_normalize_oracle_sql(sql_text)` | SQLPlus terminator, semicolon, LIMIT 등 정규화 | SQL text | normalized SQL | Migration/Conversion |
| `_extract_tuning_response(response_text)` | tuning JSON/text 응답 파싱 | response text | `(tuned_sql, tuned_result)` | Tuning |
| `_normalize_openai_base_url(base_url)` | 내부 fallback 호출이 필요할 때 OpenAI-compatible endpoint 정규화 | base_url | normalized base_url | fallback only |
| `_normalize_anthropic_base_url(base_url)` | 내부 fallback 호출이 필요할 때 Anthropic endpoint 정규화 | base_url | normalized base_url | fallback only |
| `_call_llm_text_fallback(messages, config)` | Langflow LLM node를 사용할 수 없는 background path용 내부 호출 | messages, LLM config | response text | Supervisor fallback |
| `_embedding_endpoint(base_url)` | embedding endpoint 보정 | base_url | `/v1/embeddings` endpoint | RAG |
| `_embed_texts(texts, config)` | embedding API 호출 | texts, RAG config | vector list | RAG |
| `_extract_embedding_vectors(body)` | 다양한 embedding response format 파싱 | response JSON | vector list | RAG |

##### 3.11.5 Prompt/RAG 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_load_prompt_template(filename_or_text)` | prompt 파일 또는 component input prompt 로딩 | file name/text | prompt dict/text | LLM 사용 전체 |
| `_render_prompt_template(template, context)` | prompt 변수 치환 | template, context | rendered prompt | LLM 사용 전체 |
| `_build_prompt_messages(template, context)` | LangChain message 구조 생성 | prompt template, context | messages | LLM 사용 전체 |
| `_render_input_block(name, value)` | SQL/JSON/text block rendering | input name/value | rendered text block | LLM 사용 전체 |
| `_detect_block_type(name, value)` | fenced block type 판정 | name/value | `sql/json/text` | LLM 사용 전체 |
| `_normalize_sql_shape(sql_text)` | RAG 검색용 SQL shape 정규화 | SQL text | normalized text | RAG |
| `_split_sql_into_blocks(sql_text)` | MAIN/SUBQUERY block 분해 | SQL text | block list | RAG |
| `_lexical_similarity(left, right)` | vector fallback용 token similarity | normalized texts | score | RAG |
| `_format_rag_match(rule, score)` | RAG match payload 표준화 | rule, score | dict | RAG |
| `_serialize_rag_examples_for_prompt(examples)` | prompt용 compact RAG JSON 생성 | examples | JSON string | Conversion/Tuning |

##### 3.11.6 Command/Result/Error 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_parse_command_json(command_json)` | command JSON 안전 파싱 | text/dict | command dict | Tool 전체 |
| `_validate_action(command, allowed_actions)` | action whitelist 검증 | command, actions | action 또는 error | Tool 전체 |
| `_require_fields(command, fields)` | action별 필수 field 검증 | command, field list | 누락 field list | Tool 전체 |
| `_ok(action, message, data=None, status=None)` | 성공 result 표준화 | action/message/data | result dict | Tool 전체 |
| `_fail(action, message, error=None, data=None)` | 실패 result 표준화 | action/message/error | result dict | Tool 전체 |
| `_warn(action, message, data=None)` | 경고 result 표준화 | action/message/data | result dict | Tool 전체 |
| `_exception_result(action, exc)` | 예외를 result schema로 변환 | action, exception | masked error result | Tool 전체 |
| `_elapsed_result(started, result)` | elapsed_seconds 추가 | start time, result | result dict | Tool 전체 |
| `_to_langflow_data(result)` | Langflow Data output 변환 | result dict | Data 또는 dict | Tool 전체 |
| `_summarize_for_chat(result)` | Chat Output용 짧은 요약 생성 | result dict | summary text | Chat/Dashboard |

##### 3.11.6A Output Formatter 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 Shell |
| --- | --- | --- | --- | --- |
| `_format_query_result_table(rows, columns=None, limit=20)` | DB 조회 결과를 markdown table로 변환 | rows, columns, limit | markdown table + row count | SH-01, SH-12 |
| `_format_metadata_table(metadata)` | job/status/schema 같은 key-value를 table로 표시 | dict | markdown metadata table | SH-02, SH-04, SH-09 |
| `_format_sql_block(title, sql_text, max_chars=None)` | SQL/CLOB을 code block으로 표시 | title, sql | fenced SQL block | SH-02, SH-05, SH-07 |
| `_format_step_result(steps)` | 실행 단계별 결과 표시 | step list | ordered status list | SH-04, SH-05, SH-07 |
| `_format_readiness_matrix(checks)` | self-test 결과 matrix 표시 | check results | PASS/WARN/FAIL table | SH-11 |
| `_format_rag_matches(blocks)` | block별 RAG top-k 결과 표시 | RAG payloads | block summary + match table | SH-06 |
| `_format_validation_cases(rows)` | count 비교 결과 표시 | validation rows | case table + PASS/FAIL | SH-07 |
| `_format_failure_summary(error, logs, sql_blocks)` | 실패 분석 결과 표시 | error/log/sql | stage, cause, snippets | SH-10 |
| `_build_next_actions(context, allowed_actions)` | 조회 결과 후속 action 제안 | context, actions | 1~3개 action list | SH-01, SH-02, SH-10 |
| `_truncate_for_display(text, max_chars)` | 긴 텍스트 화면 표시용 축약 | text, max chars | truncated text | 전체 |
| `_mask_display_row(row)` | table 출력 전 secret masking | row dict | masked row | 전체 |

##### 3.11.7 Logging/Self-Test 공통 함수

| 함수 후보 | 목적 | 주요 입력 | 반환/효과 | 적용 컴포넌트 |
| --- | --- | --- | --- | --- |
| `_write_component_log(event, payload)` | component 내부 공통 로그 | event, payload | log write | 전체 |
| `_write_sql_log(job, sql_kind, sql_content, status, metadata)` | SQL 생성/실행 로그 저장 | job/sql/status | `NEXT_SQL_LOG` insert | SQL 계열 |
| `_write_batch_log(event, agent, job_id, status, message)` | batch event 기록 | event fields | `NEXT_BATCH_LOG` insert | Supervisor |
| `_self_test_imports()` | import/package readiness test | 없음 | check result list | 전체 |
| `_self_test_db()` | DB readiness test | DB config | check result list | DB 사용 전체 |
| `_self_test_llm()` | LLM readiness test | LLM config | check result list | LLM 사용 전체 |
| `_self_test_prompt()` | prompt readiness test | prompt config | check result list | LLM 사용 전체 |
| `_self_test_rag()` | RAG readiness test | RAG config | check result list | RAG 사용 전체 |
| `_build_readiness_report(checks)` | PASS/WARN/FAIL/BLOCKED 집계 | check result list | readiness report | 전체 |

##### 3.11.8 우선 구현 권장 공통 함수

| 우선순위 | 함수/그룹 | 이유 |
| --- | --- | --- |
| P0 | `_build_runtime_config`, `_resolve_config_value`, `_mask_config_for_log` | 모든 component input/env/secret 정책의 기준이 된다. |
| P0 | `_ensure_packages`, `_check_required_imports` | Langflow runtime dependency 리스크를 기능 개발 전에 드러낸다. |
| P0 | `_get_db_connection`, `_test_db_connection`, `_to_text`, `_qualify_table` | DB와 CLOB이 깨지면 모든 업무 기능이 막힌다. |
| P0 | `_ok`, `_fail`, `_exception_result`, `_parse_command_json`, `_validate_action` | Tool 결과와 오류 처리를 표준화해야 Agent prompt가 단순해진다. |
| P0 | `_format_query_result_table`, `_format_metadata_table`, `_format_sql_block`, `_build_next_actions` | 조회형/상세형 shell의 출력 디자인을 먼저 통일해야 기능별 화면 품질이 흔들리지 않는다. |
| P1 | `_build_llm_messages`, `_to_langflow_lm_input`, `_extract_lm_output_text`, `_parse_sql_lm_output` | Langflow Language Model 컴포넌트를 기본 LLM 실행 경로로 쓰려면 입출력 adapter가 먼저 필요하다. |
| P1 | `_load_prompt_template`, `_render_prompt_template`, `_build_prompt_messages` | prompt 입력 매핑과 preview/test action에 공통으로 필요하다. |
| P1 | `_embed_texts`, `_split_sql_into_blocks`, `_serialize_rag_examples_for_prompt` | Conversion/Tuning RAG 리스크를 조기에 확인할 수 있다. |
| P2 | `_write_sql_log`, `_write_batch_log`, `_build_readiness_report` | 운영 관찰성과 self-test 결과 축적에 필요하다. |

### 4. Chat Agent 틀 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 4.1 | Chat Agent system prompt 작성 | 사용자 요청을 지원 action으로만 변환하도록 제한 | Chat Agent prompt | 2.4 |
| 4.2 | Chat Command Tool 구현 | `enqueue_migration`, `enqueue_sql_conversion`, `request_stop`, `status`, `failure_summary` 지원 | `Chat_Command_Tool` | 3 |
| 4.3 | Command queue 연동 | `NEXT_BATCH_COMMAND`에 1회성 실행 명령 저장 | command enqueue 기능 | 4.2 |
| 4.4 | Chat 결과 요약 정책 작성 | Tool 결과를 운영자가 이해할 수 있는 요약으로 변환 | response guide | 4.1 |
| 4.5 | Chat Flow 조립 | Chat Input -> Chat Agent -> Chat Command Tool -> Chat Output | Chat Flow | 4.1-4.4 |

### 5. Supervisor Agent 틀 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 5.1 | Supervisor 실행 모델 확정 | background loop 방식 또는 1-cycle 실행 방식 결정 | supervisor runtime 결정 | 2.6 |
| 5.2 | Batch control 구현 | `NEXT_BATCH_CONTROL` lock, heartbeat, stop request, stale takeover 처리 | control helper | 5.1 |
| 5.3 | Job polling 구현 | migration/conversion/tuning/formatting pending job 조회 및 priority gate | polling helper | 3.2 |
| 5.4 | Supervisor prompt 작성 | poll 결과 기준으로 job tool 1개만 선택하도록 지시 | Supervisor prompt | 5.3 |
| 5.5 | Supervisor Tool routing 구현 | `run_data_migration`, `run_sql_conversion`, `run_sql_tuning`, `run_sql_formatting`, `no_job` 연결 | Tool routing | 5.4 |
| 5.6 | 중복 실행 방지 구현 | cycle당 1개 job만 실행하도록 claim guard 적용 | execution guard | 5.5 |
| 5.7 | Batch log 구현 | loop start, no job, job success/fail, stop, fatal error 기록 | `NEXT_BATCH_LOG` 기록 | 5.2 |
| 5.8 | Supervisor Flow 조립 | Supervisor Component를 Langflow에서 실행 가능하게 구성 | Supervisor Flow | 5.1-5.7 |

### 6. DB Migration 기능 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 6.1 | Migration command 계약 정의 | `status`, `list_pending`, `preview_prompt`, `save_user_sql`, `run_migration_job`, `reset` 정의 | Migration command schema | 2.4 |
| 6.2 | Migration job load 구현 | `NEXT_MIG_INFO`, detail, dependency, retry_count, user_edited 조회 | job loader | 3.2 |
| 6.3 | DDL 조회 구현 | Oracle source/target DDL 조회 및 prompt input 구성 | DDL reader | 6.2 |
| 6.4 | Dependency check 구현 | prior job, target priority, failed dependency 처리 | dependency checker | 6.2 |
| 6.5 | Migration SQL 생성 구현 | `USER_EDITED=Y` 우선 사용, 아니면 LLM prompt 기반 생성 | MIG_SQL generator | 6.3 |
| 6.6 | Verify SQL 생성 구현 | row count/validation용 SQL 생성 | VERIFY_SQL generator | 6.5 |
| 6.7 | Migration SQL 실행 구현 | truncate 옵션, target insert/update 실행, affected rows 기록 | SQL executor | 6.5 |
| 6.8 | Verify SQL 실행 구현 | 검증 SQL 실행 및 PASS/FAIL 판정 | verifier | 6.6 |
| 6.9 | Business retry 구현 | `FAIL-TRUNCATE`, `FAIL-INSERT`, `FAIL-TEST`별 재시도 분기 | retry loop | 6.7-6.8 |
| 6.10 | 최종 상태 저장 구현 | `PASS`, `FAIL-*`, `WAITING`, `SKIP` 저장 및 log 기록 | persistence | 6.9 |
| 6.11 | Migration Command Tool 통합 | 위 기능을 Langflow Custom Component Tool로 묶기 | Migration Tool | 6.1-6.10 |
| 6.12 | Migration Agent prompt 작성 | 자연어 요청을 migration command JSON으로 변환 | Migration Agent prompt | 6.11 |

### 7. SQL Conversion 기능 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 7.1 | Conversion command 계약 정의 | `status`, `list_pending`, `preview_*_prompt`, `generate_*`, `run_sql_conversion_job`, `reset` 정의 | Conversion command schema | 2.4 |
| 7.2 | SQL job load 구현 | `NEXT_SQL_INFO` 조회, source SQL/CLOB read, tag_kind, user_edited 처리 | SQL job loader | 3.2 |
| 7.3 | Mapping rule 조회 구현 | 전체 mapping rule, map_type, sql_length classification | rule loader | 7.2 |
| 7.4 | TO_SQL 생성 구현 | 기존 `generate_tobe_sql` 동작을 Langflow Tool 내부로 연결. `SQL_CONVERSION` RAG context 포함 여부 반영 | TO_SQL generator | 7.3, 3.5.11 |
| 7.5 | Bind parameter 추출 구현 | source/tobe SQL에서 bind param 추출 | bind extractor | 7.4 |
| 7.6 | BIND_SQL 생성 및 실행 구현 | bind SQL 생성, 실행, bind case JSON 생성. bind pre-tuning을 사용할 경우 Tuning RAG 선행 검증 필요 | bind flow | 7.5, 3.7.2 |
| 7.7 | TEST_SQL 생성 및 실행 구현 | conversion 검증 SQL 생성, 실행, row count 비교 | test flow | 7.6 |
| 7.8 | Retry prompt context 구현 | last_error, attempt, final_retry_mode 반영 | retry context | 7.7 |
| 7.9 | Non-SELECT 처리 구현 | SELECT가 아닌 job은 test 없이 conversion pass 처리 | non-select branch | 7.4 |
| 7.10 | 최종 상태 저장 구현 | TO_SQL, BIND_SQL, BIND_SET, TEST_SQL, status, final_log 저장 | persistence | 7.7-7.9 |
| 7.11 | Conversion Command Tool 통합 | 전체 conversion workflow를 1개 Tool action으로 묶기 | Conversion Tool | 7.1-7.10 |
| 7.12 | Conversion Agent prompt 작성 | 자연어 요청을 conversion command JSON으로 변환 | Conversion Agent prompt | 7.11 |

### 8. SQL Tuning 기능 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 8.1 | Tuning command 계약 정의 | `status`, `list_pending`, `preview_prompt`, `run_sql_tuning_job` 정의 | Tuning command schema | 2.4 |
| 8.2 | Tuning job load 구현 | TO_SQL 기준 job 조회, target/source table context 구성 | tuning job loader | 3.2 |
| 8.3 | Tuning rule/RAG 조회 구현 | FAISS/RAG rule 검색, lexical fallback, hit count 갱신 | rule retriever | 8.2, 3.5.12 |
| 8.4 | TUNED_TO_SQL 생성 구현 | tuning examples와 last_error를 반영해 SQL 생성 | tuned SQL generator | 8.3, 3.3.5 |
| 8.5 | Tuned SQL 검증 구현 | baseline TO_SQL과 tuned SQL 비교 test 생성/실행 | tuned verifier | 8.4 |
| 8.6 | Tuning retry 구현 | tuned test 실패 시 retry context 반영 | retry loop | 8.5 |
| 8.7 | 최종 상태 저장 구현 | TUNED_TO_SQL, tuned_result, tuned_test, block_rag_content 저장 | persistence | 8.6 |
| 8.8 | Tuning Command Tool 통합 | Langflow Tool action으로 묶기 | Tuning Tool | 8.1-8.7 |

### 9. SQL Formatting 기능 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 9.1 | Formatting command 계약 정의 | `status`, `list_pending`, `run_sql_formatting_job` 정의 | Formatting command schema | 2.4 |
| 9.2 | Formatting input 선택 구현 | `TUNED_TO_SQL` 우선, 없으면 `TO_SQL` 사용 | input selector | 3.2 |
| 9.3 | Formatting prompt 작성 | SQL indentation/formatting 전용 prompt 작성 | formatting prompt | 9.2 |
| 9.4 | FORMATTED_SQL 생성 구현 | LLM 기반 formatting 결과 생성 | formatted SQL generator | 9.3 |
| 9.5 | 최종 상태 저장 구현 | `FORMATTED_SQL` 및 status 저장 | persistence | 9.4 |
| 9.6 | Formatting Command Tool 통합 | Langflow Tool action으로 묶기 | Formatting Tool | 9.1-9.5 |

### 10. 운영 보조 Tool 개발

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 10.1 | Dashboard Tool 구현 | pending/running/pass/fail summary 조회 | dashboard action | 3.2 |
| 10.2 | Failure Analysis Tool 구현 | failed job, last_error, log, generated SQL 요약 | failure summary action | 3.5 |
| 10.3 | Rerun Tool 구현 | failed job reset, retry_count 초기화, command queue 등록 | rerun action | 3.2 |
| 10.4 | RAG Rule Tool 구현 | mapping/tuning rule 조회, hit count, 관련 SQL 예시 조회 | rag rule action | 8.3 |
| 10.5 | Supervisor Control Tool 구현 | start/stop/status, heartbeat, current active job 조회 | control action | 5.2 |

### 11. Flow 조립 및 프롬프트 작성

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 11.1 | Chat 운영 Flow 조립 | 사용자가 운영 명령을 입력하는 기본 Flow | Chat Flow export | 4, 10 |
| 11.2 | Supervisor 배치 Flow 조립 | background batch 실행용 Flow | Supervisor Flow export | 5 |
| 11.3 | Language Model 공통 노드 조립 | model/base_url/api_key/temperature/max_tokens를 Langflow LLM Component에서 관리 | LLM node preset | 3.3 |
| 11.4 | Prompt Builder -> LLM -> Parser 패턴 조립 | 업무 Component 내부 LLM 호출 대신 Langflow LLM node를 통과하는 표준 wiring 구성 | standard LLM wiring | 3.11.0 |
| 11.5 | 수동 Migration Flow 조립 | 특정 `map_id`를 수동 실행/조회하는 Flow. 생성 단계는 Prompt Builder -> LLM -> SQL Parser -> Persist로 구성 | Migration Flow export | 6, 11.4 |
| 11.6 | 수동 SQL Conversion Flow 조립 | 특정 `row_id`를 수동 실행/조회하는 Flow. TO/BIND/TEST 생성 단계는 LLM node를 통과하도록 구성 | Conversion Flow export | 7, 11.4 |
| 11.7 | Tuning/Formatting LLM Flow 조립 | tuning JSON parser, formatting SQL parser를 LLM output 뒤에 연결 | Tuning/Formatting Flow export | 8-9, 11.4 |
| 11.8 | Agent prompt 검수 | 금지 action, secret 노출 금지, command schema 준수 검수 | prompt review checklist | 11.1-11.7 |
| 11.9 | Tool description 작성 | Langflow Tool Mode에서 LLM이 선택하기 쉬운 description 작성 | Tool metadata | 11.8 |

### 12. 테스트 및 검증

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 12.1 | Component 단위 테스트 | DB helper, prompt helper, command parser, result schema 테스트 | unit test 결과 | 3 |
| 12.2 | Tool 직접 실행 테스트 | Agent 없이 Tool action을 직접 호출해 검증 | Tool test checklist | 6-10 |
| 12.3 | Agent routing 테스트 | 자연어 입력이 올바른 command JSON/Tool call로 변환되는지 검증 | routing test 결과 | 11 |
| 12.4 | Migration 회귀 테스트 | 기존 migration happy path/fail/retry/user_edited 케이스 비교 | migration regression 결과 | 6 |
| 12.5 | Conversion 회귀 테스트 | no-bind, bind, test fail retry, non-select, user_edited 케이스 비교 | conversion regression 결과 | 7 |
| 12.6 | Tuning/Formatting 회귀 테스트 | no tuning, tuning pass/fail, formatting 저장 검증 | tuning/formatting 결과 | 8-9 |
| 12.7 | Supervisor 장시간 테스트 | heartbeat, stop request, no job sleep, 중복 실행 방지 검증 | soak test 결과 | 5 |
| 12.8 | 장애 주입 테스트 | DB error, LLM timeout/rate limit, SQL execution error 검증 | failure injection 결과 | 12.4-12.7 |

### 13. 배포 및 운영 문서화

| WBS | Task | 주요 내용 | 산출물 | 선행 |
| --- | --- | --- | --- | --- |
| 13.1 | Langflow runtime 설치 문서 | Python package, Oracle client, custom component path 정리 | install guide | 12 |
| 13.2 | 환경 변수/입력값 문서 | DB/LLM/schema/prompt/timeout 입력값 정리 | config guide | 13.1 |
| 13.3 | Flow export/import 문서 | Langflow Flow JSON export/import 절차 | deployment guide | 11 |
| 13.4 | 운영 runbook 작성 | start/stop/status/failure/rerun 절차 | runbook | 12 |
| 13.5 | 롤백 계획 작성 | 기존 `src` runtime으로 되돌리는 조건과 절차 | rollback plan | 12 |

## 4. 권장 개발 순서

1. 3장의 공통 런타임 검증을 먼저 수행하고 `runtime readiness report`를 만든다.
2. `BLOCKED` 항목이 없을 때만 업무 기능 개발로 넘어간다.
3. `Chat Agent + Chat Command Tool`은 command queue와 운영 요청 진입점으로 안정화한다.
4. `Supervisor Agent`는 Chat/Command 구조가 잡힌 뒤 batch 실행 틀로 붙인다.
5. 업무 Tool은 `DB Migration -> SQL Conversion -> SQL Tuning -> SQL Formatting` 순서로 구현한다.
6. 각 업무 Tool은 Agent 연결 전에 Tool 직접 실행으로 먼저 검증한다.
7. retry가 있는 업무는 Langflow node로 세분화하지 말고 Tool 내부 loop로 먼저 완성한다.
8. 운영 보조 Tool은 batch가 동작한 뒤 dashboard/failure/rerun 순서로 붙인다.

## 5. 1차 MVP 범위 제안

1차 MVP는 다음 범위로 제한하는 것이 좋다.

| 포함 | 제외 또는 2차 |
| --- | --- |
| Chat Agent command queue 등록 | 복잡한 대화형 SQL 수정 wizard |
| Supervisor start/stop/status | 다중 worker scale-out |
| Migration `status/list_pending/run_migration_job` | Migration prompt 세부 튜닝 UI |
| SQL Conversion `status/list_pending/run_sql_conversion_job` | Tuning/Formatting 완전 자동 연쇄 |
| `NEXT_BATCH_CONTROL`, `NEXT_BATCH_LOG` | 별도 웹 dashboard |
| Tool 직접 실행 테스트 | Langflow 화면 기반 E2E 자동화 |

MVP 완료 기준은 "Langflow에서 batch를 시작하고, pending migration 또는 SQL conversion job 1건을 조회해 실행하고, 결과와 로그가 기존 DB 테이블에 저장되는 것"으로 잡는다.

## 6. 리스크 및 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| Langflow component 간 import path 불안정 | 배포 후 component 로딩 실패 | 기존 `src`를 package로 설치하거나 component 내부 dependency를 명확히 vendor 처리 |
| 중간 SQL/CLOB을 edge로 전달 | JSON escape, truncation, prompt 오염 | 긴 SQL은 Tool 내부에서 처리하고 DB에 저장 |
| LLM이 잘못된 Tool을 선택 | 잘못된 job 실행 | Supervisor는 poll 결과 기반으로 Tool 후보를 제한하고 cycle당 1건 guard 적용 |
| background thread 중복 실행 | 동일 job 중복 처리 | `NEXT_BATCH_CONTROL` lock/heartbeat/stale takeover 구현 |
| retry state 손실 | 실패 후 복구 어려움 | retry loop는 Tool 내부 local state + DB log로 관리 |
| secret 노출 | 보안 사고 | secret은 Langflow component input으로만 받고 command JSON/Agent message에 넣지 않음 |

## 7. 완료 정의

전체 Langflow 전환 작업은 다음 조건을 만족하면 완료로 본다.

1. Langflow에서 Chat/Supervisor/Migration/Conversion/Tuning/Formatting Flow가 export 가능한 형태로 구성되어 있다.
2. 기존 `src` 로직의 주요 상태 전이와 DB 저장 결과가 Langflow 실행에서도 동일하다.
3. LLM prompt, Tool description, command JSON schema가 문서화되어 있다.
4. batch start/stop/status와 failure/rerun 운영 절차가 문서화되어 있다.
5. migration/conversion의 happy path, retry path, user-edited path, no-bind/non-select path가 검증되어 있다.
