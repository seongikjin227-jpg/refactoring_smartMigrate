# Chapter 1. User Guide

이 문서는 SmartMigrate에서 사용할 수 있는 주요 기능, 요청 형식, 필수 입력값, 처리 결과를 정리한 사용자 안내서다.

## 1.1 기능 요약

| 구분 | 기능 | 주요 대상 | 처리 결과 |
|---|---|---|---|
| 조회 | 전체 현황, 진행 현황, 실패 원인, SQL 원문 조회 | 전체 작업, `MAP_ID`, `SQL_ID` + `SPACE_NM` | DB 상태와 로그를 조회하고 근거 기반 답변을 반환한다. |
| 실행 | DB Migration, SQL Conversion, SQL Tuning, SQL Formatting, 전체 workflow 실행 | 전체 잔여 작업 또는 특정 작업 | 실행 가능한 job을 DB에서 다시 조회한 뒤 도메인별 executor를 실행한다. |
| 수정 | 실패 job 상태 초기화, priority 변경, 담당자 보정 SQL 저장, `USER_EDITED` 변경, DB Migration `USE_YN` 변경 | `MAP_ID` 또는 `SQL_ID` + `SPACE_NM` | 상태값, retry count, priority, `USER_EDITED`, `USE_YN` 또는 SQL CLOB를 DB에 반영한다. |
| RAG Guide 관리 | Conversion/Tuning 가이드 조회, 추가, 수정, 비활성화 | `NEXT_MIG_RAG_INFO`, `RAG_ID` | RAG rule 원천 테이블을 관리한다. 신규/수정분은 VectorDB 동기화 후 검색에 반영된다. |
| AS-IS SQL 유사도 검색 | 유사한 원본 SQL 및 재시도 후보 조회 | SQL 본문 또는 `SQL_ID` + `SPACE_NM` | AS-IS SQL 벡터 검색 뒤 Oracle 최신 상태를 기준으로 후보를 반환한다. |
| VectorDB 관리 | RAG Guide, Correct SQL, AS-IS SQL 동기화 | Oracle 원천 테이블, Milvus collection | 조건을 만족하는 원천 row를 Milvus에 upsert한다. 기존 문서는 자동 삭제·비활성화하지 않는다. |

## 1.2 조회 및 분석 요청

