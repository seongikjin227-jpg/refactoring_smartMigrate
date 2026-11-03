# 설치와 대화 시연

**단계 분리 시연은 [일곱 단계와 최신 DSL 반영 절차](DEMO_STAGES.md)를 따릅니다.** SQL 변환·튜닝 요청에 이관을 포함하지 않으며, 원문 요청과 실행 계획의 단계 검사가 추가됐습니다.

**최신 기준은 [실행 Tool·입력 연결](AUTO_INPUT_SETUP.md)입니다.** 01이 실제 작업을 실행하고 02의 표준 고정 Tool 목록에 session_transport 연결이 사전 입력돼 있습니다. 아래 수동 연결 절차는 이전 버전 기록입니다.

[2026-10-19 업데이트](UPDATE_2026_10_19.md)의 수동 연결 절차도 변경 이력입니다. Agent tools 배열 자체의 변수 참조 방식은 렌더링 오류 대응으로 폐기했습니다.

현재 대상은 Dify 1.15이며 공식 1.15.0 소스로 점검했습니다. 생성 DSL은 0.6.0입니다. 변경 근거와 한계는 [1.15 호환성 기록](DIFY_1_15.md)을 참고하세요.

현재 버전은 전체 테이블·요청 ID를 session_json 하나로 전달합니다. 기존 Tool의 입력 이름 12개는 호환성을 위해 유지하지만 개별 테이블 연결은 필요 없습니다. 기존 서버 업데이트는 [가져오기 안내](../IMPORT.md)의 Code 교체 순서를 따릅니다. 아래 개별 테이블 연결 설명은 구버전 호환 경로이며 현재 02에서는 session_json만 변수 연결하고 request만 자동입니다.

## 1. 통합 Workflow 준비

1. Dify Studio에서 DSL 가져오기로 `dsl/01_smartmigrate_tool.yml`을 가져옵니다.
2. `통합 작업 계획·SQL 생성` LLM 노드에서 등록 모델을 선택합니다. 현재 서버의 출력 토큰 설정 상한은 4096이므로 max_tokens=4096을 사용합니다.
3. Workflow를 Tool로 발행합니다. 도구 이름은 정확히 **smartmigrate_execute**로 지정합니다.
4. 테이블별 JSON 9개와 `session_json`, `request`, `request_id` 총 12개 입력을 노출합니다. 모두 문자열입니다. [파일·입력 연결표](../samples/TABLE_INPUTS.md)를 참고합니다.
5. 출력 `result_json`을 유지합니다. Tool 설명은 “샘플 테이블 조회·매핑 관리·이관·SQL 변환·튜닝·서식 처리”로 설정합니다.

Workflow를 수정하면 Tool의 발행본도 갱신해야 합니다. Chatflow를 Tool로 발행하는 구성이 아닙니다.

## 2. 메인 Chatflow 연결

1. `dsl/02_smartmigrate_chatflow.yml`을 가져옵니다.
2. Agent에서 환경에 설치된 **공식 Function Calling** 전략과 Tool 호출을 지원하는 등록 모델을 선택합니다. 사용자 제작 plugin은 필요 없습니다. 공식 전략이 없으면 Dify 관리자에게 기본 Agent 사용 가능 여부를 확인해야 합니다.
3. 현재 02에는 사용자 서버의 기존 smartmigrate_execute Tool ID를 반영했습니다. 같은 서버에서는 해당 연결을 사용하고, 다른 서버라면 그곳에서 발행한 Tool을 선택합니다. Agent에 도구는 하나만 둡니다.
4. Tool 입력을 아래와 같이 **변수 선택 UI로 직접 연결**합니다. JSON 입력에 mixed 템플릿 문자열을 붙여넣지 않습니다.

| Tool 입력 | 설정 |
|---|---|
| `request` | Agent 자동 생성 |
| 테이블별 `*_json` 9개 | 수동 지정: `대화 상태 준비` 노드의 같은 이름 출력 |
| `session_json` | 수동 지정: `대화 상태 준비` 노드의 `session_json` |
| `request_id` | 수동 지정: `대화 상태 준비` 노드의 `request_id` |

