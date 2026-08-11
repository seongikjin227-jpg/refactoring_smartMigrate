# smart_migrate Package

SmartMigrate의 핵심 런타임 패키지입니다. 최상위 실행 흐름은 `supervisor`가 잡고, 실제 업무 처리는 `agents`의 각 하위 에이전트가 담당합니다.

## 전체 호출 구조

```text
외부 실행 스크립트/서비스
  -> smart_migrate.supervisor.SupervisorAgent.SupervisorAgent.run()
     -> while not is_stop_requested()
        -> build_supervisor_graph()로 만든 LangGraph를 cycle마다 1회 invoke
           -> poll_jobs
           -> supervisor_decide
           -> run_action 또는 wait
           -> END
        -> 다음 cycle 반복
```

## 패키지 역할

- `supervisor/`: batch cycle, job polling, route 결정, tool 호출을 담당합니다.
- `supervisor/tools/`: supervisor graph에서 호출하는 LangChain tool wrapper입니다.
- `agents/`: Supervisor가 위임하는 실제 업무 agent입니다.
- `repositories/`: Oracle table 조회/갱신을 담당하는 DB 접근 계층입니다.
- `integrations/`: Oracle, LLM, Langflow 같은 외부 시스템 연결 계층입니다.
- `shared/`: 공통 status, type, exception, logging을 둡니다.
- `config/`: 환경변수 기반 런타임 설정과 prompt 파일을 둡니다.
- `utilities/`: 운영자가 별도로 실행하는 보조 기능입니다.

## 의존 방향

```text
supervisor
  -> agents
  -> repositories
  -> integrations
  -> config/shared
```

업무 상태 변경은 가능하면 `repositories`를 통해 수행하고, 외부 연결의 세부 구현은 `integrations`에 둡니다.
