# Langflow newType TODO List

기준: 파일 하나를 기본 개발 task 단위로 본다.  
테스트는 별도 task로 분리한다.  
1 WD = 개발자 1 working day.

## 0. 공통 / Flow

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | newType 전체 노드 연결 문서 최신화 | `01_architecture.md` | 0.5 WD |
| 개발 | 사용자 입력을 `GENERAL_CHAT`, `MANAGEMENT`, `JOB_EXECUTION`으로 분류하는 Request Classifier LLM 프롬프트 정리 | `01_requestClassifierPrompt.md` | 1.0 WD |
| 테스트 | Request Classifier LLM 샘플 입력별 JSON route 검증 | `01_requestClassifierPrompt.md` | 0.5 WD |
| 개발 | 1차 route를 multi-output으로 분기하는 Conditional Router 정리 | `02_intentRouter.py` | 0.5 WD |
| 테스트 | 1차 Router에서 선택되지 않은 branch가 실행되지 않는지 검증 | `02_intentRouter.py` | 0.5 WD |
| 개발 | 일반 대화 branch LLM 응답 프롬프트 정리 | `03_llmResponsePrompt.md` | 0.25 WD |
| 개발 | 최종 Chat Output 메시지 생성 컴포넌트 정리 | `13_finalSummary.py` | 0.75 WD |
| 테스트 | Final Summary가 성공/실패/선행작업차단/대상없음 결과를 모두 메시지로 변환하는지 검증 | `13_finalSummary.py` | 0.5 WD |

공통 소계: 4.5 WD

## 1. Management

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | Management 요청을 Dashboard, Status Change, Correct SQL Input으로 분기하는 Router 정리 | `04_managementRouter.py` | 1.0 WD |
| 테스트 | Management Router 샘플 입력별 route 및 Exception Message 검증 | `04_managementRouter.py` | 0.5 WD |
| 개발 | Dashboard 조회 branch를 Langflow 컴포넌트 형태로 리팩토링 | `04_dashboard.py` | 1.0 WD |
| 테스트 | Dashboard branch DB 조회 결과 payload/message 검증 | `04_dashboard.py` | 0.5 WD |
| 개발 | Status/priority/USE_YN 변경 branch를 Langflow 컴포넌트 형태로 리팩토링 | `04_statusChange.py` | 1.5 WD |
| 테스트 | priority/status/USE_YN 변경 요청 payload 검증 | `04_statusChange.py` | 0.75 WD |
| 개발 | Correct SQL 입력 branch를 Langflow 컴포넌트 형태로 리팩토링 | `04_correctSqlInput.py` | 1.5 WD |
| 테스트 | Correct SQL 입력 시 USER_EDITED 및 SQL 저장 payload 검증 | `04_correctSqlInput.py` | 0.75 WD |

Management 소계: 7.5 WD

## 2. Job Target Execution Routing

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | 전체 pending 작업 후보를 조회해서 domain별 context로 만드는 컴포넌트 정리 | `06_getPendingJobs.py` | 1.25 WD |
| 테스트 | MIG/SQL Conversion/Tuning/Formatting 후보 조회 결과 구조 검증 | `06_getPendingJobs.py` | 0.75 WD |
| 개발 | 작업 실행 요청을 domain과 `all_pending`/`targeted` 실행 모드로 분기하는 Router 정리 | `08_jobExecutionRouter.py` | 1.25 WD |
| 테스트 | `map_id`/`sql_id`/`space_nm` 단건 및 복수건 지정 대상 추출 검증 | `08_jobExecutionRouter.py` | 0.75 WD |
| 테스트 | 각 domain route가 `09_executionPlanSummary`로 연결되는지 검증 | `08_jobExecutionRouter.py` | 0.5 WD |
| 개발 | 실행 전 작업 수, 실행 모드, job list를 Chat Output으로 안내하는 컴포넌트 정리 | `09_executionPlanSummary.py` | 1.0 WD |
| 테스트 | Execution Plan Summary의 `Notice`와 `Payload` 출력이 각각 Chat Output/Pipeline으로 연결 가능한지 검증 | `09_executionPlanSummary.py` | 0.5 WD |

