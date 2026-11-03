매핑 2건과 상세 규칙 10건을 확인했습니다. 두 매핑의 실행 상태는 아직 기록되지 않았고, 현재 TOBE 부서·직원 테이블은 비어 있습니다.

현재 데이터와 매핑으로 이관을 실행하면 부서 3건과 직원 10건을 신규 추가할 것으로 예상됩니다. 기본키가 이미 있는 데이터는 변경된 값만 갱신합니다. 먼저 **“이관 실행해줘”**로 진행한 뒤 SQL 변환 → 튜닝 → 서식 정리 순서로 확인할 수 있습니다.

위 설명은 출력 예시입니다. 실제 AI 설명 문구는 달라지며, 아래 표와 예상 건수는 첨부된 실제 조회 결과를 Code로 재검증하여 생성했습니다.

현재 테이블 조회

### NEXT_MIG_INFO

| MAP_ID | FR_TABLE | TO_TABLE | USE_YN | STATUS |
| --- | --- | --- | --- | --- |
| 1 | ASIS_HR_DEPT | TOBE_HR_DEPT | Y | — |
| 2 | ASIS_HR_EMP | TOBE_HR_EMP | Y | — |

총 2행 (최대 30행 표시)

### NEXT_MIG_INFO_DTL

| MAP_ID | MAP_DTL | FR_COL | TO_COL |
| --- | --- | --- | --- |
| 1 | 1 | DEPARTMENT_ID | DEPT_ID |
| 1 | 2 | DEPARTMENT_NAME | DEPT_NAME |
| 2 | 1 | EMPLOYEE_ID | EMP_ID |
| 2 | 2 | FIRST_NAME | GIVEN_NAME |
| 2 | 3 | LAST_NAME | FAMILY_NAME |
| 2 | 4 | EMAIL | EMAIL_ADDR |
| 2 | 5 | HIRE_DATE | JOIN_DATE |
| 2 | 6 | JOB_ID | JOB_CODE |
| 2 | 7 | SALARY | BASE_SALARY |
| 2 | 8 | DEPARTMENT_ID | DEPT_ID |

총 10행 (최대 30행 표시)

### 이관 실행 예상

| 매핑 ID | 추가 예상 | 갱신 예상 | 동일 예상 |
| --- | --- | --- | --- |
| 1 | 3 | 0 | 0 |
| 2 | 10 | 0 | 0 |

현재 데이터로 계산한 예상입니다. 실제 실행은 요청을 받은 뒤 진행합니다.
