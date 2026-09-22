# DB Migration 검증 가이드: Count Verify + Record Verify

## 1. 목적과 적용 컴포넌트

이 문서는 `10C_migOneJobPocExecutor2.py`의 DB Migration 검증 로직을 설명한다. Migration은 SQL이 실행됐다는 사실만으로 완료로 판단하지 않는다. 다음 두 검증을 반드시 순서대로 수행한다.

```text
GENERATE MIG_SQL / VERIFY_SQL
        ↓
EXECUTE MIG_SQL
        ↓
VERIFY_COUNT                 실패 → FAIL-TEST
        ↓ PASS
VERIFY_RECORDS               실패 → FAIL-TEST2
        ↓ PASS
PASS
```

- Count Verify는 전체 건수의 이관 완전성을 검사한다.
- Record Verify는 표본 row의 컬럼값 이관 정확성을 검사한다.
- Count가 PASS가 아니면 Record Verify는 절대 실행하지 않는다.

## 2. 검증 1: Count Verify

### 2.1 입력

`MIG_SQL`과 짝으로 생성·저장한 `VERIFY_SQL`을 실행한다. 일반적으로 AS-IS source query의 건수와 TOBE target table의 이관 범위 건수를 비교한다.

```sql
SELECT source_count, target_count, source_count - target_count AS diff_count
FROM ...
```

실제 생성 SQL의 결과 형식은 프로젝트 검증 함수가 해석할 수 있어야 하며, 모든 비교값이 일치해야 PASS다.

### 2.2 판정

| 상황 | 상태 | 다음 동작 |
|---|---|---|
| count가 모두 일치 | `PASS` (내부 상태 `COUNT_VERIFIED`) | Record Verify 진행 |
| count 불일치 | `FAIL-TEST` | VERIFY_SQL 재생성 후 count 검증 재시도 |
| VERIFY_SQL 실행 오류 | `FAIL-TEST` | VERIFY_SQL 재생성 후 count 검증 재시도 |

`FAIL-TEST`는 INSERT가 이미 수행된 상태다. 재시도에서 MIG INSERT를 다시 하지 않고, MIG_SQL은 유지한 채 VERIFY_SQL만 다시 생성·실행한다.

### 2.3 Count Verify 로그

| LOG_TYPE | STEP_NAME | 본문 CLOB |
|---|---|---|
| `VERIFY_SQL` | `VERIFY_COUNT` | 실제 실행한 count `VERIFY_SQL` |

## 3. 검증 2: Record Verify

### 3.1 시작 조건

Record Verify는 Count Verify가 PASS인 경우에만 실행한다. 목적은 “건수가 같다”를 넘어, MIG_SQL이 source의 어떤 값들을 target의 어떤 컬럼으로 넣었는지 표본 단위로 증명하는 것이다.

입력 MIG_SQL은 아래처럼 target column 목록이 명시된 `INSERT ... SELECT` 형식이어야 한다.

```sql
INSERT INTO TOBE_EMP (EMP_NO, EMP_NAME, DEPT_CD, MEMO)
SELECT S.EMP_ID,
       S.LAST_NAME || S.FIRST_NAME,
       S.DEPT_CODE,
       S.MEMO
  FROM ASIS_EMP S
 WHERE S.USE_YN = 'Y'
```

`INSERT` target column과 `SELECT` expression 개수가 다르거나, `INSERT ... VALUES`, 다중 statement, 지원하지 않는 SQL 구조라면 예상 데이터셋을 안전하게 만들 수 없으므로 `FAIL-TEST2`로 처리한다.

### 3.2 가상 AS-IS 데이터셋 생성

위 MIG_SQL을 실행하지 않고 SELECT 부분을 가상 TOBE shape로 재구성한다.

```sql
SELECT S.EMP_ID AS EMP_NO,
       S.LAST_NAME || S.FIRST_NAME AS EMP_NAME,
       S.DEPT_CODE AS DEPT_CD,
       S.MEMO AS MEMO
  FROM ASIS_EMP S
 WHERE S.USE_YN = 'Y'
```

이 결과를 로그에서는 `ASIS_DATASET_VIRTUAL`이라고 부른다. 엄밀히는 source table과 migration SELECT expression으로 만든 “이관 전 예상 target row”다. 따라서 `S.LAST_NAME || S.FIRST_NAME AS EMP_NAME`처럼 source expression과 target column의 매핑이 위 SQL에 명시된다.

### 3.3 표본 선정

가상 데이터셋에서 기본 3건(`Record Verify Sample Size`)을 결정적으로 선택한다.

```sql
ROW_NUMBER() OVER (ORDER BY ORA_HASH(key_columns...))
```

- 같은 데이터 상태에서는 같은 key row가 선택되어 재현 가능하다.
- random 함수로 매번 다른 row를 뽑지 않으므로 장애 재현과 로그 비교가 가능하다.
- sample size는 1~100으로 제한한다.

### 3.4 Row key 결정 알고리즘

각 표본의 실제 TOBE row를 찾기 위한 key는 다음 우선순위다.