| 기능 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|
| 전체 대시보드 조회 | 없음 | 없음 | `전체 대시보드 보여줘.` | DB Migration, SQL Conversion, SQL Tuning, SQL Formatting의 상태별 건수를 보여준다. |
| 현재 진행 현황 조회 | 없음 | 없음 | `지금 실행 중인 작업과 최근 로그 5건 보여줘.` | running job과 최근 workflow 로그 5건을 보여준다. |
| DB Migration 상태 조회 | `MAP_ID` | 조회할 SQL 컬럼, 로그 범위 | `MAP_ID={MAP_ID} 상태와 최근 로그 보여줘.` | `NEXT_MIG_INFO` 기준 상태, 대상 테이블, 최근 로그를 조회한다. |
| DB Migration SQL 원문 조회 | `MAP_ID`, SQL 컬럼명 | 없음 | `MAP_ID={MAP_ID}의 MIG_SQL과 VERIFY_SQL 원문 보여줘.` | `MIG_SQL`, `VERIFY_SQL` 등 요청한 CLOB 원문을 반환한다. |
| SQL job 상태 조회 | `SQL_ID`, `SPACE_NM` | 도메인 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} 상태 알려줘.` | `NEXT_SQL_INFO` 기준 Conversion/Tuning/Formatting 상태와 로그를 조회한다. |
| SQL job SQL 원문 조회 | `SQL_SEQ` 또는 `SQL_ID`, `SPACE_NM`, SQL 컬럼명 | 없음 | `SQL_SEQ=42의 TO_SQL, BIND_SQL, TEST_SQL 보여줘.` | 요청한 SQL 컬럼의 CLOB 원문을 반환한다. |
| 실패 원인 분석 | 없음 | 도메인, `MAP_ID`, `SQL_ID`, `SPACE_NM`, 건수 | `최근 SQL Conversion 실패 10건 원인 요약해줘.` | 상태, 로그, 저장 SQL을 근거로 실패 원인과 확인 포인트를 요약한다. |
| AS-IS SQL 유사도 검색 | SQL 본문 또는 `SQL_ID`, `SPACE_NM` | 상태 필터, Conversion/Tuning 범위, 건수 | `SQL_ID=S001, SPACE_NM=PAYMENT와 비슷한 AS-IS SQL을 가진 실패 SQL ID를 찾아줘.` | `EDIT_FR_SQL` 우선, 없으면 `FR_SQL`을 임베딩해 유사 SQL 목록을 찾고 Oracle 최신 상태로 필터링한다. 검색만으로 상태는 변경하지 않는다. |
| RAG Guide 조회 | 없음 | `CATEGORY`, `RULE_TYPE`, 검색어, 사용 여부, 조회 건수, 원문 포함 여부 | `SQL Conversion RAG Guide 중 CUSTOMER가 포함된 항목 20건 조회해줘.` | 조건에 맞는 `NEXT_MIG_RAG_INFO` row를 조회한다. 검색어는 `SOURCE_TABLES`, `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL`에서 찾는다. |

## 1.3 실행 요청

| 실행 기능 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|
| DB Migration 전체 실행 | 없음 | 우선순위, 실행 건수 | `남은 DB Migration 작업 실행해줘.` | 실행 가능한 migration job을 조회해 MIG SQL 생성, 실행, 검증을 수행한다. |
| DB Migration 단건 실행 | `MAP_ID` | 없음 | `MAP_ID={MAP_ID} DB Migration 실행해줘.` | 지정한 migration job을 대상으로 실행한다. |
| SQL Conversion 전체 실행 | 없음 | 우선순위, 실행 건수 | `SQL Conversion 남은 작업 실행해줘.` | `TO_SQL`, `BIND_SQL`, `TEST_SQL` 생성과 검증을 수행한다. |
| SQL Conversion 단건 실행 | `SQL_ID`, `SPACE_NM` | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Conversion 실행해줘.` | 지정한 SQL job의 변환 SQL을 생성하고 검증한다. |
| SQL Tuning 전체 실행 | 없음 | 우선순위, 실행 건수 | `SQL Tuning 남은 작업 실행해줘.` | RAG/Correct SQL 힌트를 참고해 튜닝 SQL과 검증 결과를 저장한다. |
| SQL Tuning 단건 실행 | `SQL_ID`, `SPACE_NM` | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Tuning 실행해줘.` | 지정한 SQL job의 튜닝 SQL을 생성하고 검증한다. |
| SQL Formatting 전체 실행 | 없음 | 우선순위, 실행 건수 | `SQL Formatting 남은 작업 실행해줘.` | 변환/튜닝 SQL을 formatting하고 `FORMATTED_SQL`에 저장한다. |
| SQL Formatting 단건 실행 | `SQL_ID`, `SPACE_NM` | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Formatting 실행해줘.` | 지정한 SQL job의 formatting 결과를 저장한다. |
| 전체 workflow 실행 | 없음 | 도메인별 실행 조건 | `전체 작업을 DB Migration부터 SQL Formatting까지 진행해줘.` | DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 순서로 실행한다. |

SQL 관련 단건 작업은 `SQL_ID`와 `SPACE_NM`을 모두 입력해야 한다. `SQL_ID`만으로는 `NEXT_SQL_INFO`의 단일 row를 확정하지 않는다.

## 1.4 수정 및 저장 요청

