# Langflow newType TODO List

이 문서는 `langflow_260819_newType` 기준으로 앞으로 개발/리팩토링해야 할 작업을 개발 티켓 단위로 나눈 목록이다.

## 0. 공통

- [ ] 전체 Langflow PoC 플로우 확정
  - 대상 파일: `01_architecture.md`
  - 작업 내용: Chat Input -> LLM Classifier -> Conditional Router -> Fast/General/Long Job -> Pending Job Context -> Long Job LLM Router -> 각 Pipeline -> Final Summary 흐름을 최종 확정한다.
  - 완료 기준: Langflow 화면에서 각 노드 연결 방향과 출력 포트가 문서와 동일하다.

- [ ] `01_llmClassifier.py` 컴포넌트 개발 완료
  - 작업 내용: 사용자 입력을 실제 LLM으로 분류하고, `GENERAL_CHAT`, `FAST_STATUS`, `LONG_RUNNING_JOB` 중 하나로 안정적으로 반환한다.
  - 완료 기준: LLM API 정보 입력, 프롬프트 수정, JSON 파싱 실패 시 fallback, route/reason/confidence 출력이 모두 동작한다.

- [ ] `02_intentRouter.py` Conditional Router 검증
  - 작업 내용: Langflow의 `ConditionalRouterComponent` 패턴에 맞게 multi-output 분기만 수행하도록 검증한다.
  - 완료 기준: 선택되지 않은 브랜치 컴포넌트가 실행되지 않고, `active: false` 중간 응답도 사용자에게 노출되지 않는다.

- [ ] 공통 Payload 계약 정의
  - 작업 내용: 모든 컴포넌트가 주고받는 공통 필드를 정리한다.
  - 권장 필드: `route`, `job_type`, `run_all_pending`, `processed_jobs`, `completed_jobs`, `failed_jobs`, `answer_text`, `status`, `error`, `metadata`.
  - 완료 기준: 각 컴포넌트의 input/output 이름과 타입이 문서화되어 있고, 임의 문자열이 아닌 구조화 데이터로 연결된다.

- [ ] 공통 전체 실행 계약 정의
  - 작업 내용: Long Job Pipeline 계열 컴포넌트가 전체 pending 작업 실행 포맷을 통일한다.
  - 예시: `{"action":"run_migration_job","run_all_pending":true}`.
  - 완료 기준: DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 모두 단건 실행 없이 전체 pending 실행만 지원한다.

- [ ] 공통 설정 입력 방식 정리
  - 작업 내용: LLM 설정, DB 설정, API Key, Prompt 입력을 컴포넌트마다 중복 구현하지 않도록 패턴을 통일한다.
  - 완료 기준: Secret 입력은 `SecretStrInput`, 선택값은 `DropdownInput`, 긴 프롬프트는 `MultilineInput`으로 일관되게 구성된다.

- [ ] 공통 에러/상태 응답 포맷 정의
  - 작업 내용: 실패, 대기, 성공, 부분 성공을 동일한 형식으로 반환한다.
  - 완료 기준: 최종 응답 컴포넌트가 모든 Pipeline 결과를 같은 방식으로 요약할 수 있다.

- [ ] `13_finalSummary.py` 응답 계약 정리
  - 작업 내용: General/Fast/Long Job/Pipeline 결과를 사용자용 최종 메시지로 변환한다.
  - 완료 기준: 중간 컴포넌트 출력이 아니라 최종 요약만 Chat Output으로 전달된다.

- [ ] 공통 Pending Job 조회 구조 정리
  - 대상 파일: `06_getPendingJobs.py`
  - 작업 내용: MIG, SQL Conversion, Tuning, Formatting 대기 작업을 같은 방식으로 조회할 수 있게 확장 구조를 만든다.
  - 완료 기준: 각 도메인별 pending 조건과 우선순위 계산 방식이 분리되어 있다.

