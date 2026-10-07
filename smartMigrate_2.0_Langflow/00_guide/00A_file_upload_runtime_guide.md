# 파일 업로드 런타임 가이드

기준일: 2026-10-07. 실제 연결은 `Chat Input → 00A → 02 → 04 Router → Mapping Rule Update SQL Generate`입니다.

## 입력과 파싱

00A는 원본 Langflow Message를 `CHAT_INPUT` 로그의 `GENERATE_SQL` CLOB에 기록하고 DB logger를 등록합니다. `Message.data`에서 `downloadURL`, `download_url`, `presignedURL`, `presigned_url`, `fileURL`, `file_url`을 재귀 검색합니다. HTTP/HTTPS URL이 있으면 aiohttp로 다운로드하고 XLSX의 모든 시트를 pandas/openpyxl로 파싱합니다. 원문 text, session_id, files 등은 보존하고 data에 결과를 추가합니다.

```json
{"uploaded_attachment":{"byte_count":12345,"parsed_excel":{"format":"xlsx","sheets":[{"name":"테이블매핑","columns":["MAP_ID","FR_TABLE","TO_TABLE"],"rows":[{"MAP_ID":101,"FR_TABLE":"CUSTOMER","TO_TABLE":"MEMBER"}]}]}}}
```

- 패키지: `aiohttp`, `pandas`, `openpyxl`을 Langflow 서버에 설치합니다.
- 파싱 실패는 `uploaded_attachment.error`와 `00A_ATTACHMENT_PARSE / FAIL` 로그로 전달합니다. 매핑 생성기는 이를 발견하면 SQL을 생성·실행하지 않습니다.
- URL이 없고 `files` 상대 경로만 있으면 자동 파일 읽기를 하지 않습니다. 다운로드 URL 또는 명시적 매핑 본문을 제공합니다.
- XLSX만 지원합니다. CSV, PDF, 구형 XLS는 파일 경로 감지 대상일 수 있으나 이 파서의 지원 형식이 아닙니다.
- `archive/00B_presignedUrlExcelParserTest.py`는 단독 진단용입니다.

## 전달 계약

```text
00A Message.data.uploaded_attachment
  → 02 payload.uploaded_attachment 및 message_data/source_message.data
  → 04 Router Data payload 전체
  → 04_mappingRuleUpdateSqlGenerate.router_payload
```

02의 LLM은 route만 결정합니다. 원본 metadata와 parsed_excel은 Python 코드로 보존합니다. 04 Router의 `Mapping Rule Update` 출력을 전용 생성기의 `router_payload`에 연결합니다. Agent의 File Command Tool은 제거합니다.

## 매핑 PK와 실행

- master PK: `NEXT_MIG_INFO.MAP_ID`.
- detail PK: Oracle constraint에서 `(MAP_ID, MAP_DTL)` 또는 `(MAP_ID, FR_COL)`을 조회합니다. 다른 PK나 접근 불가능한 constraint는 오류로 종료합니다.
- 표준 실행기에는 MAP_DTL 컬럼이 필요합니다. 구형 FR_COL PK를 사용하는 환경에서는 실행기와의 호환도 별도로 확인합니다. 시트 순번/M/D 표시의 부모 관계와 MAP_DTL 의미가 명확하지 않으면 추측하지 않습니다. 부모 MAP_ID와 source column을 명시합니다.
- 생성기는 PK snapshot을 읽고 기존 키는 UPDATE, 신규 키는 INSERT로 처리합니다. 데이터 누락·모호함은 추측하지 않습니다.
- `execute_updates=false`가 기본값이며 SQL preview만 반환합니다. DB 적용하려면 컴포넌트의 `Execute Generated SQL`을 켭니다. 채팅의 “적용”이라는 표현만으로 설정이 바뀌지는 않습니다.
- 검증·미리보기 요청은 실행 설정이 켜져 있어도 DB를 변경하지 않습니다.
- 실행은 모든 문장을 하나의 transaction으로 묶습니다. 한 문장이라도 실패하거나 영향 행 수가 1이 아니면 전부 rollback합니다.

## 로그 점검

| 경계 | LOG_TYPE | 확인 내용 |
|---|---|---|
| 원본 수신 | `CHAT_INPUT` | 실제 Langflow Message와 URL metadata |
| 파싱 | `00A_ATTACHMENT_PARSE` | parsed_excel 또는 error |
| 02 수신 | `02_INPUT_MESSAGE` | data와 files 보존 |
| 02 출력 | `02_FINAL_OUTPUT` | route, uploaded_attachment, 원본 metadata |
| 매핑 생성기 | `04_MAPPING_RULE` | STEP_NAME과 CLOB의 PK snapshot, LLM 응답, 생성 SQL, 실행·rollback 결과 |

매핑 생성기는 LOG_TYPE=`04_MAPPING_RULE`, STEP_NAME=`LOAD_PK:SELECT`, `GENERATE_SQL:LLM`, `EXECUTE_SQL:STATEMENT`, `ROLLBACK:TRANSACTION` 등으로 기록합니다. handler는 LOG_TYPE을 20자로 자릅니다.
