# Chapter 1. User Guide

이 문서는 SmartMigrate에서 사용할 수 있는 주요 기능, 요청 형식, 필수 입력값, 처리 결과를 정리한 사용자 안내서다.

## 1.1 기능 요약

| 구분 | 기능 | 주요 대상 | 처리 결과 |
|---|---|---|---|
| 조회 | 전체 현황, 진행 현황, 실패 원인, SQL 원문 조회 | 전체 작업, `MAP_ID`, `SQL_ID` + `SPACE_NM` | DB 상태와 로그를 조회하고 근거 기반 답변을 반환한다. |
| 실행 | DB Migration, SQL Conversion, SQL Tuning, SQL Formatting, 전체 workflow 실행 | 전체 잔여 작업 또는 특정 작업 | 실행 가능한 job을 DB에서 다시 조회한 뒤 도메인별 executor를 실행한다. |
| 수정 | 실패 job 상태 초기화, priority 변경, 담당자 보정 SQL 저장 | `MAP_ID` 또는 `SQL_ID` + `SPACE_NM` | 상태값, retry count, priority 또는 SQL CLOB를 DB에 반영한다. |
| RAG Guide 관리 | Conversion/Tuning 가이드 조회, 추가, 수정, 비활성화 | `NEXT_MIG_RAG_INFO`, `RAG_ID` | RAG rule 원천 테이블을 관리한다. 신규/수정분은 VectorDB 동기화 후 검색에 반영된다. |
| VectorDB 관리 | RAG Guide와 Correct SQL 동기화 | Oracle 원천 테이블, Milvus collection | Oracle snapshot 기준으로 Milvus 검색 인덱스를 갱신한다. |

## 1.2 조회 및 분석 요청

| 기능 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|
| 전체 대시보드 조회 | 없음 | 없음 | `전체 대시보드 보여줘.` | DB Migration, SQL Conversion, SQL Tuning, SQL Formatting의 상태별 건수를 보여준다. |
| 현재 진행 현황 조회 | 없음 | 최근 로그 건수 | `지금 실행 중인 작업과 최근 로그 20건 보여줘.` | running job과 최근 workflow 로그를 보여준다. |
| DB Migration 상태 조회 | `MAP_ID` | 조회할 SQL 컬럼, 로그 범위 | `MAP_ID={MAP_ID} 상태와 최근 로그 보여줘.` | `NEXT_MIG_INFO` 기준 상태, 대상 테이블, 최근 로그를 조회한다. |
| DB Migration SQL 원문 조회 | `MAP_ID`, SQL 컬럼명 | 없음 | `MAP_ID={MAP_ID}의 MIG_SQL과 VERIFY_SQL 원문 보여줘.` | `MIG_SQL`, `VERIFY_SQL` 등 요청한 CLOB 원문을 반환한다. |
| SQL job 상태 조회 | `SQL_ID`, `SPACE_NM` | 도메인 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} 상태 알려줘.` | `NEXT_SQL_INFO` 기준 Conversion/Tuning/Formatting 상태와 로그를 조회한다. |
| SQL job SQL 원문 조회 | `SQL_ID`, `SPACE_NM`, SQL 컬럼명 | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM}의 TO_SQL, BIND_SQL, TEST_SQL 보여줘.` | 요청한 SQL 컬럼의 CLOB 원문을 반환한다. |
| 실패 원인 분석 | 없음 | 도메인, `MAP_ID`, `SQL_ID`, `SPACE_NM`, 건수 | `최근 SQL Conversion 실패 10건 원인 요약해줘.` | 상태, 로그, 저장 SQL을 근거로 실패 원인과 확인 포인트를 요약한다. |
| RAG Guide 조회 | 없음 | `CATEGORY`, `RULE_TYPE`, `KEYWORD`, `USE_YN`, `LIMIT`, `FULL_TEXT` | `SQL Conversion RAG Guide 중 KEYWORD=CUSTOMER인 항목 20건 조회해줘.` | 조건에 맞는 `NEXT_MIG_RAG_INFO` row를 조회한다. |

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
| DB Migration 상태 초기화 | `NEXT_MIG_INFO` | `MAP_ID` | `PRIORITY=1` 또는 `PRIORITY=5` | `MAP_ID={MAP_ID} 다시 실행할 수 있게 상태 초기화하고 PRIORITY=1로 설정해줘.` | 상태와 retry count를 초기화하고 priority를 반영한다. SQL 본문은 유지한다. |
| SQL job 상태 초기화 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM`, 작업 종류 | `PRIORITY=1` 또는 `PRIORITY=5` | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM} SQL Conversion 상태 초기화해줘.` | 해당 도메인 상태와 retry count를 초기화한다. |
| DB Migration 보정 SQL 저장 | `NEXT_MIG_INFO` | `MAP_ID`, SQL 컬럼명, SQL 본문 | 없음 | `MAP_ID={MAP_ID}의 MIG_SQL을 아래 SQL로 저장해줘. SQL=...` | 사용자가 제공한 SQL을 지정 컬럼에 저장하고 `USER_EDITED='Y'`로 표시한다. |
| SQL job 보정 SQL 저장 | `NEXT_SQL_INFO` | `SQL_ID`, `SPACE_NM`, SQL 컬럼명, SQL 본문 | 없음 | `SQL_ID={SQL_ID}, SPACE_NM={SPACE_NM}의 TO_SQL을 아래 SQL로 저장해줘. SQL=...` | 사용자가 제공한 SQL을 지정 컬럼에 저장하고 `USER_EDITED='Y'`로 표시한다. |

