"""Historical experiment; NOT included in current DSL (editor render issue).

Build typed Tool configuration from authoritative state, without templates.

The Agent tools parameter must use type=variable. Do not serialize this array
into a mixed/constant template: state may contain arbitrary quotes and newlines.
"""
import json


def main(session_json: str) -> dict:
    packet = json.loads(session_json)
    if not isinstance(packet, dict) or packet.get("kind") != "smartmigrate.session.v2":
        raise ValueError("현재 대화 상태 패킷이 필요합니다.")
    if not isinstance(packet.get("state"), dict) or not packet.get("request_id"):
        raise ValueError("상태 또는 요청 ID가 누락됐습니다.")
    tool = json.loads(TOOL_CONFIG_JSON)
    # A concrete runtime value, generated again on every turn. No UI binding
    # and no model copying of state is involved. Only request is model-written.
    tool["parameters"] = {
        "request": {"auto": 1, "value": None},
        "session_json": {"auto": 0, "value": {"type": "constant", "value": session_json}},
    }
    return {"tools": [tool]}
