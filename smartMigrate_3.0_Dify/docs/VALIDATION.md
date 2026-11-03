# 검증 기록

2026-10-25 SQL 변환 대상 조회 보강: Tool 내부의 `query_view=sql_source` 기능은 기존부터 있었으나, 보고된 `tool_responses=[]`에서는 Agent가 Tool을 호출하지 않아 진입할 수 없었습니다. 메인 commit Code에 해당 단독 읽기 요청의 현재 JSON 상태 조회 경로를 추가했습니다. 이때 `READ_ONLY`로 표시하고 Tool 호출이 없었다는 사실을 답변에 남깁니다. 변환·이관·튜닝 실행과 잘못된 Tool 응답에는 적용하지 않습니다. 내보낸 DSL Code를 포함한 79개 로컬 테스트가 통과했습니다. 실제 Dify 서버 적용·모델 호출은 확인하지 않았습니다. [상세](LOOKUP_AND_CHAT.md).

2026-10-25 Conversion 프롬프트·문맥 분리: TO_SQL 생성의 매핑 전용 지침을 강화했습니다. 단독 Conversion에 SQL_CONVERSION 규칙과 FR_SQL만 전달하고, 튜닝 규칙·과거 TO_SQL/TUNED_TO_SQL은 LLM 문맥에서 제외합니다. 단독 Tuning에는 SQL_TUNING 규칙과 저장 TO_SQL을 전달합니다. 원래 상태 보존·조회 및 복합 요청 문맥 유지 검사를 추가해 77개 로컬 테스트를 통과했습니다. 구조 변경을 거부하는 실행 Code 검사는 추가하지 않았으며 실제 모델의 생성 결과는 Dify에서 재확인이 필요합니다.

2026-10-25 조회·일반 대화 보완: **74개 테스트 통과**. SQL 대상 조회의 FR_SQL 전용 출력·조회 중 업무 행 보존·기존 변환/튜닝 결과 제외·0건 처리, 인사/소개/기능 안내의 CHAT 처리·일반 대화에 Tool 오류가 붙지 않는 render 출력, 실제 변환 요청과 조회의 구분을 검증했습니다. 첨부된 Agent 입력의 상태로 조회 Code를 재생해 SQL 한 개의 FR_SQL과 변경 없음도 확인했습니다. 사용자는 스트리밍 ON에서 매핑 조회 성공을 확인했으며 이 설정은 유지합니다. 현재 스트리밍 ON의 SQL 실행 실패는 해당 Agent 출력 json 대조가 필요하고 서버 복구를 단정하지 않습니다. [상세](LOOKUP_AND_CHAT.md).

2026-10-25 stop 오류 대응: 첨부 입력에 stop이 없는데 ReAct가 Observation stop을 추가하는 공식 구현과 실제 Azure/LiteLLM 400을 대조했습니다. 기본 DSL·프롬프트·설정 복원 경로를 Function Calling·반복 1로 수정했고, 재설정 시 ReAct/stop이 다시 유입되지 않는 검사를 보강했습니다. 실제 서버 요청 성공 및 이전 빈 Function Calling 응답 해결은 미검증입니다. [오류와 수정](GPT55_STOP_ERROR.md).

2026-10-25 최신 Dify 단일 SQL 시연: **67개 테스트 통과**. 바인드가 있는 두 테이블 조인 SQL 한 개, 활성 RAG 규칙 조회·적용·비활성화, actions 순차 처리·오류 시 취소, 첨부한 빈 ROUND/Thought 로그의 EMPTY_AGENT_RESPONSE 진단, 공식 ReAct CALL 로그의 상태 반영을 검사했습니다. 매핑룰 기본 조회는 대상명 없이 전체 마스터·상세를 반환합니다. ReAct 서버 모델 호출 복구·Oracle 실행은 미검증입니다. [현재 절차](RAG_TUNING_DEMO.md). 아래 기록의 다중 SQL·통합 단계는 이전 구현 이력입니다.