허용 SQL 컬럼은 DB Migration의 경우 `MIG_SQL`, `VERIFY_SQL`이고, SQL job의 경우 `TO_SQL`, `BIND_SQL`, `TEST_SQL`, `TUNED_TO_SQL`, `FORMATTED_SQL`이다.

## 1.5 RAG Guide 관리

RAG Guide는 `NEXT_MIG_RAG_INFO`에 저장된다. 추가, 수정, 비활성화는 Oracle 원천 테이블을 변경하고, Milvus 검색에는 VectorDB 동기화 후 반영된다.

### RAG Guide 조합별 입력값

| 구분 | CATEGORY | RULE_TYPE | 필수 입력 | 입력하지 않는 값 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|---|---|
| SQL Conversion 공통 가이드 추가 | `SQL_CONVERSION` | `GENERAL` | `SOURCE_TABLES`, `GUIDANCE_TEXT` | 없음 | `SQL Conversion GENERAL RAG Guide 추가해줘. SOURCE_TABLES={테이블명}. GUIDANCE_TEXT={변환 공통 규칙}` | 지정 테이블을 사용하는 Conversion에 적용할 공통 변환 가이드를 저장한다. |
| SQL Conversion 검색 가이드 추가 | `SQL_CONVERSION` | `SEARCH` | `SOURCE_TABLES`, `SOURCE_SQL`, `TARGET_SQL` | 없음 | `SQL Conversion SEARCH RAG Guide 추가해줘. SOURCE_TABLES={테이블명}. SOURCE_SQL={원본 SQL}. TARGET_SQL={변환 SQL}` | 지정 테이블을 사용하는 Conversion에 적용할 유사 SQL 예시를 저장한다. |
| SQL Tuning 전체 가이드 추가 | `SQL_TUNING` | `GENERAL` | `GUIDANCE_TEXT` | `SOURCE_TABLES` | `SQL Tuning GENERAL RAG Guide 추가해줘. GUIDANCE_TEXT={모든 튜닝에 적용할 지침}` | 모든 Tuning에 공통 적용할 가이드를 저장한다. |
| SQL Tuning 검색 가이드 추가 | `SQL_TUNING` | `SEARCH` | `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL` | `SOURCE_TABLES` | `SQL Tuning SEARCH RAG Guide 추가해줘. GUIDANCE_TEXT={튜닝 의도}. SOURCE_SQL={튜닝 전 SQL}. TARGET_SQL={튜닝 후 SQL}` | 유사 SQL 검색으로 참고할 튜닝 예시와 적용 지침을 저장한다. |

### RAG Guide 관리 작업

| 작업 | 필수 입력 | 선택 입력 | 요청 템플릿 | 처리 결과 |
|---|---|---|---|---|
| 조회 | 없음 | `CATEGORY`, `RULE_TYPE`, `KEYWORD`, `USE_YN`, `LIMIT`, `FULL_TEXT` | `CATEGORY=SQL_TUNING, RULE_TYPE=GENERAL RAG Guide 전체 내용 조회해줘.` | 조건에 맞는 RAG Guide 목록과 본문을 조회한다. |
| 수정 | `RAG_ID`, 수정할 필드 | 없음 | `RAG_ID={RAG_ID}의 GUIDANCE_TEXT를 아래 내용으로 수정해줘. GUIDANCE_TEXT=...` | 해당 row만 update한다. |
| 비활성화 | `RAG_ID` | 없음 | `RAG_ID={RAG_ID} RAG Guide 비활성화해줘.` | 물리 삭제 대신 `USE_YN='N'`으로 변경한다. |
| VectorDB 동기화 | 없음 | 없음 | `RAG Guide와 Correct SQL을 VectorDB에 동기화해줘.` | Oracle 원천 snapshot을 기준으로 Milvus collection을 갱신한다. |

### RAG Guide 입력 규칙

| 규칙 | 설명 |
|---|---|
| `SQL_CONVERSION`은 `SOURCE_TABLES`가 필수다. | 어떤 테이블이 쓰이는 Conversion에 해당 변환 룰을 적용할지 결정하는 기준이다. |
| `SQL_CONVERSION + SEARCH`는 `SOURCE_SQL`과 `TARGET_SQL`을 모두 입력한다. | 유사 SQL 검색 예시로 쓰이므로 원본 SQL과 변환 SQL이 한 쌍이어야 한다. |
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
| Correct SQL 저장은 사용자가 제공한 SQL만 반영한다. | LLM이 보정 SQL을 새로 작성해 저장하지 않는다. |
| 상태 초기화는 SQL 본문을 삭제하지 않는다. | status, retry count, priority만 변경하고 기존 SQL CLOB는 유지한다. |
| RAG Guide 추가 후 VectorDB 동기화가 필요하다. | Oracle에는 즉시 저장되지만 Milvus 검색 결과에는 동기화 이후 반영된다. |
