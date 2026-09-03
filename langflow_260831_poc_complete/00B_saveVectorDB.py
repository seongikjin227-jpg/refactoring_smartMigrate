from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


RAG_TABLE = "NEXT_MIG_RAG_INFO"
SQL_TABLE = "NEXT_SQL_INFO"
VECTOR_COLUMN = "EMBEDDING_VECTOR"
BATCH_SIZE = 32


class NewType00BSaveVectorDB(Component):
    display_name = "00B Save Vector DB"
    description = "One-shot loader that regenerates NEXT_MIG_RAG_INFO and NEXT_SQL_INFO SQL vectors into EMBEDDING_VECTOR."
    name = "NewType00BSaveVectorDB"
    icon = "Database"

    inputs = [
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", value="SFAADM", required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=True),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run", types=["Data"])]

    def run(self) -> Data:
        started = time.perf_counter()
        db_config = self._db_config()
        embed_config = self._embed_config()
        rag_table = self._qualify(RAG_TABLE, db_config.get("system_schema"))
        sql_table = self._qualify(SQL_TABLE, db_config.get("system_schema"))

        self._require_db_config(db_config)
        self._require_embed_config(embed_config)
        rag_rows = self._load_rag_rows(db_config, rag_table)
        sql_rows = self._load_sql_rows(db_config, sql_table)

        rag_updated = 0
        sql_updated = 0
        failures: list[dict[str, Any]] = []
        for batch in self._chunks(rag_rows, BATCH_SIZE):
            texts = [self._embedding_text(row) for row in batch]
            try:
                vectors = self._embed_texts(texts, embed_config)
                if len(vectors) != len(batch):
                    raise ValueError(f"embedding response count mismatch: expected={len(batch)}, actual={len(vectors)}")
                self._save_rag_vectors(db_config, rag_table, batch, vectors)
                rag_updated += len(batch)
            except Exception as exc:
                failures.append({"table": RAG_TABLE, "ids": [row["rag_id"] for row in batch], "error": str(exc)})

        for batch in self._chunks(sql_rows, BATCH_SIZE):
            texts = [self._embedding_text(row) for row in batch]
            try:
                vectors = self._embed_texts(texts, embed_config)
                if len(vectors) != len(batch):
                    raise ValueError(f"embedding response count mismatch: expected={len(batch)}, actual={len(vectors)}")
                self._save_sql_vectors(db_config, sql_table, batch, vectors)
                sql_updated += len(batch)
            except Exception as exc:
                failures.append({"table": SQL_TABLE, "ids": [row["row_id"] for row in batch], "error": str(exc)})

        result = {
            "ok": not failures,
            "component": "00B_saveVectorDB",
            "tables": [rag_table, sql_table],
            "vector_column": VECTOR_COLUMN,
            "update_mode": "ALL",
            "row_scope": {
                RAG_TABLE: "USE_YN='Y' AND SOURCE_SQL IS NOT NULL",
                SQL_TABLE: "FR_SQL or EDIT_FR_SQL is not empty",
            },
            "batch_size": BATCH_SIZE,
            "embedding_model": embed_config["model"],
            "loaded_count": len(rag_rows) + len(sql_rows),
            "updated_count": rag_updated + sql_updated,
            "rag_loaded_count": len(rag_rows),
            "rag_updated_count": rag_updated,
            "sql_loaded_count": len(sql_rows),
            "sql_updated_count": sql_updated,
            "failed_batch_count": len(failures),
            "failures": failures[:10],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "storage_format": "numpy-compatible float32 little-endian bytes",
        }
        self.status = result
        return Data(data=result)

    def _load_rag_rows(
        self,
        db_config: dict[str, Any],
        table: str,
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT RAG_ID, CATEGORY, RULE_TYPE, SOURCE_SQL
              FROM {table}
             WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
               AND SOURCE_SQL IS NOT NULL
             ORDER BY RAG_ID
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return [
                {
                    "rag_id": int(row[0]),
                    "category": self._lob_to_str(row[1]),
                    "rule_type": self._lob_to_str(row[2]),
                    "source_sql": self._lob_to_str(row[3]),
                }
                for row in cur.fetchall()
            ]

    def _load_sql_rows(
        self,
        db_config: dict[str, Any],
        table: str,
    ) -> list[dict[str, Any]]:
        columns = self._table_columns(db_config, table)
        if "FR_SQL" not in columns:
            return []
        edit_fr_expr = "EDIT_FR_SQL" if "EDIT_FR_SQL" in columns else "TO_CLOB(NULL)"
        status_expr = "STATUS_CONVERSION" if "STATUS_CONVERSION" in columns else "CAST(NULL AS VARCHAR2(100))"
        space_nm_expr = "SPACE_NM" if "SPACE_NM" in columns else "CAST(NULL AS VARCHAR2(4000))"
        sql_id_expr = "SQL_ID" if "SQL_ID" in columns else "CAST(NULL AS VARCHAR2(4000))"
        order_expr = "UPD_TS DESC NULLS LAST" if "UPD_TS" in columns else "ROWID"
        where_sql = "FR_SQL IS NOT NULL"
        if "EDIT_FR_SQL" in columns:
            where_sql = f"{where_sql} OR EDIT_FR_SQL IS NOT NULL"
        sql = f"""
            SELECT ROWIDTOCHAR(ROWID), {space_nm_expr}, {sql_id_expr}, FR_SQL, {edit_fr_expr}, {status_expr}
              FROM {table}
             WHERE {where_sql}
             ORDER BY {order_expr}
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows: list[dict[str, Any]] = []
            for row in cur.fetchall():
                edit_fr_sql = self._lob_to_str(row[4]).strip()
                fr_sql = self._lob_to_str(row[3]).strip()
                source_sql = edit_fr_sql or fr_sql
                if not source_sql:
                    continue
                rows.append(
                    {
                        "row_id": self._lob_to_str(row[0]).strip(),
                        "space_nm": self._lob_to_str(row[1]).strip(),
                        "sql_id": self._lob_to_str(row[2]).strip(),
                        "status_conversion": self._lob_to_str(row[5]).strip(),
                        "source_sql": source_sql,
                    }
                )
            return rows

    def _save_rag_vectors(
        self,
        db_config: dict[str, Any],
        table: str,
        rows: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None:
        import numpy as np
        import oracledb

        columns = self._table_columns(db_config, table)
        if VECTOR_COLUMN not in columns:
            raise ValueError(f"{table}.{VECTOR_COLUMN} column does not exist")

        payloads = []
        for row, vector in zip(rows, vectors):
            blob = np.asarray(vector, dtype="<f4").tobytes()
            payloads.append({"rag_id": row["rag_id"], "embedding_vector": blob})

        set_clause = f"{VECTOR_COLUMN} = :embedding_vector"
        if "UPDATED_AT" in columns:
            set_clause += ", UPDATED_AT = SYSTIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.setinputsizes(embedding_vector=oracledb.DB_TYPE_BLOB)
            cur.executemany(
                f"""
                UPDATE {table}
                   SET {set_clause}
                 WHERE RAG_ID = :rag_id
                """,
                payloads,
            )
            conn.commit()

    def _save_sql_vectors(
        self,
        db_config: dict[str, Any],
        table: str,
        rows: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None:
        import numpy as np
        import oracledb

        columns = self._table_columns(db_config, table)
        if VECTOR_COLUMN not in columns:
            raise ValueError(f"{table}.{VECTOR_COLUMN} column does not exist")

        payloads = []
        for row, vector in zip(rows, vectors):
            blob = np.asarray(vector, dtype="<f4").tobytes()
            payloads.append({"row_id": row["row_id"], "embedding_vector": blob})

        set_clause = f"{VECTOR_COLUMN} = :embedding_vector"
        if "UPD_TS" in columns:
            set_clause += ", UPD_TS = CURRENT_TIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.setinputsizes(embedding_vector=oracledb.DB_TYPE_BLOB)
            cur.executemany(
                f"""
                UPDATE {table}
                   SET {set_clause}
                 WHERE ROWID = CHARTOROWID(:row_id)
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

    def _db_config(self) -> dict[str, Any]:
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "SFAADM").strip(),
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

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        owner, table_name = self._split_owner_table(table)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            if owner:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                      FROM ALL_TAB_COLUMNS
                     WHERE OWNER = :owner
                       AND TABLE_NAME = :table_name
                    """,
                    {"owner": owner, "table_name": table_name},
                )
            else:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                      FROM USER_TAB_COLUMNS
                     WHERE TABLE_NAME = :table_name
                    """,
                    {"table_name": table_name},
                )
            return {str(row[0]).upper() for row in cur.fetchall()}

    def _split_owner_table(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." not in value:
            return None, self._clean_identifier(value)
        owner, table_name = value.split(".", 1)
        return self._clean_identifier(owner), self._clean_identifier(table_name)

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
