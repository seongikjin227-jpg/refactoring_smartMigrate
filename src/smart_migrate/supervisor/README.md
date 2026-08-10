# Supervisor Package

이 패키지는 batch cycle을 돌며 어떤 agent job을 실행할지 결정하는 수퍼바이저 계층입니다.

주요 책임:

- cycle 시작/종료
- LangGraph supervisor graph 구성
- job polling 결과를 registry에 적재
- 한 cycle에 하나의 실제 job만 실행하도록 제어
- cycle metric 저장

실제 migration, SQL conversion, tuning, formatting 로직은 `agents/`에 둡니다.