| 기능 | 대상 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|---|
| DB Migration 재시도 준비 | `NEXT_MIG_INFO` | `MAP_ID` | `PRIORITY=1` 또는 `PRIORITY=5` | `MAP_ID={MAP_ID} RETRY_COUNT를 0으로 바꾸고 PRIORITY=1로 설정해줘.` | `STATUS`와 SQL 본문을 유지하고 retry count만 초기화한다. |
| SQL Conversion 재시도 준비 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM` | `PRIORITY=1` 또는 `PRIORITY=5` | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Conversion 재시도 준비해줘.` | `STATUS_CONVERSION`을 유지하고 `RETRY_COUNT=0`으로 바꾼다. |
| SQL Tuning 재시도 준비 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM` | `PRIORITY=1` 또는 `PRIORITY=5` | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Tuning 재시도 준비해줘.` | `STATUS_TUNING`을 유지하고 `RETRY_COUNT=0`으로 바꾼다. |
| DB Migration 보정 SQL 저장 | `NEXT_MIG_INFO` | `MAP_ID`, SQL 컬럼명, SQL 본문 | 없음 | `MAP_ID={MAP_ID}의 MIG_SQL을 아래 SQL로 저장해줘. SQL=...` | 사용자가 제공한 SQL을 지정 컬럼에 저장하고 `USER_EDITED='Y'`로 표시한다. |
| SQL job 보정 SQL 저장 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM`, SQL 컬럼명, SQL 본문 | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM}의 TO_SQL을 아래 SQL로 저장해줘. SQL=...` | 사용자가 제공한 SQL을 지정 컬럼에 저장하고 `USER_EDITED='Y'`로 표시한다. |
| DB Migration USER_EDITED 변경 | `NEXT_MIG_INFO` | `MAP_ID`, `USER_EDITED=Y` 또는 `USER_EDITED=N` | 없음 | `MAP_ID={MAP_ID}의 USER_EDITED를 N으로 바꿔줘.` | SQL 본문은 유지하고 `USER_EDITED` 값만 변경한다. |
| SQL job USER_EDITED 변경 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM`, `USER_EDITED=Y` 또는 `USER_EDITED=N` | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM}의 USER_EDITED를 N으로 바꿔줘.` | SQL 본문과 상태는 유지하고 `USER_EDITED` 값만 변경한다. |
| DB Migration USE_YN 변경 | `NEXT_MIG_INFO` | `MAP_ID`, `USE_YN=Y` 또는 `USE_YN=N` | 없음 | `MAP_ID={MAP_ID}의 USE_YN을 N으로 바꿔줘.` | SQL 본문과 상태는 유지하고 실행 대상 사용 여부만 변경한다. |

허용 SQL 컬럼은 DB Migration의 경우 `MIG_SQL`, `VERIFY_SQL`이고, SQL job의 경우 `TO_SQL`, `BIND_SQL`, `TEST_SQL`, `TUNED_TO_SQL`, `FORMATTED_SQL`이다.

### AS-IS SQL 유사도 검색 및 실패 재시도

`SM_ASIS_SQL`은 `NEXT_SQL_INFO`의 `SQL_ID`, `SPACE_NM`, `TAG_KIND`, `TARGET_TABLE`, `FR_SQL`, `EDIT_FR_SQL`만 보관하는 AS-IS 전용 Milvus 컬렉션이다. 검색 content는 `EDIT_FR_SQL`이 있으면 이를, 없으면 `FR_SQL`을 사용한다. TO-BE SQL이나 실행 상태는 벡터 DB에 저장하지 않는다.

| 단계 | 사용자 요청 예시 | 처리 |
|---|---|---|
| 유사 SQL 검색 | `아래 SQL과 비슷한 실패 SQL 최대 20개 찾아줘. SQL=...` | 사용자 반환 결과는 최대 20건이다. 실패 판정은 항상 `STATUS_CONVERSION='FAIL' OR LIKE 'FAIL-%'`만 사용하며 SQL Tuning 실패 상태는 검색하지 않는다. 기본 최소 유사도는 Tool 입력의 70%다. 사용자가 유사도 조건을 말하지 않으면 command JSON의 `min_similarity`는 생략하며, `유사도 80% 이상` 요청 시에만 값을 전달한다. AS-IS SQL 유사도가 검색 기준이고, `TARGET_TABLE`이 겹치는 후보를 먼저 정렬한다. 결과 표에는 `SQL_ID`, `SPACE_NM`, `TARGET_TABLE`, `TARGET_TABLE 겹침`, `STATUS_CONVERSION`, 유사도가 포함된다. |
| 기준 job으로 검색 | `SQL_ID=S001, SPACE_NM=PAYMENT와 비슷한 실패 SQL 찾아줘.` | 기준 row의 `EDIT_FR_SQL` 우선, 없으면 `FR_SQL`을 임베딩한다. 기준 row 자신은 기본적으로 결과에서 제외한다. |
| 다음 요청 안내 | 없음 | 채팅 기록을 사용하지 않으므로 Agent는 각 후보마다 완전한 요청 문장을 제공한다. 예: `SQL_ID=Q001, SPACE_NM=SALES 재시도 상태로 변경해줘.` `위 목록`, `그것들`처럼 이전 결과를 가리키는 요청은 사용하지 않는다. |
| 재시도 상태 변경 | `SQL_ID=Q001, SPACE_NM=SALES 재시도 준비해줘.` | 확인된 `FAIL`/`FAIL-*` 행의 상태는 보존하고 `RETRY_COUNT`만 0으로 초기화한다. 이 단계는 작업을 실행하지 않는다. |
| 실제 작업 실행 | `SQL_ID=Q001, SPACE_NM=SALES SQL Conversion 실행해줘.` | `STATUS_CONVERSION` 재시도 상태 변경이 완료된 뒤 별도 요청으로 Conversion executor를 실행한다. |

