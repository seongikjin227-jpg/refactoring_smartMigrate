# Prompt Templates

LLM 호출에 사용하는 JSON prompt template 모음입니다.

## 호출 구조

```text
agents의 LLM service
  -> integrations.llm.PromptLoader.build_prompt_messages(filename, **kwargs)
  -> load_prompt_template()
  -> render_prompt_template()
  -> LLM client 호출
```

## 주요 template

- `migration_prompt.json`: DB migration SQL/verification SQL 생성용 prompt입니다.
- `tobe_sql_prompt.json`: legacy SQL을 TO-BE SQL로 변환하는 prompt입니다.
- `bind_sql_prompt.json`: bind parameter 추출/실행용 SQL 생성 prompt입니다.
- `bind_sql_final_retry_prompt.json`: bind SQL 최종 재시도용 prompt입니다.
- `test_sql_prompt.json`: TO-BE SQL 검증용 test SQL 생성 prompt입니다.
- `test_sql_final_retry_prompt.json`: test SQL 최종 재시도용 prompt입니다.
- `tobe_sql_tuning_prompt.json`: TO-BE SQL 튜닝 prompt입니다.
- `tuned_test_sql_prompt.json`: 튜닝 전후 SQL 비교 검증 prompt입니다.
- `sql_indent_format_prompt.json`: SQL formatting prompt입니다.
- `planner_prompt.json`: planning 용도로 남아 있는 prompt입니다.
- `bind_tuned_sql_prompt.json`: 긴 bind source SQL을 사전 튜닝할 때 쓰는 prompt입니다.
