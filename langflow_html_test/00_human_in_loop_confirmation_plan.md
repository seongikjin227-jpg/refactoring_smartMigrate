# Human In The Loop Confirmation Plan

SmartMigrate Full Workflow의 per-job HITL 실험 설계 문서입니다.

## 목표

18B Full Workflow Loop가 내보내는 각 `item`을 실제 executor로 넘기기 전에 사람에게 현재 진행 상황과 이번 job 정보를 보여주고 승인받습니다.

```text
18B Full Workflow Loop.item
  -> 20A Full Workflow HITL Prompt.payload_json

20A Prompt Message
  -> 20B Full Workflow Human Input.prompt_message

20A Job Item
  -> 20B Full Workflow Human Input.job_item

20B Approve
  -> 10C MIG One Job POC Executor.job_item

20B Fallback
  -> 10C MIG One Job POC Executor.job_item

20B Reject
  -> manual review or isolated rejection handling
```

`08H` and `08R` are not part of this experiment.

## 공식 Human Input 기준

Langflow Human Input은 flow를 pause하고, 사용자가 선택한 action branch만 계속 실행합니다. fallback을 켜면 timeout 이후 fallback branch가 추가되며, 선택되지 않은 branch는 `stop(...)`으로 중단합니다.

Reference:

- https://docs.langflow.org/next/human-input
- https://github.com/langflow-ai/langflow/blob/main/src/lfx/src/lfx/components/flow_controls/human_input.py

## 20A 역할

`20A_fullWorkflowHitlPrompt.py`

- 18B loop `item` payload를 입력으로 받습니다.
- 18D dashboard 스타일의 진행상황을 실행 전 기준으로 정리합니다.
- "이번에 진행할 Job은 ... 입니다. 진행하시겠습니까?" 메시지를 만듭니다.
- 원본 job item은 `Data` output으로 그대로 전달합니다.
- payload를 메시지 안에 숨겨 넣지 않습니다.

## 20B 역할

`20B_fullWorkflowHumanInput.py`

- 공식 Human Input 형태를 따릅니다.
- `Approve`, `Reject`, `Fallback` branch를 가집니다.
- 기본 timeout은 30 seconds입니다.
- fallback은 자동 승인으로 간주하고 `APPROVED_BY_TIMEOUT` 상태로 job item을 통과시킵니다.
- approve/fallback branch만 executor로 연결합니다.
- reject branch는 executor와 연결하지 않습니다.
- pause 중에는 모든 branch를 `stop(...)`해서 빈 payload가 downstream으로 퍼지지 않게 합니다.
- resume 후에는 선택되지 않은 branch를 `stop(...)`합니다.

## 중요 규칙

18B item을 10C로 직접 연결하지 않습니다.

허용 경로는 다음뿐입니다.

```text
18B item -> 20A -> 20B Approve/Fallback -> 10C
```

이 구조가 유지되어야 사람이 승인하기 전에 job item이 executor로 전파되지 않습니다.
