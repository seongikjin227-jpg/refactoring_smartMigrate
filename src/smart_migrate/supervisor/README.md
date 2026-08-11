# Supervisor Package

SmartMigrate의 최상위 batch orchestration 계층입니다.

이 패키지는 **polling은 코드로 고정하고, 실행할 agent 선택은 LLM tool-calling에 맡기는 구조**입니다. 구현상 LLM은 agent를 직접 호출하지 않고, 각 agent를 감싼 LangChain tool wrapper를 `tool_calls`로 선택합니다.

## 핵심 구조

```text
SupervisorAgent.run()
  -> while not is_stop_requested()
     -> self._graph.invoke(initial_state)

SupervisorGraph 1 cycle
  -> poll_jobs
  -> supervisor_agent_tool_call
  -> tools 또는 wait
  -> END
```

`poll_jobs`는 선택사항이 아닙니다. 매 cycle에서 코드가 반드시 먼저 실행합니다. 그 다음 LLM은 이미 polling된 registry snapshot을 보고 실행할 agent wrapper 중 하나를 선택합니다.

## Graph는 누가 만드는가?

Graph는 LLM이 만들지 않습니다. `build_supervisor_graph()`가 코드로 고정 구성합니다.

```text
workflow = StateGraph(SupervisorState)
workflow.add_node("poll_jobs", poll_jobs_node)
workflow.add_node("supervisor_tool_call", supervisor_tool_call_node)
workflow.add_node("tools", ToolNode(agent_tools))
workflow.add_node("wait", wait_node)

poll_jobs -> supervisor_tool_call -> tools 또는 wait -> END
```

즉 노드와 엣지는 코드가 정합니다. LLM은 `supervisor_tool_call_node()` 안에서만 호출됩니다.

## 한 cycle의 상세 흐름

```text
poll_jobs_node()
  -> poll_jobs.invoke({})
  -> DB pending job 조회
  -> mig/sql/tuning/formatting registry 갱신
  -> poll_result 저장

supervisor_tool_call_node()
  -> LLM.bind_tools(agent_tools)
  -> poll_result를 LLM에 전달
  -> LLM이 agent tool_call 0개 또는 1개 선택

route_after_tool_choice()
  -> tool_calls가 있으면 tools node
  -> tool_calls가 없으면 wait node

ToolNode(agent_tools)
  -> LLM이 선택한 agent wrapper 실행

wait_node()
  -> 작업이 있었으면 짧게 대기
  -> 작업이 없었으면 길게 대기
```

## LLM이 선택할 수 있는 agent wrapper

LLM에게 bind되는 tool은 실제 agent를 감싼 wrapper입니다.

```text
run_data_migration(map_id)
run_sql_conversion(row_id)
run_sql_tuning(row_ids)
run_sql_formatting(row_ids)
```

`poll_jobs`는 LLM에게 bind하지 않습니다. 따라서 LLM이 `poll_jobs`를 반복 호출하거나 누락할 수 없습니다.

용어를 정확히 쓰면 다음과 같습니다.

```text
설계 관점:
  Supervisor가 실행할 agent를 선택한다.

구현 관점:
  LLM이 LangChain tool wrapper를 tool_call로 선택한다.

실제 호출:
  tool wrapper -> SupervisorJobRegistry callback -> Agent.process_job()
```

## LLM이 하는 일

LLM은 `poll_result`를 보고 어떤 agent wrapper를 호출할지 결정합니다.

```text
poll_result:
  migration jobs
  sql conversion jobs
  tuning jobs
  formatting jobs

policy:
  poll_jobs는 이미 실행됨
  실행할 job이 있으면 정확히 하나의 agent wrapper 호출
  실행할 job이 없으면 tool call 없이 응답
  우선순위: migration -> conversion -> tuning -> formatting
  poll_result에 있는 ID만 사용
```

LLM 응답 예:

```text
agent wrapper tool_call:
  name = run_sql_conversion
  args = {"row_id": "..."}
```

job이 없으면 LLM은 tool call 없이 짧은 응답만 반환합니다. 이 경우 graph는 `wait_node()`로 이동합니다.

