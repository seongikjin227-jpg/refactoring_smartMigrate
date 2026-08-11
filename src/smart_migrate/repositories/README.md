# Repositories Package

Oracle table SELECT/DML을 담당하는 DB 접근 계층입니다. Supervisor와 agent는 repository를 통해 job을 조회하고 실행 결과를 저장합니다.

## 호출 구조

```text
supervisor polling
  -> MigrationJobRepository.get_pending_jobs()
  -> SqlJobRepository.get_pending_jobs()
  -> SqlJobRepository.get_tuning_jobs()
  -> SqlJobRepository.get_formatting_jobs()

agent workflow
  -> repository update/log 함수
  -> OracleConnection.get_connection()
```

## 주요 파일

- `MigrationJobRepository.py`: `NEXT_MIG_INFO` pending job 조회, batch count 증가, migration status 갱신, dependency 확인을 담당합니다.
- `MigrationHistoryRepository.py`: migration SQL, verification SQL, business history log 저장을 담당합니다.
- `SqlJobRepository.py`: `NEXT_SQL_INFO` conversion/tuning/formatting job 조회와 `update_cycle_result()`, `update_formatted_sql()` 같은 결과 갱신을 담당합니다.
- `SqlLogRepository.py`: TOBE/BIND/TEST/TUNED_TEST SQL 실행 로그를 저장합니다.
- `MappingRuleRepository.py`: mapping rule 조회, target table 준비 여부, SQL map type 판정을 담당합니다.

## 사용 기준

DB schema/table 이름 조합은 `integrations/oracle`의 helper를 사용합니다. agent 내부에서 직접 SQL DML을 늘리기보다 이 패키지에 함수로 추가하는 편이 호출 구조 추적에 유리합니다.