상태는 Milvus metadata가 아니라 검색 직후와 UPDATE 시점에 모두 Oracle `NEXT_SQL_INFO`에서 재확인한다. 따라서 동기화 이후 상태가 `PASS-*`로 바뀐 row는 검색 결과에 포함되거나 재시도 처리되지 않는다.

## 1.5 RAG Guide 관리

RAG Guide는 `NEXT_MIG_RAG_INFO`에 저장된다. 추가, 수정, 비활성화는 Oracle 원천 테이블을 변경하고, Milvus 검색에는 VectorDB 동기화 후 반영된다.

### RAG Guide 조합별 입력값

| 구분 | CATEGORY | RULE_TYPE | 필수 입력 | 입력하지 않는 값 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|---|---|
| SQL Conversion 공통 가이드 추가 | `SQL_CONVERSION` | `GENERAL` | `SOURCE_TABLES` | `GUIDANCE_TEXT`는 비움 | `SQL Conversion GENERAL RAG Guide 추가해줘. SOURCE_TABLES=CUSTOMER, ORDER` | 특정 테이블군에 공통 적용할 변환 규칙 메타데이터를 저장한다. |
| SQL Conversion 검색 가이드 추가 | `SQL_CONVERSION` | `SEARCH` | `SOURCE_TABLES`, `SOURCE_SQL`, `TARGET_SQL` | `GUIDANCE_TEXT`는 비움 | `SQL Conversion SEARCH RAG Guide 추가해줘. SOURCE_TABLES=CUSTOMER. SOURCE_SQL={원본 SQL}. TARGET_SQL={변환 SQL}` | 유사 SQL 검색에 사용할 변환 예시와 범위를 저장한다. |
| SQL Tuning 전체 가이드 추가 | `SQL_TUNING` | `GENERAL` | `GUIDANCE_TEXT` | `SOURCE_TABLES` | `SQL Tuning GENERAL RAG Guide 추가해줘. GUIDANCE_TEXT={모든 튜닝에 적용할 지침}` | 모든 Tuning에 공통 적용할 가이드를 저장한다. |
| SQL Tuning 검색 가이드 추가 | `SQL_TUNING` | `SEARCH` | `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL` | `SOURCE_TABLES` | `SQL Tuning SEARCH RAG Guide 추가해줘. GUIDANCE_TEXT={튜닝 의도}. SOURCE_SQL={튜닝 전 SQL}. TARGET_SQL={튜닝 후 SQL}` | 유사 SQL 검색으로 참고할 튜닝 예시와 적용 지침을 저장한다. |

### 요청 템플릿 예시 (다양한 입력 조합)

