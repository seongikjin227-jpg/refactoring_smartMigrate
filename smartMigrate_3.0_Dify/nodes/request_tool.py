"""Stateless Workflow Tool. The Agent generates only the user's request."""
import hashlib
import json


def main(request: str) -> dict:
    if not isinstance(request, str) or not request.strip() or len(request) > 6000:
        raise ValueError("request는 1~6000자의 요청이어야 합니다.")
    request = request.strip()
    receipt = {"kind": "smartmigrate.request.v1", "request": request,
               "request_hash": hashlib.sha256(request.encode("utf-8")).hexdigest()}
    return {"result_json": json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))}
