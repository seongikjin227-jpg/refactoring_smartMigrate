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

12C는 `NEXT_SQL_INFO.TARGET_TABLE` 값을 source table scope로 보고 PASS mapping rule을 조회한다. SIMPLE 및 단일 테이블명을 가진 COMPLEX는 `FR_TABLE`의 테이블명으로, SELECT를 가진 COMPLEX는 FROM/JOIN 내부 물리 테이블로 범위를 비교한다. 쉼표 조인과 중첩 쿼리도 포함하며, 주석·문자열·CTE 이름·inline view alias는 물리 테이블로 취급하지 않는다.

- master와 detail은 LEFT JOIN한다. 상세 행이 없는 MAP_ID도 SIMPLE/COMPLEX와 관계없이 테이블 매핑을 프롬프트에 전달한다. `STATUS='PASS'` 조건은 유지하며 `USE_YN`으로 제한하지 않는다.
- 상세 없는 매핑은 `COLUMN_MAPPING_STATUS=NOT_PROVIDED (table mapping only)`로 표시한다. 빈 `FR_COL -> __UNUSED__` 행을 만들지 않으며 `FR_TABLE`/`TO_TABLE`과 조건은 유지한다. 프롬프트 표시 순서는 SIMPLE → COMPLEX → 기타 유형이다.

- `TARGET_TABLE` 값은 단일 table일 수도 있고 여러 table 후보일 수도 있다.
- 여러 table 후보 중 mapping rule이 존재하는 table이 하나라도 있으면 SQL Conversion은 계속 진행한다.
- 프롬프트에는 조회된 mapping rule만 포함한다.
- 여러 table 후보 중 일부 table의 mapping rule이 없더라도 그 이유만으로 fail 처리하지 않는다.
- 전체 후보에서 조회된 mapping rule이 0건이어도 원본 테이블명과 컬럼명을 유지하여 SQL Conversion을 진행한다.
- `TARGET_TABLE` 자체가 비어 있으면 mapping rule scope를 알 수 없으므로 `FAIL-TOBE`로 종료한다.
- COMPLEX가 조회되면 원천 SELECT 전체와 `SOURCE_PHYSICAL_TABLES`, 컬럼 매핑을 함께 전달한다. 물리 테이블 집합은 후보 조회 기준이며 해당 테이블마다 독립적인 1:1 매핑이 있다는 뜻은 아니다.

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

SIMPLE 매핑과 적용 가능한 COMPLEX 쿼리 범위를 모두 확인한 뒤 매핑이 없는 참조에만 원본 테이블명과 해당 테이블의 컬럼명, alias, join, filter, projection, MyBatis fragment를 유지한다. COMPLEX `FR_TABLE`이 SELECT라는 이유로 내부 테이블을 미매핑으로 판단하지 않는다.

- mapping rule에 없는 source table은 TO-BE SQL에서 원본 이름으로 사용한다. 스키마 prefix는 기존 target_schema 규칙을 따른다.
- 물리 테이블명은 원본 SQL의 테이블명 또는 명시된 `TO_TABLE`만 사용한다. RAG, Correct SQL 힌트, 업무 의미로 테이블명을 추측하거나 생성하지 않는다.
- 컬럼 상세가 제공된 SIMPLE 매핑에서 목록에 없는 source column은 미사용 column이다. 상세가 전혀 없는 테이블 매핑에서는 모든 컬럼을 미사용으로 판단하지 않고 원본 컬럼 참조를 유지한다. 임의의 컬럼 변경은 생성하지 않으며 실제 대상 스키마와 검증 결과를 확인한다.
- 매핑 없는 테이블의 컬럼에는 미사용 column 정책을 적용하지 않는다.
- 컬럼 상세 목록을 제공하는 매핑에서는 계속 사용할 컬럼을 이름이 같더라도 명시해야 한다. 테이블 매핑만 제공하는 경우에는 컬럼 상세 미제공 상태로 구분한다.

### COMPLEX 원천 SELECT 해석