2026-10-25 단계 분리: **53개 로컬 테스트 통과**. 원문 sys.query 패킷 공급과 Agent의 request 변조 시 우선순위, 잘못된 full/migration 차단, SQL 전용 conversion_tuning, 이관/변환/튜닝의 독립 변경 범위, 가이드 읽기 전용, 기존 TO_SQL 보존, WHERE 규칙의 OR·NULL·리터럴·주석·집계·정렬/반복 적용을 검사했습니다. WHERE 조건 보존의 실행 비교는 SQLite 소규모 fixture이며 Oracle 동등성 검증을 뜻하지 않습니다. 내보낸 DSL Code 재생 12개 경로와 오프라인 전체 샘플 실행도 통과했습니다. Dify 서버·실제 모델·Oracle은 호출하지 않았습니다. [변경과 적용 방법](DEMO_STAGES.md). 아래는 이전 검증 이력입니다.

최신 결과 확인 노드: 사용자 첨부 입력(37832f0f)을 exported commit Code로 재생하여 OK/revision=1/매핑 마스터 2행·상세 10행 복원. 반환 키를 DSL에 선언된 문자열 4개로 제한하고 내부 빈 배열·boolean이 노드 최상위 출력에 노출되지 않게 정리했습니다. 조회·이관·결과 없음 경로의 출력 계약 검증 포함 총 41개 테스트 통과. 사용자 보고 '[] 오류'의 실제 FAIL 상세가 없어 이 변경이 서버 오류 원인을 해결했다고 단정하지 않습니다.

Console 기반 추가 수정: Agent json 오류 기본값 value=[]를 JSON 텍스트 value='[]'로 변경. Dify 1.15의 default-value 폼 → CodeEditor → Monaco로 배열이 전달되던 경로를 확인했습니다. 해당 기본값이 문자열이며 빈 배열로 역직렬화되는지 회귀 검사했고 총 40개 테스트 통과했습니다. 실제 서버 패널 복구 확인은 미완료입니다. [수정 근거와 적용](AGENT_PANEL_RENDER_FIX.md).

02 렌더링 오류 대응: tools 변수 참조를 폐기하고 constant/Tool 객체 배열로 교체했습니다. session_json은 자동 OFF 및 V3 전달 변수 사전 연결입니다. 새 고정 목록으로 템플릿→JSON 왕복·01 실행·다음 턴 보존 등 로컬 39개 테스트가 통과했습니다. 실제 React 렌더링/사용자 서버 적용은 미검증입니다. 아래 변수 tools 구조 검증 내용은 이전 기록입니다.

2026-10-19 최신: 실제 실행 Tool 복원 및 tools 배열 변수 참조 구조로 로컬 39개 테스트 통과. 이전 접수 전용 구조의 테스트를 실행 Tool·상태 자동 공급 검증으로 교체했습니다. 전체 처리, 다음 턴 상태 보존·매핑 수정, 특수문자/템플릿 형태 문자열 보존, 빈/손상 상태 거부, 결과 없음·충돌·오래된 상태 거부를 확인했습니다. LLM은 고정 SQL 산출물로 대체했습니다. 실제 Dify 서버 import·UI 저장·tools 변수 참조 유지·모델 호출은 미검증입니다. 최신 절차: [입력 자동 구성](AUTO_INPUT_SETUP.md).

검증일: 2026-10-16. Python 3.11 / PyYAML 6.0.3 환경에서 로컬 실행했습니다.

## 완료한 검증

```powershell
python smartMigrate_3.0_Dify/scripts/build.py
python -m unittest discover -s smartMigrate_3.0_Dify/tests -v
python smartMigrate_3.0_Dify/scripts/demo.py
```

**18개 테스트 통과.** (2026-10-18 재검증) 내보낸 DSL에 포함된 Code까지 실행하여 Tool의 입력 → 상태 확인 → 고정 SQL 산출물 적용 → Agent 로그 모양의 결과 → 메인 상태 반영을 확인했습니다.

