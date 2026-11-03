# Dify 1.15 호환성 점검

확인일: 2026-10-18. 사용자가 알려준 버전은 1.15이며, 비교 기준은 공식 저장소 **1.15.0 태그**입니다. 설치 서버의 패치 버전·공식 Agent strategy plugin 버전은 아직 확인하지 않았습니다.

## 판단과 반영 사항

기본 Agent + 통합 Workflow Tool 하나 + 대화 변수 저장 구조는 유지합니다. 데이터·업무 엔진의 변경 없이 DSL 생성기와 입력 연결 방식을 수정했습니다.

| 항목 | 공식 소스 확인 / 적용 |
|---|---|
| DSL 형식 | 1.15.0의 CURRENT_APP_DSL_VERSION은 0.6.0. 생성 DSL과 manifest를 0.6.0으로 갱신 |
| 기존 0.3.1 DSL | 버전 검사 자체는 낮은 minor 버전을 경고 포함으로 허용. 0.3.1이면 무조건 실행 불가라는 의미는 아님 |
| Agent 기본 계약 | agent_strategy_* / agent_parameters / tool_node_version 지원. 현재 구조 유지 |
| Tool 데이터 연결 | 수동 입력을 mixed 템플릿 문자열에서 type=variable + selector 배열로 변경 |
| 상태 결과 읽기 | Agent json 출력에 data를 포함한 로그가 실리는 구조 확인. 현재 CALL/ROUND 결과 추출 유지 |
| Workflow Tool 결과 | TEXT에 End 출력 JSON을 반환하며 JSON 출력은 suppress_output=True. 현재 TEXT 경유 결과 해석과 일치 |

수동 입력 수정은 1.15 전용 새 기능을 도입한 것이 아니라 기존 템플릿의 잠재 문제를 고친 것입니다. Agent runtime은 tools 목록 전체를 JSON 직렬화한 후 템플릿을 처리합니다. 그 안에 테이블 JSON을 문자열 치환하면 따옴표·줄바꿈으로 재파싱이 깨질 수 있습니다. 변수 selector로 지정하면 그 다음 단계에서 원래 문자열 값을 직접 가져옵니다.

```yaml
# Agent의 Tool 입력 예시
next_sql_info_json:
  auto: 0
  value:
    type: variable
    value: [initialize, next_sql_info_json]
```

테이블 9개, session_json, request_id에 같은 방식을 적용했습니다. request만 Agent 자동 생성입니다. 사용자 입력이나 테이블 셀 안에 Dify 템플릿처럼 보이는 문자가 있어도 수동 Tool 입력에서 다시 치환하지 않습니다.

## 적용 방법

새로 설치한다면 갱신된 두 DSL을 사용해 SETUP 가이드대로 모델과 Tool을 선택합니다.

이전 DSL을 이미 가져왔다면 메인 Chatflow를 갱신하거나 Agent Tool 입력에서 테이블별 JSON·session_json·request_id를 **변수 선택 UI로 직접 연결**합니다. 예전 mixed 템플릿 문자열을 남기지 않습니다. Tool의 테이블별 입력 계약은 이번 변경으로 달라지지 않았습니다.

Agent strategy는 Dify 서버와 별도 버전을 가질 수 있으므로 해당 서버에서 공식 Function Calling 전략을 선택하고 현재 workspace의 Workflow Tool을 연결합니다. 임의 plugin 식별자를 하드코딩하지 않습니다. configure.py를 사용하면 참조 export에서 설치된 식별자를 가져오면서 수정된 변수 연결을 유지합니다.

## 검증과 남은 확인

로컬 테스트 18개가 통과했습니다. 새 회귀 검증은 테이블 JSON의 따옴표·역슬래시·줄바꿈·템플릿 모양 문자열이 직접 변수 전달에서 보존되는지와 두 DSL의 버전을 확인합니다. 기존 이관/관리/대화 상태 검증도 통과했습니다.

실제 Dify 1.15 서버의 import·모델 실행·plugin 로그는 아직 실행하지 않았습니다. 현재 결과는 공식 소스 비교와 로컬 회귀 검증이며 서버 실행 성공 보장은 아닙니다.

## 공식 근거

- [1.15.0 DSL 버전](https://github.com/langgenius/dify/blob/1.15.0/api/constants/dsl_version.py)
- [DSL 버전 호환 처리](https://github.com/langgenius/dify/blob/1.15.0/api/services/dsl_version.py)
- [Agent 입력 스키마](https://github.com/langgenius/dify/blob/1.15.0/api/core/workflow/nodes/agent/entities.py)
- [Agent 수동 변수 입력 처리](https://github.com/langgenius/dify/blob/1.15.0/api/core/workflow/nodes/agent/runtime_support.py)
- [Agent json 출력과 로그](https://github.com/langgenius/dify/blob/1.15.0/api/core/workflow/nodes/agent/message_transformer.py)
- [Workflow Tool 결과 메시지](https://github.com/langgenius/dify/blob/1.15.0/api/core/tools/workflow_as_tool/tool.py)