5. Agent 최대 반복은 2로 둡니다. 프롬프트는 한 턴에 Tool 한 번만 호출하도록 되어 있습니다.
6. 후속 `Tool 결과 확인·상태 확정` Code 입력에 Agent의 **json 출력**과 text가 연결되어 있는지 확인합니다.
7. `대화 테이블 저장`에서 `commit.state_json`을 대화 변수 `demo_state`에 **덮어쓰기**로 저장합니다.
8. Answer는 `commit.answer`를 출력합니다. Agent text를 직접 최종 출력으로 바꾸면 검증된 표와 상태 반영 여부를 놓칠 수 있습니다.

임포트 시 알 수 없는 provider 오류가 나면 해당 워크스페이스의 Tool ID가 필요합니다. 아래 참조 export 방법으로 연결 후 다시 가져오거나 가이드 구성대로 노드를 수동 배치합니다.

## 3. 워크스페이스 ID 연결을 자동화하는 선택 방법

1. 해당 Dify에서 빈 테스트 Chatflow를 만듭니다.
2. 기본 Agent를 추가하고 공식 Function Calling 전략·등록 모델·`smartmigrate_execute` Tool을 선택합니다.
3. 이 테스트 앱을 DSL로 내보내 로컬 파일로 저장합니다. 실제 대화 데이터나 API key를 넣을 필요는 없습니다.
4. 아래 스크립트는 참조 export를 읽어 모델·Tool provider ID·Agent strategy 식별자를 로컬 DSL에 연결합니다. 참조 파일은 수정하지 않습니다.

```powershell
python smartMigrate_3.0_Dify/scripts/configure.py --agent-export "C:\path\reference-agent.yml"
```

5. 연결된 `02_smartmigrate_chatflow.yml`을 새로 가져옵니다. 이미 발행한 통합 Tool의 LLM 모델 선택은 1절에서 진행한 값을 유지합니다.

모델명/provider ID를 이미 알고 있다면 빌드 인자로도 지정할 수 있습니다.

```powershell
python smartMigrate_3.0_Dify/scripts/build.py --tool-provider-id "WORKSPACE_TOOL_PROVIDER_UUID" --model-provider "REGISTERED_PROVIDER_ID" --model "REGISTERED_MODEL_NAME"
```

build.py는 workspace_binding.json에 저장한 모델/Tool 연결을 유지합니다. bind_workspace.py와 configure.py는 02를 직접 수정하며 03을 추가하지 않습니다. Dify 화면에서 수정한 내용은 원본 export로 보관하세요.

## 4. 테이블 파일 관리와 첫 시연

샘플 기본값은 Code 내부뿐 아니라 **Start 입력 필드의 default**에도 들어 있습니다. 메인 sample_json과 테이블별 JSON 9개, Tool의 테이블/세션 입력이 모두 사전 채움 대상입니다. 변경한 DSL을 다시 가져온 뒤 새 미리보기 또는 새 대화에서 확인하세요. 기존 앱의 모델·Tool 연결은 가져온 뒤 확인해야 합니다.

최초 입력의 우선순위는 기본 샘플 → 수정한 sample_json → 직접 수정한 개별 테이블입니다. 변경하지 않은 테이블별 기본값은 수정된 sample_json을 덮어쓰지 않습니다. 같은 대화를 이어갈 때는 이 초기 입력보다 저장된 최신 상태가 우선합니다.

`samples/tables/ASIS_HR_EMP.json`, `NEXT_MIG_INFO.json`, `NEXT_SQL_INFO.json` 등에서 테이블별 데이터를 관리합니다. 각 파일에는 해당 테이블의 columns·primary_key·rows가 들어 있습니다. 원본 수정 후 build.py를 실행하면 통합 샘플과 DSL 기본값에 반영되며 원본 파일은 유지됩니다.

메인 Chatflow의 asis_hr_emp_json, next_mig_info_json 등 개별 입력에 파일 내용을 붙여넣어도 됩니다. 최초에는 통합 기본값에 개별 입력을 반영하고 다음 대화부터는 저장된 최신 상태를 사용합니다.

Tool을 직접 테스트할 때는 각 파일 내용을 대응 입력에, samples/session.json 내용을 session_json에 넣고 request_id를 지정합니다. Dify가 PC의 파일 경로를 직접 읽는 구성이 아니므로 **파일 내용**을 전달해야 합니다. Agent에서는 매번 원본 파일을 상수로 넣지 말고 최신 initialize 출력을 연결해야 이전 변경을 유지합니다.

