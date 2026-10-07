# SmartMigrate 사용자 운영 가이드

이 문서는 운영자와 SQL 검토자가 채팅/Management 화면에서 작업을 조회하고, Correct SQL을 저장하고, 재실행을 준비하는 방법을 설명한다. 사용자는 Langflow payload JSON이나 LLM endpoint를 직접 입력할 필요가 없다.

## 1. 핵심 원칙

- 재개 위치는 `USER_EDITED`나 SQL CLOB 존재 여부가 아닌 **DB status**가 결정한다. `USER_EDITED='Y'`는 사람이 수정했음을 보여 주는 표시값이다.
- Correct SQL을 저장하면 그 SQL 단계가 사람 확인을 통과했다는 뜻으로 다음 status와 `RETRY_COUNT=0`을 함께 저장한다. 같은 단계를 다시 생성하지 않는다.
- Management에서 재시도 가능 상태로 만드는 일과 executor 실제 실행은 별도다. 준비 뒤에는 별도로 실행을 요청한다.
- SQL Conversion의 유사 AS-IS SQL 검색/일괄 반영은 제공하지만 DB Migration에는 제공하지 않는다.
- Correct SQL은 저장 성공 뒤 해당 한 단계만 Vector DB에 동기화한다. 상태 변경이나 RAG Guide 변경만으로는 자동 동기화하지 않는다.

## 2. 작업 조회와 자동 실행 후보

SQL 작업은 `SQL_SEQ`로 찾는 것이 가장 정확하다. 이를 모르면 `SQL_ID`와 `SPACE_NM`을 함께 제공한다. DB Migration은 `MAP_ID`로 찾는다.

| 원하는 일 | 자연어 요청 예시 |
|---|---|
| Migration 상태/로그 | `MAP_ID 101의 Migration 상태와 최근 로그를 보여줘.` |
| Migration SQL | `MAP_ID 101의 MIG_SQL과 VERIFY_SQL을 보여줘.` |
| Conversion SQL | `SQL_SEQ 42의 Conversion 상태와 TO_SQL을 보여줘.` |
| SQL_SEQ를 모르는 작업 | `SQL_ID S001, SPACE_NM PAYMENT의 상태를 보여줘.` |
| 남은 작업 | `Conversion, Tuning, Formatting의 남은 작업을 보여줘.` |
| 실패 원인 | `최근 SQL Conversion 실패 원인을 요약해줘.` |

| 도메인 | 자동 실행 후보 조건 |
|---|---|
| DB Migration | `USE_YN='Y'`, `STATUS`가 NULL 또는 `FAIL-*`, `RETRY_COUNT < 2` |
| SQL Conversion | `STATUS_CONVERSION`이 NULL 또는 `FAIL-*`, `RETRY_COUNT < 2` |
| SQL Tuning | Conversion이 PASS이고 `STATUS_TUNING`이 NULL 또는 `FAIL-*`, `RETRY_COUNT < 2` |
| SQL Formatting | Tuning이 PASS이고 `FORMATTED_SQL`이 비어 있음 |

`RUNNING-*`은 이미 처리 중인 상태이므로 자동 후보가 아니다.

## 3. DB Migration

표준 실행기는 `10C_migOneJobPocExecutor3.py`다. 생성 → INSERT 실행 → 건수 검증 → 레코드 검증 순서로 진행한다.

| status | 의미 | 다음 실행의 시작점 |
|---|---|---|
| `FAIL-TRUNCATE` | target 초기화/실행 준비 실패 | 새 executor 실행에서는 MIG_SQL 생성부터 다시 진행 |
| `FAIL-INSERT` | MIG_SQL 생성 또는 INSERT 실행 실패 | MIG_SQL 생성부터 재시도 |
| `FAIL-TEST` | INSERT 완료 후 건수 검증 실패 | VERIFY_SQL 생성·건수 검증부터 재시도 |
| `FAIL-TEST2` | 건수 검증 통과 후 레코드 비교 실패 | 레코드 검증만 재시도 |
| `PASS` | Migration 및 검증 완료 | 자동 재실행 대상 아님 |

### Migration Correct SQL

Correct SQL은 한 번에 한 종류만 저장한다.

| 저장 대상 | 사용자가 보장하는 의미 | 저장 직후 status | 다음 동작 |
|---|---|---|---|
| `MIG_SQL` | INSERT가 이미 사람 확인을 통과함 | `FAIL-TEST`, `RETRY_COUNT=0` | INSERT를 다시 하지 않고 VERIFY_SQL 생성·건수 검증부터 시작 |
| `VERIFY_SQL` | 검증까지 사람이 완료함 | `PASS`, `RETRY_COUNT=0` | Migration 완료 |

예시: `MAP_ID 101의 Correct MIG_SQL을 아래 SQL로 저장해줘. SQL: ...`

