# SmartMigrate 인수인계 가이드

**먼저 [새 프로젝트 시작 가이드](00_new_project_start_guide.html)를 여세요.** HTML은 더블클릭으로 열 수 있습니다.

| 읽을 순서 | 문서 | 필요한 작업 |
|---|---|---|
| 1 | [새 프로젝트 시작 가이드](00_new_project_start_guide.html) | 소스 데이터·생성된 타겟 테이블 확인, 권한·컴포넌트 설정, Langflow 저장·에이전트 빌더 배포, 매핑·Milvus 준비 |
| 2 | [운영 가이드](01_operations_guide.html) | 조회·실행, 실패 재시도, Correct SQL 저장, 유사 SQL·RAG 관리 |
| 3 | [개발 참고서](02_developer_reference.html) | 컴포넌트 역할, payload, Loop, 실행기, 매핑 검증·로그 계약 |
| 필요 시 | [컴포넌트 스티커 메모](04_component_sticky_notes.html) | 각 컴포넌트 파일의 한 줄 설명 복사 |
| 필요 시 | [템플릿 모음](03_request_templates.html) | 기능별 요청문 복사와 기대 결과 확인 |
| 필요 시 | [구조·기능 도식](00_architecture_chapter6_erd.html) | Oracle·Milvus 구조와 처리 흐름 확인 |

## 실행 SQL

| 파일 | 실행 시점 |
|---|---|
| [sql/01_create_langflow_tables.sql](sql/01_create_langflow_tables.sql) | 기존 타겟 계정에 SmartMigrate 관리 테이블·시퀀스 최초 생성 |
| [sql/02_register_project_examples.sql](sql/02_register_project_examples.sql) | 실제 프로젝트 이름·SQL로 수정한 뒤 필요한 예시만 등록 |
| [sql/03_verify_installation.sql](sql/03_verify_installation.sql) | 설치 후 객체·작업·로그 확인 |

담당자에게는 이 폴더와 `01_agent_start`, `02_flow_management`, `03_job_execution`의 컴포넌트 코드를 함께 전달하세요. 기존 Langflow Flow export, 새 환경 접속정보·권한, LLM·Embedding·Milvus 서비스 정보도 별도로 인계하세요.

사전 조건: 소스 스키마에는 AS-IS 데이터가 있고, 타겟 스키마에는 TO-BE 업무 테이블·컬럼·PK가 이미 생성되어 있어야 합니다. 제공 SQL은 SmartMigrate 관리 테이블만 생성합니다.

일반 구성: `db_username = system_schema = target_schema = TGT_NEW`. 타겟 계정에 소스 테이블 SELECT 권한을 부여하세요.
