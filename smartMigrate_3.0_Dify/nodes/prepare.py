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
