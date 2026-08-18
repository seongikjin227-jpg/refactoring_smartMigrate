# SmartMigrate Chat Command Tool

`Chat_Command_Tool.py`는 Chat Agent가 command tool을 직접 호출하던 구조에서 쓰던 통합 command tool입니다.

현재 권장 구조가 `Chat Agent -> specialized agent -> command tool`이라면 이 파일은 기본 라우팅 경로에 두지 않는 편이 맞습니다.
각 specialized agent는 아래 전용 command tool을 사용합니다.

- Dashboard Agent -> `dashboard_command_tool.py`
- Fail Analysis Agent -> `fail_analysis_command_tool.py`
- Rerun Agent -> `rerun_command_tool.py`
- RAG Rule Agent -> `rag_rule_command_tool.py`
- Supervisor Control Agent -> `supervisor_control_command_tool.py`

## 역할

통합 command tool은 여러 action을 한 도구에서 처리합니다.

지원 action:
- `enqueue_migration`
- `enqueue_sql_conversion`
- `request_stop`
- `status`
- `failure_summary`

## 사용 기준

이 tool은 다음 경우에만 사용합니다.

1. 중간 specialized agent 없이 Chat Agent가 직접 command를 호출하는 단순 구조를 테스트할 때
2. 기존 flow 호환을 위해 단일 tool만 연결해야 할 때
3. Supervisor command queue 등록, status 조회, failure summary 조회를 한 tool에서 처리해야 할 때

중간 agent 구조에서는 이 tool의 md prompt가 별도로 필요하지 않습니다.
각 agent별 prompt 파일이 action JSON 생성을 담당합니다.
