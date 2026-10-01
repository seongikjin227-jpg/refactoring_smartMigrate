from __future__ import annotations

import json
import logging
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput
from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data


PREVIEW_SYSTEM_PROMPT = """You prepare an Oracle mapping-import SQL preview. This is PREVIEW ONLY.
Never connect to Oracle, never execute DML, and never claim that data was saved.

The uploaded text is a lossy Excel-to-text conversion. It has the sheet markers
`# Sheet : 테이블매핑` and `# Sheet : 컬럼매핑`; cells are represented as whitespace-separated text.
Validate that both sheets and their required identity values can be understood. If a value is ambiguous,
put it in validation_errors and do not invent a value.

Identity contract:
- Sheet 1 is `테이블매핑`. Its `순번` is MAP_ID (the master identity).
- Sheet 1 mappings are: `TOBE 테이블명` -> TO_TABLE, `ASIS 테이블명` -> FR_TABLE,
  `ASIS 필터` -> CONDITION, `Trunc 여부` -> TRUNC_YN, and
  `선행완료필요대상순번` -> PRIOR_MAP_ID.
- Sheet 2 is `컬럼매핑`. Its row/marker M is the mother MAP_ID and D is the detail identity MAP_DTL.
- Sheet 2 `TOBE 컬럼` is TO_COL. `상세 변환 규칙` is FR_COL. Do not use a
  display-only AS-IS column name in place of `상세 변환 규칙`.
- NEXT_MIG_INFO primary key is MAP_ID. Produce one MERGE per master row.
- NEXT_MIG_INFO_DTL primary key is MAP_DTL. Produce one MERGE per detail row, setting MAP_ID, FR_COL and TO_COL.

Oracle tables:
NEXT_MIG_INFO(MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, USE_YN, TRUNC_YN, PRIORITY,
 STATUS, USER_EDITED, PRIOR_MAP_ID, CONDITION, MIG_SQL, VERIFY_SQL, BATCH_CNT,
 ELAPSED_SECONDS, RETRY_COUNT, CREATED_AT, UPD_TS)
NEXT_MIG_INFO_DTL(MAP_DTL, MAP_ID, FR_COL, TO_COL)

SQL rules:
- Use Oracle MERGE INTO <schema>.NEXT_MIG_INFO / <schema>.NEXT_MIG_INFO_DTL.
- Match master with MAP_ID and detail with MAP_DTL only.
- In MATCHED clauses update only mapping-definition fields. Never overwrite STATUS, MIG_SQL,
 VERIFY_SQL, BATCH_CNT, ELAPSED_SECONDS, RETRY_COUNT, CREATED_AT, or USER_EDITED.
- In NOT MATCHED clauses insert mapping-definition fields and safe defaults: MAP_TYPE='TABLE',
 USE_YN='Y', TRUNC_YN='N', PRIORITY=999, USER_EDITED='N' when absent.
- Normalize Trunc 여부 only to Y or N. If its source value cannot be unambiguously normalized,
  report a validation error instead of guessing.
- PRIOR_MAP_ID is NULL when the corresponding sheet value is blank; otherwise it must be numeric.
- Do not emit DELETE, TRUNCATE, ALTER, DROP, PL/SQL execution blocks, COMMIT, or a database call.
- Preserve source values exactly. Use Oracle-safe quoted literals and make null values SQL NULL.

Return exactly one JSON object with:
{
  "validation_errors": ["..."],
  "warnings": ["..."],
  "master_count": 0,
  "detail_count": 0,
  "master_merges": ["MERGE ..."],
  "detail_merges": ["MERGE ..."]
}
Do not wrap the JSON in Markdown fences."""