- [ ] Long Job LLM Router 개발/검증
  - 대상 파일: `08_longJobRouter.py`
  - 작업 내용: 사용자 요청과 pending job 조회 결과를 함께 보고 MIG, SQL Conversion, SQL Tuning, SQL Formatting, No Runnable Job, Need More Info로 분기한다.
  - 완료 기준: 별도 `07_prioritySelector.py` 없이 `06 Get Pending Jobs` 결과가 바로 `08 Long Job LLM Router`로 들어간다.

- [ ] Langflow 수동 테스트 시나리오 작성
  - 작업 내용: 일반 대화, 빠른 상태 조회, priority/status 조정, 전체 작업 실행, 대기 작업 없음, 실패 케이스를 테스트한다.
  - 완료 기준: 입력 문장별 기대 route, 실행 컴포넌트, 최종 응답이 표로 정리되어 있다.

- [ ] 불필요한 Stub/임시 컴포넌트 정리 기준 수립
  - 작업 내용: 실제 Pipeline이 구현된 뒤 `Stub` 컴포넌트를 제거할지, 테스트용으로 유지할지 결정한다.
  - 완료 기준: PoC 전용 파일과 운영 전환 대상 파일이 구분되어 있다.

## 1. DB Migration

- [ ] `09_dbMigrationAgent.py` 프롬프트 정리
  - 작업 내용: `03_agent_guides.md`의 DB Migration Agent Prompt 중 전체 `run_migration_job` 실행에 필요한 내용만 유지한다.
  - 완료 기준: Agent가 `{"action":"run_migration_job","run_all_pending":true}`를 payload 내부에 생성한다.

- [ ] `10_migPipeline.py` 전체 작업 실행 검증
  - 작업 내용: payload 내부의 `{"action":"run_migration_job","run_all_pending":true}` 입력 시 pending MIG 작업을 반복 조회하고 실행한다.
  - 완료 기준: 대기 작업이 없어질 때까지 실행하거나, dependency/WAITING/실패 조건에서 정확히 중단한다.

- [ ] 기존 `migration_command_tool.py` 리팩토링
  - 대상 파일: `langflow/components/unused/migration_command_tool.py`
  - 작업 내용: `run_migration_job`에 필요한 내부 함수와 상태 업데이트 로직을 재사용 가능한 모듈로 분리한다.
  - 완료 기준: newType Pipeline이 `unused` 파일을 동적 import하지 않아도 된다.

- [ ] MIG 실행 로그/상태 업데이트 검증
  - 작업 내용: `NEXT_MIG_LOG`, `NEXT_MIG_INFO` 상태 업데이트, 에러 메시지 저장, 재실행 조건을 확인한다.
  - 완료 기준: 성공/실패/대기 상태가 DB에 일관되게 남는다.

- [ ] USER_EDITED 소스 처리 정책 검증
  - 작업 내용: 사용자가 수정한 SQL이 있을 때 MIG가 어떤 SQL을 기준으로 실행할지 확인한다.
  - 완료 기준: 기존 도구의 `USER_EDITED` 처리와 newType Pipeline 결과가 동일하다.

- [ ] MIG Agent Prompt 입력값 정리
  - 작업 내용: `mig_sql_prompt`, `verify_sql_prompt` 등 실행에 필요한 Prompt 입력을 컴포넌트 input으로 노출한다.
  - 완료 기준: Langflow에서 Prompt를 직접 교체해도 Pipeline이 정상 실행된다.

- [ ] MIG 최종 요약 포맷 구현
  - 작업 내용: 처리한 job list, 성공/실패 수, 실패 원인을 `13_finalSummary.py`에서 보여줄 수 있게 결과를 반환한다.
  - 완료 기준: 사용자는 중간 JSON이 아니라 실행 결과 요약만 받는다.

## 2. SQL Conversion

- [ ] SQL Conversion Agent 컴포넌트 신규 작성
  - 작업 내용: 사용자 요청과 pending context를 받아 SQL Conversion 전체 실행 payload를 생성한다.
  - 권장 파일: `11_sqlConversionAgent.py`
  - 완료 기준: `run_sql_conversion_job` action과 필수 파라미터가 생성된다.

