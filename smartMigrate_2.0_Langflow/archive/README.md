# 구버전 및 진단 컴포넌트 보관

2026-10-07 기준 현재 표준 흐름에서 제외한 파일을 보관합니다. 외부 Langflow Flow의 연결 상태는 이 저장소만으로 확인할 수 없으므로 삭제하지 않았습니다.

| 파일 | 분류 | 현재 표준 |
|---|---|---|
| `10C_migOneJobPocExecutor.py` | 구버전 Migration 실행기 | `../03_job_execution/10C_migOneJobPocExecutor3.py` |
| `10C_migOneJobPocExecutor2.py` | 구버전 record 검증 실행기 | `../03_job_execution/10C_migOneJobPocExecutor3.py` |
| `18B_fullWorkflowLoop.py` | 구버전 전체 Loop | `../03_job_execution/18B_fullWorkflowLoop2.py` |
| `00B_presignedUrlExcelParserTest.py` | URL 파싱 단독 진단용 | 실제 흐름은 `../01_agent_start/00A_logRuntimeStart.py` |

기존 Flow에 복사된 컴포넌트 코드는 파일 이동만으로 교체되지 않습니다. 표준 버전으로 전환할 때 Langflow의 Custom Component 코드와 연결을 직접 갱신합니다. 00B는 필요할 때 단독 Playground 진단에만 사용합니다.