## Tool 실행은 어디서 일어나는가?

실제 Python 함수 실행은 `ToolNode(agent_tools)`에서 일어납니다.

```text
LLM response.tool_calls
  -> route_after_tool_choice()
  -> ToolNode(agent_tools)
  -> run_data_migration.invoke(...)
     또는 run_sql_conversion.invoke(...)
     또는 run_sql_tuning.invoke(...)
     또는 run_sql_formatting.invoke(...)
```

tool wrapper 내부는 `SupervisorJobRegistry` callback을 통해 실제 agent를 호출합니다.

```text
ToolNode
  -> supervisor/tools/* agent wrapper
  -> SupervisorJobRegistry callback
  -> Agent.process_job(job)
```

재시도, 검증, 상태 전이는 supervisor가 아니라 각 agent 내부 graph/workflow가 담당합니다.

```text
run_data_migration wrapper
  -> MigrationOrchestrator.process_job()
  -> MigrationGraph
  -> DB migration retry / execute / verify

run_sql_conversion wrapper
  -> SqlConversionAgent.process_job()
  -> SqlConversionCoordinator / SqlConversionGraph
  -> conversion retry / bind SQL / test SQL / validation

run_sql_tuning wrapper
  -> SqlTuningAgent.process_job()
  -> SqlTuningWorkflow
  -> tuning rule retrieval / tuned SQL validation

run_sql_formatting wrapper
  -> SqlFormattingAgent.process_job()
  -> SqlFormattingWorkflow
  -> formatted SQL generation / persistence
```

## 한 cycle에 하나의 job만 실행하는 장치

LLM prompt는 “정확히 하나의 agent wrapper만 호출하라”고 지시합니다. 그래도 LLM이 여러 tool call을 만들 가능성은 있으므로 wrapper 내부에서 `claim_job_execution()`으로 한 cycle 1건 실행을 방어합니다.

```text
첫 번째 agent wrapper:
  claim_job_execution() == True
  실제 job 실행

두 번째 agent wrapper:
  claim_job_execution() == False
  SKIP 반환
```

즉 LLM이 실수해도 실제 job은 1건만 실행됩니다.

## callback 주입 구조

`SupervisorAgent.__init__()`에서 각 업무 agent를 만들고 `build_supervisor_graph()`에 callback을 주입합니다.

```text
MigrationOrchestrator().process_job
SqlConversionAgent().process_job
SqlTuningAgent().process_job
SqlFormattingAgent().process_job
```

`build_supervisor_graph()`는 이 callback들을 `init_callbacks()`로 registry에 저장합니다.

```text
init_callbacks(
  mig_proc=mig_process_job,
  sql_proc=sql_process_job,
  tune_proc=tune_process_job,
  format_proc=format_process_job,
  refresh_jobs=refresh_jobs_after_run,
)
```

## 기존 완전 ReAct 방식과의 차이

기존 방식은 LLM에게 `poll_jobs`까지 bind해서 LLM이 polling tool도 직접 선택했습니다.

```text
LLM tool choices:
  poll_jobs
  run_data_migration
  run_sql_conversion
  request_wait
  ...
```

현재 방식은 다릅니다.

```text
코드가 고정 실행:
  poll_jobs
  wait

LLM이 선택:
  업무 job tool 1개
```

이렇게 해서 `poll_jobs` 누락/반복 문제를 막으면서도, supervisor가 polling 결과를 보고 tool을 선택하는 구조는 유지합니다.

## 주요 파일

- `SupervisorAgent.py`: 외부 batch loop, signal 처리, graph invoke를 담당합니다.
- `SupervisorGraph.py`: fixed poll + LLM job tool-call graph를 구성합니다.
- `SupervisorJobPolling.py`: pending job 조회, registry 적재, batch size, priority gate를 담당합니다.
- `SupervisorJobRegistry.py`: job registry, callback registry, active job, cycle metric 상태를 관리합니다.
- `SupervisorState.py`: graph state schema입니다.
- `SupervisorPrompt.py`: supervisor prompt입니다.
- `tools/`: ToolNode가 실행하는 agent wrapper입니다.
