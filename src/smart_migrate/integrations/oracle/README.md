# Oracle Integration

Oracle 연결, schema/table 이름 정규화, DDL/metadata 조회를 담당합니다.

## 호출 구조

```text
repositories 또는 agent node
  -> OracleConnection.get_connection()
  -> qualify_* helper로 table 이름 구성
  -> SQL 실행 또는 DDL 조회
```

## 주요 파일

- `OracleConnection.py`: Oracle client 초기화, connection 생성, schema/table qualification helper를 제공합니다.
- `OracleDdlReader.py`: source/target table DDL과 migration log table 이름 조회를 담당합니다.
- `OracleSqlExecutor.py`: SQL 실행에 필요한 기본 connection wrapper와 값 변환 helper를 제공합니다.

이 패키지는 low-level 연결 계층입니다. job status 갱신이나 retry 정책은 `repositories/`와 `agents/`에서 처리합니다.
