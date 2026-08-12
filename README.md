# SmartMigrate Refactoring

이 폴더는 기존 `server` 중심 구조를 `src/smart_migrate` 패키지 구조로 실제 이관한 리팩토링 산출물입니다.

## 현재 상태

- 루트 `server/` 폴더는 제거했습니다.
- 잘못된 숨김 구조인 `src/smart_migrate/server/`도 사용하지 않습니다.
- 기존 `server` 코드는 역할별 패키지로 이동했습니다.
- 백그라운드 agent 진입점은 `main.py`입니다.
- Streamlit UI 진입점은 `app/app.py`입니다.

## 실행 진입점

```text
main.py
  -> smart_migrate.runtime.RuntimeEntrypoint
  -> smart_migrate.supervisor.SupervisorAgent
  -> smart_migrate.supervisor.SupervisorGraph
  -> smart_migrate.supervisor.tools.*
  -> smart_migrate.agents.*
  -> smart_migrate.repositories.*
```

## 패키지 구조

```text
src/smart_migrate/
├─ runtime/                  # 프로세스 실행, pid/pause/wake 파일, startup check
├─ supervisor/               # LangGraph supervisor, polling, registry, tool adapter
│  └─ tools/                 # LangChain tool adapter
├─ agents/                # 실제 job 처리 agent
│  ├─ db_migration/          # DB migration agent
│  ├─ sql_conversion/        # SQL conversion agent
│  ├─ sql_tuning/            # SQL tuning agent
│  └─ sql_formatting/        # SQL formatting agent
├─ repositories/             # Oracle table SELECT/DML
├─ integrations/             # Oracle, LLM 연동
│  ├─ oracle/
│  └─ llm/
├─ utilities/                # XML import/export 같은 운영 보조 기능
│  └─ xml/
├─ config/                   # 환경 설정
└─ shared/                   # 공통 type, status, exception, logging
```

## 주요 이관 내역

| 기존 역할 | 새 위치 |
| --- | --- |
| supervisor agent/graph/state/prompt | `src/smart_migrate/supervisor/` |
| supervisor tools/context/polling | `src/smart_migrate/supervisor/`, `src/smart_migrate/supervisor/tools/` |
| migration agent/graph/executor/verifier | `src/smart_migrate/agents/db_migration/` |
| SQL conversion coordinator/workflow/validation/binding | `src/smart_migrate/agents/sql_conversion/` |
| SQL tuning agent/rule retrieval | `src/smart_migrate/agents/sql_tuning/` |
| SQL formatting agent/service | `src/smart_migrate/agents/sql_formatting/` |
| migration/sql repositories | `src/smart_migrate/repositories/` |
| Oracle connection/DDL/executor | `src/smart_migrate/integrations/oracle/` |
| LLM client/fallback/prompt loader | `src/smart_migrate/integrations/llm/` |
| migration/sql domain models/statuses/exceptions/logging | `src/smart_migrate/shared/` |
| XML parser/import/export helper | `src/smart_migrate/utilities/xml/` |

## 백그라운드 배치 동작

이 프로그램은 OS scheduler가 매번 새로 실행하는 방식이 아니라, `python main.py`로 뜬 Python 프로세스가 계속 살아 있으면서 내부 loop를 반복합니다.

```text
SupervisorAgent.run()
  -> while not stop_requested
  -> poll_jobs()
  -> job tool 최대 1개 실행
  -> request_wait()
  -> 다음 cycle
```

운영 제어 파일:

```text
runtime/agent.pid       # 실행 중인 agent PID
runtime/agent.pause     # 있으면 pause
runtime/agent.wake      # wait 중단 후 다음 cycle
runtime/active_job.json # 현재 실행 중인 job 표시
```

더 자세한 설명은 원본 작업 폴더의 `BatchSchedulerBackground.md`를 기준으로 보면 됩니다.

## 실행 방법

```powershell
pip install -r requirements.txt
python main.py
```

Streamlit UI:

```powershell
streamlit run app/app.py
```

## 검증 결과

다음 검증을 통과했습니다.

```powershell
python -m compileall main.py src app scripts tests
python -c "import sys; sys.path.insert(0, r'.\src'); import smart_migrate.runtime.RuntimeEntrypoint; import smart_migrate.supervisor.SupervisorAgent; import smart_migrate.supervisor.SupervisorGraph; print('backend imports ok')"
python -c "import sys; sys.path.insert(0, r'.\src'); import smart_migrate.repositories.SqlJobRepository; import smart_migrate.agents.sql_conversion.SqlConversionAgent; import smart_migrate.agents.db_migration.MigrationAgent; print('agent/repository imports ok')"
```

실제 DB/LLM 실행은 `.env`, Oracle client, DB 접속 정보, LLM endpoint가 필요합니다.


