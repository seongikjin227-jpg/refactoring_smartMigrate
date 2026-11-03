# Dify 단일 SQL 시연과 Agent 호출 점검

이번 변경은 Dify용입니다. Langflow 구현은 수정하지 않았습니다. 샘플 SQL은 SQL_SEQ=1, EMP_DEPT_TUNING_DEMO 한 개입니다.

## 시연 순서

1. **안녕, 어떤 에이전트야?** — 역할과 기능을 안내합니다. Tool 호출과 데이터 변경은 없습니다.
2. **매핑룰 조회해줘** — NEXT_MIG_INFO·NEXT_MIG_INFO_DTL 전체 조회. 기본 직원·부서 매핑이 있으므로 별도 대상명을 요구하지 않습니다.
3. **DB Migration 실행해줘** — JSON 이관만 수행. 원천/대상 데이터, COUNT·PK별 CONCAT, 최종 결과, MIG_SQL·VERIFY_SQL·VERIFY2_SQL을 출력합니다.
4. **SQL 변환 대상 조회해줘** — NEXT_SQL_INFO의 FR_SQL과 바인드 값을 확인합니다. 변환은 실행하지 않습니다.
5. **SQL 변환 실행해줘** — 기본 SQL 한 개를 변환합니다. 테이블·컬럼만 바꾸며 바인드·WHERE·(+)·IN 구조는 보존합니다. 이관·튜닝은 실행하지 않습니다.
6. **SQL 튜닝룰 조회해줘** — NEXT_MIG_RAG_INFO의 활성 SQL_TUNING 규칙과 SOURCE_SQL/TARGET_SQL을 조회합니다. 전체 원문은 `NEXT_MIG_RAG_INFO 조회해줘`로 확인합니다.
7. **SQL 튜닝 실행해줘** — 직전 TO_SQL에 활성 규칙을 적용해 TUNED_TO_SQL·TUNED_RESULT를 만듭니다. 원래 TO_SQL을 보존합니다.

결과만 보려면 `SQL 결과 보여줘`를 사용합니다. 여러 작업을 동시에 요청하면 `actions` 배열로 요청한 순서대로 처리합니다. 예: `SQL 변환 및 튜닝 진행해줘`는 conversion과 tuning 두 작업이며 이관은 추가하지 않습니다. 별도의 통합 실행 단계는 없습니다. 한 작업이 실패하면 이번 요청의 업무 변경을 취소합니다.

## 단일 샘플

```sql
SELECT e.EMPLOYEE_ID, e.FIRST_NAME, d.DEPARTMENT_NAME, e.SALARY
FROM ASIS_HR_EMP e, ASIS_HR_DEPT d
WHERE e.DEPARTMENT_ID = d.DEPARTMENT_ID(+)
  AND e.DEPARTMENT_ID = :dept_id
  AND e.EMPLOYEE_ID IN (
    SELECT s.EMPLOYEE_ID FROM ASIS_HR_EMP s
    WHERE s.SALARY >= :min_salary
  )
ORDER BY e.EMPLOYEE_ID
```

BIND_SET은 `{"dept_id":20,"min_salary":7000}`입니다. 실제 바인드 자리가 있는 입력 SQL입니다. Dify에서 실제 DB의 값을 추출했다는 의미는 아닙니다.

Conversion 후에도 두 바인드·(+)·IN 구조가 남아 있어야 합니다. 튜닝 결과의 예상 형태는 다음과 같습니다. 아래는 고정 fixture로 확인한 예시이며 실제 모델의 실행 결과를 뜻하지 않습니다.

TO_SQL은 LLM이 생성합니다. 생성 프롬프트에 테이블·컬럼 매핑만 적용하고 조인·서브쿼리·조건 구조를 변경하지 않도록 명시했습니다. 단독 Conversion의 LLM 문맥에는 SQL_CONVERSION 규칙만 전달하며 튜닝 규칙·과거 튜닝 산출물은 제외합니다. 실제 저장 상태는 유지하고 LLM 입력만 분리합니다. Tuning에는 SQL_TUNING 규칙과 저장된 TO_SQL을 전달합니다. 구조 변경을 판정해 거부하는 새 실행 검사는 추가하지 않았습니다.

```sql
SELECT e.EMP_ID, e.GIVEN_NAME, d.DEPT_NAME, e.BASE_SALARY
FROM TOBE_HR_EMP e
LEFT JOIN TOBE_HR_DEPT d ON e.DEPT_ID = d.DEPT_ID
WHERE 1 = 1 AND (
  e.DEPT_ID = :dept_id AND e.BASE_SALARY >= :min_salary
)
ORDER BY e.EMP_ID
```

## 튜닝 규칙 원본

`samples/tables/NEXT_MIG_RAG_INFO.json`을 관리합니다. 기존 컬럼은 유지합니다.

