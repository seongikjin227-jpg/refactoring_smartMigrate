"""Validate the request-only Tool receipt, then read state locally.

engine.py and commit.py are prepended to supply bounded log parsers.
"""


def main(state_json: str, request_id: str, user_request: str,
         agent_json: list, agent_text: str) -> dict:
    state = validate(json.loads(state_json))
    receipts = {}
    responses = tool_responses(agent_json)
    for response in responses:
        for item in envelopes(response, target_kind="smartmigrate.request.v1"):
            request = item.get("request")
            if not isinstance(request, str) or not request.strip() or len(request) > 6000:
                continue
            expected = hashlib.sha256(request.encode("utf-8")).hexdigest()
            if item.get("request_hash") == expected:
                receipts[dumps(item)] = item
    if len(receipts) == 1:
        ready, message = True, ""
    elif len(receipts) > 1:
        ready, message = False, "서로 다른 Tool 요청이 여러 개 접수되어 이번 작업을 실행하지 않았습니다. 요청을 하나씩 입력해주세요."
    elif responses:
        ready, message = False, ("요청 전용 Tool 접수를 확인하지 못했습니다. 01과 발행 Tool의 입력이 request 하나인지 확인하세요. "
                                 "이전 session_json/테이블 입력은 이번 버전에서 사용하지 않습니다.")
    else:
        ready, message = False, agent_text or "Tool 호출 결과가 없어 작업을 실행하지 않았습니다."
    if not isinstance(user_request, str) or not user_request.strip() or len(user_request) > 6000:
        ready, message = False, "사용자 요청은 1~6000자여야 합니다."
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("현재 실행 request_id가 필요합니다.")
    context = {"tables": state["tables"], "last_target": state.get("last_target"),
               "pending_action": state.get("pending_action"),
               "reference_rules": [r for r in rows(state, "NEXT_MIG_RAG_INFO") if r.get("USE_YN") == "Y"]}
    # Use the real user's query, never an Agent-rewritten request, as authority.
    return {"ready": ready, "message": message, "request": user_request,
            "context_json": dumps(context), "state_json": dumps(state), "request_id": request_id}