- `FR_TABLE_QUERY`는 `TO_TABLE`을 구성한 원천 데이터셋 정의다. 이를 TO-BE SQL에 그대로 복사하는 것을 변환 완료로 보지 않는다.
- `FR_COL`은 원천 SELECT의 출력 컬럼/alias를 기준으로 해석한다. 실제 원천 컬럼명과 alias가 다르면 출력 표현식을 추적해 `TO_COL`과 연결한다.
- AS-IS SQL의 조인·서브쿼리·투영·조건이 이 데이터셋과 대응하는 범위를 찾아 `TO_TABLE`과 `TO_COL` 기준으로 재작성한다.
- 여러 원천 테이블을 하나의 대상 테이블로 합치는 매핑에서 원천 테이블을 각각 같은 `TO_TABLE`로 단순 치환하지 않는다.
- 내부 조인·필터 컬럼은 SELECT 출력 alias인 `FR_COL`과 이름이 다르다는 이유만으로 미사용 처리하지 않는다.
- 동일 테이블이 다른 쿼리 범위에도 등장하거나 여러 매핑이 겹치면 테이블명만으로 대응 관계를 단정하지 않는다. 원천 SELECT와 컬럼 lineage를 함께 확인한다.

## 5. TO-BE SQL 생성 기준

TO-BE SQL 생성기는 명시된 table/column mapping을 적용하고, 테이블 매핑이 없는 대상은 원본 이름을 유지한다.

- 매핑된 source table은 실제 `TO_TABLE`로 변환하고, 매핑 없는 source table은 원본 이름으로 사용한다.
- 매핑된 source table의 컬럼은 실제 `TO_COL`로 매핑된 경우에 사용한다. 매핑 없는 테이블의 컬럼은 원본 이름으로 유지한다.
- 매핑된 테이블의 미사용 column 전용 `SELECT`, `WHERE`, `JOIN`, `GROUP BY`, `HAVING`, `ORDER BY`, MyBatis dynamic fragment는 제거한다.
- mapped object와 unused object가 하나의 predicate/expression에 섞여 있으면 안전하게 분리 가능한 unused 조건만 제거한다.
- 안전하게 분리하기 어렵다면 해당 predicate/expression 전체를 제거해 invalid TO-BE reference를 피한다.

## 6. TEST SQL 생성 기준

TEST SQL은 AS-IS `FROM SQL`과 변환된 `TO-BE SQL`의 row count를 비교한다. 단, TO-BE에서 사용하지 않는 조건은 AS-IS count 쪽에서도 제거해 비교 범위를 맞춘다.

- TO-BE로 넘어오며 사용하지 않게 된 AS-IS filter/condition은 `FROM_COUNT` 쪽에서도 제거한다.
- 매핑된 테이블의 미사용 column 전용 join/filter/HAVING/GROUP BY/ORDER BY/MyBatis fragment는 `FROM_COUNT` 쪽에서 제거한다. 매핑 없는 테이블의 참조와 조건은 유지한다.
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

### 여러 원천 테이블을 합치는 COMPLEX 매핑

```text
MAP_TYPE = COMPLEX
FR_TABLE = SELECT a.ID AS CUSTOMER_ID, b.TYPE_CD AS CUSTOMER_TYPE
           FROM ASIS_CUSTOMER a JOIN ASIS_TYPE b ON a.TYPE_ID = b.ID
TO_TABLE = TOBE_MEMBER
FR_COL = CUSTOMER_ID   -> TO_COL = MEMBER_ID
FR_COL = CUSTOMER_TYPE -> TO_COL = MEMBER_TYPE
```

`SOURCE_PHYSICAL_TABLES`는 `ASIS_CUSTOMER,ASIS_TYPE`으로 전달된다. 해당 조인 결과와 대응하는 AS-IS 조회는 `TOBE_MEMBER.MEMBER_ID`, `TOBE_MEMBER.MEMBER_TYPE`을 사용하는 조회로 재작성한다. `a.ID`와 `b.TYPE_CD`는 출력 alias의 표현식을 통해 매핑한다. 두 원천 테이블을 각각 `TOBE_MEMBER`로 바꿔 자기 조인을 만드는 방식은 이 매핑의 의미가 아니다.

## 8. 실패 기준

| 상황 | 처리 |
|---|---|
| `TARGET_TABLE`이 비어 있음 | `FAIL-TOBE` |
| `TARGET_TABLE` 후보 전체에서 PASS mapping rule 0건 | 원본 테이블명과 컬럼명을 유지해 진행 |
| `TARGET_TABLE` 후보 중 일부만 mapping rule 있음 | 조회된 rule만 사용해 진행 |
| `TO_COL`이 null/blank 계열 | 해당 column 미사용 |
| 매핑된 source table의 column이 mapping rule에 없음 | 해당 column 미사용 |
| source table이 mapping rule에 없음 | 원본 테이블명과 해당 컬럼 및 조건 유지 |

