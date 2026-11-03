# 실행 Tool과 사전 연결된 상태 입력 — 렌더링 수정본

**Agent 클릭 시 editor.main.js / $.create 오류의 실제 수정은 [Agent 패널 렌더링 수정](AGENT_PANEL_RENDER_FIX.md)을 참고합니다.** 02 오류 기본값 json의 실제 배열 []를 문자열 '[]'로 수정했으며 01 재발행은 필요 없습니다. 아래 Tool 목록 변경이 이번 Monaco 오류의 직접 원인이라는 이전 추정은 정정했습니다. 최신 로컬 검증은 40개 테스트입니다.

이 문서가 이전 요청 접수 전용 구조·수동 연결 안내보다 우선합니다.

- **01:** Start → 상태 준비 Code → 작업 계획 LLM → 실행·검증 Code → End. 조회·관리·이관·SQL 변환·튜닝·서식을 실제 처리합니다.
- **02:** 상태 준비 → Agent → Tool 결과 검증 → 대화 상태 저장 → 해설 → Answer.
- Tool 이름: **smart_migrate_tool**, 공개 입력: **request, session_json**.

## 자동 구성

Dify의 추론 구성 ‘자동’은 LLM이 인자를 작성하게 하는 옵션입니다. 테이블 전체를 LLM이 재작성하는 방법은 쓰지 않습니다.

Agent의 agent_parameters.tools는 **type: constant, value: [Tool 객체]**인 표준 목록입니다. request는 auto=1, session_json은 auto=0이며 값은 DSL에 `{{#initialize.session_transport#}}`로 사전 연결돼 있습니다. initialize는 매 턴 현재 테이블과 요청 ID를 SM_STATE_V3 전달 문자열로 만듭니다. 01은 이 문자열을 복원하여 실제 작업을 실행합니다. 사용자가 수동으로 JSON을 붙여넣거나 변수를 고를 필요는 없습니다.

**모든 추론 스위치 ON을 지원하는 방식은 아닙니다.** session_json 자동을 켜면 LLM이 상태를 작성하게 되므로 정확한 전달을 보장할 수 없습니다. Tool을 삭제 후 다시 추가하거나 전략을 재선택하면 사전 연결이 초기화될 수 있습니다. 가져온 DSL의 기존 목록을 유지합니다. 이 플랫폼 제한을 Tool 역할 변경으로 숨기지 않습니다.

변수 tools 목록 방식은 02 렌더링 오류 보고 후 폐기했습니다. 공식 [1.15 use-config](https://github.com/langgenius/dify/blob/1.15.0/web/app/components/workflow/nodes/agent/use-config.ts)는 폼에 value를 직접 전달하며 tools 변수 참조를 해석하지 않습니다. 실행 엔진의 variable 지원만으로 편집기 호환성을 판단했던 것이 부족했습니다. 새 목록은 Tool 객체 배열이고, V3는 JSON 따옴표/줄바꿈이 없는 ASCII라 전체 배열의 템플릿 치환 후 JSON 재파싱을 보존합니다. 서버 브라우저 스택 없이 렌더링 오류의 정확한 발생 줄까지 확정하지는 않습니다.

## 적용

1. 01_smartmigrate_tool.yml을 Workflow로 가져와 모델을 확인하고 발행합니다. 기존 앱/등록을 갱신할 수 있으면 Tool ID를 유지합니다. 새 Tool 등록은 ID가 바뀔 수 있습니다.
2. **도구로서 발행/등록 설정도 갱신**합니다. 입력은 request와 session_json이어야 합니다. 단순 Workflow 발행만으로 Tool 등록이 완료되는 것은 아닙니다.
3. 기존 Tool 이름과 ID를 유지했으면 현재 02를 가져옵니다. ID가 바뀌면 아래 스크립트로 02를 갱신한 뒤 가져옵니다.
4. 02는 표준 Tool 목록과 상태 연결을 이미 포함합니다. 목록을 다시 추가하거나 테이블별 변수를 수동 연결하지 않습니다. request 자동 ON, session_json 자동 OFF와 session_transport 연결을 유지합니다.
5. 매핑 조회 → 이관 → 매핑 수정 → 재조회로 상태 유지와 표를 확인한 뒤 02를 발행합니다. 기존 앱은 검증이 끝날 때까지 보관합니다.

```powershell
python smartMigrate_3.0_Dify/scripts/bind_workspace.py --tool-provider-id 실제_TOOL_UUID --tool-name smart_migrate_tool --model-provider langgenius/openai_api_compatible/openai_api_compatible --model gpt-5.5
```

현재 로컬 ID: e3baab53-054c-4302-8d05-b2058d2fcd4b. 이전 7c7dee2e ID는 변경 이력입니다. 스크립트는 Dify 서버를 변경하지 않습니다. Agent 전략은 Function Calling, 모델 Function Call Type은 Tool Call입니다. temperature=1, max_tokens≤4096입니다.

Agent 로그에서 session_json:null이고 01이 빈 세션 오류로 실패한다면, 02 Agent → 도구 목록 → SmartMigrate 통합 실행 → 추론 구성에서 session_json 자동을 끄고 **대화 상태 준비.session_transport** 변수를 선택합니다. request는 자동 ON을 유지합니다. 기존 서버에서 이 설정 한 곳만 복원하면 되며 01 재발행이나 Tool 재등록은 필요 없습니다. 정상 Agent 입력 session_json은 SM_STATE_V3:로 시작합니다.

01 직접 테스트에도 현재 상태 session_json이 필요합니다. 빈 값을 샘플로 대체하지 않습니다. 02 샘플 이관은 부서 3건/직원 10건, MIG_SQL·VERIFY_SQL·VERIFY2_SQL 및 COUNT/CONCAT 결과를 출력합니다.

로컬 **39개 테스트 통과**. SQL 생성 LLM은 고정 산출물로 대체합니다. 정적 목록의 객체 구조, 특수문자를 포함한 상태의 템플릿/JSON 왕복, 01 실제 실행·다음 턴 보존을 확인했습니다. 실제 Dify 서버의 브라우저 렌더링·모델 호출은 미검증입니다. 현재 01이 이미 request/session_json을 받아 실제 실행하는 최신 버전이면 이번 수정은 **02만 교체**하면 됩니다.