class NewType04MappingImportPreviewTool(Component):
    display_name = "04 Mapping Import SQL Preview Tool"
    description = "Uses an LLM to validate an uploaded mapping workbook and return Oracle MERGE previews only; it never executes SQL."
    name = "NewType04MappingImportPreviewTool"
    icon = "FileSpreadsheet"

    inputs = [
        MessageTextInput(
            name="mapping_text",
            display_name="Uploaded Mapping Text",
            required=True,
            tool_mode=True,
            info="Pass the complete uploaded workbook text, including # Sheet markers and all chunks.",
        ),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"]),
    ]
    outputs = [Output(display_name="Preview Result", name="result", method="run", types=["Data"])]

    def run(self) -> Data:
        try:
            mapping_text = str(getattr(self, "mapping_text", "") or "").strip()
            self._validate_upload_shape(mapping_text)
            schema = self._identifier(getattr(self, "system_schema", ""))
            result = self._generate_preview(mapping_text, schema)
            response = {
                "ok": True,
                "component": "04_mappingImportPreviewTool",
                "preview_only": True,
                "database_executed": False,
                "system_schema": schema,
                "result": result,
                "final": True,
            }
            logging.getLogger("smartmigrate.workflow").info(
                "Mapping import SQL preview generated (no database execution)",
                extra={"workflow_log": [0, "WORKFLOW", "04_MAPPING_IMPORT_PREVIEW", "INFO", "PREVIEW", "PASS", 0]},
            )
        except Exception as exc:
            response = {
                "ok": False,
                "component": "04_mappingImportPreviewTool",
                "preview_only": True,
                "database_executed": False,
                "error": str(exc),
                "final": True,
            }
        self.status = response
        return Data(data=response)

    def _generate_preview(self, mapping_text: str, schema: str) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("Connect a LanguageModel to 04 Mapping Import SQL Preview Tool.")
        response = llm.invoke(
            [
                SystemMessage(content=PREVIEW_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps({"system_schema": schema, "uploaded_mapping_text": mapping_text}, ensure_ascii=False)),
            ]
        )
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(item if isinstance(item, str) else str(item.get("text") or "") for item in content)
        parsed = self._parse_json(str(content or ""))
        self._validate_preview(parsed)
        return parsed

    @staticmethod
    def _validate_upload_shape(text: str) -> None:
        if not text:
            raise ValueError("mapping_text is required")
        sheets = set(re.findall(r"(?mi)^\s*#\s*Sheet\s*:\s*(.+?)\s*$", text))
        missing = {"테이블매핑", "컬럼매핑"} - sheets
        if missing:
            raise ValueError(f"Required sheet marker is missing: {', '.join(sorted(missing))}")

    @staticmethod
    def _identifier(value: Any) -> str:
        cleaned = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", cleaned):
            raise ValueError("system_schema must be a valid Oracle identifier")
        return cleaned

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        parsed = json.loads(match.group(0) if match else cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("LLM preview must be a JSON object")
        return parsed

    @staticmethod
    def _validate_preview(preview: dict[str, Any]) -> None:
        forbidden = re.compile(r"\b(?:DELETE|TRUNCATE|ALTER|DROP|COMMIT|BEGIN|EXEC(?:UTE)?)\b", re.I)
        for name in ("master_merges", "detail_merges"):
            statements = preview.get(name, [])
            if not isinstance(statements, list) or not all(isinstance(item, str) for item in statements):
                raise ValueError(f"LLM preview field {name} must be a string list")
            for statement in statements:
                if not re.match(r"^\s*MERGE\s+INTO\s+", statement, flags=re.I):
                    raise ValueError(f"{name} contains a non-MERGE statement")
                if forbidden.search(statement):
                    raise ValueError(f"{name} contains a forbidden SQL keyword")
                if ";" in statement:
                    raise ValueError(f"{name} must contain one MERGE statement without a statement separator")
                required_table = "NEXT_MIG_INFO_DTL" if name == "detail_merges" else "NEXT_MIG_INFO"
                required_key = "MAP_DTL" if name == "detail_merges" else "MAP_ID"
                if required_table not in statement.upper() or required_key not in statement.upper():
                    raise ValueError(f"{name} must target {required_table} using {required_key}")
