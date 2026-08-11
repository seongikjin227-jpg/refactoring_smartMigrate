# Supervisor Package

batch cycle을 돌며 어떤 agent job을 실행할지 결정하는 최상위 오케스트레이션 계층입니다. 실제 migration, SQL conversion, tuning, formatting 업무 로직은 `agents/`에 두고, 이 패키지는 “언제 무엇을 호출할지”만 관리합니다.

## 진입점

```text
SupervisorAgent.SupervisorAgent()
  -> __init__()
     -> MigrationOrchestrator()
     -> SqlConversionAgent()
     -> SqlTuningAgent()
     -> SqlFormattingAgent()
     -> build_supervisor_graph(...각 agent의 process_job callback...)
```

`SupervisorAgent.__init__()`은 repository 조회 함수와 agent callback을 `build_supervisor_graph()`에 주입합니다. 이 구조 때문에 supervisor graph는 DB/agent 구현을 직접 import하지 않고 callback으로 실행합니다.

## 반복 실행 구조

```text
SupervisorAgent.run()
  -> _register_signal_handlers()
  -> start_batch_metrics(batch_no)
  -> while not is_stop_requested()
     -> start_cycle_metrics(cycle)
     -> _read_chat_command()
     -> initial_state 구성
     -> self._graph.invoke(initial_state, recursion_limit=SUPERVISOR_RECURSION_LIMIT)
     -> finish_cycle_metrics()
```

반복은 `SupervisorAgent.run()`의 `while` 루프가 담당합니다. `SupervisorGraph`는 한 cycle 안에서 한 번 실행되는 LangGraph입니다.

## Graph 1회 실행 흐름

```text
build_supervisor_graph()
  -> init_callbacks(...)
  -> build_poll_jobs_tool(...)
  -> StateGraph(SupervisorState)
     -> poll_jobs
     -> supervisor_decide
     -> run_action 또는 wait
     -> END
```

- `poll_jobs_node()`: `poll_jobs.invoke({})`로 DB job을 조회하고 registry를 갱신합니다.
- `supervisor_decide_node()`: LLM에 현재 polling 결과를 전달하고 한 cycle에서 실행할 route를 JSON으로 받습니다.
- `route_after_decision()`: LLM 결정이 우선순위 정책과 registry 상태에 맞는지 보정합니다.
- `run_action_node()`: registry에서 job 1건을 꺼내 tool을 호출합니다.
- `wait_node()`: 작업 후 짧게 대기하거나 job이 없으면 긴 대기를 수행합니다.

## 주요 파일

- `SupervisorAgent.py`: 외부 반복 루프, signal 처리, graph invoke를 담당합니다.
- `SupervisorGraph.py`: cycle 1회의 LangGraph 노드와 route 조건을 정의합니다.
- `SupervisorJobPolling.py`: DB polling tool 생성, batch size, agent enable flag, priority gate를 담당합니다.
- `SupervisorJobRegistry.py`: pending job registry, callback registry, active job, metric 상태를 관리합니다.
- `SupervisorState.py`: supervisor graph state schema입니다.
- `SupervisorPrompt.py`: supervisor 판단용 system prompt입니다.