Job Target Execution Routing 소계: 6.0 WD

## 3. DB Migration Pipeline

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | DB Migration 작업 대상 실행 Pipeline을 POC 테스트 결과 반환 형태로 정리 | `10_migPipeline.py` | 1.0 WD |
| 테스트 | `map_id=101` 지정 실행 시 `FAIL-TEST`와 로그가 반환되는지 검증 | `10_migPipeline.py` | 0.5 WD |
| 테스트 | `08 -> 09 -> 10 -> 13` DB Migration 대상 실행 흐름 검증 | `10_migPipeline.py` | 1.0 WD |

DB Migration 소계: 2.5 WD

## 4. SQL Conversion Pipeline

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | SQL Conversion 작업 대상 실행 Pipeline을 POC 테스트 결과 반환 형태로 정리 | `12_sqlConversionPipeline.py` | 1.0 WD |
| 개발 | `selected_jobs`/`planned_jobs`를 Pipeline 입력 형식으로 변환 | `12_sqlConversionPipeline.py` | 0.5 WD |
| 테스트 | `08 -> 09 -> 12 -> 13` SQL Conversion 대상 실행 흐름 검증 | `12_sqlConversionPipeline.py` | 1.0 WD |
| 테스트 | SQL Conversion 실패/부분 성공 결과 payload 검증 | `12_sqlConversionPipeline.py` | 0.5 WD |

SQL Conversion 소계: 3.0 WD

## 5. SQL Tuning Pipeline

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | SQL Tuning 작업 대상 실행 Pipeline을 POC 테스트 결과 반환 형태로 정리 | `15_sqlTuningPipeline.py` | 1.0 WD |
| 개발 | `selected_jobs`/`planned_jobs`를 Pipeline 입력 형식으로 변환 | `15_sqlTuningPipeline.py` | 0.5 WD |
| 테스트 | `08 -> 09 -> 15 -> 13` SQL Tuning 대상 실행 흐름 검증 | `15_sqlTuningPipeline.py` | 1.0 WD |
| 테스트 | SQL Tuning 실패/부분 성공 결과 payload 검증 | `15_sqlTuningPipeline.py` | 0.5 WD |

SQL Tuning 소계: 3.0 WD

## 6. SQL Formatting Pipeline

| 구분 | Task | 파일 | 기간 |
|---|---|---|---:|
| 개발 | SQL Formatting 작업 대상 실행 Pipeline을 POC 테스트 결과 반환 형태로 정리 | `17_sqlFormattingPipeline.py` | 1.0 WD |
| 개발 | `selected_jobs`/`planned_jobs`를 Pipeline 입력 형식으로 변환 | `17_sqlFormattingPipeline.py` | 0.5 WD |
| 테스트 | `08 -> 09 -> 17 -> 13` SQL Formatting 대상 실행 흐름 검증 | `17_sqlFormattingPipeline.py` | 0.75 WD |
| 테스트 | SQL Formatting 실패/부분 성공 결과 payload 검증 | `17_sqlFormattingPipeline.py` | 0.5 WD |

SQL Formatting 소계: 2.75 WD

## 7. 전체 추정

| 구분 | 기간 |
|---|---:|
| 공통 / Flow | 4.5 WD |
| Management | 7.5 WD |
| Job Target Execution Routing | 6.0 WD |
| DB Migration Pipeline | 2.5 WD |
| SQL Conversion Pipeline | 3.0 WD |
| SQL Tuning Pipeline | 3.0 WD |
| SQL Formatting Pipeline | 2.75 WD |
| 총합 | 29.25 WD |

이 일정은 기존 구현 로직을 Langflow 컴포넌트 형태로 리팩토링한다는 전제다. 새 알고리즘 개발이나 프롬프트 재설계는 포함하지 않는다.
