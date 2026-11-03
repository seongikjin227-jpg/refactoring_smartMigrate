"""Dify Code node: standard library only, JSON tables, no database or network.

The builder embeds this whole file in the execute node. Do not import local modules.
"""
import copy
import base64
import hashlib
import json
import re

MAX_STATE = 180000
MAX_ROWS = 200
RESULT_KIND = "smartmigrate.result.v1"
TABLE_INPUTS = {name: name.lower() + "_json" for name in (
    "ASIS_HR_DEPT", "ASIS_HR_EMP", "TOBE_HR_DEPT", "TOBE_HR_EMP", "NEXT_MIG_INFO",
    "NEXT_MIG_INFO_DTL", "NEXT_SQL_INFO", "NEXT_MIG_RAG_INFO", "NEXT_MIG_LOG")}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def table(state, name):
    if name not in state["tables"]:
        raise ValueError("없는 테이블: " + str(name))
    return state["tables"][name]


def rows(state, name):
    return table(state, name)["rows"]


def validate(state):
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("schema_version=1인 샘플 JSON이 필요합니다.")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise ValueError("revision은 0 이상의 정수여야 합니다.")
    if not isinstance(state.get("tables"), dict):
        raise ValueError("tables 객체가 필요합니다.")
    names = ("NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL", "NEXT_SQL_INFO", "NEXT_MIG_RAG_INFO", "NEXT_MIG_LOG")
    for name in names:
        table(state, name)
    for name, spec in state["tables"].items():
        if not isinstance(spec, dict):
            raise ValueError(name + ": 테이블 정의 오류")
        cols, data = spec.get("columns"), spec.get("rows")
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols) or len(set(cols)) != len(cols):
            raise ValueError(name + ": columns 오류")
        if not all(isinstance(c, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", c) for c in cols):
            raise ValueError(name + ": 컬럼명 오류")
        if not isinstance(data, list) or len(data) > MAX_ROWS:
            raise ValueError(name + ": 행 수 제한 200")
        keys = spec.get("primary_key", [])
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or not set(keys) <= set(cols):
            raise ValueError(name + ": primary_key 오류")
        seen = set()
        for row in data:
            if not isinstance(row, dict) or set(row) - set(cols):
                raise ValueError(name + ": 선언하지 않은 컬럼")
            if any(isinstance(v, (dict, list)) for v in row.values()):
                raise ValueError(name + ": 셀은 문자열/숫자/null/bool만 지원")
            key = tuple(row.get(k) for k in keys)
            if any(v is None for v in key) or key in seen:
                raise ValueError(name + ": 중복 또는 빈 키 " + str(key))
            seen.add(key)
    required = {"NEXT_MIG_INFO": {"MAP_ID", "FR_TABLE", "TO_TABLE", "STATUS", "USE_YN"},
                "NEXT_MIG_INFO_DTL": {"MAP_ID", "MAP_DTL", "FR_COL", "TO_COL"},
                "NEXT_SQL_INFO": {"SQL_SEQ", "SPACE_NM", "SQL_ID", "TARGET_TABLE", "FR_SQL", "TO_SQL", "STATUS_CONVERSION", "STATUS_TUNING", "FORMATTED_SQL"},
                "NEXT_MIG_RAG_INFO": {"RAG_ID", "CATEGORY", "RULE_TYPE", "USE_YN", "GUIDANCE_TEXT"},
                "NEXT_MIG_LOG": {"LOG_ID", "MESSAGE", "STATUS"}}
    for name, columns in required.items():
        if not columns <= set(table(state, name)["columns"]):
            raise ValueError(name + ": 필수 컬럼이 없습니다.")
    masters = {m["MAP_ID"]: m for m in rows(state, "NEXT_MIG_INFO")}
    for m in masters.values():
        if not isinstance(m["MAP_ID"], int):
            raise ValueError("MAP_ID는 정수")
        src, dst = table(state, m["FR_TABLE"]), table(state, m["TO_TABLE"])
        if not m["FR_TABLE"].startswith("ASIS_") or not m["TO_TABLE"].startswith("TOBE_"):
            raise ValueError("매핑은 ASIS_ → TOBE_만 지원")
        if m.get("USE_YN") not in ("Y", "N") or m.get("TRUNC_YN", "N") != "N":
            raise ValueError("USE_YN은 Y/N, TRUNC_YN은 N만 지원")
        prior = m.get("PRIOR_MAP_ID")
        if prior is not None and prior not in masters:
            raise ValueError("없는 선행 MAP_ID")
        path, cursor = set(), m["MAP_ID"]
        while cursor is not None:
            if cursor in path:
                raise ValueError("선행 매핑 순환 참조")
            path.add(cursor)
            cursor = masters[cursor].get("PRIOR_MAP_ID")
        used = set()
        for d in rows(state, "NEXT_MIG_INFO_DTL"):
            if d["MAP_ID"] != m["MAP_ID"]:
                continue
            if not isinstance(d["MAP_DTL"], int):
                raise ValueError("MAP_DTL은 정수")
            if d.get("FR_COL") not in src["columns"] or d.get("TO_COL") not in dst["columns"]:
                raise ValueError("매핑에 없는 원천/대상 컬럼")
            if d["TO_COL"] in used:
                raise ValueError("한 매핑에 중복 대상 컬럼")
            used.add(d["TO_COL"])
    for d in rows(state, "NEXT_MIG_INFO_DTL"):
        if d["MAP_ID"] not in masters:
            raise ValueError("상세 규칙에 없는 MAP_ID")
    sql_keys = set()
    for job in rows(state, "NEXT_SQL_INFO"):
        if not isinstance(job["SQL_SEQ"], int):
            raise ValueError("SQL_SEQ는 정수")
        key = (job.get("SPACE_NM"), job.get("SQL_ID"))
        if None in key or key in sql_keys:
            raise ValueError("SPACE_NM/SQL_ID 중복 또는 누락")
        sql_keys.add(key)
        scope = [x.strip() for x in job.get("TARGET_TABLE", "").split(",") if x.strip()]
        if not scope or any(n not in state["tables"] or not n.startswith("ASIS_") for n in scope):
            raise ValueError("TARGET_TABLE은 기존 ASIS 테이블 목록이어야 함")
    sequences = {j["SQL_SEQ"] for j in rows(state, "NEXT_SQL_INFO")}
    for job in rows(state, "NEXT_SQL_INFO"):
        ref = job.get("REF_SEQ")
        if ref is not None and (ref not in sequences or ref == job["SQL_SEQ"]):
            raise ValueError("REF_SEQ는 존재하는 다른 SQL_SEQ여야 합니다.")
    for rule in rows(state, "NEXT_MIG_RAG_INFO"):
        if rule.get("CATEGORY") not in ("SQL_CONVERSION", "SQL_TUNING") or rule.get("RULE_TYPE") not in ("GENERAL", "SEARCH") or rule.get("USE_YN") not in ("Y", "N"):
            raise ValueError("참고 규칙 CATEGORY/RULE_TYPE/USE_YN 오류")
    if len(dumps(state)) > MAX_STATE:
        raise ValueError("시연 상태 크기 제한 초과. 로그/SQL을 줄이세요.")
    return state


def markdown(state, names, selected_columns=None):
    def cell(value):
        if value is None:
            return "—"
        return str(value).replace("|", "&#124;").replace("\n", "<br>").replace("\r", "")
    result = []
    for name in dict.fromkeys(names):
        spec = table(state, name)
        section = ["### " + name, ""]
        cols = spec["columns"]
        # Wide SQL/log tables: display a useful subset; JSON retains every field.
        if selected_columns and name in selected_columns:
            cols = selected_columns[name]
        elif name == "NEXT_SQL_INFO":
            cols = ["SQL_SEQ", "SQL_ID", "STATUS_CONVERSION", "STATUS_TUNING", "FR_SQL", "TO_SQL", "TUNED_TO_SQL", "TUNED_RESULT", "FORMATTED_SQL"]
        elif name == "NEXT_MIG_INFO":
            cols = ["MAP_ID", "FR_TABLE", "TO_TABLE", "USE_YN", "STATUS"]
        elif name == "NEXT_MIG_INFO_DTL":
            cols = ["MAP_ID", "MAP_DTL", "FR_COL", "TO_COL"]
        section.append("| " + " | ".join(cols) + " |")
        section.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in spec["rows"][-30:]:
            section.append("| " + " | ".join(cell(row.get(c)) for c in cols) + " |")
        section += ["", "총 " + str(len(spec["rows"])) + "행 (최대 30행 표시)"]
        result.append("\n".join(section))
    return "\n\n".join(result)


def sql_source_report(state):
    jobs = rows(state, "NEXT_SQL_INFO")
    view = {"tables": {"NEXT_SQL_INFO": {"columns": ["SQL_SEQ", "SQL_ID", "TARGET_TABLE", "BIND_SET"], "rows": jobs}}}
    lines = ["SQL 변환 대상 " + str(len(jobs)) + "건을 조회했습니다. AS-IS SQL만 표시하며 변환·이관·튜닝은 실행하지 않았습니다.",
             "", markdown(view, ["NEXT_SQL_INFO"], {"NEXT_SQL_INFO": view["tables"]["NEXT_SQL_INFO"]["columns"]})]
    for job in jobs[:30]:
        lines += ["", "### SQL_SEQ " + str(job["SQL_SEQ"]) + " · FR_SQL (AS-IS)", "", "```sql", job["FR_SQL"], "```"]
    return "\n".join(lines)


def is_sql_source_query(request):
    text = request.lower()
    return (requested_stages(request) == {"query"} and "sql" in text
            and bool(re.search(r"대상|as[-_ ]?is|원본|샘플", text))
            and not re.search(r"결과|to[-_ ]?be|튜닝", text))


def log(state, action, status, message):
    data = rows(state, "NEXT_MIG_LOG")
    ident = max([r["LOG_ID"] for r in data] + [0]) + 1
    data.append({"LOG_ID": ident, "MAP_ID": None, "MIG_KIND": action, "LOG_TYPE": "DEMO",
                 "LOG_LEVEL": "INFO" if status == "OK" else "WARN", "STEP_NAME": action,
                 "STATUS": status, "MESSAGE": message, "RETRY_COUNT": 0, "GENERATE_SQL": None,
                 "CREATED_AT": "TURN-" + str(state["revision"] + 1)})
    del data[:-30]


def invalidate(state):
    for m in rows(state, "NEXT_MIG_INFO"):
        m["STATUS"] = "STALE"
    for job in rows(state, "NEXT_SQL_INFO"):
        job["STATUS_CONVERSION"] = "STALE"
        job["STATUS_TUNING"] = "STALE"
        job["LOG"] = "매핑 또는 SQL 변경: 이전 산출물은 이력이며 재생성 필요"


def select_one(state, name, key):
    spec = table(state, name)
    if not isinstance(key, dict) or set(key) != set(spec["primary_key"]):
        raise ValueError("정확한 기본 키가 필요합니다: " + ", ".join(spec["primary_key"]))
    found = [r for r in spec["rows"] if all(r.get(k) == v for k, v in key.items())]
    if len(found) != 1:
        raise ValueError("대상 행이 없습니다: " + dumps(key))
    return found[0]


def manage(state, plan):
    action, name = plan["action"], plan.get("table")
    editable = {"NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL", "NEXT_SQL_INFO", "NEXT_MIG_RAG_INFO"}
    if name not in editable:
        raise ValueError("관리 대상은 매핑·SQL 작업·참고 규칙 테이블입니다.")
    spec = table(state, name)
    if action == "insert":
        value = plan.get("values", {})
        if not isinstance(value, dict) or not value:
            raise ValueError("추가할 values가 필요합니다.")
        default = {"USE_YN": "Y", "PRIORITY": 5, "TRUNC_YN": "N", "MAP_TYPE": "SIMPLE", "USER_EDITED": "N", "BATCH_CNT": 0, "RETRY_COUNT": 0}
        row = {c: default.get(c) for c in spec["columns"]}
        row.update(value)
        spec["rows"].append(row)
        key = {c: row.get(c) for c in spec["primary_key"]}
    else:
        key = plan.get("key", {})
        row = select_one(state, name, key)
        if action == "update":
            value = plan.get("values", {})
            if not isinstance(value, dict) or not value or set(value) & set(spec["primary_key"]):
                raise ValueError("수정값이 없거나 기본 키 변경 요청입니다.")
            allowed = {
                "NEXT_MIG_INFO": {"FR_TABLE", "TO_TABLE", "CONDITION", "USE_YN", "PRIORITY", "PRIOR_MAP_ID", "STATUS"},
                "NEXT_MIG_INFO_DTL": {"FR_COL", "TO_COL"},
                "NEXT_SQL_INFO": {"FR_SQL", "EDIT_FR_SQL", "TO_SQL", "TUNED_TO_SQL", "FORMATTED_SQL", "STATUS_CONVERSION", "STATUS_TUNING", "PRIORITY", "TARGET_TABLE"},
                "NEXT_MIG_RAG_INFO": {"CATEGORY", "RULE_TYPE", "SOURCE_TABLES", "USE_YN", "GUIDANCE_TEXT", "SOURCE_SQL", "TARGET_SQL"},
            }
            if set(value) - allowed[name]:
                raise ValueError("수정이 허용되지 않은 컬럼")
            row.update(value)
        elif action == "delete":
            if name == "NEXT_MIG_INFO":
                if any(m.get("PRIOR_MAP_ID") == row["MAP_ID"] for m in rows(state, name)):
                    raise ValueError("다른 매핑이 참조 중입니다. 선행 매핑 관계를 먼저 변경하세요.")
                rows(state, "NEXT_MIG_INFO_DTL")[:] = [d for d in rows(state, "NEXT_MIG_INFO_DTL") if d["MAP_ID"] != row["MAP_ID"]]
            if name == "NEXT_SQL_INFO" and any(j.get("REF_SEQ") == row["SQL_SEQ"] for j in rows(state, name)):
                raise ValueError("REF_SEQ에서 참조 중인 SQL입니다.")
            spec["rows"].remove(row)
        else:
            raise ValueError("지원하지 않는 관리 action")
    mapping_change = name.startswith("NEXT_MIG_INFO") and not (action == "update" and set(plan["values"]) <= {"STATUS"})
    if mapping_change:
        invalidate(state)
    elif name == "NEXT_SQL_INFO" and action == "update":
        if set(plan.get("values", {})) & {"FR_SQL", "EDIT_FR_SQL", "TARGET_TABLE", "TO_SQL", "TUNED_TO_SQL"}:
            row["STATUS_CONVERSION"] = "STALE"
            row["STATUS_TUNING"] = "STALE"
    elif name == "NEXT_MIG_RAG_INFO":
        for job in rows(state, "NEXT_SQL_INFO"):
            if job.get("STATUS_TUNING"):
                job["STATUS_TUNING"] = "STALE"
                job["FORMATTED_SQL"] = None
    state["last_target"] = {"table": name, "key": key}
    state["pending_action"] = None
    return [name] + (["NEXT_MIG_INFO_DTL"] if name == "NEXT_MIG_INFO" else []), action + ": " + name + " " + dumps(key)


def filtered(data, condition):
    if not condition or not condition.strip():
        return data
    # Deliberately small condition language; never eval source text.
    m = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)\s*(=|>=|<=|>|<|!=)\s*('(?:[^']|'')*'|-?\d+(?:\.\d+)?)\s*", condition)
    if not m:
        raise ValueError("CONDITION은 단일 컬럼 비교만 지원합니다.")
    col, op, text = m.groups()
    value = text[1:-1].replace("''", "'") if text.startswith("'") else float(text)
    checks = {"=": lambda a: a == value, "!=": lambda a: a != value,
              ">": lambda a: a > value, "<": lambda a: a < value,
              ">=": lambda a: a >= value, "<=": lambda a: a <= value}
    if any(col not in r for r in data):
        raise ValueError("CONDITION에 없는 컬럼")
    return [r for r in data if r[col] is not None and checks[op](r[col])]