- [ ] `11_sqlPipelineStub.py`를 실제 Pipeline으로 교체
  - 작업 내용: Stub 대신 기존 SQL 변환 실행 로직을 연결한다.
  - 권장 파일: `12_sqlConversionPipeline.py`
  - 완료 기준: Langflow에서 SQL Conversion 브랜치가 실제 변환 작업을 실행한다.

- [ ] 기존 SQL Conversion Tool 분석/분리
  - 작업 내용: 기존 구현에서 `run_sql_conversion_job`에 해당하는 로직만 분리한다.
  - 완료 기준: Chat Agent Tool 의존 없이 Pipeline 컴포넌트에서 직접 호출 가능하다.

- [ ] SQL Conversion pending 조건 정의
  - 작업 내용: 어떤 row를 SQL Conversion 대기 작업으로 볼지 확정한다.
  - 예시: `STATUS_CONVERSION IS NULL`, source SQL 존재, MIG 선행 여부 등.
  - 완료 기준: `06_getPendingJobs.py`에서 SQL Conversion 작업을 안정적으로 조회한다.

- [ ] SQL Conversion 전체 실행 계약 확정
  - 작업 내용: 전체 pending SQL Conversion 실행 action과 pending 조회 조건을 표준화한다.
  - 완료 기준: 단건 `space_nm/sql_id/row_id` 실행은 Long Job에서 지원하지 않고 Fast Status의 priority/status 조정으로 처리한다.

- [ ] SQL Conversion Prompt 입력값 정리
  - 작업 내용: 변환 프롬프트, 바인드 처리 프롬프트, 테스트 SQL 프롬프트 등을 컴포넌트 input으로 노출한다.
  - 완료 기준: Langflow에서 프롬프트 변경만으로 변환 정책을 조정할 수 있다.

- [ ] SQL Conversion 전체 실행 정책 구현
  - 작업 내용: DB Migration처럼 승인 없이 모든 SQL Conversion pending 작업을 실행한다.
  - 완료 기준: 처리한 SQL job list와 결과가 최종 요약에 반환된다.

- [ ] SQL Conversion 결과 저장/요약 구현
  - 작업 내용: 변환 SQL, 실패 사유, 검증 결과, 처리 시간을 저장하고 최종 요약에 반영한다.
  - 완료 기준: 사용자가 어떤 SQL이 변환됐고 무엇이 실패했는지 확인 가능하다.

## 3. SQL Tuning

- [ ] SQL Tuning 요구사항 정의
  - 작업 내용: 현재 newType에는 기존 개발물이 없으므로 튜닝의 입력, 출력, 성공 기준을 먼저 정의한다.
  - 완료 기준: 튜닝 대상 SQL, 실행 계획, 인덱스 추천, SQL rewrite 중 어디까지 처리할지 확정한다.

- [ ] SQL Tuning Agent 컴포넌트 신규 작성
  - 작업 내용: 사용자 요청과 pending context를 받아 SQL Tuning 전체 실행 payload를 생성한다.
  - 권장 파일: `13_sqlTuningAgent.py`
  - 완료 기준: `run_sql_tuning_job` action과 필수 파라미터가 생성된다.

- [ ] SQL Tuning Pipeline 컴포넌트 신규 작성
  - 작업 내용: LLM 기반 튜닝 프롬프트, 룰 기반 튜닝, 테스트 실행 여부를 포함한 Pipeline을 만든다.
  - 권장 파일: `14_sqlTuningPipeline.py`
  - 완료 기준: Langflow에서 SQL Tuning 브랜치가 실제 튜닝 결과를 생성한다.

- [ ] SQL Tuning pending 조건 정의
  - 작업 내용: 어떤 SQL을 튜닝 대기 상태로 볼지 결정한다.
  - 예시: Conversion 성공, Tuning 미완료, Formatting 미완료.
  - 완료 기준: `06_getPendingJobs.py`에서 Tuning 작업 조회가 가능하다.

