# SmartMigrate 발표 자료

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
사용자가 지정한 HTML 기반 스크롤 웹페이지. 정적 HTML/CSS/JavaScript. A 리포트형으로 확정했으며 B·C와 비교 화면은 제거한다. 데스크톱만 수정·검증한다.

## Users
개발자 청중, 20~25분 발표. 주요 활용 사용자는 DBA와 B/E 개발자다. 사람과 AI의 역할 분담, 매핑 확정 이후의 활용, Workflow·실패 분석과 Dify 컴포넌트 책임을 구체적으로 설명한다.

## Product Purpose
노후 플랫폼 재설계·확장에서 기존 AS-IS SQL을 재활용하고 매핑룰을 체계화하여 마이그레이션 반복 업무를 자동화한다.

SQL을 사람이 생성하더라도 검증 SQL과 조건별 테스트를 별도로 준비해야 한다. 테이블·SQL이 많아질수록 결과 대조와 재검증의 반복이 어렵다는 점을 핵심 기획 배경으로 설명한다. 인원 조회 SQL의 동일한 업무 결과는 목표 기준이며, 현재 Dify가 값 동등성을 실행 검증한다고 표현하지 않는다.

## Team and Origin
팀명은 SQL깍두기다. 발표자는 신입사원으로 SI 개발 프로젝트의 노후 플랫폼 리팩토링에 참여하며 Migration과 개발 업무의 병목을 느꼈다. 대표 경험은 AS-IS SQL 분석과 TO-BE SQL 수정의 반복이다. 이 경험을 해결하고자 SmartMigrate Agent를 기획했다. 개선 효과나 절감률은 측정된 사실로 표현하지 않는다.

## Operating Context
목표 흐름은 Excel 또는 DB의 매핑룰 입력 → INSERT SQL·검증 SQL 생성 → Oracle 연결 시 이관 실행·검증 → AS-IS SQL을 TO-BE SQL로 변환·검증이다.

## Capabilities and Constraints
- 사람은 현업 업무 흐름과 데이터 정합성을 고려해 매핑룰을 확정한다. 이 역할 분담을 별도의 승인·권한 시스템 구현으로 표현하지 않는다.
- DBA는 이관·검증 자동화와 상태·로그·SQL을 통한 실패 원인 분석에 활용한다. 완전 자율 진단·복구로 표현하지 않는다.
- B/E 개발자는 매핑룰과 생성 TO-BE SQL을 조회·검토해 개발에 활용한다. 애플리케이션 코드 자동 반영을 주장하지 않는다.
- 원본 구현은 Supervisor와 Migration, Conversion, Tuning, Formatting Agent로 구성된다.
- Excel 업로드·생성은 사용자 설명에 따른 확장 구상이다.
- Dify 시연은 실제 Oracle 연결이 불가능한 환경에서 JSON 더미 데이터를 사용한다. Dify 제품 전체가 Oracle에 연결할 수 없다는 주장이 아니다.
- 현재 Dify 시연의 이관 산출물은 MERGE/upsert이고, 발표의 INSERT SQL은 목표 산출물이다.
- 더미 데이터 비교, 로컬 고정 SQL fixture 검증, 실제 Oracle 실행·검증을 구분한다.
- 실제 서버 실행 성공이나 성능 개선 수치를 만들어내지 않는다.

## Evidence on Hand
- src/smart_migrate/agents/*/README.md: Agent 역할과 검증 설계.
- smartMigrate_3.0_Dify/README.md 및 docs/CONTRACT.md: 시연 범위.
- smartMigrate_3.0_Dify/REFACTORING.md: DB 연결 제외 결정.
- smartMigrate_3.0_Dify/samples/results/migration_verification_example.md: 샘플 결과 기록.

## Product Principles
- 업무 목적에서 시작해 설계와 검증의 근거로 연결한다.
- Agent별 책임과 실행 순서를 시각적으로 보여준다.
- 목표 설계, 현재 시연, 남은 검증을 정확히 표현한다.
