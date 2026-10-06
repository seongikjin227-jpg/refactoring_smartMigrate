# 파일 업로드 전달 구조를 Presigned URL 방식으로 설계한 이유

## 1. 출발점: Langflow Playground에서는 files가 있었다

처음 Langflow Playground에서 파일을 첨부했을 때 Chat Input Message에는 files 값이 들어왔다.

~~~
Message.files
  = ["{session_id}/{timestamp}_{filename}.xlsx"]
~~~

이 값은 파일 본문이 아니라 Langflow 저장소의 상대 경로다. 기본 Langflow Agent는 이 경로를 사용해 내부적으로 파일을 읽고 LLM Message에 파일 내용을 추가할 수 있다.

따라서 처음에는 다음처럼 생각했다.

~~~
Chat Input.files
  -> 00A
  -> 01/02
  -> 04 Agent
~~~

즉 files를 그대로 전달하면 Agent가 파일을 인식할 것으로 기대했다.

## 2. 문제 발견: 서버 Chat Input에서는 files가 비어 있었다

사내 서버의 Chat Input과 Playground는 파일 전달 방식이 달랐다.

- Playground: Message.files에 파일 경로가 존재
- 사내 서버: session_id와 user metadata는 data에 존재하지만 Message.files는 빈 배열

00A의 원본 Message CLOB 로그로 확인한 형태는 다음과 유사했다.

~~~json
{
  "text": "첨부한 엑셀의 매핑 룰을 확인해줘.",
  "session_id": "New Session",
  "data": {
    "session_id": "New Session",
    "downloadURL": "http://..."
  },
  "files": []
}
~~~

이 상태에서는 downstream 컴포넌트가 session_id만으로 파일명이나 실제 업로드 파일을 복구할 수 없다. 따라서 files 경로 방식만 의존하는 설계는 서버 환경에서 실패한다.

## 3. 설계 변경: 파일 경로가 아니라 Presigned URL을 00A에서 처리

사내 Chat Input data에는 파일 접근용 downloadURL 또는 Presigned URL이 전달되는 것을 확인했다.

그래서 파일 처리 책임을 가장 첫 경계인 00A로 옮겼다.

~~~
서버 Chat Input
  -> Message.data.downloadURL
  -> 00A가 URL 다운로드
  -> Excel을 JSON으로 변환
  -> 변환 JSON을 Message.data에 다시 저장
~~~

이 설계의 목적은 다음 세 가지다.

1. files가 비어 있어도 파일 내용을 확보한다.
2. 파일 다운로드·변환 지점을 00A 하나로 고정한다.
3. 이후 02, 04, Agent는 URL이나 파일 저장소를 다시 알 필요 없이 JSON 데이터만 사용한다.

## 4. 00A가 서버 JSON에서 무엇을 확인하는가

00A는 Chat Input Message 전체를 먼저 JSON envelope로 기록한다.

대상 필드:

~~~text
text
sender / sender_name
session_id / context_id
files
data
properties
content_blocks
~~~

이 로그는 파일 전달 방식이 바뀌었을 때 실제 서버가 무엇을 보냈는지 확인하기 위한 관측 지점이다.

그 뒤 00A는 data 내부를 재귀적으로 검사한다. 인식하는 URL 키는 다음과 같다.

~~~text
downloadURL, download_url
presignedURL, presigned_url
fileURL, file_url
~~~

현재는 HTTP와 HTTPS URL을 모두 다운로드 대상으로 허용한다.

## 5. URL에서 엑셀 데이터까지 변환하는 과정

00A의 처리 단계:

~~~text
Presigned URL
  -> aiohttp로 파일 bytes 다운로드
  -> BytesIO로 메모리 전달
  -> pandas.read_excel
  -> 모든 Sheet를 columns + rows JSON으로 변환
  -> Message.data.uploaded_attachment에 저장
~~~

변환 후 저장 구조:

~~~json
{
  "uploaded_attachment": {
    "presigned_url": "http://...",
    "byte_count": 12345,
    "parsed_excel": {
      "format": "xlsx",
      "sheets": [
        {
          "name": "테이블매핑",
          "columns": ["순번", "ASIS 테이블명", "TOBE 테이블명"],
          "rows": []
        }
      ]
    }
  }
}
~~~

