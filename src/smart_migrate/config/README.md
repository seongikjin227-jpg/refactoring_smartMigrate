# Config Package

환경변수 기반 runtime 설정과 prompt JSON 파일을 둡니다.

## 호출 구조

```text
AppSettings.py
  -> os.getenv(...)로 DB/LLM/table/runtime 설정 로드
  -> supervisor, agents, repositories, integrations에서 import

config/prompts/*.json
  -> integrations.llm.PromptLoader
  -> agents의 LLM service
```

## 주요 설정 영역

- DB 연결: `DB_USER`, `DB_PASS`, `DB_HOST`, `DB_PORT`, `DB_SID`, `ORACLE_CLIENT_PATH`
- LLM: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_MAX_TOKENS`
- Table: `MAPPING_RULE_TABLE`, `MAPPING_RULE_DETAIL_TABLE`, `RESULT_TABLE`, `RAG_INFO_TABLE`
- RAG/tuning: `RAG_EMBED_*`, `TOBE_SQL_TUNING_TOP_K`, `TOBE_SQL_TUNING_MAX_ITERATIONS`
- Supervisor/runtime: `SUPERVISOR_RECURSION_LIMIT`, `RUNTIME_DIR`, `MIG_KIND`

설정 값은 import 시점에 평가되므로 테스트나 실행 전에 환경변수를 먼저 세팅해야 합니다.