def row_concat(record, columns):
    # Length-prefix JSON tokens so delimiters, null, empty text and types cannot collide.
    tokens = [dumps(record.get(column)) for column in columns]
    return "".join(str(len(token)) + ":" + token for token in tokens)


def migration_verification(state, mapping):
    details = sorted([d for d in rows(state, "NEXT_MIG_INFO_DTL") if d["MAP_ID"] == mapping["MAP_ID"]], key=lambda d: d["MAP_DTL"])
    columns = [d["TO_COL"] for d in details]
    spec = table(state, mapping["TO_TABLE"])
    keys = spec["primary_key"]
    source = [{d["TO_COL"]: row.get(d["FR_COL"]) for d in details}
              for row in filtered(rows(state, mapping["FR_TABLE"]), mapping.get("CONDITION"))]
    expected = {tuple(row.get(k) for k in keys): row for row in source}
    actual = {tuple(row.get(k) for k in keys): row for row in spec["rows"]}
    scoped = {key: actual[key] for key in expected if key in actual}
    column_counts = [{"column": column, "source": sum(r.get(column) is not None for r in source),
                      "target": sum(r.get(column) is not None for r in scoped.values())} for column in columns]
    matched, mismatches = 0, []
    for key, record in expected.items():
        target = scoped.get(key)
        if target is not None and row_concat(record, columns) == row_concat(target, columns):
            matched += 1
        else:
            mismatches.append({"key": list(key), "columns": columns if target is None else
                [column for column in columns if dumps(record.get(column)) != dumps(target.get(column))]})
    count_pass = len(source) == len(scoped) and all(c["source"] == c["target"] for c in column_counts)
    return {"count_result": "PASS" if count_pass else "FAIL", "source_count": len(source),
            "target_count": len(scoped), "target_total_count": len(spec["rows"]),
            "preserved_extra_rows": len(actual) - len(scoped), "column_counts": column_counts,
            "concat_result": "PASS" if not mismatches else "FAIL", "matched_count": matched,
            "mismatch_count": len(mismatches), "mismatches": mismatches[:10],
            "comparison_scope": "매핑 조건 적용 후 원천 PK에 대응하는 대상 행/매핑 컬럼",
            "verified_at_revision": state["revision"] + 1, "method": "JSON_COUNT_AND_PK_CONCAT"}


