# Utilities Package

Supervisor가 주기적으로 실행하는 핵심 workflow가 아니라, 운영자나 UI/스크립트에서 명시적으로 호출하는 보조 기능을 둡니다.

## 현재 포함 범위

- `xml/`: MyBatis mapper XML import/export, include 확장, schema prefix 정리, `NEXT_SQL_INFO` 적재 보조 기능입니다.

## 호출 기준

```text
운영자 스크립트 또는 UI
  -> utilities.xml.XmlMapperImportHelper
  -> Oracle table 조회/갱신
```

## 규칙

- LangGraph supervisor tool 목록에 직접 포함하지 않습니다.
- migration/sql conversion/tuning/formatting job 처리 흐름과 분리합니다.
- 반복 실행되는 batch agent가 아니라 필요할 때 명시적으로 실행하는 helper 성격으로 유지합니다.
