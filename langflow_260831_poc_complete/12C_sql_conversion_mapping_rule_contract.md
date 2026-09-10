# 12C SQL Conversion Mapping Rule Contract

이 문서는 DBA가 `NEXT_MIG_INFO` / `NEXT_MIG_INFO_DTL` 매핑룰을 작성할 때 지켜야 하는 기준이다. 12C SQL Conversion의 `TO_SQL`, `BIND_SQL`, `TEST_SQL` 생성은 아래 의미를 전제로 동작한다.

## 1. 기준 테이블

| 테이블 | 역할 |
|---|---|
| `NEXT_SQL_INFO` | SQL Conversion 대상 SQL과 결과를 저장한다. |
| `NEXT_SQL_INFO.TARGET_TABLE` | 해당 SQL에서 참조하는 AS-IS source table 후보 목록이다. |
| `NEXT_MIG_INFO` | source table에서 target table로 가는 migration mapping master이다. |
| `NEXT_MIG_INFO_DTL` | source column에서 target column으로 가는 column mapping detail이다. |

## 2. Mapping Rule 조회 기준

12C는 `NEXT_SQL_INFO.TARGET_TABLE` 값을 source table scope로 보고 `NEXT_MIG_INFO.FR_TABLE`과 매칭되는 PASS mapping rule을 조회한다.

- `TARGET_TABLE` 값은 단일 table일 수도 있고 여러 table 후보일 수도 있다.
- 여러 table 후보 중 mapping rule이 존재하는 table이 하나라도 있으면 SQL Conversion은 계속 진행한다.
- 프롬프트에는 조회된 mapping rule만 포함한다.
- 여러 table 후보 중 일부 table의 mapping rule이 없더라도 그 이유만으로 fail 처리하지 않는다.
- 전체 후보에서 조회된 mapping rule이 0건이면 `FAIL-TOBE`로 종료한다.
- `TARGET_TABLE` 자체가 비어 있으면 mapping rule scope를 알 수 없으므로 `FAIL-TOBE`로 종료한다.

## 3. Column Mapping 의미

`NEXT_MIG_INFO_DTL`의 한 row는 `FR_COL -> TO_COL` 매핑을 뜻한다.

| `TO_COL` 값 | 의미 |
|---|---|
| 실제 target column name | 해당 `FR_COL`을 TO-BE SQL에서 사용할 수 있다. |
| `NULL` | 해당 `FR_COL`은 TO-BE SQL에서 미사용 컬럼이다. |
| blank | 해당 `FR_COL`은 TO-BE SQL에서 미사용 컬럼이다. |
| `NONE`, `N/A`, `NA`, `-` | 해당 `FR_COL`은 TO-BE SQL에서 미사용 컬럼이다. |

12C 프롬프트에는 미사용 컬럼을 `TO_COL=__UNUSED__`로 표시한다.

## 4. Unmapped Object 정책

매핑룰에 없는 source object는 이름이 그대로 유지되는 것이 아니다. 미사용으로 해석한다.

- mapping rule에 없는 source table은 TO-BE SQL에서 미사용 table이다.
- mapping rule에 없는 source column은 TO-BE SQL에서 미사용 column이다.
- DBA가 TO-BE SQL에서 계속 사용해야 하는 table/column은 이름이 AS-IS와 TO-BE에서 같더라도 반드시 mapping rule에 명시해야 한다.
- 즉, "매핑룰에 없음"은 "이름 변경 없음"이 아니라 "TO-BE에서 사용하지 않음"이다.

## 5. TO-BE SQL 생성 기준

TO-BE SQL 생성기는 mapping rule에 있는 table/column만 target 구조로 변환해 사용한다.

- source table은 mapping rule이 실제 `TO_TABLE`로 매핑한 경우에만 TO-BE SQL에서 사용한다.
- source column은 mapping rule이 실제 `TO_COL`로 매핑한 경우에만 TO-BE SQL에서 사용한다.
- 미사용 table/column 전용 `SELECT`, `WHERE`, `JOIN`, `GROUP BY`, `HAVING`, `ORDER BY`, MyBatis dynamic fragment는 제거한다.
- mapped object와 unused object가 하나의 predicate/expression에 섞여 있으면 안전하게 분리 가능한 unused 조건만 제거한다.
- 안전하게 분리하기 어렵다면 해당 predicate/expression 전체를 제거해 invalid TO-BE reference를 피한다.

## 6. TEST SQL 생성 기준

TEST SQL은 AS-IS `FROM SQL`과 변환된 `TO-BE SQL`의 row count를 비교한다. 단, TO-BE에서 사용하지 않는 조건은 AS-IS count 쪽에서도 제거해 비교 범위를 맞춘다.

- TO-BE로 넘어오며 사용하지 않게 된 AS-IS filter/condition은 `FROM_COUNT` 쪽에서도 제거한다.
- 미사용 table/column 전용 join/filter/HAVING/GROUP BY/ORDER BY/MyBatis fragment는 `FROM_COUNT` 쪽에서 제거한다.
- 제거된 AS-IS filter에만 쓰이던 bind parameter는 최종 TEST SQL에서도 요구하지 않는다.
- 목적은 "원본 SQL 전체 조건"과 비교하는 것이 아니라, "TO-BE SQL로 이관된 의미 범위"와 비교하는 것이다.

## 7. DBA 작성 예시

### 명시 매핑

```text
NEXT_MIG_INFO
- FR_TABLE = ASIS_CUSTOMER
- TO_TABLE = TOBE_MEMBER

NEXT_MIG_INFO_DTL
- FR_COL = CUST_ID   -> TO_COL = MEMBER_ID
- FR_COL = CUST_NM   -> TO_COL = MEMBER_NAME
- FR_COL = LEGACY_YN -> TO_COL = NULL
```

의미:

- `CUST_ID`, `CUST_NM`은 TO-BE SQL에서 사용 가능하다.
- `LEGACY_YN`은 TO-BE SQL에서 미사용이다.
- AS-IS SQL에 `WHERE LEGACY_YN = 'Y'`가 있으면 TO-BE SQL에서 제거하고, TEST SQL의 `FROM_COUNT` 쪽에서도 제거한다.

### 이름이 같아도 명시 필요

```text
FR_COL = STATUS_CD -> TO_COL = STATUS_CD
```

의미:

- 이름이 같더라도 DBA가 이 컬럼을 TO-BE에서 사용하겠다고 명시한 것이다.
- 이 row가 없으면 `STATUS_CD`는 미사용 컬럼으로 해석된다.

## 8. 실패 기준

| 상황 | 처리 |
|---|---|
| `TARGET_TABLE`이 비어 있음 | `FAIL-TOBE` |
| `TARGET_TABLE` 후보 전체에서 PASS mapping rule 0건 | `FAIL-TOBE` |
| `TARGET_TABLE` 후보 중 일부만 mapping rule 있음 | 조회된 rule만 사용해 진행 |
| `TO_COL`이 null/blank 계열 | 해당 column 미사용 |
| source column이 mapping rule에 없음 | 해당 column 미사용 |

