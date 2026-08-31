from __future__ import annotations

import json
import os
import sys

import requests


def main() -> int:
    base_url = os.getenv("LANGFLOW_SERVER_URL", "").rstrip("/")
    flow_id = os.getenv("LANGFLOW_FLOW_ID", "")
    api_key = os.getenv("LANGFLOW_API_KEY", "")
    if not base_url or not flow_id:
        print("LANGFLOW_SERVER_URL and LANGFLOW_FLOW_ID are required", file=sys.stderr)
        return 2

    url = f"{base_url}/api/v1/run/{flow_id}?stream=true"
    headers = {
        "accept": "text/event-stream, application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key

    payload = {
        "input_value": os.getenv("LANGFLOW_INPUT_VALUE", "전체 작업 실행"),
        "input_type": "chat",
        "output_type": "chat",
        "output_component": os.getenv("LANGFLOW_OUTPUT_COMPONENT") or None,
        "session_id": os.getenv("LANGFLOW_SESSION_ID") or "stream-test",
        "tweaks": json.loads(os.getenv("LANGFLOW_TWEAKS", "{}")),
    }

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=None) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if line:
                print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