| 우선순위 | key | 조건 |
|---|---|---|
| 1 | `Record Verify Key Columns` | Langflow 입력에 쉼표로 명시한 컬럼. 예: `EMP_NO,BASE_DT` |
| 2 | TOBE table PK | Oracle `ALL_CONSTRAINTS` / `ALL_CONS_COLUMNS`에서 조회한 PK |
| 3 | 복합 fallback key | MIG_SQL INSERT 대상 중 non-LOB 컬럼 전체 |

fallback은 PK가 없는 staging/legacy table을 위한 방법이다. CLOB/BLOB/LONG 계열은 WHERE equality key에서 제외한다. fallback key로 실제 TOBE row가 두 건 이상 발견되면 어떤 row가 대응되는지 증명할 수 없으므로 `FAIL-TEST2`다.

### 3.5 실제 TOBE row 조회와 컬럼 비교

각 표본 key로 TOBE table에서 row를 조회한다.

- 0건: `MISSING_TARGET_ROW`
- 정확히 1건: MIG_SQL INSERT 대상 컬럼을 expected와 actual로 비교
- 2건 이상: duplicate row로 보고 mismatch

실제 TOBE row는 **TOBE table DDL의 전체 컬럼**을 로그에 표시한다. 다만 비교 판정은 MIG_SQL INSERT 대상 컬럼에만 한다. INSERT에 없는 DEFAULT, IDENTITY, trigger 생성 컬럼은 source SELECT에 expected 값이 없으므로 “표시는 하되 판정 대상에서는 제외”한다.

CLOB은 앞 4,000자, BLOB은 앞 4,000 byte까지만 비교·표시한다. 로그에는 원본 길이와 truncation 여부도 남긴다.

### 3.6 판정

| 상황 | 결과 |
|---|---|
| 모든 표본 row가 정확히 한 건씩 조회되고 비교 컬럼이 모두 일치 | `PASS` |
| key로 target row를 찾지 못함 | `FAIL-TEST2` |
| key로 target row가 복수 조회됨 | `FAIL-TEST2` |
| 비교 컬럼 값 불일치 | `FAIL-TEST2` |
| PK/key/DDL/MIG_SQL 구조를 해석할 수 없음 | `FAIL-TEST2` |

## 4. 사람이 읽는 Record Verify 로그

`LOG_TYPE=VERIFY_RECORDS`, `STEP_NAME=VERIFY_RECORDS` 로그 본문은 아래와 같은 구조다.

```text
[ASIS_TO_TOBE_COLUMN_MAPPING_SQL]
SELECT S.EMP_ID AS EMP_NO,
       S.LAST_NAME || S.FIRST_NAME AS EMP_NAME,
       S.DEPT_CODE AS DEPT_CD
  FROM ASIS_EMP S
 WHERE S.USE_YN = 'Y'

[RECORD_VERIFY_SUMMARY]
key_strategy=TARGET_PRIMARY_KEY
key_columns=['EMP_NO']
selected_count=3
mismatch_count=1

[CASE 1] result=MATCH
[RECORD_KEY]
{"EMP_NO": "1001"}
[ASIS_DATASET_VIRTUAL]
{"EMP_NO": "1001", "EMP_NAME": "홍길동", "DEPT_CD": "HR"}
[TOBE_DATASET_ACTUAL_ALL_COLUMNS]
{"EMP_NO": "1001", "EMP_NAME": "홍길동", "DEPT_CD": "HR", "LOAD_TS": "...", "BATCH_ID": "..."}
[COLUMN_DIFF]
[]

[CASE 2] result=VALUE_MISMATCH
[RECORD_KEY]
{"EMP_NO": "1002"}
[ASIS_DATASET_VIRTUAL]
{"EMP_NO": "1002", "EMP_NAME": "김철수", "DEPT_CD": "FIN"}
[TOBE_DATASET_ACTUAL_ALL_COLUMNS]
{"EMP_NO": "1002", "EMP_NAME": "김철수", "DEPT_CD": "ACCOUNTING", "LOAD_TS": "..."}
[COLUMN_DIFF]
[{"column": "DEPT_CD", "expected": "FIN", "actual": "ACCOUNTING"}]
```

따라서 운영자는 mapping SQL로 AS-IS expression → TOBE column 관계를 확인하고, 각 CASE에서 예상 데이터셋과 실제 TOBE 전체 row를 나란히 확인할 수 있다.

## 5. Retry / 상태 전이

```text
FAIL-INSERT    → generate / execute부터 재시도
FAIL-TRUNCATE  → execute부터 재시도
FAIL-TEST      → VERIFY_SQL generate → VERIFY_COUNT 재시도
FAIL-TEST2     → VERIFY_RECORDS만 재시도
```

`FAIL-TEST2`는 이미 INSERT와 Count Verify가 성공했다는 뜻이다. 그러므로 재시도 시 다음을 절대 반복하지 않는다.

- MIG_SQL LLM generate
- MIG_SQL INSERT
- Count VERIFY_SQL 실행

`FAIL-TEST2` 로그 본문에는 MIG INSERT나 count verify SQL이 아니라 record projection과 case별 comparison만 기록한다.