| 유형 | 예시 요청 | 핵심 입력값 |
|---|---|---|
| SQL Conversion 테이블 범위 가이드 추가 | `SQL Conversion RAG Guide 추가해줘. CATEGORY=SQL_CONVERSION, RULE_TYPE=GENERAL, SOURCE_TABLES=CUSTOMER, ORDER` | `SOURCE_TABLES` 필수, `GUIDANCE_TEXT` 비움 |
| SQL Conversion 예시 추가 | `SQL Conversion SEARCH RAG 추가해줘. SOURCE_TABLES=CUSTOMER, SOURCE_SQL=SELECT * FROM CUSTOMER WHERE CUST_ID = 1, TARGET_SQL=SELECT CUST_ID, NAME FROM CUSTOMER WHERE CUST_ID = 1` | `SOURCE_TABLES` + `SOURCE_SQL` + `TARGET_SQL` |
| SQL Conversion 특정 테이블 조회 | `CUSTOMER 테이블 관련 SQL Conversion RAG Guide 20건 조회해줘.` | `category=SQL_CONVERSION`, `keyword=CUSTOMER`, `limit=20` |
| SQL Conversion 보정된 예시 수정 | `RAG_ID=12의 SOURCE_TABLES를 CUSTOMER, ORDER로 수정해줘.` | `RAG_ID` + 수정 필드 |
| SQL Tuning 공통 가이드 추가 | `SQL Tuning GENERAL RAG Guide 추가해줘. GUIDANCE_TEXT=중복 조인은 서브쿼리로 분리하고 인덱스 사용 가능 컬럼을 우선 고려한다.` | `GUIDANCE_TEXT` 필수 |
| SQL Tuning 검색 예시 추가 | `SQL Tuning SEARCH RAG 추가해줘. GUIDANCE_TEXT=인덱스 힌트는 조건이 넓은 테이블에 우선 적용한다. SOURCE_SQL=..., TARGET_SQL=...` | `GUIDANCE_TEXT` + `SOURCE_SQL` + `TARGET_SQL` |
| RAG Guide 비활성화 | `RAG_ID=25 RAG Guide 비활성화해줘.` | `RAG_ID` |
| VectorDB 동기화 | `RAG Guide, Correct SQL, AS-IS SQL을 VectorDB에 동기화해줘.` | 없음 |

### RAG Guide 관리 작업

| 작업 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|
| 조회 | 없음 | `CATEGORY`, `RULE_TYPE`, 검색어, 사용 여부, 조회 건수, 원문 포함 여부 | `SQL Tuning GENERAL RAG Guide 전체 내용 조회해줘.` | 조건에 맞는 RAG Guide 목록과 본문을 조회한다. 검색어는 `SOURCE_TABLES`, `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL`에서 찾는다. |
| 수정 | `RAG_ID`, 수정할 필드 | 없음 | `RAG_ID={RAG_ID}의 SOURCE_TABLES를 CUSTOMER, ORDER로 수정해줘.` | 해당 row만 update한다. |
| 비활성화 | `RAG_ID` | 없음 | `RAG_ID={RAG_ID} RAG Guide 비활성화해줘.` | 물리 삭제 대신 `USE_YN='N'`으로 변경한다. |
| VectorDB 동기화 | 없음 | 없음 | `RAG Guide, Correct SQL, AS-IS SQL을 VectorDB에 동기화해줘.` | Oracle 원천 snapshot을 기준으로 `SM_RAG_RULES`, Correct SQL collection, `SM_ASIS_SQL`을 갱신한다. |

Correct SQL은 반드시 채팅으로 받은 하나의 단계 SQL만 저장한다. Correct TOBE 저장은 `STATUS_CONVERSION=FAIL-BIND`, `RETRY_COUNT=0`으로 바꿔 Bind 생성부터 재개한다. Correct BIND 저장은 실행 가능한 `BIND_SQL`과 비어 있지 않은 JSON 배열 `BIND_SET`을 함께 요구하고 `STATUS_CONVERSION=FAIL-TEST`, `RETRY_COUNT=0`으로 바꿔 Test 생성·실행부터 재개한다. Correct TEST 저장은 사람이 검증까지 완료했다는 뜻이므로 `STATUS_CONVERSION=PASS-CONVERSION`으로 종료한다. 각 저장 직후 `sync_correct_sql(sql_seq, correct_sql_kind)`으로 동일 단계 문서만 벡터 DB에 저장한다. executor는 `USER_EDITED`를 사용하지 않고 status stage만 사용한다.

### RAG Guide 입력 규칙

