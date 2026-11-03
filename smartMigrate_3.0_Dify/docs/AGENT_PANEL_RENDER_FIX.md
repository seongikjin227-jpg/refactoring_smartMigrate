# Agent 클릭 시 $.create 오류

Console의 editor.main.js → createModel → `TypeError: $.create is not a function`은 이번 생성 DSL에서 편집기로 실제 배열을 전달한 오류와 일치합니다.

02 Agent의 오류 처리 기본값을 수정했습니다.

```yaml
error_strategy: default-value
default_value:
  - key: json
    type: array[object]
    value: '[]'
```

`value: []`는 실제 배열이고 `value: '[]'`는 편집기에서 사용할 JSON 텍스트입니다. 생성기에서 배열을 넣었던 것을 문자열로 바로잡았습니다. 업무 테이블이나 session_json의 타입을 바꾸라는 뜻이 아닙니다.

공식 Dify 1.15 [오류 기본값 폼](https://github.com/langgenius/dify/blob/1.15.0/web/app/components/workflow/nodes/_base/components/error-handle/default-value.tsx)은 form.value를 그대로 CodeEditor에 전달합니다. [CodeEditor](https://github.com/langgenius/dify/blob/1.15.0/web/app/components/workflow/nodes/_base/components/editor/code-editor/index.tsx)는 해당 경로에서 배열을 문자열로 변환하지 않습니다. [기본값 생성 유틸리티](https://github.com/langgenius/dify/blob/1.15.0/web/app/components/workflow/nodes/_base/components/error-handle/utils.ts)는 배열 기본값에 문자열 '[]'를 사용합니다.

수정된 dsl/02_smartmigrate_chatflow.yml을 가져와 Agent 패널을 확인합니다. 기존 앱을 로컬에서 직접 수정할 수 있다면 위 값만 고쳐도 됩니다. **01 재발행, Tool 재등록, 모델 변경은 필요 없습니다.**

로컬 40개 테스트 통과. 실제 서버의 패널 복구 여부는 아직 미확인입니다. 반복되는 WebSocket localhost:5001 연결 거부는 별도 서버 연결 설정 문제이며 이 DSL 변경으로 해결했다고 주장하지 않습니다.
