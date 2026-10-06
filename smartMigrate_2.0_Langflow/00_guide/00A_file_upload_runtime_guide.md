# 00A 파일 업로드 런타임 가이드

## 전체 흐름

00A는 Chat Input Message의 metadata를 JSON으로 기록하고, data 안의 Presigned URL을 찾아 엑셀을 다운로드·파싱한 뒤 02와 04까지 전달한다.

~~~
Chat Input Message
  -> 00A.run
  -> 원본 Message CLOB 로그
  -> Presigned URL 다운로드
  -> pandas Excel 파싱
  -> Message.data.uploaded_attachment 저장
  -> 02 최종 payload
  -> 04 Router / Management Agent
~~~

관련 구현 파일:

- 01_agent_start/00A_logRuntimeStart.py
- 01_agent_start/02_intentRouter.py
- 02_flow_management/04_managementRouter.py

## 1. 00A 로그의 JSON 객체

함수: NewType00ALogRuntimeStart._message_payload_json(message)

이 함수가 Chat Input의 Message를 아래 필드로 새 JSON envelope로 만든다.

~~~
text
sender
sender_name
session_id
context_id
content_blocks
properties
data
files
~~~

따라서 00A 로그의 JSON은 LLM 응답이 아니라, Chat Input Message 전체를 진단용으로 직렬화한 값이다.

함수: _json_value(value)

Pydantic 객체, dict, list를 JSON으로 저장 가능한 Python 값으로 변환한다. 순환 참조도 막는다.

## 2. run 함수의 처리 순서

NewType00ALogRuntimeStart.run은 다음 순서다.

1. input_message가 Langflow Message인지 확인한다.
2. _message_payload_json으로 원본 envelope JSON을 만든다.
3. CHAT_INPUT 로그를 NEXT_MIG_LOG에 기록한다.
4. _enrich_presigned_attachment로 Presigned URL 파일을 처리한다.
5. 파일 처리 결과와 성공 시 전체 파싱 JSON을 CLOB에 기록한다.
6. 파싱 결과가 포함된 Message를 02에 반환한다.

반환 Message의 text는 inspect 가능한 envelope JSON이며, 실제 파싱 결과는 data에 저장된다.

## 3. Presigned URL 탐색

함수: _find_presigned_url(value)

message.data를 재귀 탐색해서 아래 키를 찾는다.

~~~
downloadURL / download_url
presignedURL / presigned_url
fileURL / file_url
~~~

URL이 없으면 파일 처리 없이 원래 Message를 다음 단계로 전달한다.

## 4. 다운로드

함수: async _download_presigned_url(url)

aiohttp로 URL 응답 body를 bytes로 다운로드한다.

- HTTP와 HTTPS Presigned URL을 모두 허용한다.

다운로드 오류는 parse_error와 CLOB 로그에 남는다.

## 5. 엑셀 파싱

함수: _parse_excel(content)

pandas.read_excel을 sheet_name=None으로 호출해 모든 시트를 읽는다. 각 시트는 name, columns, rows 구조의 JSON으로 변환된다.

결과 예시:

~~~json
{
  "format": "xlsx",
  "sheets": [
    {
      "name": "테이블매핑",
      "columns": ["순번", "ASIS 테이블명", "TOBE 테이블명"],
      "rows": [{"순번": 101, "ASIS 테이블명": "ASIS_CUSTOMER", "TOBE 테이블명": "TOBE_MEMBER"}]
    }
  ]
}
~~~

서버에는 다음 패키지가 필요하다.

~~~powershell
pip install pandas openpyxl aiohttp
~~~

현재 개발 환경에는 openpyxl이 없으므로, 서버에도 없다면 xlsx 파싱은 parse_error로 기록된다.

## 6. 파싱 결과 저장 위치

함수: _enrich_presigned_attachment(message)

원래 Message.data에 uploaded_attachment를 추가한다.

~~~json
{
  "uploaded_attachment": {
    "presigned_url": "http://...",
    "byte_count": 12345,
    "parsed_excel": {"format": "xlsx", "sheets": []}
  }
}
~~~

파일 경로가 Message.files에 없어도 data의 Presigned URL만 있으면 이 경로로 파일 내용을 확보할 수 있다.

## 7. CLOB 로그 확인 위치

| LOG_TYPE | STEP_NAME | GENERATE_SQL CLOB 내용 |
|---|---|---|
| CHAT_INPUT | MESSAGE | 최초 Chat Input Message envelope |
| 00A_ATTACHMENT_PARSE | PRESIGNED_URL | URL host, 파일 크기, 시트 수 또는 오류 |
| 00A_ATTACHMENT_DATA | PARSED_EXCEL | 전체 시트, 컬럼, 행 JSON |
| 02_INPUT_MESSAGE | RECEIVE_00A | 02가 받은 metadata |
| 02_LLM_ROUTE_RESPONSE | CLASSIFY | LLM route 응답 |
| 02_FINAL_OUTPUT | COMPILE_PAYLOAD | 02 최종 payload |
| 04_PARSED_EXCEL | RECEIVE_PARSED_EXCEL | 04 Router가 받은 파싱 데이터 |

MESSAGE 컬럼은 4,000자 제한이 있으므로 전체 원본은 GENERATE_SQL CLOB을 조회한다.

## 8. 02와 04 전달 계약

02는 LLM이 metadata를 복사하게 하지 않는다. LLM은 route만 반환하고, 02가 Message의 session_id, files, data, properties, content_blocks를 코드로 조합한다.

~~~
00A Message.data.uploaded_attachment
  -> 02 payload.uploaded_attachment
  -> 04 Router payload.uploaded_attachment
  -> 04 Management Agent
~~~

## 9. 매핑 룰 SQL preview

엑셀 계약:

- 테이블매핑 시트의 순번은 MAP_ID
- 컬럼매핑 시트 M 행 순번은 MAP_ID
- 컬럼매핑 시트 D 행 순번은 MAP_DTL

Agent 처리 순서:

1. File Command Tool로 수신 입력을 CLOB 로그에 기록한다.
2. Select Tool preview_mapping_rule_conflicts action으로 실제 DB와 비교한다.
3. 충돌 행은 update_sql_preview, 신규 행은 insert_sql_preview로 받는다.
4. SQL은 보여주기만 하고 DB에서 실행하지 않는다.

## 10. 설명용 핵심 문장

사내 Chat Input이 files를 비워서 보내는 경우를 대비해, 00A가 data의 Presigned URL을 직접 찾는다. 00A는 파일을 다운로드하고 엑셀 전체 시트를 JSON으로 변환해 uploaded_attachment에 저장한다. 02는 이 데이터를 코드로 보존해 04로 전달한다. 각 경계는 CLOB 로그로 추적할 수 있으며, 04는 MAP_ID와 MAP_DTL 충돌을 조회해 실행하지 않는 UPDATE/INSERT SQL preview만 생성한다.
