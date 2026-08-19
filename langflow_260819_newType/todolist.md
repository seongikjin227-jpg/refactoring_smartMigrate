# Langflow newType TODO List

기준: 1 working day = 개발자 1명이 하루 동안 집중해서 처리하는 작업량.

범위 기준:

- 포함: Langflow 컴포넌트화, 라우팅, payload 변환, 기존 stand alone 로직 호출, DB/LLM 설정 연결, 실행 전 요약, 결과 요약, E2E 검증
- 제외: stand alone에 이미 있는 요구사항 정의, 프롬프트 설계, 도메인 알고리즘 설계, SQL 변환/튜닝/포맷팅 자체 로직 설계

## 0. 공통

| Task | 파일/대상 | 기간 |
|---|---|---:|
| 전체 newType 노드 연결도 최신화 | `01_architecture.md` | 0.5 WD |
| 1차 LLM Classifier를 실제 LLM 컴포넌트 설정과 맞춰 검증 | `01_llmClassifier.py` | 0.75 WD |
| 1차 Conditional Router multi-output 동작 검증 | `02_intentRouter.py` | 0.5 WD |
| Fast Status 3분기 라우터 동작 검증 | `04_fastStatusRouter.py` | 0.5 WD |
| Long Job Router 선행 작업 차단 및 실행 계획 요약 연결 검증 | `08_longJobRouter.py` | 0.75 WD |
| 실행 전 계획 요약 컴포넌트 검증 | `09_executionPlanSummary.py` | 0.75 WD |
| 공통 payload 입출력 필드 정리 | 전체 컴포넌트 | 0.75 WD |
| Pipeline 결과 표준 필드 정리 | `processed_jobs`, `completed_jobs`, `failed_jobs` | 0.5 WD |
| Final Summary가 모든 branch 결과를 Chat Output용 메시지로 변환하도록 검증 | `13_finalSummary.py` | 0.75 WD |
| Langflow 수동 테스트 케이스 작성 | 일반 대화, fast, long, prerequisite blocked, no runnable | 0.75 WD |

공통 소계: 6.5 WD

## 1. Fast Status

| Task | 파일/대상 | 기간 |
|---|---|---:|
| Dashboard branch를 기존 stand alone 상태 조회 로직에 연결 | `04_dashboard.py` | 1.0 WD |
| Status Change branch를 기존 상태/priority/USE_YN update 로직에 연결 | `04_statusChange.py` | 1.5 WD |
| 단건 실행 요청을 실제 실행이 아니라 다음 전체 실행 대상 조정으로 변환 | `04_statusChange.py` | 0.75 WD |
| Correct SQL Input branch를 기존 USER_EDITED/SQL 저장 로직에 연결 | `04_correctSqlInput.py` | 1.5 WD |
| Fast Status 각 branch 결과 메시지 표준화 | 04 계열 컴포넌트 | 0.5 WD |
| Fast Status E2E 검증 | 04 계열 컴포넌트 | 0.75 WD |

Fast Status 소계: 6.0 WD

## 2. DB Migration

| Task | 파일/대상 | 기간 |
|---|---|---:|
| `08 Long Job Router.MIG`에서 `09 Execution Plan Summary`를 거쳐 `10 MIG Pipeline`으로 연결 검증 | `08`, `09`, `10` | 0.75 WD |
| MIG Pipeline에서 기존 `run_migration_job` 호출 경로 안정화 | `10_migPipeline.py` | 1.5 WD |
| 전체 pending 반복 실행 결과를 표준 결과 필드로 변환 | `10_migPipeline.py` | 0.75 WD |
| 기존 `migration_command_tool.py` 동적 import 제거 또는 공용 모듈화 | `10_migPipeline.py`, legacy tool | 1.5 WD |
| WAITING/dependency/duplicate pending 중단 결과를 Final Summary와 맞춤 | `10_migPipeline.py`, `13_finalSummary.py` | 0.75 WD |
| DB Migration E2E 검증 | 08 -> 09 -> 10 -> 13 | 1.0 WD |

