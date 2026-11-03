"""Restore the exact state inside the executing Workflow Tool."""


def main(session_json: str, request: str) -> dict:
    # Keep legacy decoding isolated in prepare.py while publishing two inputs.
    return prepare_legacy(session_json=session_json, request=request, request_id="",
                          **{name: "" for name in TABLE_INPUTS.values()})
