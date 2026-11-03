"""Offline demo with fixed SQL fixtures. No LLM or Dify server invocation."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_demo import ENGINE, call, committed, fixture_artifacts, seed, pipeline_plan


def main():
    state = seed()
    result = call(state, pipeline_plan(state), "offline-demo")
    saved = committed(state, result)
    if saved["commit_status"] != "OK":
        raise RuntimeError(saved["answer"])
    output = ROOT / "samples/offline_full_result.json"
    output.write_text(json.dumps(json.loads(saved["state_json"]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "samples/offline_full_result.md").write_text("# 오프라인 고정 SQL 시연 결과\n\nLLM·Dify 서버·Oracle 호출 없이 실행한 결과입니다.\n\n" + saved["answer"] + "\n", encoding="utf-8")
    print("Offline fixture demo: 3 departments, 10 employees, 1 SQL result. Saved to samples/.")


if __name__ == "__main__":
    main()
