"""Main Chatflow Code node. engine.py is prepended when exporting DSL."""


def main(saved_state: str, sample_json: str, turn_id: str,
         asis_hr_dept_json: str = "", asis_hr_emp_json: str = "",
         tobe_hr_dept_json: str = "", tobe_hr_emp_json: str = "",
         next_mig_info_json: str = "", next_mig_info_dtl_json: str = "",
         next_sql_info_json: str = "", next_mig_rag_info_json: str = "",
         next_mig_log_json: str = "", user_request: str = "") -> dict:
    inputs = locals().copy()
    defaults = json.loads(DEFAULT_SAMPLE_JSON)
    payload = (saved_state or "").strip() or (sample_json or "").strip()
    if not payload:
        # Defined by the builder from samples/tables/*.json.
        payload = DEFAULT_SAMPLE_JSON
    if len(payload) > MAX_STATE:
        raise ValueError("입력 JSON 크기 제한 초과")
    state = json.loads(payload)
    if not (saved_state or "").strip():
        # All visible fields now have defaults. An unchanged per-table default
        # must not overwrite a user's edits to the complete sample_json.
        custom_sample = bool((sample_json or "").strip()) and state != defaults
        for name, variable in TABLE_INPUTS.items():
            if (inputs[variable] or "").strip():
                value = json.loads(inputs[variable])
                if not custom_sample or value != defaults["tables"][name]:
                    state["tables"][name] = value
    state = validate(state)
    if not turn_id:
        raise ValueError("sys.workflow_run_id가 필요합니다.")
    packet = {"kind": "smartmigrate.session.v2", "state": state, "request_id": turn_id}
    if user_request:
        if not isinstance(user_request, str) or len(user_request) > 6000:
            raise ValueError("요청은 1~6000자 문자열")
        packet["user_request"] = user_request
    result = {"state_json": dumps(state), "session_json": dumps(packet), "request_id": turn_id}
    result["session_transport"] = "SM_STATE_V3:" + base64.urlsafe_b64encode(dumps(packet).encode("utf-8")).decode("ascii")
    result.update({variable: dumps(state["tables"][name]) for name, variable in TABLE_INPUTS.items()})
    return result
