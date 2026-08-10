# Utilities Package

`utilities`는 supervisor가 주기적으로 실행하는 핵심 workflow가 아니라 운영자가 필요할 때 사용하는 부가 기능을 둡니다.

현재 포함 범위:

- `xml/`: MyBatis mapper XML import/export, include 확장, schema prefix 정리 같은 보조 기능

규칙:

- LangGraph supervisor tool 목록에 직접 포함하지 않습니다.
- migration/sql conversion/tuning/formatting job 처리 흐름과 분리합니다.
- UI나 운영 스크립트에서 명시적으로 호출하는 helper 성격으로 유지합니다.

