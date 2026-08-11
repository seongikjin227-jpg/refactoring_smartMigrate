# Shared Package

프로젝트 공통 status, exception, logging, type 정의를 둡니다. 업무 흐름은 이 패키지에 두지 않습니다.

## 주요 파일

- `SqlStatuses.py`: conversion/tuning PASS/FAIL status 상수와 `normalize_status()`, `is_conversion_pass()`, `is_tuning_pass()`, `is_fail()`, `sql_in()` helper를 제공합니다.
- `SharedTypes.py`: `SqlInfoJob`, `MappingRuleItem` 같은 SQL conversion 계열 데이터 타입입니다.
- `MigrationTypes.py`: `MappingRule`, `MappingDetail` 같은 DB migration 계열 데이터 타입입니다.
- `SharedExceptions.py`: agent 공통 예외, LLM 예외, DB SQL 예외, verification 예외를 정의합니다.
- `SharedLogging.py`: `runtime/agent.log`에 기록하는 공통 logger를 구성합니다.

## 호출 기준

```text
agents/repositories/supervisor
  -> shared status/type/exception/logger import
```

여기에 DB 접근이나 LLM 호출을 추가하지 않습니다. 공통 정의만 유지해야 의존 방향이 단순합니다.
