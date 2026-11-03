# 구현 계약

## 테이블과 기존 2.0의 대응

기존 `00_guide/00_architecture_chapter6_oracle_ddl.md`와 실행기·관리 코드를 참고했습니다. 주요 컬럼 이름은 유지하고 저장소를 JSON으로 바꿨습니다. 테이블별 전체 컬럼은 `samples/table_definitions.json`에 있습니다.

| 테이블 | 키 / 역할 |
|---|---|
| ASIS_HR_EMP / TOBE_HR_EMP | EMPLOYEE_ID / EMP_ID. 직원 10명, 대상은 초기 0행 |
| ASIS_HR_DEPT / TOBE_HR_DEPT | DEPARTMENT_ID / DEPT_ID. 부서 3개, 대상은 초기 0행 |
| NEXT_MIG_INFO | MAP_ID. 테이블 매핑, 선행 작업, 사용 여부, 이관 SQL·상태 |
| NEXT_MIG_INFO_DTL | MAP_ID + MAP_DTL. 직접 컬럼 매핑 |
| NEXT_SQL_INFO | JSON 런타임 키 SQL_SEQ. 기존 SPACE_NM + SQL_ID의 유일성도 검사 |
| NEXT_MIG_RAG_INFO | RAG_ID. 이름만 유지한 참고 규칙 데이터; 검색 서비스 없음 |
| NEXT_MIG_LOG | LOG_ID. 같은 대화의 최근 30개 이벤트 |

`samples/tables/*.json`이 테이블별 관리 원본입니다. manifest.json은 파일·테이블·Tool 입력을 연결하고 samples/session.json은 초기 대화 메타데이터를 담습니다. `hr_demo.json`은 이 원본을 합친 생성 파일입니다. `columns`, `primary_key`, `rows` 구조입니다. 날짜는 ISO 형식 문자열이고 Oracle 자료형/제약조건을 모두 재현하지 않습니다. 로그 시간은 실제 시각 대신 `TURN-n`으로 표시합니다.

## Tool 입출력

현재 공개 Tool 입력은 **request/string, session_json/string 두 개**입니다. Agent tools는 표준 고정 Tool 객체 배열입니다. request는 모델 자동 입력, session_json은 자동 OFF/initialize.session_transport로 DSL에 사전 연결합니다. V3 문자열은 현재 테이블과 요청 ID를 포함합니다. 01이 복원·계획·실행·검증하고 smartmigrate.result.v1을 반환합니다. 모델이 전체 상태를 재작성하지 않습니다.

이관 실행 시 MIG_SQL/VERIFY_SQL/VERIFY2_SQL을 저장합니다. VERIFY_SQL/VERIFY2_SQL은 실제 Oracle에서 검증하지 않은 참고 SQL입니다. JSON 검증 결과는 migration_results 및 NEXT_MIG_INFO.LOG의 verification에 저장합니다. COUNT는 필터 원천과 대응 대상 PK 범위 및 컬럼 비-null 건수를 비교하고, CONCAT은 매핑 컬럼의 길이 접두 JSON 토큰을 PK별로 비교합니다. 범위 밖 대상 행은 보존되며 비교에 포함하지 않습니다. 실패하면 해당 실행 전체를 롤백하고 ERROR를 반환합니다.

현재 메인은 Start → initialize → Agent → commit → 상태 저장 → 해설 LLM → Answer입니다. 01 Tool은 Start → prepare → planner → execute → End입니다. tools 변수 참조 방식은 편집기 렌더링 오류 보고 후 폐기했습니다. 고정 배열의 템플릿 치환에는 따옴표/줄바꿈이 없는 V3만 사용합니다. Agent는 사용자 원문을 request로 전달하도록 지시받으며, 모델 전달의 정확성은 서버 검증이 필요합니다.

commit은 실제 Agent Tool 응답의 실행 결과를 받아 request_id/hash/revision을 검증합니다. 결과가 없으면 NO_RESULT로 입력 상태를 보존합니다. 표는 확정 상태에서 Code가 생성하며 narrative_json 요약으로 해설 LLM이 설명합니다. Agent/계획 LLM 실패 시 NO_RESULT/ERROR를 표시하고 업무 테이블을 변경하지 않습니다.

다음은 **01 Tool 내부 실행 Code의 결과 형식**입니다.

```json
{
  "kind": "smartmigrate.result.v1",
  "request_id": "현재 workflow_run_id",
  "base_hash": "입력 상태 SHA-256",
  "base_revision": 0,
  "status": "OK 또는 ERROR 또는 NEEDS_INPUT",
  "state_json": "갱신된 전체 상태 JSON 문자열",
  "answer": "표를 포함한 결과"
}
```