`sample_json`은 선택 입력입니다. 비워 두면 DSL에 내장된 HR 샘플로 시작합니다. 직접 데이터를 넣으려면 `samples/hr_demo.json` 전체 내용을 최초 입력에 붙여넣습니다.

다음 대화부터는 최신 `demo_state`를 사용하며 초기 JSON으로 되돌리지 않습니다. 초기화하려면 새 대화를 시작합니다. 상태를 이어가려면 “현재 데이터 JSON 내보내줘” 결과를 새 대화의 `sample_json`으로 넣습니다.

권장 대화 순서:

1. “매핑룰과 SQL 작업 목록 보여줘.”
2. “전체 작업 실행해줘.”
3. “TOBE_HR_EMP 테이블과 NEXT_SQL_INFO 결과 보여줘.”
4. “MAP_ID 2, MAP_DTL 2의 FR_COL을 LAST_NAME으로 변경해줘.”
5. “이관 작업 전체 다시 실행해줘.”
6. “TOBE_HR_EMP 보여줘.” → GIVEN_NAME이 원천 LAST_NAME 값으로 갱신되고 직원은 10행 유지됩니다.
7. “MAP_ID 2, MAP_DTL 2의 FR_COL을 FIRST_NAME으로 복구해줘.”
8. “SQL_SEQ 1 변환해줘.”
9. “SQL_SEQ 1 튜닝해줘.”
10. “SQL_SEQ 1 서식 정리해줘.”

관리 기능 예시:

- “MAP_ID 2, MAP_DTL 8 매핑 삭제해줘.”
- “MAP_ID 2에 MAP_DTL 8, FR_COL DEPARTMENT_ID, TO_COL DEPT_ID 매핑 추가해줘.”
- “MAP_ID 2 상태를 READY로 변경해줘.”
- “SQL_SEQ 1의 EDIT_FR_SQL을 SELECT e.EMPLOYEE_ID FROM ASIS_HR_EMP e 로 변경해줘.”
- “MAP_ID 2 전체 매핑 삭제해줘.” → 마스터와 상세가 같이 삭제됩니다.
- “MAP_ID 1 삭제해줘.” → MAP_ID 2가 선행 매핑으로 참조 중이면 먼저 관계 변경을 요청합니다.
- “현재 데이터 JSON 내보내줘.”

모호한 “그 매핑 변경해줘”에는 질문을 반환하며 실제 변경은 하지 않습니다. 다음 답변은 `pending_action`과 `last_target`을 바탕으로 이어집니다.

## 5. 문제 확인

### 01/02의 역할과 빈 컨텍스트

01만 Workflow Tool로 발행하며 02는 메인 채팅 앱으로 사용합니다. 01의 LLM context 칸이 빈 것은 정상입니다. 데이터는 USER 프롬프트의 prepare.context_json 변수로 전달됩니다. 별도 RAG context 입력이나 02의 Tool 등록은 필요하지 않습니다.

02에 있는 변수 연결이 서버 입력에서 계속 null로 보이면 실제 실행 앱을 DSL로 내보내 저장된 parameters의 auto/value와 selector 노드 ID를 비교해야 합니다. UI의 Tool 목록 존재만으로 상태 입력 연결을 확인한 것으로 간주하지 않습니다. 현재 로컬 02는 request만 자동이고 나머지 11개가 직접 변수 연결입니다. 수정 파일을 다시 가져오는 작업만 반복하기보다 서버 export를 기준으로 확인합니다.

### 도구 목록에 있지만 Agent가 ‘도구가 없다’고 답하고 호출하지 않음

**이후 실제 입력 로그에서 확인한 사항:** smartmigrate_execute는 enabled=true로 존재하지만 parameters가 모두 null이었습니다. Dify 1.15는 자동 생성 파라미터를 호출 전 입력 로그에서 null로 표시합니다. request는 자동이어도 되지만 상태 JSON 9개·session_json·request_id는 직접 변수 연결이어야 합니다. 도구 재추가 후 이 값들도 자동 모드가 되었다면 먼저 11개 연결을 복구합니다. 사용자 Tool ID와 입력 연결을 반영한 파일은 [02 메인 Chatflow](../dsl/02_smartmigrate_chatflow.yml)입니다. 중복 03은 제거했습니다. 이는 미호출 원인의 전부를 확정한 것은 아니며 연결 복구 후 CALL 로그가 생기는지 확인해야 합니다.

