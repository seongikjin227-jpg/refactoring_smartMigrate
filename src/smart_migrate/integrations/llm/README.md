# LLM Integration

LLM client, model fallback, prompt loading을 담당합니다.

## 호출 구조

```text
agent LLM service
  -> PromptLoader.build_prompt_messages()
  -> LlmClient.chat() 또는 provider별 client
  -> LlmFallback.model_candidates()
```

## 주요 파일

- `LlmClient.py`: OpenAI-compatible client 생성과 chat 호출 wrapper입니다.
- `LlmFallback.py`: fallback model 후보 생성, 현재 active model 저장, fallback 가능 오류 판정을 담당합니다.
- `PromptLoader.py`: `config/prompts/*.json` 파일을 로드하고 입력값을 렌더링해 message list를 만듭니다.

Supervisor의 route 결정은 `SupervisorGraph._build_llm()`에서 직접 `ChatOpenAI`를 만들지만, fallback 판단은 이 패키지의 `LlmFallback`을 사용합니다.