def migration_verify_sqls(mapping, details, keys):
    columns = [d["TO_COL"] for d in details]
    projection = ", ".join("S." + d["FR_COL"] + " AS " + d["TO_COL"] for d in details)
    condition = " WHERE " + mapping["CONDITION"] if mapping.get("CONDITION") else ""
    join = " AND ".join("A." + k + " = T." + k for k in keys)
    cte = ("WITH ASIS_DATA AS (\n  SELECT " + projection + " FROM " + mapping["FR_TABLE"] + " S" + condition +
           "\n), TOBE_SCOPE AS (\n  SELECT T.* FROM " + mapping["TO_TABLE"] +
           " T WHERE EXISTS (SELECT 1 FROM ASIS_DATA A WHERE " + join + ")\n)")
    counts = ", ".join("COUNT(" + column + ") AS C" + str(i) for i, column in enumerate(columns))
    equal = " AND ".join(["A.ROW_CNT = T.ROW_CNT"] + ["A.C" + str(i) + " = T.C" + str(i) for i in range(len(columns))])
    count_sql = (cte + ", ASIS_COUNTS AS (SELECT COUNT(*) AS ROW_CNT, " + counts +
        " FROM ASIS_DATA), TOBE_COUNTS AS (SELECT COUNT(*) AS ROW_CNT, " + counts + " FROM TOBE_SCOPE)\n" +
        "SELECT A.ROW_CNT AS ASIS_CNT, T.ROW_CNT AS TOBE_CNT,\n  CASE WHEN " + equal +
        " THEN 'PASS' ELSE 'FAIL' END AS COUNT_RESULT\nFROM ASIS_COUNTS A CROSS JOIN TOBE_COUNTS T")
    def concat(alias):
        parts = []
        for column in columns:
            value = alias + "." + column
            text = "CAST(" + value + " AS VARCHAR2(4000))"
            parts.append("CASE WHEN " + value + " IS NULL THEN 'N;' ELSE 'V' || LENGTH(" + text + ") || ':' || " + text + " || ';' END")
        return " || ".join(parts)
    # Build concatenation once per dataset, then join records by PK.
    concat_sql = (cte + ", A_ROWS AS (SELECT A.*, " + concat("A") + " AS ROW_CONCAT FROM ASIS_DATA A),\n" +
        "T_ROWS AS (SELECT T.*, " + concat("T") + " AS ROW_CONCAT FROM TOBE_SCOPE T)\n" +
        "SELECT COUNT(*) AS ASIS_CNT,\n  COUNT(CASE WHEN T." + keys[0] +
        " IS NOT NULL AND A.ROW_CONCAT = T.ROW_CONCAT THEN 1 END) AS MATCH_CNT,\n  COUNT(*) - COUNT(CASE WHEN T." +
        keys[0] + " IS NOT NULL AND A.ROW_CONCAT = T.ROW_CONCAT THEN 1 END) AS MISMATCH_CNT\n" +
        "FROM A_ROWS A LEFT JOIN T_ROWS T ON " + join)
    note = "-- 참고 SQL: Oracle 미실행. 실제 DDL 자료형/NLS에 맞춘 변환 검토 필요.\n"
    return note + count_sql, note + concat_sql