DB Migration 소계: 6.25 WD

## 3. SQL Conversion

현재 빈 컴포넌트:

- `12_sqlConversionPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Conversion 실행 전 계획 요약 연결 | `09_executionPlanSummary.py` | 0.5 WD |
| SQL Conversion Pipeline에서 기존 stand alone 실행 함수 호출 | `12_sqlConversionPipeline.py` | 2.0 WD |
| `06_getPendingJobs.py`의 SQL Conversion 후보를 stand alone 입력으로 변환 | `06_getPendingJobs.py`, `12_sqlConversionPipeline.py` | 0.75 WD |
| 전체 pending SQL Conversion 반복 실행 결과를 표준 결과 필드로 변환 | `12_sqlConversionPipeline.py` | 0.75 WD |
| SQL Conversion 실패/부분 성공 결과를 Final Summary와 맞춤 | `12_sqlConversionPipeline.py`, `13_finalSummary.py` | 0.5 WD |
| SQL Conversion E2E 검증 | 08 -> 09 -> 12 -> 13 | 1.0 WD |

SQL Conversion 소계: 5.5 WD

## 4. SQL Tuning

현재 빈 컴포넌트:

- `15_sqlTuningPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Tuning 실행 전 계획 요약 연결 | `09_executionPlanSummary.py` | 0.5 WD |
| SQL Tuning Pipeline에서 기존 stand alone 실행 함수 호출 | `15_sqlTuningPipeline.py` | 2.0 WD |
| `06_getPendingJobs.py`의 SQL Tuning 후보를 stand alone 입력으로 변환 | `06_getPendingJobs.py`, `15_sqlTuningPipeline.py` | 0.75 WD |
| 전체 pending SQL Tuning 반복 실행 결과를 표준 결과 필드로 변환 | `15_sqlTuningPipeline.py` | 0.75 WD |
| SQL Tuning 선행 작업 차단 정책을 라우터와 검증 | `08_longJobRouter.py` | 0.5 WD |
| SQL Tuning E2E 검증 | 08 -> 09 -> 15 -> 13 | 1.0 WD |

SQL Tuning 소계: 5.5 WD

## 5. SQL Formatting

현재 빈 컴포넌트:

- `17_sqlFormattingPipeline.py`

| Task | 파일/대상 | 기간 |
|---|---|---:|
| SQL Formatting 실행 전 계획 요약 연결 | `09_executionPlanSummary.py` | 0.5 WD |
| SQL Formatting Pipeline에서 기존 stand alone 실행 함수 호출 | `17_sqlFormattingPipeline.py` | 1.5 WD |
| `06_getPendingJobs.py`의 SQL Formatting 후보를 stand alone 입력으로 변환 | `06_getPendingJobs.py`, `17_sqlFormattingPipeline.py` | 0.75 WD |
| 전체 pending SQL Formatting 반복 실행 결과를 표준 결과 필드로 변환 | `17_sqlFormattingPipeline.py` | 0.5 WD |
| SQL Formatting 선행 작업 차단 정책을 라우터와 검증 | `08_longJobRouter.py` | 0.5 WD |
| SQL Formatting E2E 검증 | 08 -> 09 -> 17 -> 13 | 0.75 WD |

SQL Formatting 소계: 4.5 WD

## 6. 전체 추정

| 구분 | 기간 |
|---|---:|
| 공통 | 6.5 WD |
| Fast Status | 6.0 WD |
| DB Migration | 6.25 WD |
| SQL Conversion | 5.5 WD |
| SQL Tuning | 5.5 WD |
| SQL Formatting | 4.5 WD |
| 총합 | 34.25 WD |

stand alone 로직을 그대로 재사용한다는 전제의 추정치다. 새 도메인 로직 개발이나 프롬프트 재설계가 포함되면 별도 일정으로 분리해야 한다.