**설정 위치:** 워크스페이스 설정 → 모델 제공자 → OpenAI-API-compatible 카드의 등록 모델 목록 → gpt-5.5 행의 자격증명/인증 편집입니다. 화면에 따라 모델 행의 메뉴(···)나 인증 관리 버튼에서 들어갑니다. Function Call Type은 Agent 캔버스의 temperature 설정창이 아니라 이 개별 모델 등록 폼에 있습니다.

`does not have provider_credential_schema` 오류는 제공자 공통 인증 폼을 요청했으나 해당 plugin에 그 스키마가 없다는 뜻입니다. 이 plugin은 model_credential_schema 기반이므로 개별 모델의 자격증명 편집 경로로 들어갑니다. 실제 모델 편집 UI나 권한이 보이지 않으면 관리 가능한 계정인지 확인하고 모델 목록 화면을 기준으로 경로를 확인해야 합니다.

API 키 인증 화면의 ‘권한 이름’은 저장된 자격증명을 구분하는 별칭입니다. 예를 들어 smartmigrate-gpt55로 지정할 수 있으며 실제 API key나 모델명과는 별개입니다. 이미 같은 모델로 답변이 온다면 기존/공유 인증으로 호출되었을 수 있으므로 신규 인증을 만들기보다 Agent가 선택한 모델의 기존 설정에서 Function Call Type을 확인합니다.

사용자 로그의 제공자는 OpenAI-API-compatible이며 tool_responses=[], tool_input=[], tool_name=''입니다. 이는 호출이 없다는 증거이며 실제 Tool 미등록을 확정하는 증거는 아닙니다. 사용자는 smartmigrate_execute가 Agent Tool 목록에 있다고 확인했습니다.

1. 모델 제공자 설정에서 **OpenAI-API-compatible → 등록한 gpt-5.5 편집 → Function Call Type**을 확인합니다. 해당 API 경로가 tools/tool_calls를 지원한다면 **Tool Call**을 선택합니다. 공식 플러그인의 기본값은 Not Support(no_call)입니다. legacy Function Call과도 구분합니다.
2. 저장 후 Agent 전략을 Function Calling으로 두고 모델을 다시 선택해 메타데이터를 갱신합니다. temperature=1은 유지합니다.
3. Tool이 활성화되어 있는지 확인하고, Allowed tools 입력이 있는 전략이라면 우선 비워 필터링을 해제합니다.
4. 새 대화에서 ‘매핑룰 조회해줘’로 실행하고 CALL smartmigrate_execute와 tool_responses가 생기는지 봅니다.
5. 계속 호출이 없으면 Agent 실행의 입력 중 tools 배열이 비어 있는지 확인합니다. 비어 있으면 설정/필터 문제를 먼저 조사합니다. 입력에 도구가 있다면 제공자 요청의 tools와 API 응답의 tool_calls를 확인할 차례입니다. API key나 전체 credential을 공유할 필요는 없습니다.

현재 서버의 모델 등록 설정과 gateway 요청을 직접 확인하지 않았으므로 원인은 아직 확정하지 않습니다. 이 설정은 모델 제공자 측에서 관리하며 DSL에 임의 기능 플래그를 넣어 지원이 확인된 것으로 표시하지 않습니다.

