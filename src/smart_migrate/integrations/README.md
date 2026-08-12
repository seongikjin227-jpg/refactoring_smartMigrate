# Integrations Package

Oracle, LLM 같은 외부 시스템 연결을 담당합니다. business status, retry 정책, job routing은 이 계층에 두지 않고 `agents/`, `supervisor/`, `repositories/`에서 처리합니다.

## 하위 패키지

- `oracle/`: Oracle connection, schema/table qualification, DDL 조회를 제공합니다.
- `llm/`: LLM client, prompt template loader, model fallback helper를 제공합니다.

## 호출 방향

```text
agents/repositories
  -> integrations.oracle

agents/supervisor
  -> integrations.llm
```
