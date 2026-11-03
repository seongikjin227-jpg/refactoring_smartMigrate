# 폐기한 구조: 요청 접수 전용 Tool

사용자 요구와 달리 Tool 실행 책임을 02로 옮겼던 구조입니다. 현재 적용하면 안 됩니다. [실행 Tool·입력 자동 구성](AUTO_INPUT_SETUP.md)을 따릅니다. 이하 내용은 변경 이력입니다.

이 가이드가 이전 session_json·V3 연결 안내보다 우선합니다. Tool 이름은 **smart_migrate_tool**, 표시 이름은 **SmartMigrate 통합 실행**입니다.

## 바뀐 구조

- **01 Workflow Tool:** Start → 요청 접수 Code → End. 입력은 request 문자열 하나이고 자동 ON입니다. 반환은 요청 접수 결과이며 이관 완료 결과가 아닙니다.
- **02 Chatflow:** 상태 준비 → Agent/Tool 호출 → 접수 검증 → 현재 상태로 작업 계획 → Code 실행·검증 → 상태 확정·저장 → AI 해설·표 출력.
- 테이블 JSON·요청 ID는 02 Code 노드 사이의 일반 변수로 전달합니다. Agent Tool의 수동 입력·base64·session_json 전달은 사용하지 않습니다.
- 매핑/SQL 관리, 이관 upsert, MIG_SQL·VERIFY_SQL·VERIFY2_SQL 생성, COUNT/PK CONCAT 비교, 대화 상태 저장은 02에서 계속 수행합니다.

## 기존 Tool ID를 유지하며 01을 바꾸기

현재 로컬 02에 저장된 Tool ID는 `7c7dee2e-7081-4e6a-8e63-87565b76fe89`입니다. 기존 등록을 유지하려면 아래처럼 **기존 01 앱**을 편집합니다.

1. Start의 입력에서 **request만 남기고**, session_json·request_id·테이블 JSON 9개를 제거합니다.
2. 기존 준비 Code와 계획 LLM 노드를 제거합니다. 실행 Code 노드는 남깁니다.
3. 실행 Code의 코드 전체를 [01_request_tool_code.py](../deployment/01_request_tool_code.py) 내용으로 교체합니다.
4. 실행 Code의 입력 변수도 **request 하나**로 바꿉니다. 타입은 string, 값은 Start.request입니다. 출력 변수는 result_json/string 하나를 유지합니다.
5. **Start → 실행 Code → End**로 연결합니다. End.result_json은 실행 Code.result_json을 가리켜야 합니다.
6. 기존 Workflow를 발행 업데이트한 뒤 **Tool 발행 설정의 입력 목록도 request 하나로 갱신**합니다. Tool 이름은 smart_migrate_tool을 유지합니다.

발행 설정에 입력 12개가 계속 보이면 아직 이전 Tool 규약입니다. 모든 필드를 자동으로 켜는 것만으로는 새 버전이 되지 않습니다.

새 앱으로 시작한다면 [01 DSL](../dsl/01_smartmigrate_tool.yml)을 가져와 Tool로 등록할 수도 있습니다. 이 경우 Tool ID가 바뀔 수 있으므로 새 ID를 02에 반영해야 합니다. 기존 이름과 중복 등록할 수 없는 경우 기존 등록을 무조건 삭제하지 말고 기존 앱 편집 경로를 사용합니다.

## 02 가져오기

[02 DSL](../dsl/02_smartmigrate_chatflow.yml)을 새 Chatflow로 가져옵니다. Agent 전략은 Function Calling, 등록 모델의 Function Call Type은 Tool Call입니다. Agent와 계획/해설 LLM은 등록된 gpt-5.5를 사용합니다. 최대 토큰은 허용 상한 4096, temperature는 1입니다.

도구 설정의 **추론 구성에 request 하나만 표시되고 자동이 켜져 있으면 정상**입니다. session_json이나 테이블 입력을 수동으로 다시 연결하지 않습니다.

Tool을 UI에서 다시 선택해도 request만 자동 생성하면 됩니다. Tool ID가 바뀌었다면 아래 스크립트에 실제 ID를 넣어 02를 갱신합니다.

```powershell
python smartMigrate_3.0_Dify/scripts/bind_workspace.py --tool-provider-id 실제_TOOL_UUID --tool-name smart_migrate_tool --model-provider langgenius/openai_api_compatible/openai_api_compatible --model gpt-5.5
```

## 실행 확인

1. 매핑룰 조회 → 마스터 2건/상세 10건 확인.
2. 이관 진행 → 부서 3건/직원 10건 처리, COUNT와 CONCAT 비교 확인.
3. 같은 대화에서 매핑 상태 변경 → 다음 조회에 반영되는지 확인.
4. SQL 결과 조회 → 변환/튜닝/서식 또는 이관 SQL을 문맥에 맞게 조회.

Tool 직접 미리보기는 접수 결과만 반환합니다. 실제 작업 결과는 **02를 실행해서 확인**합니다. 접수 기록이 없거나 구버전 결과가 반환되면 실행을 막습니다. Agent가 요청을 바꿔 보내더라도 실제 계획은 sys.query 원문과 02의 현재 상태를 사용합니다.

기존 대화 상태는 같은 앱/대화에서 유지됩니다. 새 Chatflow의 새 대화는 샘플로 시작하므로 기존 앱은 정상 확인까지 보관합니다.

로컬 42개 테스트가 통과했습니다. 실제 Dify 서버 import·모델 호출은 아직 미검증입니다. 새 str.get 오류가 남으면 이번 실행의 실패 노드와 Traceback으로 발생 위치를 확인해야 합니다.