commit은 공식 Agent의 json 로그 중 data.output.tool_response 또는 data.output.tool_responses[]를 읽습니다. 도구명 smart_migrate_tool과 이전 별칭 smartmigrate_execute를 받습니다. TEXT/JSON 중복·이어 붙인 응답도 처리합니다. Agent 답변, llm_response, tool_call_input에서 상태를 복원하지 않습니다.

인사·소개·기능 안내는 예외적으로 업무 실행이 아닌 CHAT 응답입니다. commit의 user_request는 sys.query에서 연결하며 이 요청은 Tool 결과를 요구하지 않고 업무 상태·revision을 보존합니다. 최종 render Code는 CHAT의 일반 답변만 표시하여 NO_RESULT 진단이나 중복 해설이 붙지 않게 합니다. 업무 실행 요청은 여전히 검증된 Tool 결과가 필요합니다.

SQL 변환 대상 조회는 query_view=sql_source이며 NEXT_SQL_INFO의 식별자·대상 범위·BIND_SET과 원본 FR_SQL만 출력합니다. 변환 결과·튜닝 결과를 섞지 않고 실행 상태를 변경하지 않습니다. 로그·revision은 조회 이벤트로 증가할 수 있습니다.

같은 결과가 로그에 중복 기록된 경우 하나로 취급합니다. 다른 결과가 여러 개면 CONFLICT로 원래 상태를 유지합니다. request_id, base_revision, base_hash, 반환 상태 revision을 확인한 후 저장합니다. 해시는 오래된 상태 검사용이며 외부 인증 수단이 아닙니다.

## 실행과 갱신

각 단계는 `migration`, `conversion`, `tuning`, `formatting`입니다. 복합 요청은 actions 배열로 각 단계를 순서대로 실행하며 통합 단계는 없습니다. initialize가 sys.query를 패킷에 담고 prepare가 원문을 복원하며 execute는 요청 밖의 단계를 거부합니다. 변환/튜닝에 이관을 선행 실행하지 않습니다. 기본 SQL은 두 테이블 조인과 실제 바인드 자리를 포함한 한 개입니다. [시연 계약과 튜닝 규칙](RAG_TUNING_DEMO.md).

이관은 활성 매핑을 선행 작업 순서로 정렬하고 직접 컬럼 매핑을 적용합니다. 동일 키는 매핑 컬럼을 갱신하고 새 키는 추가합니다. 원천에 없는 대상 행은 유지합니다. 매핑 결과의 중복/빈 키는 오류입니다. 생성 MERGE SQL은 설명용이며 실행하지 않습니다.

지원 CONDITION은 빈 값 또는 `SALARY >= 7000`, `JOB_ID = 'IT_PROG'` 같은 단일 비교입니다. 수식 컬럼·함수 매핑·다중 조건은 지원하지 않습니다. 임의 Python/SQL을 eval/exec하여 업무 로직으로 실행하지 않습니다.

SQL 변환은 시연 우선으로 USE_YN과 작업 범위에 관계없이 전체 테이블·컬럼 매핑 및 SQL_CONVERSION 규칙을 생성기에 전달합니다. 매핑 가능한 부분을 변환하고 매핑이 없는 식별자는 원문을 유지합니다. 변환 결과는 비어 있지 않은 단일 SELECT/WITH인지 검사하며, 활성 매핑·FROM/JOIN 일치·컬럼 존재·바인드 일치·ASIS 잔존 검사는 하지 않습니다. CTE/inline view/스키마 접두사 등도 변환 단계에서 차단하지 않습니다. 생성기 실패나 빈 결과까지 성공으로 처리하지는 않습니다.

튜닝은 기존의 활성 매핑 범위 → 단일 SELECT → FROM/JOIN 대상 → qualified 컬럼 → 바인드 이름 보존 검사를 유지합니다. 튜닝의 지원 문법은 별칭을 명시한 단순 SELECT/JOIN이며 범용 SQL 파서는 아닙니다. 튜닝 전후 의미 동등성이나 Oracle 실행 가능성을 증명하지 않습니다. 서식 정리는 문자열 리터럴·바인드·SQL 토큰의 동일성을 검사합니다.