저장 성공 뒤 `MIG_SQL` 또는 `VERIFY_SQL` kind로 Vector DB 동기화를 수행한다. `VERIFY2_SQL`은 시스템이 `MIG_SQL`과 `VERIFY_SQL`로 조합하는 레코드 비교용 SQL이므로 Correct SQL로 직접 저장하거나 formatting 대상으로 지정하지 않는다. 레코드 비교 결과는 `MATCH_CNT`, `MISMATCH_CNT` 중심으로 확인하며, `MISMATCH_CNT=0`이면 통과다.

## 4. SQL Conversion

Conversion은 `TO_SQL` → `BIND_SQL`/`BIND_SET` → `TEST_SQL` 순서다. SELECT가 아닌 작업은 bind/test를 건너뛸 수 있다.

| status | 의미 | 다음 실행의 시작점 |
|---|---|---|
| `FAIL-TOBE` | TO_SQL 생성 실패 | TO_SQL 생성 |
| `FAIL-BIND` | BIND_SQL 생성 실패 | 저장된 TO_SQL을 재사용하고 bind 생성 |
| `FAIL-TEST` | TEST_SQL 생성·실행 실패 | 저장된 TO_SQL/BIND 정보를 재사용하고 test 생성·실행 |
| `PASS-CONVERSION` | Conversion 완료 | Tuning으로 진행 |

`FAIL-BIND` 또는 `FAIL-TEST`에서 필요한 이전 단계 SQL이 비어 있으면 시스템은 이전 단계로 되돌아가 생성하지 않는다. 현재 failure status를 보존한 채 실패로 남긴다. 즉 `FAIL-BIND`가 `FAIL-TOBE`로 역행하지 않는다.

### Conversion Correct SQL

Correct SQL은 `TO_SQL`, `BIND_SQL`, `TEST_SQL` 중 정확히 하나만 저장한다. `USER_EDITED='Y'`도 기록되지만 재개 위치는 아래 status 전이로 정해진다.

| 저장 대상 | 필수 추가값 | 저장 직후 status | 다음 실행 |
|---|---|---|---|
| `TO_SQL` | 없음 | `FAIL-BIND`, `RETRY_COUNT=0` | TO_SQL을 재생성하지 않고 bind 단계부터 시작 |
| `BIND_SQL` | `BIND_SET` | `FAIL-TEST`, `RETRY_COUNT=0` | bind를 재생성하지 않고 test 단계부터 시작 |
| `TEST_SQL` | 없음 | `PASS-CONVERSION`, `RETRY_COUNT=0` | Conversion 완료, Tuning 대상 |

예시:

- `SQL_SEQ 42의 Correct TO_SQL을 저장해줘. SQL: ...`
- `SQL_SEQ 42의 Correct BIND_SQL과 BIND_SET을 저장해줘.`
- `SQL_SEQ 42의 Correct TEST_SQL을 저장해줘.`

저장 성공 후 해당 kind만 Vector DB에 동기화한다. 현재 Update Tool은 Correct `BIND_SQL` 저장 시 `BIND_SET`을 JSON 객체 배열로 검증하며 빈 배열은 허용하지 않는다. bind 변수가 없는 SELECT에서 빈 bind set을 허용해야 한다는 운영 정책과는 불일치하므로, 이 경우에는 자동 저장 전에 관리자 확인이 필요하다.

### 유사 SQL 찾기 및 일괄 반영

이 기능은 SQL Conversion 전용이다.

1. Correct SQL을 저장하고 해당 kind의 Vector DB 동기화를 완료한다.
2. `SQL_SEQ 42와 AS-IS SQL이 유사한 실패 Conversion 작업을 찾아줘.`라고 요청한다.
3. 후보의 SQL 식별자, target table, `STATUS_CONVERSION`, 유사도를 확인한다.
4. 승인된 후보에는 `REF_SEQ=42`와 `RETRY_COUNT=0`만 반영한다. 후보는 Correct SQL 종류와 같은 실패 단계여야 한다.
5. `STATUS_CONVERSION`은 보존된다. 이후 실행 시 해당 stage에서 `REF_SEQ` Correct SQL을 힌트로 사용한다.

유사도 검색은 읽기 전용이다. PASS 행이나 Tuning status를 기준으로 후보를 고르지 않으며, DB Migration에는 이 기능이 없다.

## 5. SQL Tuning과 Formatting

Conversion이 `PASS-CONVERSION`이면 같은 실행 흐름에서 Tuning으로 이어진다.

| 도메인 | status/결과 | 재개 방식 |
|---|---|---|
| SQL Tuning | `FAIL-TUNED` | TUNED_TO_SQL 생성/튜닝 규칙 적용부터 재시도 |
| SQL Tuning | `FAIL-TEST` | 저장된 TUNED_TO_SQL으로 tuned test 생성·검증만 재시도 |
| SQL Tuning | `PASS-TUNING` | Formatting 대상 |
| SQL Formatting | `FORMATTED_SQL` 비어 있음 | 이번 실행에서 생성된 SQL만 formatting |