- [ ] SQL Tuning Prompt 설계
  - 작업 내용: DB 종류, 실행 계획, 변환 SQL, 성능 이슈를 입력으로 받는 프롬프트를 설계한다.
  - 완료 기준: 튜닝 결과가 근거, 변경 SQL, 리스크를 함께 반환한다.

- [ ] SQL Tuning 결과 저장 구조 정의
  - 작업 내용: 튜닝 SQL, 권장 인덱스, 변경 이유, 리스크, 테스트 결과를 저장할 컬럼/로그 구조를 정한다.
  - 완료 기준: 후속 Formatting 단계가 튜닝 결과 SQL을 입력으로 받을 수 있다.

- [ ] SQL Tuning 실패/보류 정책 정의
  - 작업 내용: 정보 부족, 실행 계획 없음, 튜닝 불필요, LLM 실패 케이스를 분리한다.
  - 완료 기준: 실패가 전체 플로우를 무조건 중단하지 않고 상태로 남는다.

## 4. SQL Formatting

- [ ] SQL Formatting Agent 컴포넌트 신규 작성
  - 작업 내용: 사용자 요청과 pending context를 받아 SQL Formatting 전체 실행 payload를 생성한다.
  - 권장 파일: `15_sqlFormattingAgent.py`
  - 완료 기준: `run_sql_formatting_job` action과 필수 파라미터가 생성된다.

- [ ] SQL Formatting Pipeline 컴포넌트 신규 작성
  - 작업 내용: 변환/튜닝 완료 SQL을 표준 스타일로 포맷팅한다.
  - 권장 파일: `16_sqlFormattingPipeline.py`
  - 완료 기준: SQL 키워드 대문자, 들여쓰기, 줄바꿈, alias 정렬 등 포맷 규칙이 적용된다.

- [ ] SQL Formatting pending 조건 정의
  - 작업 내용: 어떤 SQL을 Formatting 대기 상태로 볼지 결정한다.
  - 예시: Conversion 성공, Tuning 완료 또는 생략, Formatting 미완료.
  - 완료 기준: `06_getPendingJobs.py`에서 Formatting 작업 조회가 가능하다.

- [ ] Formatting 스타일 가이드 작성
  - 작업 내용: 팀에서 원하는 SQL 포맷 규칙을 문서화한다.
  - 완료 기준: LLM 또는 포맷터가 같은 스타일로 결과를 낸다.

- [ ] SQL Formatter 선택
  - 작업 내용: LLM만 사용할지, `sqlparse` 같은 포맷터를 사용할지, 둘을 혼합할지 결정한다.
  - 완료 기준: 포맷 결과가 재현 가능하고 불필요한 SQL 의미 변경이 없다.

- [ ] SQL Formatting 결과 저장/요약 구현
  - 작업 내용: 포맷된 SQL, 변경 여부, 실패 사유를 저장하고 최종 요약에 포함한다.
  - 완료 기준: 최종 사용자가 포맷 완료 여부와 결과 위치를 확인할 수 있다.

## 5. 우선순위 높은 작업 순서

1. 공통 Payload/전체 실행 결과 계약 확정
2. `01_llmClassifier.py` 실제 LLM 분류 검증
3. `02_intentRouter.py` multi-output 분기 검증
4. DB Migration `09_dbMigrationAgent.py` -> `10_migPipeline.py` 전체 실행 검증
5. `06_getPendingJobs.py`, `08_longJobRouter.py`를 다중 도메인 구조로 확장
6. SQL Conversion Stub 제거 및 실제 Pipeline 연결
7. SQL Tuning 신규 설계/개발
8. SQL Formatting 신규 설계/개발
9. `13_finalSummary.py`에서 모든 도메인의 결과 요약 통합
10. 기존 Tool 기반 구조에서 newType Pipeline 구조로 리팩토링 완료
