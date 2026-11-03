# Local replay of attached JSON state

Exported execute/commit Code only. No Dify server, model or Oracle call.

SQL 변환 대상 1건을 조회했습니다. AS-IS SQL만 표시하며 변환·이관·튜닝은 실행하지 않았습니다.

### NEXT_SQL_INFO

| SQL_SEQ | SQL_ID | TARGET_TABLE | BIND_SET |
| --- | --- | --- | --- |
| 1 | EMP_DEPT_TUNING_DEMO | ASIS_HR_EMP,ASIS_HR_DEPT | {"dept_id": 20, "min_salary": 7000} |

총 1행 (최대 30행 표시)

### SQL_SEQ 1 · FR_SQL (AS-IS)

```sql
SELECT e.EMPLOYEE_ID, e.FIRST_NAME, d.DEPARTMENT_NAME, e.SALARY
FROM ASIS_HR_EMP e, ASIS_HR_DEPT d
WHERE e.DEPARTMENT_ID = d.DEPARTMENT_ID(+)
  AND e.DEPARTMENT_ID = :dept_id
  AND e.EMPLOYEE_ID IN (
    SELECT s.EMPLOYEE_ID FROM ASIS_HR_EMP s
    WHERE s.SALARY >= :min_salary
  )
ORDER BY e.EMPLOYEE_ID
```