| 검증 | 결과 |
|---|---|
| 원천 샘플·NEXT 계약 | 직원 10행·부서 3행·매핑 2개/상세 10개·SELECT 8개 |
| 네 단계 전체 실행 | TOBE 부서 3행·직원 10행, SQL 8건의 변환·튜닝·서식 결과 생성 |
| 재이관 | 같은 직원 ID의 변경된 급여 갱신, 직원 수 10행 유지 |
| 매핑 변경과 다음 턴 | FIRST_NAME 매핑을 LAST_NAME으로 바꾼 후 실제 대상 데이터 변경 |
| 추가·삭제 | 상세 규칙 삭제 후 재추가, 마스터 삭제 시 상세 제거 |
| 참조 관계 | 선행 매핑 참조 중 삭제 거부, 잘못된 SQL REF_SEQ 거부 |
| 모호한 요청 | pending_action 유지, 확인 질문과 실제 변경 분리 |
| 관리 상태·SQL 편집 | 지정 상태 유지, SQL 편집 시 후속 단계 STALE |
| 오류 원자성 | SQL 결과 누락/잘못된 포매팅이면 이관 변경도 되돌림 |
| SQL 산출물 검사 | 잘못된 대상 테이블·qualified 컬럼·바인드·복수 문장 거부 |
| 서식 검사 | 리터럴·바인드를 포함한 SQL 토큰 변경 거부 |
| 상태 반영 | 중복 로그 허용, 오래된 상태·충돌 결과·Agent 문장/입력에 섞인 결과는 반영 거부 |
| 공식 전략 로그 형태 | CALL/ROUND 중복 결과와 구버전 TEXT+JSON 이어붙임 처리 |
| DSL 코드와 연결 | Code 컴파일·실행, 단일 Tool, 수동 상태 입력, 대화 변수 연결 확인 |
| 워크스페이스 설정 스크립트 | 테스트 export로 모델·Tool ID를 연결하고 상태 입력 수동 설정 유지 |

예상 결과는 `samples/offline_full_result.md`와 `samples/offline_full_result.json`에서 볼 수 있습니다. SQL은 테스트용 고정 fixture이며 실제 LLM 출력이 아닙니다.

추가 검증: 테이블별 원본 파일 수정이 통합 샘플에 반영되고 원본은 유지됨을 확인했습니다. 개별 테이블 초기 입력 → 이관 → 다음 턴 최신 테이블 재분리 → Tool 재조합 과정의 데이터와 해시 일치도 확인했습니다.

1.15 호환 회귀 검증: DSL 0.6.0과 Agent 수동 Tool 입력의 직접 변수 selector를 확인했습니다. JSON의 따옴표·줄바꿈·역슬래시·템플릿 모양 문자열이 보존되는지 검사했습니다. 상세 근거는 [DIFY_1_15.md](DIFY_1_15.md)에 있습니다.

## 실제 Dify에서 남은 확인

2026-10-18 입력 사전 채움 변경 후 총 **20개 테스트 통과**. 메인/Tool default JSON과 원본 파일 일치, 기본값만으로 Tool 입력 구성, sample_json 수정값 보존·개별 입력 우선·기존 대화 상태 유지가 추가 확인되었습니다. 실제 서버의 default 표시 UI는 미검증입니다.

다음 항목은 로컬 단위 검증으로 대체할 수 없습니다. 현재 Dify 1.15라는 정보와 공식 1.15.0 소스는 확인했으나 서버 연결 정보·정확한 패치/plugin 버전·등록 모델·워크스페이스 Tool ID가 없어 실행하지 않았습니다.

- 두 앱의 DSL 가져오기와 공식 Agent strategy 선택.
- 통합 Workflow 발행 및 Agent의 테이블별 JSON/session_json/request_id 수동 연결.
- 등록 모델의 자연어 해석·SQL 생성·출력 길이 확인.
- 해당 설치 버전 Agent의 json 로그에 Tool 응답이 실제 포함되는지 확인.
- 실제 채팅에서 매핑 추가 → 변경 → 이관 → 재실행 → 삭제 → 조회의 상태 유지 확인.

실행 준비 절차는 `docs/SETUP.md`에 있습니다. 상태 로그를 찾지 못하면 상태를 보존하고 NO_RESULT로 표시하며, 실행에 성공했다고 간주하지 않습니다.
