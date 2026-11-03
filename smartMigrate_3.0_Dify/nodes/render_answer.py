"""Keep general chat separate from operational narration and verified tables."""
import json


def main(narrative_json: str, explanation: str, answer: str) -> dict:
    facts = json.loads(narrative_json)
    if facts.get("commit_status") in ("CHAT", "READ_ONLY"):
        return {"text": answer}
    return {"text": "\n\n".join(part.strip() for part in (explanation or "", answer or "") if part.strip())}