| RAG_ID | CATEGORY | GUIDANCE_TEXT 식별자 | 역할 |
| --- | --- | --- | --- |
| 1 | SQL_CONVERSION | 매핑·원래 구조 보존 | Conversion에만 적용 |
| 2 | SQL_TUNING | [WHERE_ONE] | WHERE 1 = 1 작성 규칙. 성능 개선 주장이 아님 |
| 3 | SQL_TUNING | [ANSI_JOIN] | (+) → ANSI LEFT/RIGHT JOIN. NULL·조건 위치 보존 |
| 4 | SQL_TUNING | [REDUCE_SUBQUERY] | 같은 테이블 PK를 다시 확인하는 IN 축소. PK 유일성·NOT NULL 전제 |

USE_YN=Y이고 SOURCE_TABLES와 SQL의 AS-IS 범위가 겹치는 규칙만 튜닝에 적용합니다. 빈 SOURCE_TABLES는 공통입니다. 활성 규칙이 없으면 TO_SQL을 유지합니다. Code의 WHERE 보정·잔존 (+) 검사는 활성 규칙에 의해 켜집니다. ANSI·서브쿼리 변경은 LLM 제안이며 정적 검사만으로 의미 동등성을 증명하지 않습니다.

`튜닝 규칙 RAG_ID 2를 비활성화해줘`라고 하면 가이드에서 빠지고 WHERE 규칙을 강제하지 않습니다. 규칙 변경 시 기존 튜닝은 STALE로 표시하며 포매팅 결과를 비웁니다. 임베딩·벡터 검색은 없습니다.

## 사용자가 첨부한 로그의 판정

`ROUND 1 status=success`, `Thought status=success`이지만 `text=""`, `tool_responses=[]`, `tool_input=[]`, `tool_name=""`였습니다. 이는 Tool CALL 성공 로그가 아닙니다. 모델의 텍스트와 Tool 호출이 모두 비어 있고, 원인 제공 계층은 아직 모델/API 호환 제공자/Agent 연동 중에서 확정되지 않았습니다. 매핑 대상 이름 누락이 원인은 아닙니다.

이를 재현하는 회귀 테스트를 추가하고 NO_RESULT의 원인을 EMPTY_AGENT_RESPONSE로 표시했습니다. 후속 narrator는 이 오류에 고객·매핑명을 재요청하는 설명을 만들지 않도록 했습니다.

현재 기본 DSL은 **Function Calling·최대 반복 1**입니다. ReAct로 전환했던 시도는 현재 Azure/LiteLLM의 stop 미지원 400을 발생시켜 되돌렸습니다. ReAct가 stop을 내부에 추가하므로 입력 JSON에서 필드만 지워서는 해결되지 않습니다. [확인된 오류와 대응](GPT55_STOP_ERROR.md). 이전 빈 Function Calling 응답 원인까지 해결됐다고 주장하지 않습니다. 모델·Tool ID는 유지하며 업무 상태는 대화 JSON으로 관리합니다. ReAct 로그 수신 호환성은 남아 있지만 기본 전략으로 사용하지 않습니다.

근거: [공식 ReAct 전략 정의](https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/ReAct.yaml), [공식 ReAct 호출·로그 구현](https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/ReAct.py). 서버에 설치된 0.0.18과 최신 소스의 모든 동작이 동일하다고 검증한 것은 아닙니다.

## Dify에 적용

1. `dsl/01_smartmigrate_tool.yml`을 Workflow에 반영하고 Tool로 다시 발행합니다. 공개 입력은 request/session_json 두 개, End 출력은 result_json입니다.
2. `dsl/02_smartmigrate_chatflow.yml`을 반영합니다. Agent는 Function Calling·최대 반복 1·Tool enabled, request 자동 ON, session_json 자동 OFF이며 initialize.session_transport에 연결되어야 합니다. 이번 stop 오류만 수정할 때 01 Tool 재발행은 필요 없습니다.
3. 새 앱으로 가져오면 Tool ID가 달라질 수 있으므로 실제 발행된 Tool을 선택합니다. 모델·Tool의 워크스페이스 설정은 로컬 빌드에서 유지합니다.
4. **새 대화**를 시작합니다. 기존 대화에는 예전 SQL 여러 개와 예전 RAG 데이터가 남습니다.
5. 첫 문장 `매핑룰 조회해줘` 후 Agent 추적에서 실제 **CALL smart_migrate_tool**과 `data.output` 안의 `result_json`을 확인합니다. 메인 commit_status는 OK, 매핑 마스터·상세 표가 나와야 합니다.

400이 사라진 뒤에도 모델이 빈 텍스트와 빈 tool_calls를 반환하면 프롬프트만으로 해결됐다고 볼 수 없습니다. 같은 제공자의 일반 LLM 노드에서 텍스트 응답과 native tool_calls·스트리밍을 확인하고 실제 endpoint/model 매핑·제공자 설정을 점검해야 합니다. 로컬에서는 서버 모델을 호출하지 않아 복구 성공을 단정하지 않습니다.

로컬 검증은 고정 계획·SQL fixture와 exported Code입니다. JSON 이관 비교와 소규모 SQLite fixture의 NULL/미매칭 부서·필터 결과를 검사했습니다. 실제 Dify 모델·Oracle 실행·실행 계획·성능은 미검증입니다. Dify의 BIND_SQL/TEST_SQL은 실제 값 조회·대입 실행을 구현한 것이 아니며, 원본 Oracle Agent 설명과 구분합니다.
