# Langflow newType TODO List

기준: 1 working day = 개발자 1명이 하루 동안 집중해서 처리하는 작업량.  
기간은 PoC 기준 추정치이며, 실제 DB/LLM 환경 이슈와 레거시 코드 품질에 따라 조정될 수 있다.

## 0. 공통

| Task | 파일/대상 | 기간 |
|---|---|---:|
| 전체 Langflow PoC 플로우 확정 및 연결 검증 | `01_architecture.md` | 1.0 WD |
| 1차 LLM Classifier 프롬프트/출력 스키마 검증 | `01_llmClassifier.py` | 1.0 WD |
| 1차 Conditional Router multi-output 검증 | `02_intentRouter.py` | 0.5 WD |
| Fast Status 3분기 Router 검증 | `04_fastStatusRouter.py` | 0.5 WD |
| Dashboard 빈 컴포넌트 유지 및 실제 조회 설계 | `04_dashboard.py` | 0.5 WD |
| Status Change 빈 컴포넌트 유지 및 DB update 설계 | `04_statusChange.py` | 1.0 WD |
| Correct SQL Input 빈 컴포넌트 유지 및 저장 정책 설계 | `04_correctSqlInput.py` | 1.0 WD |
| Long Task Notice 문구/상태 필드 정리 | `05_longTaskNotice.py` | 0.25 WD |
| 전체 pending 후보 조회 컨텍스트 검증 | `06_getPendingJobs.py` | 1.0 WD |
| Long Job Router 선행 작업 차단 정책 검증 | `08_longJobRouter.py` | 1.0 WD |
| 공통 Payload 계약 정리 | 전체 컴포넌트 | 1.0 WD |
| 공통 실행 결과 계약 정리 | `processed_jobs`, `completed_jobs`, `failed_jobs` | 0.5 WD |
| Final Summary 메시지 포맷 정리 | `13_finalSummary.py` | 0.75 WD |
| Langflow 수동 테스트 시나리오 작성 | 일반/fast/long/blocked/fail | 1.0 WD |

공통 소계: 10.0 WD

## 1. DB Migration

| Task | 파일/대상 | 기간 |
|---|---|---:|
| DB Migration Agent 프롬프트 정리 | `09_dbMigrationAgent.py` | 0.5 WD |
| 전체 pending MIG 실행 payload 검증 | `09_dbMigrationAgent.py` | 0.5 WD |
| MIG Pipeline 전체 실행 반복 검증 | `10_migPipeline.py` | 1.5 WD |
| 기존 `run_migration_job` 연동 안정화 | `migration_command_tool.py` 연동 | 2.0 WD |
| 레거시 동적 import 제거 리팩토링 설계 | `migration_command_tool.py` | 1.5 WD |
| `NEXT_MIG_INFO`, `NEXT_MIG_LOG` 상태/로그 검증 | DB | 1.0 WD |
| dependency/WAITING/duplicate pending 중단 정책 검증 | `10_migPipeline.py` | 1.0 WD |
| USER_EDITED SQL 처리 정책 검증 | MIG 실행 로직 | 1.0 WD |
| MIG 결과 요약 품질 개선 | `13_finalSummary.py` | 0.5 WD |

DB Migration 소계: 9.5 WD

## 2. SQL Conversion

현재 빈 컴포넌트:

- `11_sqlConversionAgent.py`
- `12_sqlConversionPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Conversion Agent 프롬프트 설계 | `11_sqlConversionAgent.py` | 1.0 WD |
| 전체 pending SQL Conversion payload 계약 확정 | `11_sqlConversionAgent.py` | 0.5 WD |
| SQL Conversion Pipeline 실제 구현 | `12_sqlConversionPipeline.py` | 3.0 WD |
| 기존 SQL Conversion Tool 분석 | 레거시 SQL Conversion 코드 | 1.5 WD |
| `run_sql_conversion_job` 로직 분리/재사용화 | 레거시 Tool | 2.0 WD |
| SQL Conversion pending 조건 확정 | `06_getPendingJobs.py`, DB | 1.0 WD |
| SQL Conversion 프롬프트 입력값 노출 | 변환/바인드/테스트 SQL prompt | 1.0 WD |
| SQL Conversion 결과 저장/로그 검증 | DB | 1.0 WD |
| SQL Conversion 최종 요약 연동 | `13_finalSummary.py` | 0.5 WD |

SQL Conversion 소계: 11.5 WD

## 3. SQL Tuning

현재 빈 컴포넌트:

- `14_sqlTuningAgent.py`
- `15_sqlTuningPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Tuning 요구사항 정의 | 입력/출력/성공 기준 | 1.0 WD |
| SQL Tuning Agent 프롬프트 설계 | `14_sqlTuningAgent.py` | 1.0 WD |
| 전체 pending SQL Tuning payload 계약 확정 | `14_sqlTuningAgent.py` | 0.5 WD |
| SQL Tuning Pipeline 신규 구현 | `15_sqlTuningPipeline.py` | 4.0 WD |
| SQL Tuning pending 조건 정의 | `06_getPendingJobs.py`, DB | 1.0 WD |
| 실행 계획/성능 정보 입력 구조 정의 | DB/LLM 입력 | 1.5 WD |
| 튜닝 결과 저장 구조 정의 | 튜닝 SQL, 인덱스 추천, 리스크 | 1.0 WD |
| 튜닝 실패/보류 정책 정의 | 정보 부족/불필요/LLM 실패 | 0.75 WD |
| SQL Tuning 최종 요약 연동 | `13_finalSummary.py` | 0.5 WD |

