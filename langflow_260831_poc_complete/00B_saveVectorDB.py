from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType00BSaveVectorDB(Component):
    display_name = "00B Save Vector DB"
    description = "One-shot loader that embeds NEXT_MIG_RAG_INFO SEARCH rules and stores vectors in a BLOB column."
    name = "NewType00BSaveVectorDB"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", value="SFAADM", required=False),
        StrInput(name="rag_table", display_name="RAG Table", value="NEXT_MIG_RAG_INFO", required=False),
        StrInput(name="vector_column", display_name="Vector BLOB Column", value="EMBEDDING_VECTOR", required=False),
        StrInput(name="category", display_name="Category", value="ALL", required=False),
        StrInput(name="rule_type", display_name="Rule Type", value="SEARCH", required=False),
        StrInput(name="update_mode", display_name="Update Mode", value="MISSING_ONLY", required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=True),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False),
        IntInput(name="batch_size", display_name="Embedding Batch Size", value=32, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run", types=["Data"])]

    def run(self) -> Data:
        started = time.perf_counter()
        payload = self._parse_payload(getattr(self, "payload_json", None))
        db_config = self._db_config(payload)
        embed_config = self._embed_config()
        table = self._qualify(str(getattr(self, "rag_table", "") or "NEXT_MIG_RAG_INFO"), db_config.get("system_schema"))
        vector_column = self._clean_identifier(str(getattr(self, "vector_column", "") or "EMBEDDING_VECTOR"))
        category = str(getattr(self, "category", "") or "ALL").strip().upper()
        rule_type = str(getattr(self, "rule_type", "") or "SEARCH").strip().upper()
        update_mode = str(getattr(self, "update_mode", "") or "MISSING_ONLY").strip().upper()

        self._require_db_config(db_config)
        self._require_embed_config(embed_config)
        rows = self._load_rows(db_config, table, vector_column, category, rule_type, update_mode)

        updated = 0
        failures: list[dict[str, Any]] = []
        for batch in self._chunks(rows, max(1, int(getattr(self, "batch_size", None) or 32))):
            texts = [self._embedding_text(row) for row in batch]
            try:
                vectors = self._embed_texts(texts, embed_config)
                if len(vectors) != len(batch):
                    raise ValueError(f"embedding response count mismatch: expected={len(batch)}, actual={len(vectors)}")
                self._save_vectors(db_config, table, vector_column, batch, vectors)
                updated += len(batch)
            except Exception as exc:
                failures.append({"rag_ids": [row["rag_id"] for row in batch], "error": str(exc)})

        result = {
            "ok": not failures,
            "component": "00B_saveVectorDB",
            "table": table,
            "vector_column": vector_column,
            "category": category,
            "rule_type": rule_type,
            "update_mode": update_mode,
            "embedding_model": embed_config["model"],
            "loaded_count": len(rows),
            "updated_count": updated,
            "failed_batch_count": len(failures),
            "failures": failures[:10],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "storage_format": "numpy-compatible float32 little-endian bytes",
        }
        self.status = result
        return Data(data=result)

    def _load_rows(
        self,
        db_config: dict[str, Any],
        table: str,
        vector_column: str,
        category: str,
        rule_type: str,
        update_mode: str,
    ) -> list[dict[str, Any]]:
        category_filter = "" if category in {"", "ALL", "*"} else "AND UPPER(TRIM(CATEGORY)) = :category"
        rule_type_filter = "" if rule_type in {"", "ALL", "*"} else "AND UPPER(TRIM(RULE_TYPE)) = :rule_type"
        vector_filter = f"AND {vector_column} IS NULL" if update_mode != "ALL" else ""
        sql = f"""
            SELECT RAG_ID, CATEGORY, RULE_TYPE, SOURCE_SQL
              FROM {table}
             WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
               AND SOURCE_SQL IS NOT NULL
               {category_filter}
               {rule_type_filter}
               {vector_filter}
             ORDER BY RAG_ID
        """
        binds: dict[str, Any] = {}
        if category_filter:
            binds["category"] = category
        if rule_type_filter:
            binds["rule_type"] = rule_type
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, binds)
            return [
                {
                    "rag_id": int(row[0]),
                    "category": self._lob_to_str(row[1]),
                    "rule_type": self._lob_to_str(row[2]),
                    "source_sql": self._lob_to_str(row[3]),
                }
                for row in cur.fetchall()
            ]

    def _save_vectors(
        self,
        db_config: dict[str, Any],
        table: str,
        vector_column: str,
        rows: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None:
        import numpy as np
        import oracledb

        payloads = []
        for row, vector in zip(rows, vectors):
            blob = np.asarray(vector, dtype="<f4").tobytes()
            payloads.append({"rag_id": row["rag_id"], "embedding_vector": blob})

        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.setinputsizes(embedding_vector=oracledb.DB_TYPE_BLOB)
            cur.executemany(
                f"""
                UPDATE {table}
                   SET {vector_column} = :embedding_vector,
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE RAG_ID = :rag_id
                """,
                payloads,
            )
            conn.commit()

    def _embed_texts(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        endpoint = self._embedding_endpoint(config["base_url"])
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"model": config["model"], "input": texts}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=config["timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        vectors = self._extract_embedding_vectors(body)
        if not vectors:
            raise ValueError("embedding response did not contain vectors")
        return vectors

    def _extract_embedding_vectors(self, body: Any) -> list[list[float]]:
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                vectors = []
                for item in data:
                    if isinstance(item, dict) and isinstance(item.get("embedding"), list):
                        vectors.append([float(value) for value in item["embedding"]])
                if vectors:
                    return vectors

            embeddings = body.get("embeddings")
            if isinstance(embeddings, list):
                vectors = []
                for item in embeddings:
                    if isinstance(item, list):
                        vectors.append([float(value) for value in item])
                if vectors:
                    return vectors

            embedding = body.get("embedding")
            if isinstance(embedding, list):
                return [[float(value) for value in embedding]]
        return []

    def _embedding_endpoint(self, base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/embeddings"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/embeddings"
        return f"{normalized}/v1/embeddings"

    def _embedding_text(self, row: dict[str, Any]) -> str:
        source_sql = str(row.get("source_sql") or "").strip()
        return "\n".join([self._normalize_sql_shape(source_sql), source_sql]).strip()

    def _normalize_sql_shape(self, sql_text: str) -> str:
        text = re.sub(r"/\*.*?\*/", " ", sql_text or "", flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", " ", text)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text.upper()

    def _embed_config(self) -> dict[str, Any]:
        return {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(getattr(self, "db_host", "") or item_config.get("db_host") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or item_config.get("db_port") or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or item_config.get("db_service_name") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or item_config.get("db_username") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)) or str(item_config.get("db_password") or ""),
            "system_schema": str(getattr(self, "system_schema", "") or item_config.get("system_schema") or "SFAADM").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing DB config: {', '.join(missing)}")

    def _require_embed_config(self, config: dict[str, Any]) -> None:
        if not config["base_url"]:
            raise ValueError("rag_embed_base_url is required")
        if not config["model"]:
            raise ValueError("rag_embed_model is required")

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        import oracledb

        dsn = oracledb.makedsn(
            str(db_config.get("db_host") or "").strip(),
            int(db_config.get("db_port") or 1521),
            service_name=str(db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(db_config.get("db_username") or "").strip(),
            password=str(db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _qualify(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return f"{self._clean_identifier(owner)}.{self._clean_identifier(name)}"
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed

    def _lob_to_str(self, value: Any) -> str:
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _secret_to_str(self, value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _chunks(self, values: list[dict[str, Any]], size: int):
        for index in range(0, len(values), size):
            yield values[index : index + size]
