# 신규 패키지 구조 계획

현재 구조는 `SupervisorAgent`가 DB 작업을 polling하고, 선택된 업무 agent를 호출하는 방식입니다. LangChain/LangGraph에서 `pipelines`라는 폴더명이 공식 표준은 아니므로, 이 프로젝트에서는 개발자가 이해하기 쉬운 `agents` 중심 구조로 정리합니다.

## 최종 실행 흐름

```text
main.py
  -> smart_migrate.runtime.RuntimeEntrypoint.main()
  -> SupervisorAgent.run()
  -> while loop
  -> SupervisorGraph
  -> poll_jobs Tool
  -> agents/db_migration 또는 agents/sql_* agent 중 최대 1개 실행
  -> request_wait
  -> 다음 cycle
```

## 패키지 트리

```text
src/smart_migrate/
├─ runtime/                    # 프로세스 실행, pid/pause/wake, startup check
├─ supervisor/                 # 장기 실행 loop, LangGraph supervisor, polling, tool adapter
│  └─ tools/                   # Supervisor가 호출하는 LangChain Tool adapter
├─ agents/                     # Supervisor가 호출하는 업무 agent
│  ├─ db_migration/            # NEXT_MIG_INFO 기반 DB migration agent
│  ├─ sql_conversion/          # NEXT_SQL_INFO 기반 TO_SQL/BIND_SQL/TEST_SQL conversion agent
│  ├─ sql_tuning/              # TUNED_TO_SQL 생성 agent
│  └─ sql_formatting/          # FORMATTED_SQL 생성 agent
├─ repositories/               # Oracle table SELECT/DML
├─ integrations/               # Oracle, LLM 외부 연결
├─ utilities/                  # XML import/export 같은 운영 보조 기능
├─ config/                     # 환경 설정
└─ shared/                     # 공통 status, type, exception, logging
```

## Agent 폴더 규칙

각 agent 폴더는 과하게 쪼개지 않습니다.

- `*Agent.py`: Supervisor가 호출하는 공개 진입점
- `*Workflow.py`: 실행 순서가 고정된 업무 흐름
- `*Graph.py`: LangGraph 분기/상태 전이가 명확히 필요한 경우에만 사용
- `*State.py`: workflow 실행 상태
- `*Components.py` 또는 역할이 분명한 helper 파일: 해당 agent 내부에서만 쓰는 세부 함수
- `README.md`: 입력 테이블, 실행 순서, 출력 컬럼, Supervisor 호출 방식을 설명

## 현재 정리 상태

- 기존 `pipelines/` 패키지는 `agents/`로 변경했습니다.
- 기존 `migration/` 폴더는 의미가 더 명확한 `db_migration/`으로 변경했습니다.
- `SqlBindCases.py`와 동일하던 `SqlBindGenerateNode.py`는 제거했습니다.
- `sql_tuning`, `sql_formatting`에도 `State`와 `Workflow` 파일을 추가했습니다.
- `APScheduler` 기반 standalone scheduler는 실제 실행 경로에서 사용하지 않아 제거했습니다.

## 남은 개선 후보

`SqlLlmService.py`는 현재 SQL conversion, tuning, formatting LLM helper가 섞여 있습니다. private helper 의존이 크기 때문에 한 번에 이동하기보다 다음 단계에서 아래처럼 agent별 component로 분리하는 것이 안전합니다.

```text
agents/sql_conversion/SqlConversionComponents.py  # generate_tobe_sql, generate_bind_sql, generate_test_sql
agents/sql_tuning/SqlTuningComponents.py          # tune_tobe_sql, tuned test generation
agents/sql_formatting/SqlFormattingComponents.py  # generate_formatted_sql
```
