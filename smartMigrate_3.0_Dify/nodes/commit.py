"""Verify actual Workflow Tool results and expose the declared Code outputs.

engine.py is prepended in exported DSL. Nested JSON strings occur in Workflow
Tool outputs and the official Function Calling strategy's tool_result logs.
execution_json is retained only for older local-execution callers/tests.
"""

def skip_variable_messages(text):
    """Skip opaque Python repr messages emitted by older official strategies.

    They precede the actual Workflow TEXT/JSON result. Never interpret their
    string payload as a result, and never execute/evaluate Python repr text.
    """
    offset = 0
    for _ in range(16):
        match = re.match(r"\s*tool response:\s*VariableMessage\(", text[offset:])
        if not match:
            return text[offset:]
        cursor = offset + match.end()
        nesting, quote, escaped = 1, None, False
        while cursor < len(text):
            char = text[cursor]
            cursor += 1
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in ("'", '"'):
                quote = char
            elif char == "(":
                nesting += 1
            elif char == ")":
                nesting -= 1
                if not nesting:
                    break
        if nesting:
            return ""
        offset = cursor
        if text[offset:offset + 1] == ".":
            offset += 1
    return ""


def envelopes(value, depth=0, target_kind=RESULT_KIND):
    if depth > 18:
        return []
    if isinstance(value, str):
        if len(value) > MAX_STATE * 3:
            return []
        value = skip_variable_messages(value)
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            # Older official strategies concatenate Workflow TEXT and JSON
            # messages, e.g. '{...}tool response: {...}.'. Decode bounded
            # top-level JSON segments instead of reading the Agent's prose.
            found, offset, attempts = [], 0, 0
            while offset < len(value) and attempts < 32:
                match = re.search(r"[\[{]", value[offset:])
                if not match:
                    break
                start = offset + match.start()
                attempts += 1
                try:
                    segment, end = json.JSONDecoder().raw_decode(value, start)
                    found.extend(envelopes(segment, depth + 1, target_kind))
                    offset = end
                except ValueError:
                    offset = start + 1
            return found
    if isinstance(value, dict):
        if value.get("kind") == target_kind:
            return [value]
        found = []
        for child in value.values():
            found.extend(envelopes(child, depth + 1, target_kind))
        return found
    if isinstance(value, list):
        found = []
        for child in value:
            found.extend(envelopes(child, depth + 1, target_kind))
        return found
    return []


def tool_responses(agent_json):
    """Only official CALL/ROUND outputs, not LLM responses or Tool inputs."""
    found = []
    if not isinstance(agent_json, list):
        return found
    for log_entry in agent_json:
        if not isinstance(log_entry, dict):
            continue
        data = log_entry.get("data")
        if (isinstance(data, dict) and str(log_entry.get("label", "")).startswith("CALL ")
                and data.get("tool_name") in ("smartmigrate_execute", "smart_migrate_tool")):
            # Official ReAct stores the CALL output directly in data.output.
            found.append(data.get("output"))
        output = data.get("output") if isinstance(data, dict) else None
        if not isinstance(output, dict):
            continue
        responses = output.get("tool_responses", [])
        if not isinstance(responses, list):
            responses = []
        for response in [output] + responses:
            if isinstance(response, dict) and response.get("tool_call_name") in ("smartmigrate_execute", "smart_migrate_tool"):
                found.append(response.get("tool_response"))
    return found