공식 근거: [Function Call Type 설정과 기본값](https://github.com/langgenius/dify-official-plugins/blob/main/models/openai_api_compatible/provider/openai_api_compatible.yaml), [tool_calls 응답 해석](https://github.com/langgenius/dify-official-plugins/blob/main/models/openai_api_compatible/models/llm/llm.py).

### 모델이 temperature=0.1을 거부하는 HTTP 400

2026-10-18 사용자 오류 로그에서 Azure/LiteLLM gpt-5.5 경로가 기본값 1만 허용한다고 응답했습니다. 제공 파일의 시연 기본값은 현재 1로 수정했습니다.

이미 가져온 앱에서는 **메인 Agent의 모델 파라미터**와 **통합 Workflow의 ‘통합 작업 계획·SQL 생성’ LLM 파라미터**를 모두 열어 temperature를 1로 변경합니다. Workflow 수정 후 Tool도 다시 발행하고 재실행합니다. JSON 테이블이나 Tool 입력 연결을 고칠 오류는 아닙니다.

configure.py는 이전 참조 export의 0.1도 시연 기본값 1로 정규화합니다. 모델별 다른 파라미터 호환성은 실제 endpoint 응답에 맞춰 확인해야 합니다.

### ‘지원되지 않는 작업 / 설치된 플러그인 버전은 이 작업을 제공하지 않습니다’

2026-10-18 사용자가 메인 Chatflow 연결 중 이 오류를 보고했고, 문제가 된 항목을 삭제하고 직접 추가하여 연결되었다고 확인했습니다. 서버 화면·설치 plugin 목록·실제 export는 확인하지 못했으므로 정확한 원인은 확정하지 않았습니다. 같은 증상이면 아래 재선택 절차를 우선 확인합니다.

오류 조사 당시 Tool provider_name은 연결 전 자리표시자였으나 현재 02에는 사용자 실제 ID를 반영했습니다. Agent 전략은 `langgenius/agent/agent`의 `function_calling`입니다. Dify 1.15 서버 버전과 설치된 Agent strategy plugin 버전은 별개입니다.

1. 오류가 Agent 전략 영역이면 전략 선택창에서 실제 설치된 공식 Function Calling 전략을 다시 선택합니다. 목록에 없으면 plugin 관리에서 Dify Agent Strategies의 설치·제공 전략을 확인합니다. 업데이트 필요 여부는 현재 설치 버전을 본 뒤 판단합니다.
2. 오류가 smartmigrate_execute Tool 항목이면 자리표시자 Tool을 제거하고, 같은 워크스페이스에서 발행한 Workflow Tool을 목록에서 다시 선택합니다. 일반 plugin 도구로 등록하는 방식이 아닙니다.
3. Tool이 목록에 없으면 01 Workflow가 단순 앱 발행뿐 아니라 Tool 발행까지 완료되었는지 확인합니다.
4. 가져온 Agent에서 전략을 바꿀 수 없으면 빈 테스트 Chatflow에 새 기본 Agent를 추가해 설치된 전략과 Tool을 선택해 봅니다. 여기서 연결되면 export 후 3절 configure.py로 실제 식별자를 반영합니다. 운영 중인 기존 앱을 삭제할 필요는 없습니다.
5. 다시 연결한 Tool의 JSON/session_json/request_id 수동 변수 참조도 복구합니다.

확인할 정보는 오류가 표시되는 영역(Agent 전략 또는 Tool), 그 영역의 이름, 설치된 plugin 이름·버전입니다. API key와 DB 비밀번호는 필요하지 않습니다.

공식 근거: [1.15.0의 전략 이름 매칭](https://github.com/langgenius/dify/blob/1.15.0/web/app/components/workflow/nodes/agent/use-config.ts), [공식 Agent 전략 선언](https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/provider/agent.yaml).

| 현상 | 확인할 항목 |
|---|---|
| Tool을 찾지 못함 | Workflow 발행 여부, provider ID, 이름이 smartmigrate_execute인지 |
| 상태가 반영되지 않음 | Agent json → commit 연결, 테이블별 JSON/session_json/request_id 수동 연결, Variable Assigner |
| NO_RESULT | 해당 Agent 전략의 json 로그에 CALL/ROUND의 tool_response가 있는지. 지원하지 않는 로그 형식에서는 상태를 유지하고 변경 성공으로 처리하지 않음 |
| CONFLICT | 한 턴에 다른 결과를 내는 Tool 호출이 여러 번 있었음. 한 요청씩 실행 |
| JSON 파싱 오류 | planner 모델의 출력 한도·프롬프트 확인. SQL 한 건씩 실행하여 출력 크기 축소 |
| 튜닝/포매팅 선행 단계 오류 | 변환 → 튜닝 → 포매팅 순서 준수. 전체 실행은 full 사용 |
| 매핑 변경 후 SQL이 STALE | 변경 전 산출물은 이력이며 새 규칙으로 변환부터 다시 실행 |

DSL은 Dify 1.15.0 공식 소스의 0.6.0 형식으로 생성합니다. 실제 서버의 패치 버전·Agent strategy plugin·등록 모델 실행은 아직 확인하지 않았습니다. 가이드의 노드·입출력 계약을 기준으로 수동 구성할 수 있습니다.
