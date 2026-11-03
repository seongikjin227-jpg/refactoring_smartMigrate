# Dify DSL 가져오기 — 실제 실행 Tool과 입력 자동 구성

현재 구조는 **Agent + 실제 통합 실행 Workflow Tool**입니다. 현재 상태 연결을 DSL에 미리 넣었습니다. 최신 절차는 [입력 연결 가이드](docs/AUTO_INPUT_SETUP.md)입니다. 02의 변수 Tool 목록을 폐기하고 표준 고정 목록으로 수정했습니다.

| 파일 | 역할 |
|---|---|
| [01_smartmigrate_tool.yml](dsl/01_smartmigrate_tool.yml) | 현재 테이블로 계획·이관·SQL 처리·관리·검증 후 변경된 상태 반환 |
| [02_smartmigrate_chatflow.yml](dsl/02_smartmigrate_chatflow.yml) | 상태 입력 사전 연결, Agent 호출, 결과 검증·저장, AI 해설·표 |

## 기존 서버 업데이트

**[입력 자동 구성 가이드](docs/AUTO_INPUT_SETUP.md)를 따릅니다.** 01의 계획·실행 기능을 복원했고 공개 입력은 request와 session_json 두 개입니다. Workflow와 도구로서 발행 설정을 모두 갱신합니다. Tool 이름은 **smart_migrate_tool**입니다.

이후 수정된 02를 가져옵니다. 현재 02는 `dsl/workspace_binding.json`에 저장된 Tool ID `e3baab53-054c-4302-8d05-b2058d2fcd4b`, 모델 gpt-5.5, 공식 Agent 0.0.18 연결을 유지했습니다. 다른 ID를 등록했다면 bind_workspace.py 또는 configure.py로 02를 갱신합니다.

02 Agent tools는 **constant/Tool 객체 배열**입니다. request 자동 ON, session_json 자동 OFF이며 initialize.session_transport 연결이 이미 들어 있습니다. 목록을 삭제 후 다시 추가하면 연결이 초기화될 수 있으므로 가져온 설정을 유지합니다. 모든 스위치 ON에서는 정확한 상태 전달을 보장할 수 없습니다.

일반 앱 발행과 **도구로서 Workflow 등록**은 별도입니다. 최초 등록은 필요하지만 매번 새로 등록할 필요는 없습니다. 기존 Tool을 갱신하면 이름/ID를 유지할 수 있습니다.

## 새 서버에 처음 가져오기

1. 01을 Workflow로 가져오고 Tool로 등록합니다. 입력은 request/string, session_json/string입니다.
2. 02에 실제 Tool ID와 등록 모델을 반영한 뒤 가져옵니다.
3. Agent 전략은 Function Calling, 모델의 Function Call Type은 Tool Call을 사용합니다.
4. 02에서 매핑 조회·이관·관리 작업을 확인합니다. 01 직접 실행도 현재 상태 session_json을 받아 실제 작업을 수행합니다.

## 샘플 데이터와 검증

테이블 원본은 [samples/tables](samples/TABLE_INPUTS.md)에 있으며, 메인 Chatflow의 sample_json과 테이블별 시작 입력에 샘플 기본값을 넣었습니다. 같은 대화에서는 갱신된 상태를 사용합니다.

이관의 MIG_SQL·VERIFY_SQL·VERIFY2_SQL 생성과 JSON COUNT/PK CONCAT 비교는 01 Tool에서 수행합니다. Oracle DB에서 SQL을 실행하지 않습니다. 표는 Code가 생성하고 별도 LLM이 설명과 다음 작업을 추천합니다.

이전 str.get 수정 이력은 [REFACTORING.md](REFACTORING.md)에 남겼습니다. 현재 계약은 [CONTRACT.md](docs/CONTRACT.md)를 봅니다. 로컬 39개 테스트 통과, 실제 서버 적용은 미검증입니다.
