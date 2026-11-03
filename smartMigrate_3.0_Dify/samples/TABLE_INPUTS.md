# 테이블 파일과 Tool 입력 연결

`tables/*.json`은 수정·관리하는 원본이며 `hr_demo.json`은 build.py가 합치는 생성 파일입니다.

| 원본 파일 | 메인 Chatflow 시작 입력 | 상태 준비 출력(확인용) |
|---|---|---|
| [ASIS_HR_DEPT](tables/ASIS_HR_DEPT.json) | `asis_hr_dept_json` | `initialize.asis_hr_dept_json` |
| [ASIS_HR_EMP](tables/ASIS_HR_EMP.json) | `asis_hr_emp_json` | `initialize.asis_hr_emp_json` |
| [TOBE_HR_DEPT](tables/TOBE_HR_DEPT.json) | `tobe_hr_dept_json` | `initialize.tobe_hr_dept_json` |
| [TOBE_HR_EMP](tables/TOBE_HR_EMP.json) | `tobe_hr_emp_json` | `initialize.tobe_hr_emp_json` |
| [NEXT_MIG_INFO](tables/NEXT_MIG_INFO.json) | `next_mig_info_json` | `initialize.next_mig_info_json` |
| [NEXT_MIG_INFO_DTL](tables/NEXT_MIG_INFO_DTL.json) | `next_mig_info_dtl_json` | `initialize.next_mig_info_dtl_json` |
| [NEXT_SQL_INFO](tables/NEXT_SQL_INFO.json) | `next_sql_info_json` | `initialize.next_sql_info_json` |
| [NEXT_MIG_RAG_INFO](tables/NEXT_MIG_RAG_INFO.json) | `next_mig_rag_info_json` | `initialize.next_mig_rag_info_json` |
| [NEXT_MIG_LOG](tables/NEXT_MIG_LOG.json) | `next_mig_log_json` | `initialize.next_mig_log_json` |

Tool 입력은 request와 session_json입니다. 테이블별 입력을 Tool에 수동 연결하지 않습니다.
02가 현재 session_transport를 session_json에 DSL로 사전 연결합니다. Agent는 request만 작성합니다.
01 Tool이 실제 조회·관리·이관·SQL 처리·검증을 수행하고 변경된 상태를 반환합니다.
대화 중에는 저장한 최신 상태를 사용합니다. 원본 파일을 매번 상수로 붙여넣으면 이전 변경이 사라집니다.
메인 Chatflow 최초 입력의 테이블별 JSON은 선택 사항입니다. 비운 항목은 내장 기본값을 사용합니다.
