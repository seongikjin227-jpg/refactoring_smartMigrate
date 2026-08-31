# Loop Output Test Plan - 260831
## 3. Loop 중간 출력 실험 방향

### 결론부터

`sleep()`을 메인 스레드에 넣는 것만으로는 서비스 응답이 중간중간 push될 가능성이 낮다. 플랫폼 연동이 Langflow 실행 API의 최종 응답만 읽는다면, `sleep()`은 마지막 응답을 늦출 뿐이다.

### 테스트할 방식

| 방법 | 목적 | 기대 결과 | 한계 |
|---|---|---|---|
| A. `sleep()` interval만 적용 | Chat Output이 시간 차를 두면 서비스가 중간 응답을 잡는지 확인 | Langflow UI에서는 천천히 보임 | REST 최종 응답만 읽으면 마지막만 보임 |
| B. 누적 transcript 출력 | 매 iteration 출력이 전체 진행 로그를 포함하게 함 | 서비스가 마지막만 받아도 전체 과정 확인 가능 | 실시간은 아니고 최종 누적 표시 |
| C. side-channel progress sink | DB/파일/API에 progress event를 매 job 저장 | 서비스가 polling/SSE로 실시간 표시 가능 | 플랫폼 쪽 progress 조회 API 필요 |
| D. Langflow streaming/event API 사용 | 실행 이벤트 자체를 클라이언트가 구독 | 진짜 스트리밍 가능 | 서비스 연동 코드 변경 필요 |

PoC 이후 운영형으로는 C 또는 D가 맞다. B는 지금 구조에서 가장 적은 변경으로 “중간 과정이 최종 메시지에 모두 남는지” 확인하는 안전한 테스트다.

## 4. 새 파일 구성

| 파일 | 용도 |
|---|---|
| `01_final_status_audit.py` | INFO/LOG 최종 상태 기준으로 성공/실패/스킵을 감사하는 Langflow 컴포넌트 |
| `02_loop_progress_output_adapter.py` | 18D 뒤에 붙여 중간 진행 메시지를 누적하고 optional sleep을 적용하는 Langflow 컴포넌트 |
| `03_progress_event_sink.py` | job마다 progress event를 JSONL 파일에 저장해서 외부 서비스 polling 테스트에 쓰는 컴포넌트 |

## 5. 권장 연결 실험

### 실험 1: 최종 메시지 누적 방식

기존:

```text
18D -> Chat Output
18D -> 18B Loop Result
```

테스트:

```text
18D.Message -> 02 Adapter.payload
18D.Loop Result -> 02 Adapter.loop_result_input
02 Adapter.Message -> Chat Output
02 Adapter.Loop Result -> 18B
```

`02 Adapter`에서 `emit_mode=ACCUMULATED`, `sleep_seconds=0.5~1.0`으로 시작한다.

### 실험 2: side-channel progress 방식

```text
18D.Loop Result -> 03 Progress Event Sink -> 18B
```

서비스는 `progress_events.jsonl` 또는 같은 구조의 DB/API sink를 polling한다. 운영 전환 시 파일 대신 `NEXT_WORKFLOW_PROGRESS` 같은 테이블을 권장한다.

## 6. 확인 질문

1. 서비스에서 Langflow를 호출하는 방식이 REST `run` 최종 응답인지, streaming/event endpoint인지 확인이 필요하다.
2. 플랫폼이 한 채팅 말풍선 안에서 partial update를 지원하는지, 아니면 새 메시지 append만 지원하는지 확인이 필요하다.
3. progress side-channel을 DB 테이블로 둘지, 서비스 메모리/API로 둘지 결정해야 한다.
4. 11B는 실패 로그 분석 전용으로 유지할지, 성공/실패 최종 감사까지 포함할지 결정해야 한다.