Formatting은 DB의 모든 SQL을 다시 포맷하지 않는다. 해당 실행에서 생성된 SQL 목록만 받아 `FORMATTED_SQL`에 저장한다. 대상이 없으면 `NO_FORMATTING_TARGETS`로 통과한다.

## 6. RAG Guide 관리

| 목적 | `CATEGORY` | `RULE_TYPE` | 주요 입력 |
|---|---|---|---|
| Conversion 공통 규칙 | `SQL_CONVERSION` | `GENERAL` | `SOURCE_TABLES`, `GUIDANCE_TEXT` |
| Conversion 사례 | `SQL_CONVERSION` | `SEARCH` | `SOURCE_TABLES`, `SOURCE_SQL`, `TARGET_SQL` |
| Tuning 공통 규칙 | `SQL_TUNING` | `GENERAL` | `GUIDANCE_TEXT` |
| Tuning 사례 | `SQL_TUNING` | `SEARCH` | `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL` |

RAG Guide를 추가/수정/비활성화한 뒤에는 필요할 때 별도로 Vector DB 동기화를 요청한다. Correct SQL 동기화와 RAG Guide 동기화는 서로 다른 작업이다.

## 7. LLM 연결과 실행 요청

LLM 연결은 executor마다 endpoint/API key를 입력하는 방식이 아니다. Langflow에서 공식 OpenAI-compatible Chat Model을 설정하고 각 executor의 `Language Model` 입력에 연결한다.

- 공식 모델 노드: `model_name`, `api_key`, `base_url`, `temperature` 설정
- 배치 실행: `stream=False`, `stream_usage=False`
- executor: 연결된 `LanguageModel`에 SQL/라우팅 프롬프트를 전달할 뿐 API key나 endpoint를 받지 않음
- RAG embedding endpoint/API key/model은 Chat Model 설정과 별도

실행 예시:

- `MAP_ID 101 Migration을 실행해줘.`
- `SQL_SEQ 42 Conversion을 재실행해줘.`
- `PAYMENT 공간의 실패한 SQL Conversion 작업을 실행해줘.`
- `전체 workflow의 남은 작업을 실행해줘.`

## 8. 자주 보는 문제

| 증상 | 확인할 내용 |
|---|---|
| 자동 실행 후보에 없음 | status가 NULL/`FAIL-*`인지, `RETRY_COUNT < 2`인지, Migration은 `USE_YN='Y'`인지 확인 |
| Correct SQL 저장 뒤 실행되지 않음 | 저장은 실행 요청이 아니다. 저장된 status를 확인하고 별도로 실행 요청 |
| `FAIL-BIND`인데 TO_SQL이 없음 | TO_SQL을 새로 만들지 않고 `FAIL-BIND`를 유지한다. 데이터 정합성을 확인 후 올바른 단계 SQL 저장 |
| Correct SQL이 검색되지 않음 | 저장 뒤 해당 kind의 Vector DB 동기화가 완료됐는지 확인 |
| Migration 유사 작업 일괄 반영 요청 | 지원하지 않는다. MAP_ID별 Correct SQL 저장과 동기화를 수행 |
| Formatting 결과가 없음 | Tuning이 PASS인지, 이번 실행에서 생성된 SQL이 있는지 확인 |

상세 상태 전이, LLM 연결 구조, DB 컬럼과 logging 규칙은 개발자용 [아키텍처 가이드](00_architecture.md)를 참고한다.

## 매핑 룰 SQL 생성과 실행

매핑 정보 전체를 요청문에 넣습니다. 예: `MAP_ID 101의 TO_TABLE을 MEMBER로 수정해줘.` 원본 테이블명/컬럼명, MAP_ID와 부모 관계를 명시해야 합니다. detail은 MAP_ID + MAP_DTL로 식별합니다. 매핑 생성기는 첨부 metadata를 별도로 읽지 않습니다.

기본 설정은 SQL 생성만 수행합니다. DB에 실제 적용하려면 운영자가 Mapping Rule Update 컴포넌트의 Execute Generated SQL 옵션을 켜야 합니다. 실행 여부는 해당 옵션 하나로 결정하며 요청문 키워드로 바뀌지 않습니다. 출력에서 등록 대상 표와 성공/실패 집계를 확인합니다. 신규 master는 USE_YN=Y, PRIORITY=5가 기본입니다. SQL 원문은 로그에서 확인합니다.

현재 Router는 이전 대화에서 작업 대상을 복원하지 않습니다. “네”, “그거 실행해” 대신 MAP_ID 또는 SQL_ID + SPACE_NM을 포함한 요청을 사용합니다. 파일 경로만 전달되고 URL이 없으면 자동 파싱할 수 없으므로 다운로드 URL 또는 매핑 본문을 제공합니다.