| 단계 | 갱신 컬럼 / 상태 |
|---|---|
| Migration | MIG_SQL, VERIFY_SQL, STATUS=`DEMO-PASS`, BATCH_CNT, LOG |
| Conversion | TO_SQL, BIND_SQL, TEST_SQL, STATUS_CONVERSION=`DEMO-GENERATED` |
| Tuning | TUNED_FR_SQL, TUNED_TO_SQL, TUNED_RESULT, STATUS_TUNING=`DEMO-PROPOSED` |
| Formatting | FORMATTED_SQL. 앞선 단계 상태로 최신성 확인 |

FR_SQL 원본과 BIND_SET 샘플은 유지합니다. BIND_SQL에는 바인드가 보존된 생성 SQL을 담고 실제 바인드 실행은 하지 않습니다. TEST_SQL/VERIFY_SQL은 실제 DB 검증 미수행을 명시합니다.

전체 실행은 하나의 변경 묶음으로 다룹니다. SQL 산출물 누락 등으로 실패하면 먼저 처리한 이관 변경까지 취소하고 실패 로그만 기록합니다. 잘못된 LLM JSON도 상태 변경 없이 오류 결과로 반환합니다. 모델 호출 자체의 실패/초기 JSON 오류는 Dify 노드 오류로 확인합니다.

## management와 상태

- `query`, `insert`, `update`, `delete`, `run`, `clarify`, `export`를 지원합니다.
- 관리 대상은 네 메타 테이블입니다. 사용자 업무 행을 임의로 쓰거나 로그를 직접 수정하지 않습니다.
- 변경/삭제에는 정확한 기본 키가 필요합니다. PK 변경은 금지하며 추가 후 삭제로 표현합니다.
- 매핑 마스터 삭제는 상세도 삭제합니다. PRIOR_MAP_ID, SQL REF_SEQ 참조 중 삭제는 거부합니다.
- 매핑 변경은 기존 작업 상태를 STALE로 표시합니다. SQL 원본/중간 SQL 변경도 후속 단계 상태를 STALE로 만듭니다. 기존 산출물은 이력으로 남습니다.
- 모호한 요청은 pending_action을 남기고 데이터를 바꾸지 않습니다. 정확한 요청은 last_target을 갱신합니다.
- 각 Tool 결과의 revision은 1 증가합니다. 같은 채팅에서 Variable Assigner로 유지하고 다른 채팅과 공유하지 않습니다.
- 테이블당 최대 200행, 전체 상태 180000자, 개별 SQL 12000자, 요청 6000자입니다. 출력 표는 테이블당 최근 30행과 대표 컬럼을 표시합니다. JSON 내보내기는 전체 값을 담습니다.

## 파일 변경 방법

실행 원본은 `nodes/`와 `prompts/`, 생성 원본은 `scripts/build.py`입니다. `nodes/engine.py`는 여러 Code 노드에 포함되므로 공유 로직 수정 후 build를 반드시 실행합니다. DSL에 포함된 코드는 로컬 모듈을 import하지 않습니다.

`scripts/demo.py`의 SQL fixture는 오프라인 검증용이며 Dify planner를 대체하는 실행 경로가 아닙니다. 샘플 기대 SQL은 고정된 입력만 대상으로 작성했습니다.

## 공식 참고

- [Agent](https://docs.dify.ai/en/use-dify/nodes/agent)
- [Workflow를 Tool로 발행](https://docs.dify.ai/en/cloud/use-dify/workspace/tools)
- [Code](https://docs.dify.ai/en/use-dify/nodes/code)
- [LLM](https://docs.dify.ai/en/use-dify/nodes/llm)
- [대화 변수 저장](https://docs.dify.ai/en/use-dify/nodes/variable-assigner)
- [공식 Chatflow DSL fixture](https://github.com/langgenius/dify/blob/main/api/tests/fixtures/workflow/basic_chatflow.yml)
- [공식 Agent JSON 출력 구현](https://github.com/langgenius/dify/blob/main/api/core/workflow/nodes/agent/message_transformer.py)
- [공식 Function Calling 전략](https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/function_calling.py)
- [공식 Workflow Tool 출력 구현, 1.9.1](https://github.com/langgenius/dify/blob/1.9.1/api/core/tools/workflow_as_tool/tool.py)

기본 문서·소스는 2026-10-16 조회했고 2026-10-18에 Dify 1.15.0 태그와 대조했습니다. 생성 DSL은 0.6.0이며 수동 Tool 입력은 직접 변수 selector입니다. [1.15 점검 기록](DIFY_1_15.md)을 참고하세요. 설치 서버에서의 실제 동작은 미검증입니다.