SQL Tuning 소계: 11.25 WD

## 4. SQL Formatting

현재 빈 컴포넌트:

- `16_sqlFormattingAgent.py`
- `17_sqlFormattingPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Formatting Agent 프롬프트 설계 | `16_sqlFormattingAgent.py` | 0.75 WD |
| 전체 pending SQL Formatting payload 계약 확정 | `16_sqlFormattingAgent.py` | 0.5 WD |
| SQL Formatting Pipeline 신규 구현 | `17_sqlFormattingPipeline.py` | 2.0 WD |
| SQL Formatting pending 조건 정의 | `06_getPendingJobs.py`, DB | 0.75 WD |
| SQL Formatting 스타일 가이드 작성 | 키워드/들여쓰기/alias 규칙 | 1.0 WD |
| Formatter 선택 및 적용 | LLM/sqlparse/혼합 방식 | 1.0 WD |
| SQL 의미 변경 방지 검증 | 포맷 전후 비교 | 1.0 WD |
| SQL Formatting 결과 저장/요약 구현 | DB, `13_finalSummary.py` | 0.75 WD |

SQL Formatting 소계: 7.75 WD

## 5. Fast Status 상세 구현

현재 빈/PoC 컴포넌트:

- `04_dashboard.py`
- `04_statusChange.py`
- `04_correctSqlInput.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| Dashboard 실제 DB 조회 구현 | `04_dashboard.py` | 1.5 WD |
| Status Change 실제 DB update 구현 | `04_statusChange.py` | 2.0 WD |
| 단건 작업 대상 지정 정책 구현 | `04_statusChange.py` | 1.0 WD |
| Correct SQL 저장 구현 | `04_correctSqlInput.py` | 2.0 WD |
| USER_EDITED='Y' 처리 및 대상 컬럼 확정 | DB | 1.0 WD |
| Fast Status 결과 메시지 표준화 | 04 계열 컴포넌트 | 0.5 WD |

Fast Status 소계: 8.0 WD

## 6. 우선순위 작업 순서

| 순서 | Task | 기간 |
|---:|---|---:|
| 1 | 공통 Payload/결과 계약 확정 | 1.0 WD |
| 2 | `01_llmClassifier.py`, `02_intentRouter.py` 분기 검증 | 1.5 WD |
| 3 | Fast Status 3분기 Router와 빈 컴포넌트 연결 검증 | 1.0 WD |
| 4 | `06_getPendingJobs.py` 전체 pending 후보 조회 검증 | 1.0 WD |
| 5 | `08_longJobRouter.py` 선행 작업 차단 정책 검증 | 1.0 WD |
| 6 | DB Migration 전체 실행 E2E 검증 | 3.0 WD |
| 7 | SQL Conversion Stub 제거 및 실제 Pipeline 연결 | 5.0 WD |
| 8 | Fast Status 실제 DB 조회/update 구현 | 5.0 WD |
| 9 | SQL Tuning 신규 개발 | 8.0 WD |
| 10 | SQL Formatting 신규 개발 | 5.0 WD |

## 7. 전체 추정

| 구분 | 기간 |
|---|---:|
| 공통 | 10.0 WD |
| DB Migration | 9.5 WD |
| SQL Conversion | 11.5 WD |
| SQL Tuning | 11.25 WD |
| SQL Formatting | 7.75 WD |
| Fast Status 상세 구현 | 8.0 WD |
| 총합 | 58.0 WD |

병렬 개발을 전제로 하면 2~3명이 나눠서 약 4~6주 범위로 줄일 수 있다. 단, DB 스키마/레거시 Tool 리팩토링 범위가 커지면 일정은 늘어난다.
