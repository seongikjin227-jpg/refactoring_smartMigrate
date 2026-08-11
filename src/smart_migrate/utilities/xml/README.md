# XML Utilities

MyBatis mapper XML을 읽고 `NEXT_SQL_INFO` 적재에 필요한 SQL payload를 만드는 보조 패키지입니다.

## 주요 호출 구조

```text
run_all_xml_parser_stages()
  -> parse_mapper_dir_to_json()
     -> parse_single_mapper_xml()
     -> include 조각 보존/파싱
     -> JSON 파일 생성
  -> upsert_json_to_next_sql_info()
  -> expand_include_to_edit_sql()
  -> strip_schema_qualifiers_from_next_sql_info()
  -> cleanup_next_sql_info_rows()
```

## 주요 함수

- `parse_single_mapper_xml(xml_path)`: mapper XML 한 파일에서 `select/insert/update/delete/sql` item을 추출합니다.
- `parse_mapper_dir_to_json(input_dir, output_dir)`: 디렉터리의 mapper XML을 JSON payload로 변환합니다.
- `upsert_json_to_next_sql_info(data_dir)`: JSON payload를 `NEXT_SQL_INFO`에 upsert합니다.
- `expand_include_to_edit_sql()`: MyBatis `<include refid="...">`를 실제 SQL 조각으로 확장해 `EDIT_FR_SQL`에 반영합니다.
- `strip_schema_qualifiers_from_next_sql_info()`: SQL 안의 schema-qualified table명을 정리합니다.
- `cleanup_next_sql_info_rows()`: target table, SQL text 등 후처리 정리를 수행합니다.
- `_main()`: CLI 실행 진입점입니다.

이 패키지는 supervisor workflow가 아니며, XML import 또는 운영 보정이 필요할 때 별도로 호출합니다.
