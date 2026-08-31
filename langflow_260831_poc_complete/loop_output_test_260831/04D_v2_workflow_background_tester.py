from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Langflow v2 Workflow API background status and event re-attach.")
    parser.add_argument("--base-url", required=True, help="Langflow server URL, e.g. http://localhost:7860")
    parser.add_argument("--flow-id", required=True, help="Flow UUID to execute")
    parser.add_argument("--api-key", default="", help="Langflow API key")
    parser.add_argument("--input-value", default="전체 작업 실행", help="Input value for the flow")
    parser.add_argument("--session-id", default="v2-background-test", help="Session ID")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Status polling interval")
    parser.add_argument("--watch-events", action="store_true", help="Print a curl command for the background event stream")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["x-api-key"] = args.api_key

    start_body = {
        "flow_id": args.flow_id,
        "input_value": args.input_value,
        "session_id": args.session_id,
        "mode": "background",
        "stream_protocol": "langflow",
    }
    start = requests.post(f"{base_url}/api/v2/workflows", headers=headers, json=start_body, timeout=60)
    start.raise_for_status()
    job = start.json()
    job_id = job["job_id"]
    print(f"queued job_id={job_id}", flush=True)
    print(json.dumps(job, ensure_ascii=False, indent=2), flush=True)

    if args.watch_events:
        event_headers = f"-H \"x-api-key: {args.api_key}\" " if args.api_key else ""
        print("event stream test command:", flush=True)
        print(f"curl -N {event_headers}{base_url}/api/v2/workflows/{job_id}/events", flush=True)

    while True:
        status_headers = {"x-api-key": args.api_key} if args.api_key else {}
        status = requests.get(
            f"{base_url}/api/v2/workflows",
            headers=status_headers,
            params={"job_id": job_id},
            timeout=60,
        )
        if status.status_code in {408, 500}:
            print(status.text, file=sys.stderr)
            return 1
        status.raise_for_status()
        body: dict[str, Any] = status.json()
        print(f"status={body.get('status')} object={body.get('object')}", flush=True)
        if body.get("object") == "response" and body.get("status") == "completed":
            print(json.dumps(body.get("output") or {}, ensure_ascii=False, indent=2), flush=True)
            return 0
        if body.get("status") in {"failed", "cancelled", "timed_out"}:
            print(json.dumps(body, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
