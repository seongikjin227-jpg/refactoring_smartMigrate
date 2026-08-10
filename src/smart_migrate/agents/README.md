# Agents Package

이 패키지는 Supervisor가 호출하는 업무 agent를 담습니다.

각 agent 폴더는 다음 기준으로 구성합니다.

- `*Agent.py`: Supervisor가 호출하는 공개 진입점
- `*Workflow.py` 또는 `*Graph.py`: job 하나를 처리하는 실행 흐름
- `*State.py`: workflow 실행 중 공유되는 상태
- `*Components.py` 또는 역할이 분명한 helper 파일: 해당 agent 내부에서만 쓰는 세부 함수

LangGraph를 꼭 여러 node 파일로 쪼개지는 않습니다. 실행 순서가 고정된 agent는 `Workflow.py` 하나로 읽히게 유지하고, 분기/상태 전이가 큰 agent만 `Graph.py`를 둡니다.
