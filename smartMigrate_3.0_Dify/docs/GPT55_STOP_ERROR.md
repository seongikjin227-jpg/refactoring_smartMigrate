# ReAct → Azure/LiteLLM stop 오류

사용자 첨부 입력에는 temperature=1, max_tokens=4096이 있고 stop은 없습니다. 그러나 오류는 `azure does not support parameters: ['stop'], for model=gpt-5.5`입니다. 이는 매핑·SQL·session_json 파싱 전에 모델 API가 400으로 거부한 요청입니다. query가 인사말이어도 발생할 수 있습니다.

Dify 공식 ReAct 전략은 설정된 stop이 없어도 내부에서 `stop.append("Observation")`을 수행하고 모델을 호출합니다. 따라서 Dify 입력 화면에서 stop 항목만 지워서는 해결되지 않습니다. ReAct로 바꾼 이전 로컬 수정이 이번 경로와 호환되지 않았습니다.

근거: [공식 ReAct 구현](https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/ReAct.py), [LiteLLM 지원하지 않는 파라미터 처리](https://docs.litellm.ai/docs/completion/drop_params).

## 현재 로컬 수정

- 기본 02 Chatflow는 Function Calling, maximum_iterations=1입니다. ReAct의 Observation stop을 추가하는 경로를 사용하지 않습니다.
- Agent 프롬프트의 ReAct Action 텍스트 예시를 제거하고 실제 함수 호출을 지시합니다.
- build/configure/bind_workspace는 이 데모의 Function Calling 설정을 유지하고 가져온 stop 파라미터를 제외합니다.
- 현재 등록 모델·Tool ID·샘플·RAG 규칙은 유지합니다. Langflow는 수정하지 않았습니다.

## Dify 화면에서 바로 반영

1. Agent 전략을 **Function Calling**, 최대 반복을 **1**로 바꿉니다.
2. 모델 파라미터에 별도 Stop 설정이 있다면 제거합니다. temperature=1은 유지합니다.
3. Agent 지침에서 ReAct Action JSON을 텍스트로 쓰라는 문장을 지웁니다. 가장 확실한 방법은 최신 `dsl/02_smartmigrate_chatflow.yml`을 반영하는 것입니다. 이 stop 수정만 적용할 때 01 Workflow 변경·재발행은 필요 없습니다.
4. 새 실행에서 `매핑룰 조회해줘`를 요청합니다. 모델 API 400이 사라졌는지 먼저 보고, 실제 CALL과 result_json이 있는지 구분합니다.

이 수정은 확인된 stop 400에 대응합니다. 이전 Function Calling의 빈 응답까지 해결했다고 단정하지 않습니다. 400이 사라진 뒤에도 `tool_responses=[]`이고 모델 텍스트가 비어 있으면, 모델/프록시의 native tool_calls 전달·스트리밍을 별도로 점검해야 합니다. 로컬 테스트는 실제 서버 모델을 호출하지 않습니다.

## ReAct를 유지하려는 경우

LiteLLM 프록시 관리자가 해당 모델 항목에 `drop_params: true` 또는 `additional_drop_params: ["stop"]`을 적용하는 방법이 있습니다. 전역 예시는 다음과 같습니다.

```yaml
litellm_settings:
  drop_params: true
```

이 설정은 프록시에서 지원하지 않는 파라미터를 버리는 것이며 모델이 stop을 지원하게 만드는 것은 아닙니다. 가능하면 기존 모델 항목에 한정해 적용합니다. 실제 프록시 설정은 이번 작업에서 변경하지 않았습니다. 단순히 allowed_openai_params에 stop을 추가하는 것은 미지원 파라미터를 제거하는 조치가 아닙니다.