즉 이후 단계는 파일명이나 서버 파일 경로가 아니라 parsed_excel JSON만 보면 된다.

## 6. 왜 01 Agent를 없애고 02에서 JSON을 조합했는가

처음에는 01 Agent에게 원본 metadata를 source_message에 그대로 복사하라고 지시했다.

하지만 LLM은 다음 값을 빈 값으로 만들거나 누락할 수 있었다.

~~~text
session_id
files
data
properties
content_blocks
~~~

metadata는 자연어 해석 결과가 아니라 시스템 전달값이므로 LLM에게 복사를 맡기면 안 된다.

그래서 현재는 다음처럼 분리했다.

~~~text
02 LLM: route만 반환
02 Python 코드: session_id, files, data, uploaded_attachment를 원본 Message에서 직접 조합
~~~

이렇게 하면 LLM이 route를 잘못 판단하는 문제와 metadata를 잃는 문제를 분리할 수 있다.

## 7. 04까지 전달됐는지 어떻게 검증하는가

파싱 데이터는 아래 경로로 전달된다.

~~~text
00A Message.data.uploaded_attachment
  -> 02 payload.uploaded_attachment
  -> 04 Router payload.uploaded_attachment
  -> 04 Management Agent
~~~

각 경계에는 CLOB 로그를 둔다.

| 단계 | LOG_TYPE | 확인 목적 |
|---|---|---|
| 원본 수신 | CHAT_INPUT | 서버 Chat Input JSON 확인 |
| URL 처리 결과 | 00A_ATTACHMENT_PARSE | URL 다운로드와 시트 수 확인 |
| 엑셀 원본 변환 | 00A_ATTACHMENT_DATA | 전체 parsed_excel JSON 확인 |
| 02 수신 | 02_INPUT_MESSAGE | 00A에서 02로 metadata가 유지됐는지 확인 |
| 02 출력 | 02_FINAL_OUTPUT | 04로 보낼 최종 JSON 확인 |
| 04 수신 | 04_PARSED_EXCEL | 04 Router까지 parsed_excel이 도착했는지 확인 |

MESSAGE 컬럼은 길이 제한이 있으므로 전체 데이터는 NEXT_MIG_LOG.GENERATE_SQL CLOB에서 확인한다.

## 8. 매핑 룰 처리의 다음 단계

파싱된 엑셀의 계약은 다음과 같다.

- 테이블매핑 시트의 순번은 MAP_ID
- 컬럼매핑 시트 M 행의 순번은 MAP_ID
- 컬럼매핑 시트 D 행의 순번은 MAP_DTL

현재 DB 변경은 하지 않는다. 04 Management Agent는 다음 순서로 처리한다.

~~~text
1. File Command Tool: Agent가 받은 입력 전체를 CLOB 로그에 남김
2. Select Tool: MAP_ID와 MAP_ID + MAP_DTL 충돌 조회
3. Select Tool: 충돌 행은 UPDATE SQL preview, 신규 행은 INSERT SQL preview 생성
4. Agent: SQL을 사용자에게 보여주되 실행하지 않음
~~~

## 발표용 요약

처음에는 Langflow Playground처럼 files 경로가 전달될 것으로 보고 파일명을 downstream으로 전달하려 했다. 그러나 사내 서버 Chat Input에서는 files가 비어 있고 data에 Presigned URL만 들어왔다. 그래서 파일 다운로드와 엑셀 파싱을 00A에서 수행하도록 변경했다. 00A는 URL을 다운로드해 엑셀 전체 시트를 JSON으로 만들고, 02는 LLM이 metadata를 복사하지 않도록 route만 판단하게 한 뒤 원본 metadata와 파싱 데이터를 코드로 보존한다. 04에서는 이 데이터가 실제 도착했는지 CLOB 로그로 검증하고, MAP_ID와 MAP_DTL 기준으로 DB 충돌을 조회해 실행하지 않는 UPDATE/INSERT SQL만 생성한다.
