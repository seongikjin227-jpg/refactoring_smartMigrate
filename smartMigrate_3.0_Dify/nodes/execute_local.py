"""Execute the plan against Chatflow state only after a valid Tool receipt."""


def main(state_json: str, plan_json: str, request_id: str,
         ready: bool, dispatch_message: str) -> dict:
    if ready is not True:
        return {"result_json": "", "execution_message": dispatch_message or "요청 접수가 확인되지 않아 실행하지 않았습니다."}
    result = execute(state_json, plan_json, request_id)
    return {"result_json": dumps(result), "execution_message": ""}