def commit_result(state_json: str, request_id: str, agent_json: list, agent_text: str, execution_json: str = None) -> dict:
    before = validate(json.loads(state_json))
    found = []
    sources = [execution_json] if execution_json is not None else tool_responses(agent_json)
    for response in sources:
        for item in envelopes(response):
            if item.get("request_id") == request_id:
                found.append(item)
    unique = {dumps(item): item for item in found}
    if not unique:
        responses = tool_responses(agent_json)
        empty_round = any(isinstance(e, dict) and isinstance(e.get("data"), dict)
                          and isinstance(e["data"].get("output"), dict)
                          and e["data"]["output"].get("llm_response") == ""
                          and e["data"]["output"].get("tool_responses") == [] for e in (agent_json or []))
        if responses:
            message = "Tool 응답은 수신했지만 현재 요청의 result_json 실행 결과를 찾지 못했습니다. 01 Workflow의 End 출력과 Tool 재발행, session_json 연결을 확인하세요."
            diagnostic = "TOOL_RESULT_MISSING"
        elif empty_round:
            message = "Agent의 모델 응답과 Tool 호출이 모두 비어 있습니다(tool_responses=[]). ROUND success는 Tool 실행 성공이 아닙니다. Agent 전략·모델의 도구 호출 호환성을 확인하세요. 매핑 대상 누락 오류가 아닙니다."
            diagnostic = "EMPTY_AGENT_RESPONSE"
        else:
            message = "Agent에서 검증 가능한 Tool 호출 결과를 받지 못했습니다. Agent의 CALL 로그와 Tool 연결을 확인하세요."
            diagnostic = "NO_TOOL_RESULT"
        return {"state_json": dumps(before), "answer": message + "\n\n이번 요청의 테이블 상태는 변경하지 않았습니다.",
                "commit_status": "NO_RESULT", "tool_diagnostic": diagnostic}
    if len(unique) != 1:
        return {"state_json": dumps(before), "answer": "한 대화 턴에 서로 다른 Tool 결과가 여러 개 반환되어 적용하지 않았습니다. 작업을 하나씩 요청해주세요.", "commit_status": "CONFLICT"}
    item = next(iter(unique.values()))
    try:
        if item.get("base_hash") != digest(before) or item.get("base_revision") != before["revision"]:
            raise ValueError("Tool의 원본 상태가 현재 대화 상태와 다릅니다.")
        after = validate(json.loads(item["state_json"]))
        if after["revision"] != before["revision"] + 1 or after.get("last_request_id") != request_id:
            raise ValueError("Tool 상태 revision/request_id 불일치")
        if item.get("status") not in ("OK", "ERROR", "NEEDS_INPUT") or not isinstance(item.get("answer"), str):
            raise ValueError("Tool 결과 형식 오류")
        migration_results = item.get("migration_results", [])
        if not isinstance(migration_results, list):
            raise ValueError("migration_results 형식 오류")
        return {"state_json": dumps(after), "answer": item["answer"], "commit_status": item["status"],
                "migration_results": migration_results, "show_migration_sql": item.get("show_migration_sql") is True,
                "steps": item.get("steps", []), "query_view": item.get("query_view", "")}
    except (ValueError, KeyError, TypeError) as exc:
        return {"state_json": dumps(before), "answer": "상태를 적용하지 않았습니다: " + str(exc), "commit_status": "REJECTED"}


def general_reply(request):
    text = re.sub(r"[\s?!.,~。]", "", (request or "").lower())
    if re.search(r"조회해|실행|진행|변환해|튜닝해|수정해|삭제해|등록해|추가해|내보내", text):
        return None
    if re.fullmatch(r"안녕(?:하세요|하십니까)?|안녕하세요반가워요|반가워(?:요)?|hi|hello|hey", text):
        return "안녕하세요! SQL깍두기의 SmartMigrate Agent입니다. 매핑룰 조회부터 시작하거나 SQL 변환 대상과 튜닝 가이드를 확인할 수 있습니다."
    if re.search(r"누구|어떤(?:에이전트|agent)|무슨(?:에이전트|agent)|뭐하는(?:에이전트|agent)|너.*소개|자기소개|정체", text):
        return "저는 SQL깍두기의 SmartMigrate Agent입니다. 확정한 매핑룰을 기준으로 데이터 이관·검증, AS-IS SQL의 TO-BE 변환, 규칙 기반 튜닝을 돕습니다. 현재 Dify 시연은 실제 DB 대신 JSON 샘플로 동작합니다."
    if re.search(r"어떤기능|무슨기능|기능.*(?:있|알려|소개)|할수있|할수잇|사용법|도움말|help", text):
        return ("매핑룰 조회·관리, JSON 데이터 이관과 COUNT·CONCAT 비교, SQL 변환 대상 조회, SQL Conversion, 튜닝 가이드 조회·적용을 지원합니다. "
                "두 테이블 조인과 바인드가 있는 SQL 한 개로 시연하며, 요청한 작업만 실행합니다. ‘매핑룰 조회해줘’ 또는 ‘SQL 변환 대상 조회해줘’로 시작해 보세요.")
    return None