def migration_report(state, results):
    selected = [m for m in rows(state, "NEXT_MIG_INFO") if m["MAP_ID"] in {r["MAP_ID"] for r in results}]
    names = list(dict.fromkeys(n for m in selected for n in (m["FR_TABLE"], m["TO_TABLE"])))
    lines = ["### AS-IS / TO-BE 데이터 조회", "", markdown(state, names), "", "### 이관 처리 건수", "", "| 매핑 ID | 추가 | 갱신 | 동일 |", "| --- | --- | --- | --- |"]
    lines += ["| {MAP_ID} | {inserted} | {updated} | {unchanged} |".format(**item) for item in results]
    lines += ["", "### 이관 검증 결과", "", "| 매핑 ID | 원천 건수 | 대상 대응 건수 | COUNT 비교 | CONCAT 일치 | CONCAT 불일치 | CONCAT 비교 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for result in results:
        v = result["verification"]
        lines.append("| {0} | {source_count} | {target_count} | {count_result} | {matched_count} | {mismatch_count} | {concat_result} |".format(result["MAP_ID"], **v))
    lines += ["", "조건 적용 원천의 PK와 매핑 컬럼을 기준으로 JSON 데이터를 비교했습니다. 원천 범위 밖의 기존 대상 행은 보존합니다."]
    passed = all(r["verification"]["count_result"] == "PASS" and r["verification"]["concat_result"] == "PASS" for r in results)
    lines += ["", "**최종 결과: " + ("DEMO-PASS" if passed else "DEMO-FAIL") + " — JSON 더미 데이터 비교 결과이며 Oracle 실행 결과가 아닙니다.**"]
    return "\n".join(lines) + "\n\n" + migration_sql_report(state, [item["MAP_ID"] for item in results])


def migration_sql_report(state, ids=None):
    lines = []
    for mapping in rows(state, "NEXT_MIG_INFO"):
        if not mapping.get("MIG_SQL") or (ids is not None and mapping["MAP_ID"] not in ids):
            continue
        lines += ["", "### MAP_ID " + str(mapping["MAP_ID"]) + " 생성 SQL"]
        for field in ("MIG_SQL", "VERIFY_SQL", "VERIFY2_SQL"):
            lines += ["", "**" + field + "**", "", "```sql", mapping.get(field) or "-- 아직 생성되지 않았습니다.", "```"]
    if not lines:
        return "아직 생성된 이관 SQL이 없습니다. 이관 실행 후 MIG_SQL·VERIFY_SQL·VERIFY2_SQL을 확인할 수 있습니다."
    return "\n".join(lines)


def migration(state, ids):
    jobs = [m for m in rows(state, "NEXT_MIG_INFO") if m.get("USE_YN") == "Y" and (not ids or m["MAP_ID"] in ids)]
    if ids and set(ids) != {m["MAP_ID"] for m in jobs}:
        raise ValueError("없거나 비활성인 MAP_ID")
    if not jobs:
        raise ValueError("실행할 매핑이 없습니다.")
    pending = sorted(jobs, key=lambda m: (m.get("PRIORITY") or 5, m["MAP_ID"]))
    selected_ids = {m["MAP_ID"] for m in jobs}
    completed, stats, changed = set(), [], ["NEXT_MIG_INFO"]
    while pending:
        ready = [m for m in pending if m.get("PRIOR_MAP_ID") is None or m["PRIOR_MAP_ID"] in completed or
                 (m["PRIOR_MAP_ID"] not in selected_ids and any(p["MAP_ID"] == m["PRIOR_MAP_ID"] and p.get("STATUS") == "DEMO-PASS" for p in rows(state, "NEXT_MIG_INFO")))]
        if not ready:
            raise ValueError("선행 매핑을 먼저 실행해야 합니다.")
        m = ready[0]
        spec = table(state, m["TO_TABLE"])
        details = sorted([d for d in rows(state, "NEXT_MIG_INFO_DTL") if d["MAP_ID"] == m["MAP_ID"]], key=lambda d: d["MAP_DTL"])
        if not details or not set(spec["primary_key"]) <= {d["TO_COL"] for d in details}:
            raise ValueError("대상 기본 키를 포함한 컬럼 매핑이 필요합니다.")
        expected = [{d["TO_COL"]: r.get(d["FR_COL"]) for d in details} for r in filtered(rows(state, m["FR_TABLE"]), m.get("CONDITION"))]
        index = {tuple(r.get(k) for k in spec["primary_key"]): r for r in spec["rows"]}
        counts = {"MAP_ID": m["MAP_ID"], "inserted": 0, "updated": 0, "unchanged": 0, "verified": 0}
        seen = set()
        for record in expected:
            key = tuple(record.get(k) for k in spec["primary_key"])
            if None in key or key in seen:
                raise ValueError("매핑 결과의 중복/빈 대상 키")
            seen.add(key)
            if key not in index:
                full = {c: record.get(c) for c in spec["columns"]}
                spec["rows"].append(full)
                index[key] = full
                counts["inserted"] += 1
            elif any(dumps(index[key].get(c)) != dumps(v) for c, v in record.items()):
                index[key].update(record)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            if not all(index[key].get(c) == v for c, v in record.items()):
                raise ValueError("샘플 레코드 검증 실패")
            counts["verified"] += 1
        on = " AND ".join("T." + k + " = S." + next(d["FR_COL"] for d in details if d["TO_COL"] == k) for k in spec["primary_key"])
        updates = [d for d in details if d["TO_COL"] not in spec["primary_key"]]
        source = "(SELECT * FROM " + m["FR_TABLE"] + (" WHERE " + m["CONDITION"] if m.get("CONDITION") else "") + ")"
        sql = "MERGE INTO " + m["TO_TABLE"] + " T USING " + source + " S ON (" + on + ")"
        if updates:
            sql += " WHEN MATCHED THEN UPDATE SET " + ", ".join("T." + d["TO_COL"] + " = S." + d["FR_COL"] for d in updates)
        sql += " WHEN NOT MATCHED THEN INSERT (" + ", ".join(d["TO_COL"] for d in details) + ") VALUES (" + ", ".join("S." + d["FR_COL"] for d in details) + ")"
        verify_sql, verify2_sql = migration_verify_sqls(m, details, spec["primary_key"])
        verification = migration_verification(state, m)
        if verification["count_result"] != "PASS" or verification["concat_result"] != "PASS":
            raise ValueError("MAP_ID " + str(m["MAP_ID"]) + " COUNT/CONCAT 검증 실패")
        counts["verification"] = verification
        m.update({"MIG_SQL": sql, "VERIFY_SQL": verify_sql, "VERIFY2_SQL": verify2_sql,
                  "STATUS": "DEMO-PASS", "BATCH_CNT": (m.get("BATCH_CNT") or 0) + 1,
                  "LOG": dumps(counts)})
        changed.append(m["TO_TABLE"])
        stats.append(counts)
        completed.add(m["MAP_ID"])
        pending.remove(m)
    return changed, stats


def check_select(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 12000:
        raise ValueError("SQL은 12000자 이내의 비어 있지 않은 문자열")
    # A lexical guard, not an Oracle parser. SQL is never executed.
    scrubbed = re.sub(r"'(?:[^']|'')*'", "''", sql)
    scrubbed = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", " ", scrubbed)
    if not re.match(r"\s*(SELECT|WITH)\b", scrubbed, re.I) or ";" in scrubbed.rstrip().rstrip(";"):
        raise ValueError("단일 SELECT/WITH SQL만 지원")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|EXECUTE|BEGIN|COMMIT)\b", scrubbed, re.I):
        raise ValueError("조회 SQL 이외 문장은 지원하지 않습니다.")
    return sql.strip().rstrip(";")


def sql_tokens(sql):
    """Token comparison for formatting; preserve literal and quoted-name text."""
    pattern = r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*[\s\S]*?\*/|:[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_$#]*|\d+(?:\.\d+)?|<>|!=|>=|<=|\|\||[^\s]"
    return [token if token.startswith(("'", '"', ":")) else token.upper()
            for token in re.findall(pattern, sql) if not token.startswith(("--", "/*"))]


def where_one(sql):
    """Normalize only the outer predicate, keeping literals/comments and OR scope."""
    checked = check_select(sql)
    masked = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*[\s\S]*?\*/",
                    lambda m: " " * len(m[0]), checked)
    depth, words = 0, []
    for match in re.finditer(r"[()]|\b[A-Za-z_][A-Za-z_0-9]*\b", masked):
        token = match[0].upper()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            words.append((token, match.start(), match.end()))
    where = next((w for w in words if w[0] == "WHERE"), None)
    tail = next((w[1] for w in words if w[0] in ("GROUP", "HAVING", "ORDER", "OFFSET", "FETCH", "UNION", "MINUS", "INTERSECT") and (not where or w[1] > where[1])), len(checked))
    if any(w[0] in ("UNION", "MINUS", "INTERSECT") for w in words):
        raise ValueError("집합 연산 SQL의 튜닝은 현재 시연 범위 밖입니다.")
    if where:
        predicate = checked[where[2]:tail].strip()
        if re.match(r"^1\s*=\s*1(?:\s+AND\b|\s*$)", masked[where[2]:tail].strip(), re.I):
            return checked
        return (checked[:where[1]] + "WHERE 1 = 1 AND (" + predicate + "\n) " + checked[tail:]).strip()
    # A newline prevents a trailing line comment from swallowing the new clause.
    return (checked[:tail].rstrip() + "\nWHERE 1 = 1\n" + checked[tail:]).strip()


def tuning_rules(state, scopes=None):
    return [r for r in rows(state, "NEXT_MIG_RAG_INFO")
            if r.get("USE_YN") == "Y" and r.get("CATEGORY") == "SQL_TUNING"
            and (scopes is None or not r.get("SOURCE_TABLES") or
                 set(x.strip() for x in r["SOURCE_TABLES"].split(",")) & set(scopes))]


def rule_enabled(rules, code):
    return any(str(r.get("GUIDANCE_TEXT", "")).startswith("[" + code + "]") for r in rules)


def tuning_guide(state):
    rules = tuning_rules(state)
    lines = ["### NEXT_MIG_RAG_INFO · 활성 SQL_TUNING 규칙", "",
             "튜닝 입력은 직전 TO_SQL이며 결과는 TUNED_TO_SQL에 저장합니다. 이 요청은 규칙 조회만 수행합니다."]
    if not rules:
        lines += ["", "활성 튜닝 규칙이 없습니다. NEXT_MIG_RAG_INFO에 규칙을 등록하세요."]
    for r in rules:
        lines += ["", "#### RAG_ID " + str(r["RAG_ID"]), r["GUIDANCE_TEXT"],
                  "대상 원천: " + str(r.get("SOURCE_TABLES") or "전체")]
        for field in ("SOURCE_SQL", "TARGET_SQL"):
            if r.get(field):
                lines += ["", field, "```sql", r[field], "```"]
    return "\n".join(lines)


def sql_table_refs(scrubbed):
    """Bounded SELECT grammar: named tables, comma joins and IN subqueries.

    This discovers references for static checks, not Oracle semantic validation.
    Inline views, CTEs, schema-qualified and quoted identifiers stay out of scope.
    """
    tokens = sql_tokens(scrubbed)
    if not tokens or tokens[0] != "SELECT":
        raise ValueError("현재 시연은 SELECT만 지원하며 CTE는 지원하지 않습니다.")
    ends = {"WHERE", "ON", "GROUP", "ORDER", "HAVING", "UNION", "OFFSET", "FETCH", "MINUS", "INTERSECT"}
    keywords = ends | {"SELECT", "FROM", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "AND", "OR"}
    levels, refs = [False], []
    for i, token in enumerate(tokens):
        if token == "(":
            levels.append(False)
        elif token == ")":
            if len(levels) == 1:
                raise ValueError("SQL 괄호 불일치")
            levels.pop()
        elif token in ends:
            levels[-1] = False
        elif token in ("FROM", "JOIN") or (token == "," and levels[-1]):
            levels[-1] = True
            name = tokens[i + 1] if i + 1 < len(tokens) else ""
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in keywords:
                raise ValueError("FROM/JOIN은 실제 테이블명만 지원합니다. inline view는 지원하지 않습니다.")
            j = i + 2
            if j < len(tokens) and tokens[j] == ".":
                raise ValueError("스키마 접두사는 현재 시연 범위 밖입니다.")
            if j < len(tokens) and tokens[j] == "AS":
                j += 1
            alias = tokens[j] if j < len(tokens) and re.fullmatch(r"[A-Z][A-Z0-9_]*", tokens[j]) and tokens[j] not in keywords else None
            refs.append((name, alias))
    if len(levels) != 1:
        raise ValueError("SQL 괄호 불일치")
    return refs


def check_target_sql(state, sql, scopes, source):
    checked = check_select(sql)
    scrubbed = re.sub(r"'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/", " ", checked)
    expected = {m["TO_TABLE"] for m in rows(state, "NEXT_MIG_INFO") if m.get("USE_YN") == "Y" and m["FR_TABLE"] in scopes}
    refs = sql_table_refs(scrubbed)
    if {name.upper() for name, alias in refs} != expected:
        raise ValueError("SQL의 FROM/JOIN 테이블이 활성 매핑 대상과 다릅니다.")
    aliases = {}
    keywords = {"WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "ON", "ORDER", "GROUP", "HAVING", "UNION", "OFFSET", "FETCH"}
    for name, alias in refs:
        aliases[name.upper()] = name.upper()
        if alias and alias.upper() not in keywords:
            if alias.upper() in aliases and aliases[alias.upper()] != name.upper():
                raise ValueError("다른 테이블에 같은 별칭을 재사용하는 중첩 SQL은 지원하지 않습니다.")
            aliases[alias.upper()] = name.upper()
    for alias, column in re.findall(r"\b([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)\b", scrubbed, re.I):
        if alias.upper() not in aliases or column.upper() not in table(state, aliases[alias.upper()])["columns"]:
            raise ValueError("SQL에 없는 대상 별칭/컬럼: " + alias + "." + column)
    strip_literals = lambda text: re.sub(r"'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/", " ", text)
    binds = lambda text: set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", strip_literals(text)))
    if binds(checked) != binds(source):
        raise ValueError("원본과 생성 SQL의 바인드 이름이 다릅니다.")
    return checked


def sql_work(state, stage, ids, artifacts):
    jobs = [j for j in rows(state, "NEXT_SQL_INFO") if not ids or j["SQL_SEQ"] in ids]
    if not jobs or (ids and set(ids) != {j["SQL_SEQ"] for j in jobs}):
        raise ValueError("대상 SQL_SEQ가 없습니다.")
    if not isinstance(artifacts, list):
        raise ValueError("sql_results 배열이 필요합니다.")
    results = {a["sql_seq"]: a for a in artifacts}
    if len(results) != len(artifacts) or set(results) != {j["SQL_SEQ"] for j in jobs}:
        raise ValueError("SQL 산출물 누락/중복/범위 오류")
    active = [m for m in rows(state, "NEXT_MIG_INFO") if m.get("USE_YN") == "Y"]
    for job in jobs:
        scopes = [x.strip() for x in job["TARGET_TABLE"].split(",")]
        if stage != "conversion" and any(not any(m["FR_TABLE"] == scope for m in active) for scope in scopes):
            raise ValueError("사용 가능한 원천→대상 매핑이 없습니다.")
        if stage in ("tuning", "formatting") and job.get("STATUS_CONVERSION") != "DEMO-GENERATED":
            raise ValueError("변환을 먼저 실행하세요.")
        if stage == "formatting" and job.get("STATUS_TUNING") != "DEMO-PROPOSED":
            raise ValueError("튜닝을 먼저 실행하세요.")
        a = results[job["SQL_SEQ"]]
        if stage == "conversion":
            # Demo conversion accepts generated SELECTs without mapping/schema
            # checks. Partial mappings and complex SELECTs must not block a demo.
            sql = check_select(a.get("to_sql"))
            job.update({"TO_SQL": sql, "STATUS_CONVERSION": "DEMO-GENERATED", "STATUS_TUNING": None,
                        "TUNED_TO_SQL": None, "FORMATTED_SQL": None, "TUNED_RESULT": None,
                        "BIND_SQL": sql, "TEST_SQL": "-- Oracle 미실행: 실제 결과 동등성 미검증"})
        if stage == "tuning":
            rules = tuning_rules(state, scopes)
            proposed = check_target_sql(state, a.get("tuned_sql"), scopes, job["TO_SQL"])
            if not rules and sql_tokens(proposed) != sql_tokens(job["TO_SQL"]):
                raise ValueError("활성 튜닝 규칙이 없습니다. TO_SQL을 유지하거나 RAG_INFO에 규칙을 등록하세요.")
            if rule_enabled(rules, "ANSI_JOIN") and re.search(r"\(\s*\+\s*\)", re.sub(r"'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/", " ", proposed)):
                raise ValueError("튜닝 산출물은 Oracle (+) 대신 의미를 보존한 ANSI JOIN을 사용하세요.")
            tuned = where_one(proposed) if rule_enabled(rules, "WHERE_ONE") else proposed
            applied = ",".join(str(r["RAG_ID"]) for r in rules) or "없음"
            job.update({"TUNED_FR_SQL": job["TO_SQL"], "TUNED_TO_SQL": tuned,
                        "TUNED_RESULT": str(a.get("reason") or "변경 제안") + " | 참고 RAG_ID: " + applied + ". Oracle 동등성·성능 미검증.",
                        "STATUS_TUNING": "DEMO-PROPOSED", "FORMATTED_SQL": None})
        if stage == "formatting":
            formatted = check_select(a.get("formatted_sql"))
            if sql_tokens(formatted) != sql_tokens(job["TUNED_TO_SQL"]):
                raise ValueError("Formatting 결과의 SQL 토큰이 원본과 다릅니다.")
            job["FORMATTED_SQL"] = formatted
        job["BATCH_CNT"] = (job.get("BATCH_CNT") or 0) + 1
        job["LOG"] = "시연 산출물 생성; Oracle 파싱·실행·성능 검증 없음"
    return ["NEXT_SQL_INFO"], len(jobs)


def requested_stages(request):
    """Conservative scope boundary, independent of the planner's stage choice."""
    text = request.lower()
    if re.search(r"규칙|룰|rag|guidance_text", text) and re.search(r"수정|변경|등록|추가|삭제|비활성|활성화|update|insert|delete", text):
        return {"manage"}
    text = re.sub(r"(?:sql\s*)?튜닝\s*(?:가이드|규칙|룰)|tuning\s*guide", "guide", text)
    if re.search(r"조회|보여|알려|결과|show|list", text) and not re.search(r"실행|진행|변환해|튜닝해|run|execute", text):
        return {"query"}
    stages = set()
    for stage, pattern in (("migration", r"migration|마이그레이션|이관"),
                           ("conversion", r"conversion|변환"), ("tuning", r"tuning|튜닝"),
                           ("formatting", r"formatting|포매팅|서식")):
        if re.search(pattern, text):
            stages.add(stage)
    return stages or ({"query"} if re.search(r"가이드|guide|조회|보여", text) else set())


def enforce_request_scope(plan, request):
    if not request:
        return  # Legacy offline callers. Published DSL always passes the request.
    scopes = requested_stages(request)
    if plan.get("action") == "run" and plan.get("stage") not in scopes:
        raise ValueError("요청 범위와 실행 단계가 다릅니다. 이관·SQL 변환·튜닝 중 실행할 단계를 명시하세요. 데이터 작업은 실행하지 않았습니다.")
    if scopes == {"query"} and plan.get("action") not in ("query", "clarify", "error"):
        raise ValueError("조회 요청으로 데이터를 변경할 수 없습니다.")
    if scopes & {"migration", "conversion", "tuning", "formatting"} and plan.get("action") in ("insert", "update", "delete"):
        raise ValueError("단계 실행 요청으로 매핑·SQL 작업 정의를 임의 변경할 수 없습니다.")


def execute_one(state_json, plan_json, request_id, request=""):
    original = validate(json.loads(state_json))
    if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
        raise ValueError("request_id 필요 (메인 Chatflow에서 고정 전달)")
    base_hash = digest(original)
    state = copy.deepcopy(original)
    status, names, migration_results, show_migration_sql = "OK", [], [], False
    query_view = ""
    try:
        text = plan_json.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        plan = json.loads(text)
        if not isinstance(plan, dict):
            raise ValueError("계획은 JSON 객체여야 합니다.")
        enforce_request_scope(plan, request)
        action = plan.get("action")
        if action == "error":
            raise ValueError(str(plan.get("message") or "작업 계획을 생성하지 못했습니다."))
        elif action == "clarify":
            message = str(plan.get("question") or "작업 대상과 변경값을 알려주세요.")
            state["pending_action"] = {"question": message, "context": plan.get("context", "")}
            status = "NEEDS_INPUT"
        elif action == "query":
            names = ["NEXT_MIG_RAG_INFO"] if plan.get("rule_category") == "SQL_TUNING" else (plan.get("tables") or list(state["tables"]))
            show_migration_sql = plan.get("include_sql") is True and "NEXT_MIG_INFO" in names
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ValueError("tables는 테이블명 배열")
            for name in names:
                table(state, name)
            message = "현재 테이블 조회"
            if plan.get("rule_category") == "SQL_TUNING":
                message = "SQL 튜닝 가이드 조회 (실행하지 않음)\n\n" + tuning_guide(state)
                names = []
            elif plan.get("query_view") == "sql_source":
                query_view = "sql_source"
                message, names = sql_source_report(state), []
        elif action in ("insert", "update", "delete"):
            names, message = manage(state, plan)
        elif action == "run":
            stage = plan.get("stage")
            if stage not in ("migration", "conversion", "tuning", "formatting"):
                raise ValueError("지원하지 않는 실행 단계")
            map_ids, sql_ids = plan.get("map_ids", []), plan.get("sql_seqs", [])
            if not isinstance(map_ids, list) or not isinstance(sql_ids, list) or not all(type(x) is int for x in map_ids + sql_ids):
                raise ValueError("map_ids/sql_seqs는 정수 배열")
            parts = []
            if stage == "migration":
                changed, counts = migration(state, map_ids)
                names += changed
                migration_results = counts
                parts.append("이관 " + str(len(counts)) + "개 매핑 완료")
            if stage != "migration":
                changed, count = sql_work(state, stage, sql_ids, plan.get("sql_results", []))
                names += changed
                parts.append("SQL " + str(count) + "건 " + stage)
            message = "; ".join(parts)
            state["pending_action"] = None
            state["last_target"] = {"stage": stage, "map_ids": map_ids, "sql_seqs": sql_ids}
        elif action == "export":
            names = []
            message = "현재 상태 JSON (아래 내용을 저장하여 새 대화의 sample_json에 입력)\n\n```json\n" + dumps(state) + "\n```"
        else:
            raise ValueError("지원하지 않는 action")
        validate(state)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        state = copy.deepcopy(original)
        migration_results = []
        show_migration_sql = False
        query_view = ""
        status, names, message = "ERROR", [], str(exc)
    log(state, "request", status, message[:1000])
    state["revision"] += 1
    state["last_request_id"] = request_id
    validate(state)
    rendered = message + ("\n\n" + markdown(state, names) if names else "")
    if migration_results:
        rendered += "\n\n" + migration_report(state, migration_results)
    elif show_migration_sql:
        rendered += "\n\n" + migration_sql_report(state)
    envelope = {"kind": RESULT_KIND, "request_id": request_id, "base_hash": base_hash,
                "base_revision": original["revision"], "status": status,
                "state_json": dumps(state), "answer": rendered, "migration_results": migration_results,
                "show_migration_sql": show_migration_sql, "query_view": query_view}
    return envelope


def execute(state_json, plan_json, request_id, request=""):
    """Run explicitly ordered actions as one state update; no implicit full stage."""
    try:
        # The unqualified demo lookup has a concrete default, never a missing
        # customer/table clarification. This is inside the actually invoked Tool.
        compact_request = re.sub(r"[\s?!.]", "", request)
        if compact_request in ("매핑룰조회해줘", "매핑룰조회", "매핑규칙조회해줘", "매핑룰보여줘"):
            plan_json = dumps({"action": "query", "tables": ["NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL"]})
        elif is_sql_source_query(request):
            plan_json = dumps({"action": "query", "tables": ["NEXT_SQL_INFO"], "query_view": "sql_source"})
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", plan_json.strip())
        plan = json.loads(text)
        actions = plan.get("actions") if isinstance(plan, dict) else None
        if actions is None:
            return execute_one(state_json, plan_json, request_id, request)
        if not isinstance(actions, list) or not 1 <= len(actions) <= 6 or any(not isinstance(a, dict) or "actions" in a for a in actions):
            raise ValueError("actions는 최대 6개의 단일 작업 계획 배열이어야 합니다.")
        for action in actions:
            enforce_request_scope(action, request)
        original = validate(json.loads(state_json))
        current, steps, reports, migrations = original, [], [], []
        for i, action in enumerate(actions):
            current = copy.deepcopy(current)
            current["revision"] = original["revision"]
            result = execute_one(dumps(current), dumps(action), request_id, request)
            steps.append({"action": action.get("action"), "stage": action.get("stage"), "status": result["status"]})
            if result["status"] != "OK":
                raise ValueError(str(i + 1) + "번째 작업 실패로 이번 요청의 업무 변경을 취소했습니다: " + result["answer"])
            current = json.loads(result["state_json"])
            reports.append("### 요청 작업 " + str(i + 1) + " · " + str(action.get("stage") or action.get("action")) + "\n\n" + result["answer"])
            migrations += result.get("migration_results", [])
        return {"kind": RESULT_KIND, "request_id": request_id, "base_hash": digest(original),
                "base_revision": original["revision"], "status": "OK", "state_json": dumps(current),
                "answer": "요청한 작업 " + str(len(steps)) + "개를 순서대로 처리했습니다.\n\n" + "\n\n".join(reports),
                "migration_results": migrations, "show_migration_sql": False, "steps": steps}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return execute_one(state_json, dumps({"action": "error", "message": str(exc)}), request_id, request)


def main(state_json: str, plan_json: str, request_id: str, request: str) -> dict:
    if not isinstance(request, str) or not request.strip():
        raise ValueError("원래 사용자 요청(request)을 prepare 노드에서 연결해야 합니다.")
    result = execute(state_json, plan_json, request_id, request)
    return {"result_json": dumps(result)}


"""Code node before the Tool's LLM. Combined with engine.py by the builder."""


def main(session_json: str, request: str, request_id: str,
         asis_hr_dept_json: str, asis_hr_emp_json: str,
         tobe_hr_dept_json: str, tobe_hr_emp_json: str,
         next_mig_info_json: str, next_mig_info_dtl_json: str,
         next_sql_info_json: str, next_mig_rag_info_json: str,
         next_mig_log_json: str) -> dict:
    inputs = locals().copy()
    if not isinstance(session_json, str) or not session_json.strip():
        raise ValueError("session_json이 비어 있습니다. Agent Tool의 session_json을 대화 상태 준비.session_transport에 연결하세요. 모델 자동 생성으로 바꾸지 마세요.")
    if session_json.startswith("SM_STATE_V3:"):
        if len(session_json) > MAX_STATE * 2:
            raise ValueError("session_json 전달 크기 제한 초과")
        try:
            session_json = base64.b64decode(session_json[len("SM_STATE_V3:"):], altchars=b"-_", validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("session_json 전달 패킷이 손상됐습니다.") from exc
    try:
        session = json.loads(session_json)
    except (ValueError, TypeError) as exc:
        raise ValueError("session_json이 올바른 JSON이 아닙니다.") from exc
    if not isinstance(session, dict):
        raise ValueError("session_json은 JSON 객체여야 합니다.")
    if session.get("kind") == "smartmigrate.session.v2":
        state = session.get("state")
        packet_id = session.get("request_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            raise ValueError("session_json의 request_id가 비어 있습니다.")
        if request_id and request_id != packet_id:
            raise ValueError("session_json과 request_id의 요청 ID가 다릅니다.")
        request_id = packet_id
        if "user_request" in session:
            # Authoritative sys.query travels through the manually bound packet,
            # so the Agent cannot broaden execution by rewriting its request arg.
            request = session["user_request"]
    else:
        # Compatibility with already published v1 table-by-table Tools.
        state = session
        state.setdefault("tables", {})
        for name, variable in TABLE_INPUTS.items():
            if not isinstance(inputs[variable], str) or not inputs[variable].strip():
                raise ValueError(variable + "이 비어 있습니다. 개별 입력 연결 또는 v2 통합 session_json을 사용하세요.")
            try:
                state["tables"][name] = json.loads(inputs[variable])
            except (ValueError, TypeError) as exc:
                raise ValueError(variable + "이 올바른 JSON이 아닙니다.") from exc
    state = validate(state)
    if not isinstance(request, str) or not request.strip() or len(request) > 6000:
        raise ValueError("요청은 1~6000자")
    # Give a single-stage request only that stage's guidance. The complete
    # state is retained for execution, management and explicit rule queries.
    stages = requested_stages(request)
    category = "SQL_CONVERSION" if stages == {"conversion"} else "SQL_TUNING" if stages == {"tuning"} else None
    rules = [r for r in rows(state, "NEXT_MIG_RAG_INFO") if (r.get("USE_YN") == "Y" or r.get("CATEGORY") == "SQL_CONVERSION")
             and (category is None or r.get("CATEGORY") == category)]
    context_tables = copy.deepcopy(state["tables"])
    if category:
        context_tables["NEXT_MIG_RAG_INFO"]["rows"] = copy.deepcopy(rules)
    if category == "SQL_CONVERSION":
        # Previous generated/tuned SQL is output, not a conversion input.
        for job in context_tables["NEXT_SQL_INFO"]["rows"]:
            for field in ("TO_SQL", "BIND_SQL", "TEST_SQL", "TUNED_TO_SQL", "TUNED_FR_SQL", "TUNED_RESULT", "FORMATTED_SQL"):
                job[field] = None
    context = {"tables": context_tables, "last_target": state.get("last_target"),
               "pending_action": state.get("pending_action"), "reference_rules": rules}
    return {"context_json": dumps(context), "state_json": dumps(state),
            "request": request, "request_id": request_id}


prepare_legacy = main

"""Restore the exact state inside the executing Workflow Tool."""


def main(session_json: str, request: str) -> dict:
    # Keep legacy decoding isolated in prepare.py while publishing two inputs.
    return prepare_legacy(session_json=session_json, request=request, request_id="",
                          **{name: "" for name in TABLE_INPUTS.values()})
