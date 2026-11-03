# SQL 대상 조회·일반 대화와 현재 SQL 호출 진단

## 현재 사용자 환경에서 확인한 사실

- 사용자 모델 설정은 Tool Call이며, **Stream function calling ON에서 매핑룰 조회가 정상 동작**합니다. 이 동작하는 설정은 유지합니다. 전체 Function Calling이 불가능하다고 단정하지 않습니다.
- 같은 설정의 SQL 요청에서는 Tool 결과가 없거나 빈 모델 응답 진단이 나왔습니다. 첨부 텍스트는 Agent 입력이며 SQL 요청의 실제 출력 json 배열은 추가 대조 대상입니다.
- 첨부 session_json을 복원하면 NEXT_SQL_INFO에 두 테이블 조인·두 바인드·(+)/PK IN 구조를 가진 SQL_SEQ=1이 있습니다. 원문 요청도 정상 공급되고 있습니다. 대상 SQL 누락으로 설명할 수 없습니다.
- request=null은 Agent 입력에서 모델이 자동 생성할 슬롯입니다. 이것만으로 실제 호출 실패를 판정하지 않습니다.
- Stream function calling OFF에서 새 오류 `unmarshal json failed: content field is required`가 관찰됐습니다. 모델 제공자는 OpenAI-API-compatible 0.0.16입니다.

공식 [PR #2912](https://github.com/langgenius/dify-official-plugins/pull/2912)는 제공자 0.0.44에서 LiteLLM Tool Call의 누락/null content를 빈 문자열로 정규화하는 수정을 명시합니다. [daemon #640](https://github.com/langgenius/dify-plugin-daemon/issues/640)도 같은 unmarshal 문구를 보고합니다. 호환되는 제공자 업데이트는 점검 대상이지만, 이것이 현재 스트리밍 ON의 SQL 요청 실패 원인과 동일하다고 검증한 것은 아닙니다. 이미지 버전을 모른 채 daemon을 임의로 다운그레이드하지 않습니다.

## SQL 변환 대상 조회

다음 요청은 NEXT_SQL_INFO 조회입니다.

```
SQL 변환 대상이 있는지 먼저 조회해줘
SQL 변환 대상 조회해줘
AS-IS SQL 원본 보여줘
```

Tool execute는 이 표현을 `query / NEXT_SQL_INFO / query_view=sql_source`로 처리합니다. Planner가 실행·명확화 계획을 잘못 만들더라도 이 단독 조회를 변환·이관으로 실행하지 않습니다. 출력은 대상 건수, SQL_SEQ·SQL_ID·TARGET_TABLE·BIND_SET, 원본 FR_SQL 코드입니다. TO_SQL·튜닝 결과는 보여주지 않습니다. 업무 데이터는 유지하며 조회 로그·revision만 기록합니다.

Agent가 Tool을 전혀 호출하지 않아 `tool_responses=[]`가 된 경우에도, 메인 commit Code가 이 **단독 읽기 요청에 한해서** 현재 대화의 JSON 상태에서 같은 FR_SQL 조회 화면을 만듭니다. 이 경로의 `commit_status=READ_ONLY`는 Tool 성공을 뜻하지 않으며, 답변에 Tool 호출이 없었다고 표시합니다. 상태·revision·로그는 모두 유지합니다. Tool 응답을 받았지만 결과가 잘못된 경우, SQL 변환·이관·튜닝 요청에는 이 우회 경로를 적용하지 않습니다. 실제 Dify 서버에서의 동작은 새 DSL을 반영한 뒤 확인해야 합니다.

실행 요청 `SQL 변환 실행해줘`는 별개입니다. 저장된 FR_SQL·매핑을 Tool에서 사용하고 TO_SQL을 생성합니다. Agent 지침에도 두 문장의 차이와 현재 등록된 SQL을 명시했습니다.

첨부 JSON 상태를 내보낸 execute·commit Code로 로컬 재생한 출력은 [sql_source_lookup_replay.md](../samples/results/sql_source_lookup_replay.md)에 있습니다. SQL 한 개 조회·FR_SQL 표시·SQL 작업 행 보존을 확인했습니다. 이는 실제 Dify 모델 호출 성공의 증거가 아닙니다.

## 인사·소개·기능 질문

`안녕`, `어떤 에이전트야?`, `어떤 기능이 있어?`, `뭘 할 수 있어?`는 CHAT입니다. Tool이 필요하지 않으며 Tool 결과 없음 오류·상태 변경 없음 같은 업무 진단을 붙이지 않습니다.

commit은 sys.query를 받아 CHAT을 구분하고, render Code가 일반 답변만 최종 출력합니다. 같은 문장에 `매핑룰 조회해줘` 등 실제 업무 요청이 있으면 CHAT으로 넘기지 않습니다. 업무 요청의 Tool 결과 검사는 유지합니다.

## Dify 반영과 점검

1. 새 `01_smartmigrate_tool.yml`을 반영하고 Tool을 재발행합니다. 원본 조회 코드와 query_view가 추가됐습니다.
2. 새 `02_smartmigrate_chatflow.yml`을 반영합니다. commit의 `user_request ← sys.query`와 narrator 뒤의 `render` Code가 추가됐습니다. 메인 노드는 8개, 내부 Tool 노드는 5개입니다.
3. Agent는 Function Calling, 모델은 Tool Call·**Stream function calling ON**을 유지합니다. ReAct 전환은 하지 않습니다.
4. 인사, SQL 변환 대상 조회, SQL 변환 실행을 서로 다른 요청으로 테스트합니다.

SQL 실행 실패는 Agent 출력의 CALL을 기준으로 나눕니다. CALL이 없으면 모델 선택/스트리밍 응답 경로를 봅니다. CALL이 있으면 Tool 내부 planner·execute의 원본 오류를 봅니다. commit의 일반적인 NO_RESULT 안내만으로 두 경우를 혼동하지 않습니다. 서버 모델을 호출할 권한·연결이 없어 실제 복구 성공을 로컬에서 단정하지 않습니다. Langflow 파일은 수정하지 않았습니다.
