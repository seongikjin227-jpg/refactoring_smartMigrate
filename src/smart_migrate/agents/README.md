# Agents Package

Supervisor가 호출하는 실제 업무 agent들이 모여 있는 패키지입니다.

각 agent는 supervisor tool에서 job을 받아 한 건씩 처리하고, 결과를 repository를 통해 DB에 저장합니다.

## 공통 호출 구조

```text
SupervisorGraph
  -> supervisor/tools/* tool
     -> Agent.process_job(job)
        -> Workflow 또는 Graph
           -> Repository 저장
```

공통 파일 역할:

- `*Agent.py`: supervisor-facing 진입점입니다.
- `*Workflow.py`: 실행 순서가 고정된 job 처리 흐름입니다.
- `*Graph.py`: 조건 분기와 retry routing이 있는 LangGraph 처리 흐름입니다.
- `*State.py`: workflow/graph 실행 중 공유하는 mutable state입니다.
- helper/service 파일: 해당 agent 내부에서 쓰는 LLM 호출, DB 실행, RAG 검색, formatting 같은 세부 기능입니다.

LangGraph를 무조건 여러 파일로 나누지는 않습니다. 분기와 상태 전이가 복잡한 agent는 `Graph.py`를 쓰고, 순서가 고정된 agent는 `Workflow.py` 하나로 명확하게 유지합니다.

## 하위 Agent

### `db_migration/`

`NEXT_MIG_INFO` 기반 DB migration job을 처리합니다.

```text
fetch_ddl -> check_dependency -> generate -> execute -> verify -> finalize
```

주요 특징:

- migration SQL과 verification SQL을 생성합니다.
- migration SQL을 실제 실행합니다.
- verification SQL로 데이터 정합성을 확인합니다.
- dependency, same-target priority, business retry가 LangGraph에 포함되어 있습니다.

### `sql_conversion/`

`NEXT_SQL_INFO` 기반 SQL conversion job을 처리합니다.

```text
tobe_generation.generate -> tobe_generation.validate
```

주요 특징:

- AS-IS SQL을 TO-BE SQL로 변환합니다.
- SELECT SQL은 bind/test SQL을 만들어 row count 비교 검증을 수행합니다.
- LONG SQL이면 필요 시 `TUNED_FR_SQL`을 먼저 생성하고 그 SQL 기준으로 `TO_SQL`을 만듭니다.
- SQL_CONVERSION RAG는 TO-BE SQL 생성 prompt에 들어갑니다.

### `sql_tuning/`

conversion이 완료된 `TO_SQL`을 tuning해 `TUNED_TO_SQL`을 생성합니다.

```text
TO_SQL -> SQL_TUNING RAG -> TUNED_TO_SQL -> tuned validation
```

주요 특징:

- SQL_TUNING GENERAL/SEARCH rule을 사용합니다.
- SELECT SQL은 기존 `TO_SQL`과 tuned SQL의 row count를 비교합니다.
- tuning 결과와 status를 기존 conversion row에 저장합니다.

### `sql_formatting/`

최종 SQL을 LLM formatting prompt 기준으로 정리해 `FORMATTED_SQL`에 저장합니다.

```text
TUNED_TO_SQL or TO_SQL -> formatted SQL -> FORMATTED_SQL
```

주요 특징:

- `TUNED_TO_SQL`을 우선 사용하고, 없으면 `TO_SQL`을 사용합니다.
- SQL 의미 변경이 아니라 formatting 목적입니다.
- conversion/tuning 결과와 status는 변경하지 않고 `FORMATTED_SQL`만 저장합니다.

## 실행 우선순위

Supervisor는 job polling 결과를 기준으로 다음 순서를 우선합니다.

```text
db_migration -> sql_conversion -> sql_tuning -> sql_formatting
```

SQL pipeline에서는 conversion PASS 후 tuning continuation이 실행되고, tuning PASS 후 formatting continuation이 실행될 수 있습니다.