| 규칙 | 설명 |
|---|---|
| `SQL_CONVERSION`은 `SOURCE_TABLES`를 필수로 둔다. | SQL 변환은 대상 테이블 범위가 있어야 매핑/검색의 정확도가 높다. 기본적으로 `SOURCE_TABLES`를 입력해야 한다. |
| `SQL_CONVERSION`에서 `GUIDANCE_TEXT`는 보통 비운다. | 변환 규칙은 SQL 예시와 테이블 범위 중심으로 관리하는 편이 더 정확하고, `GUIDANCE_TEXT`는 SQL Tuning에 더 적합하다. |
| `SOURCE_TABLES`는 범위가 명확할 때만 넣는다. | 특정 테이블군에만 적용되는 규칙이나 예시를 저장할 때 사용한다. |
| `SQL_CONVERSION + SEARCH`는 `SOURCE_TABLES`, `SOURCE_SQL`, `TARGET_SQL`을 함께 입력한다. | 유사 SQL 검색 예시로 쓰이므로 원본 SQL, 변환 SQL, 범위가 한 세트여야 한다. |
| `SQL_TUNING`은 `GUIDANCE_TEXT`가 필수다. | 튜닝은 SQL 예시만으로 적용 의도가 모호하므로 튜닝 목적과 적용 기준을 함께 남긴다. |
| `SQL_TUNING`은 `SOURCE_TABLES`를 입력하지 않는다. | 튜닝 가이드는 테이블 매핑 범위가 아니라 튜닝 규칙과 SQL 예시 기준으로 적용한다. |
| 모든 Tuning에 필수 적용할 가이드는 `SQL_TUNING + GENERAL`로 입력한다. | `SOURCE_TABLES` 없이 `GUIDANCE_TEXT`만으로 전체 튜닝 공통 규칙을 저장한다. |
| `SEARCH`는 `SOURCE_SQL`과 `TARGET_SQL`을 둘 다 입력하거나 둘 다 비운다. | 한쪽만 저장하면 검색 결과의 의미가 깨진다. 신규 `SEARCH` 추가 시에는 둘 다 필수다. |
| 신규 추가 시 `RAG_ID`를 입력하지 않는다. | `RAG_ID`는 DB identity 컬럼으로 자동 생성된다. 수정/비활성화할 때만 사용한다. |

## 1.6 요청 대상 식별자

| 대상 | 필요한 식별자 | 예시 |
|---|---|---|
| DB Migration job | `MAP_ID` | `MAP_ID=101` |
| SQL Conversion job | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT` |
| SQL Tuning job | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT` |
| SQL Formatting job | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT` |
| RAG Guide 수정/비활성화 | `RAG_ID` | `RAG_ID=25` |
| RAG Guide 조회/추가 | `CATEGORY`, `RULE_TYPE`, 조합별 필수 입력 | `CATEGORY=SQL_CONVERSION, RULE_TYPE=SEARCH, SOURCE_TABLES=CUSTOMER` |
| VectorDB 동기화 | 없음 | `VectorDB 동기화 실행해줘.` |

## 1.7 운영 기준

| 기준 | 설명 |
|---|---|
| 조회성 요청은 read-only로 처리한다. | 상태, 로그, SQL 원문, 실패 원인 분석은 조회 전용 경로를 사용한다. |
| 실행성 요청은 DB를 변경할 수 있다. | `실행`, `진행`, `남은 작업 처리` 요청은 executor로 연결되어 상태, 로그, 결과 SQL 또는 target table이 변경될 수 있다. |
| Update Command의 SQL 저장은 사용자가 제공한 SQL만 반영한다. | LLM이 보정 SQL을 새로 작성해 저장하지 않는다. |
| 재시도 준비는 `RETRY_COUNT=0`이다. | 실행 대상은 `NULL`/`FAIL`/`FAIL-*`와 `RETRY_COUNT < 2`이며, 실패 단계는 상태를 보존한다. |
| 유사 SQL 기반 재시도는 명시적 확인이 필요하다. | 검색은 read-only다. 재시도 action은 UPDATE 시점에도 해당 상태가 `FAIL-*`인지 검사하므로 PASS row는 변경하지 않고 skip한다. |
| `USER_EDITED` 변경은 SQL 본문을 삭제하지 않는다. | `USER_EDITED='N'`으로 바꾸면 이후 실행에서 저장된 보정 SQL을 강제 재사용하지 않는다. |
| DB Migration `USE_YN` 변경은 SQL 본문과 상태를 삭제하지 않는다. | `USE_YN='N'`이면 DB Migration 실행 대상에서 제외된다. `NEXT_SQL_INFO`에는 `USE_YN` 컬럼이 없다. |
| RAG Guide 추가 후 VectorDB 동기화가 필요하다. | Oracle에는 즉시 저장되지만 Milvus 검색 결과에는 동기화 이후 반영된다. |