def main(state_json: str, request_id: str, agent_json: list, agent_text: str, execution_json: str = None, user_request: str = "") -> dict:
    reply = general_reply(user_request)
    if reply:
        before = validate(json.loads(state_json))
        facts = {"commit_status": "CHAT", "verified": False, "summary": reply, "suggested_requests": [], "general_chat": True}
        return {"state_json": dumps(before), "answer": reply, "commit_status": "CHAT", "narrative_json": dumps(facts)}
    result = commit_result(state_json, request_id, agent_json, agent_text, execution_json)
    # A missing Agent call cannot be repaired inside the Workflow Tool. For this
    # exact read-only request, show the already validated conversation snapshot.
    # Never use this path for writes or when a Tool response was received.
    if (execution_json is None and result["commit_status"] == "NO_RESULT"
            and not tool_responses(agent_json) and not (agent_text or "").strip()
            and is_sql_source_query(user_request)):
        before = validate(json.loads(state_json))
        answer = sql_source_report(before) + "\n\n조회 경로: 현재 대화의 JSON 상태. 이번 턴에 Agent Tool 호출은 발생하지 않았습니다."
        facts = {"commit_status": "READ_ONLY", "verified": True, "summary": "NEXT_SQL_INFO의 FR_SQL을 현재 상태에서 조회했습니다.",
                 "sql_source_query": {"table": "NEXT_SQL_INFO", "count": len(rows(before, "NEXT_SQL_INFO")), "column": "FR_SQL"},
                 "execution_source": "conversation_state", "tool_called": False}
        return {"state_json": dumps(before), "answer": answer, "commit_status": "READ_ONLY", "narrative_json": dumps(facts)}
    facts = {"commit_status": result["commit_status"], "verified": False,
             "summary": result["answer"], "suggested_requests": [],
             "execution_environment": "JSON 테이블 시연. Oracle DB에서 실행하지 않음."}
    if result.get("tool_diagnostic"):
        facts["tool_diagnostic"] = result["tool_diagnostic"]
    if result["commit_status"] in ("OK", "ERROR", "NEEDS_INPUT"):
        facts["verified"] = True
    if result["commit_status"] == "OK":
        state = json.loads(result["state_json"])
        executed = result.get("migration_results", [])
        names = [name for name in re.findall(r"^### ([A-Z][A-Z0-9_]*)\s*$", result["answer"], re.M)
                 if name in state["tables"]]
        if executed:
            # Data tables are rendered once in migration_report below.
            data_names = {m[field] for m in rows(state, "NEXT_MIG_INFO")
                          if m["MAP_ID"] in {r["MAP_ID"] for r in executed} for field in ("FR_TABLE", "TO_TABLE")}
            names = [name for name in names if name not in data_names]
        if names and not result.get("steps") and result.get("query_view") != "sql_source":
            prefix = result["answer"].split("### ", 1)[0].strip()
            # Regenerate tables from verified state, including old Tool versions
            # that put blank lines between every Markdown table row.
            result["answer"] = prefix + "\n\n" + markdown(state, names)
            facts["summary"] = prefix
            facts["displayed_tables"] = [{"table": name, "row_count": len(rows(state, name))} for name in dict.fromkeys(names)]
        if result.get("query_view") == "sql_source":
            result["answer"] = sql_source_report(state)
            facts["sql_source_query"] = {"table": "NEXT_SQL_INFO", "count": len(rows(state, "NEXT_SQL_INFO")), "column": "FR_SQL"}
            facts["summary"] = "NEXT_SQL_INFO의 AS-IS SQL 대상 " + str(len(rows(state, "NEXT_SQL_INFO"))) + "건 조회. 변환·이관·튜닝 실행 없음."
        elif result.get("steps"):
            facts["steps"] = result["steps"]
            facts["summary"] = "요청한 작업을 순서대로 처리했습니다. " + dumps(result["steps"])
        elif executed:
            result["answer"] += "\n\n" + migration_report(state, executed)
            facts["migration_results"] = executed
            facts["suggested_requests"] = ["SQL 변환해줘", "SQL 튜닝해줘", "SQL 서식 정리해줘"]
        elif result.get("show_migration_sql"):
            result["answer"] += "\n\n" + migration_sql_report(state)
            facts["sql_artifacts"] = [{"map_id": m["MAP_ID"], "generated": [field for field in ("MIG_SQL", "VERIFY_SQL", "VERIFY2_SQL") if m.get(field)]} for m in rows(state, "NEXT_MIG_INFO")]
            facts["suggested_requests"] = ["SQL 변환해줘"]
        elif set(names) & {"NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL"}:
            facts["mapping_status"] = [{"map_id": m["MAP_ID"], "source": m["FR_TABLE"],
                "target": m["TO_TABLE"], "status": m.get("STATUS"), "enabled": m.get("USE_YN") == "Y",
                "source_rows": len(rows(state, m["FR_TABLE"])), "target_rows": len(rows(state, m["TO_TABLE"]))}
                for m in rows(state, "NEXT_MIG_INFO")]
            try:
                # Dry-run on a deep copy: same filters, dependencies, key matching
                # and validation as execution, without changing conversation state.
                _, preview = migration(copy.deepcopy(state), [])
                facts["migration_preview"] = [{key: value for key, value in item.items() if key != "verification"} for item in preview]
                facts["suggested_requests"] = ["이관 실행해줘", "SQL 변환해줘", "SQL 튜닝해줘", "SQL 서식 정리해줘"]
                lines = ["### 이관 실행 예상", "", "| 매핑 ID | 추가 예상 | 갱신 예상 | 동일 예상 |",
                         "| --- | --- | --- | --- |"]
                lines += ["| {MAP_ID} | {inserted} | {updated} | {unchanged} |".format(**item) for item in preview]
                lines += ["", "현재 데이터로 계산한 예상입니다. 실제 실행은 요청을 받은 뒤 진행합니다."]
                result["answer"] += "\n\n" + "\n".join(lines)
            except (ValueError, KeyError, TypeError) as exc:
                facts["migration_blocker"] = str(exc)
                result["answer"] += "\n\n이관 전 확인할 사항: " + str(exc)
        elif "NEXT_SQL_INFO" in names:
            jobs = rows(state, "NEXT_SQL_INFO")
            facts["sql_progress"] = {"total": len(jobs),
                "converted": sum(j.get("STATUS_CONVERSION") == "DEMO-GENERATED" for j in jobs),
                "tuned": sum(j.get("STATUS_TUNING") == "DEMO-PROPOSED" for j in jobs),
                "formatted": sum(bool(j.get("FORMATTED_SQL")) for j in jobs)}
            facts["suggested_requests"] = ["SQL 변환해줘", "SQL 튜닝해줘", "SQL 서식 정리해줘"]
    facts["summary"] = facts["summary"][:2000]
    result["narrative_json"] = dumps(facts)
    # migration_results/show_migration_sql are internal metadata, already used
    # to build the answer and narrative. Match the four DSL string outputs on
    # every branch instead of leaking undeclared []/boolean values to Dify.
    return {key: result[key] for key in ("state_json", "answer", "commit_status", "narrative_json")}
