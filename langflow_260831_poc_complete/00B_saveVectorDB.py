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
from lfx.io import IntInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


RAG_TABLE = "NEXT_MIG_RAG_INFO"
SQL_TABLE = "NEXT_SQL_INFO"
RAG_COLLECTION = "SM_RAG_RULES"
CORRECT_SQL_COLLECTION = "SM_CORRECT_SQL"
RAG_GENERAL = "GENERAL"
RAG_SEARCH = "SEARCH"
BATCH_SIZE = 32
TEXT_MAX = 65535


class NewType00BSaveVectorDB(Component):
    display_name = "00B Sync Milvus Vector DB"
    description = "One-shot sync from Oracle source tables to Milvus RAG collections."
    name = "NewType00BSaveVectorDB"
    icon = "Database"

    inputs = [
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", value="SFAADM", required=False),
        StrInput(name="milvus_uri", display_name="Milvus URI", required=True),
        StrInput(name="milvus_username", display_name="Milvus Username", required=True),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=True),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=True),
        StrInput(name="rag_collection_name", display_name="RAG Collection Name", value=RAG_COLLECTION, required=False),
        StrInput(name="correct_sql_collection_name", display_name="Correct SQL Collection Name", value=CORRECT_SQL_COLLECTION, required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=True),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run", types=["Data"])]

    def run(self) -> Data:
        started = time.perf_counter()
        db_config = self._db_config()
        milvus_config = self._milvus_config()
        embed_config = self._embed_config()
        self._require_db_config(db_config)
        self._require_milvus_config(milvus_config)
        self._require_embed_config(embed_config)

        rag_rows = self._load_rag_rows(db_config)
        correct_rows = self._load_correct_sql_rows(db_config)
        active_rows = rag_rows + correct_rows
        vector_dim = self._detect_vector_dim(active_rows, embed_config)

        client = self._milvus_client(milvus_config)
        created = {
            "rag": self._ensure_collection(client, milvus_config["rag_collection"], vector_dim),
            "correct_sql": self._ensure_collection(client, milvus_config["correct_sql_collection"], vector_dim),
        }

        rag_result = self._sync_collection(client, milvus_config["rag_collection"], rag_rows, embed_config, "NEXT_MIG_RAG_INFO")
        sql_result = self._sync_collection(client, milvus_config["correct_sql_collection"], correct_rows, embed_config, "NEXT_SQL_INFO")

        result = {
            "ok": not rag_result["failures"] and not sql_result["failures"],
            "component": "00B_syncMilvusVectorDB",
            "milvus_db_name": milvus_config["db_name"],
            "collections": {
                "rag_rules": milvus_config["rag_collection"],
                "correct_sql": milvus_config["correct_sql_collection"],
            },
            "collection_created": created,
            "vector_dim": vector_dim,
            "embedding_model": embed_config["model"],
            "source_scope": {
                RAG_TABLE: "all rows synced; USE_YN='Y' and SOURCE_SQL present become active",
                SQL_TABLE: "USER_EDITED='Y' and STATUS_CONVERSION pass rows become active for correct SQL hints",
            },
            "rag": rag_result,
            "correct_sql": sql_result,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        self.status = result
        return Data(data=result)

    def _ensure_collection(self, client: Any, collection_name: str, vector_dim: int) -> bool:
        if client.has_collection(collection_name):
            client.load_collection(collection_name=collection_name)
            return False
        try:
            self._create_collection(client, collection_name, vector_dim, with_bm25=True)
        except Exception:
            self._create_collection(client, collection_name, vector_dim, with_bm25=False)
        client.load_collection(collection_name=collection_name)
        return True

    def _create_collection(self, client: Any, collection_name: str, vector_dim: int, with_bm25: bool) -> None:
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("doc_id", DataType.VARCHAR, is_primary=True, auto_id=False, max_length=256)
        schema.add_field("source", DataType.VARCHAR, max_length=64)
        schema.add_field("rag_id", DataType.VARCHAR, max_length=128)
        schema.add_field("row_id", DataType.VARCHAR, max_length=128)
        schema.add_field("space_nm", DataType.VARCHAR, max_length=512)
        schema.add_field("sql_id", DataType.VARCHAR, max_length=512)
        schema.add_field("category", DataType.VARCHAR, max_length=64)
        schema.add_field("rule_type", DataType.VARCHAR, max_length=32)
        schema.add_field("use_yn", DataType.VARCHAR, max_length=8)
        schema.add_field("user_edited", DataType.VARCHAR, max_length=8)
        schema.add_field("status_conversion", DataType.VARCHAR, max_length=100)
        schema.add_field("tag_kind", DataType.VARCHAR, max_length=100)
        schema.add_field("source_tables", DataType.VARCHAR, max_length=2048)
        schema.add_field("target_table", DataType.VARCHAR, max_length=2048)
        schema.add_field("guidance_text", DataType.VARCHAR, max_length=8192)
        schema.add_field("source_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("target_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("to_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("bind_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("test_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        schema.add_field("content", DataType.VARCHAR, max_length=TEXT_MAX, enable_analyzer=with_bm25)
        schema.add_field("content_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("is_active", DataType.BOOL)
        schema.add_field("updated_at", DataType.VARCHAR, max_length=64)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=vector_dim)
        if with_bm25:
            from pymilvus import Function, FunctionType

            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(Function(name="content_bm25", input_field_names=["content"], output_field_names=["sparse_vector"], function_type=FunctionType.BM25))

        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        if with_bm25:
            index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25", params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params, consistency_level="Bounded")

    def _sync_collection(self, client: Any, collection_name: str, rows: list[dict[str, Any]], embed_config: dict[str, Any], source_name: str) -> dict[str, Any]:
        active_doc_ids = {row["doc_id"] for row in rows if row.get("is_active")}
        existing = self._query_existing_docs(client, collection_name, source_name)
        to_upsert = [row for row in rows if row.get("is_active") and existing.get(row["doc_id"]) != row["content_hash"]]
        skipped = len([row for row in rows if row.get("is_active")]) - len(to_upsert)
        failures: list[dict[str, Any]] = []
        upserted = 0
        for batch in self._chunks(to_upsert, BATCH_SIZE):
            try:
                vectors = self._embed_texts([row["content"] for row in batch], embed_config)
                entities = [{**row, "dense_vector": vector} for row, vector in zip(batch, vectors)]
                client.upsert(collection_name=collection_name, data=entities)
                upserted += len(entities)
            except Exception as exc:
                failures.append({"doc_ids": [row["doc_id"] for row in batch], "error": str(exc)})

        deactivated = self._deactivate_missing_docs(client, collection_name, existing, active_doc_ids)
        return {
            "source": source_name,
            "loaded_count": len(rows),
            "active_count": len(active_doc_ids),
            "upserted_count": upserted,
            "skipped_count": max(skipped, 0),
            "deactivated_count": deactivated,
            "failed_batch_count": len(failures),
            "failures": failures[:10],
        }

    def _query_existing_docs(self, client: Any, collection_name: str, source_name: str) -> dict[str, str]:
        result: dict[str, str] = {}
        try:
            rows = client.query(collection_name=collection_name, filter=f'source == "{source_name}"', output_fields=["doc_id", "content_hash", "is_active"], limit=16384)
        except TypeError:
            rows = client.query(collection_name=collection_name, filter=f'source == "{source_name}"', output_fields=["doc_id", "content_hash", "is_active"])
        for row in rows or []:
            if row.get("is_active"):
                result[str(row.get("doc_id"))] = str(row.get("content_hash") or "")
        return result

    def _deactivate_missing_docs(self, client: Any, collection_name: str, existing: dict[str, str], active_doc_ids: set[str]) -> int:
        stale_doc_ids = sorted(set(existing) - active_doc_ids)
        if not stale_doc_ids:
            return 0
        count = 0
        for batch in self._chunks([{"doc_id": item} for item in stale_doc_ids], 256):
            entities = [{"doc_id": item["doc_id"], "is_active": False} for item in batch]
            try:
                client.upsert(collection_name=collection_name, data=entities, partial_update=True)
            except Exception:
                quoted = ", ".join(json.dumps(item["doc_id"]) for item in batch)
                client.delete(collection_name=collection_name, filter=f"doc_id in [{quoted}]")
            count += len(batch)
        return count

    def _detect_vector_dim(self, rows: list[dict[str, Any]], embed_config: dict[str, Any]) -> int:
        for row in rows:
            content = str(row.get("content") or "").strip()
            if content:
                vector = self._embed_texts([content], embed_config)[0]
                return len(vector)
        raise ValueError("No active source rows found for Milvus vector sync")

    def _load_rag_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        table = self._qualify(RAG_TABLE, db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        required = {"RAG_ID", "CATEGORY", "RULE_TYPE", "USE_YN"}
        if not required.issubset(columns):
            raise ValueError("NEXT_MIG_RAG_INFO requires RAG_ID, CATEGORY, RULE_TYPE, USE_YN")
        guidance_expr = "GUIDANCE_TEXT" if "GUIDANCE_TEXT" in columns else "CAST(NULL AS VARCHAR2(4000))"
        source_sql_expr = "SOURCE_SQL" if "SOURCE_SQL" in columns else "TO_CLOB(NULL)"
        target_sql_expr = "TARGET_SQL" if "TARGET_SQL" in columns else "TO_CLOB(NULL)"
        source_tables_expr = "SOURCE_TABLES" if "SOURCE_TABLES" in columns else "CAST(NULL AS VARCHAR2(4000))"
        updated_expr = "TO_CHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI:SS')" if "UPDATED_AT" in columns else "TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS')"
        order_expr = "UPDATED_AT DESC NULLS LAST" if "UPDATED_AT" in columns else "RAG_ID"
        sql = f"""
            SELECT RAG_ID, CATEGORY, RULE_TYPE, USE_YN, {source_tables_expr}, {guidance_expr}, {source_sql_expr}, {target_sql_expr}, {updated_expr}
              FROM {table}
             ORDER BY {order_expr}
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = []
            for row in cur.fetchall():
                rag_id = self._lob_to_str(row[0]).strip()
                category = self._lob_to_str(row[1]).strip().upper()
                rule_type = self._lob_to_str(row[2]).strip().upper()
                use_yn = self._lob_to_str(row[3]).strip().upper() or "N"
                source_sql = self._lob_to_str(row[6]).strip()
                target_sql = self._lob_to_str(row[7]).strip()
                guidance = self._lob_to_str(row[5]).strip()
                # dense_vector is generated from SOURCE_SQL only; guidance/target_sql stay as prompt metadata.
                content = self._rag_content(category, rule_type, guidance, source_sql, target_sql)
                is_supported = category in {"SQL_CONVERSION", "SQL_TUNING"} and rule_type in {RAG_GENERAL, RAG_SEARCH}
                has_rule_body = bool(source_sql) if rule_type == RAG_SEARCH else bool(guidance or source_sql or target_sql)
                is_active = use_yn == "Y" and is_supported and has_rule_body
                rows.append(
                    self._entity(
                        doc_id=f"RAG:{rag_id}",
                        source=RAG_TABLE,
                        rag_id=rag_id,
                        category=category,
                        rule_type=rule_type,
                        use_yn=use_yn,
                        source_tables=self._lob_to_str(row[4]),
                        guidance_text=guidance,
                        source_sql=source_sql,
                        target_sql=target_sql,
                        content=content,
                        is_active=is_active,
                        updated_at=self._lob_to_str(row[8]),
                    )
                )
            return rows

    def _load_correct_sql_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        table = self._qualify(SQL_TABLE, db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if "FR_SQL" not in columns:
            return []
        edit_fr_expr = "EDIT_FR_SQL" if "EDIT_FR_SQL" in columns else "TO_CLOB(NULL)"
        status_expr = "STATUS_CONVERSION" if "STATUS_CONVERSION" in columns else "CAST(NULL AS VARCHAR2(100))"
        user_edited_expr = "USER_EDITED" if "USER_EDITED" in columns else "CAST(NULL AS VARCHAR2(8))"
        tag_kind_expr = "TAG_KIND" if "TAG_KIND" in columns else "CAST(NULL AS VARCHAR2(100))"
        target_table_expr = "TARGET_TABLE" if "TARGET_TABLE" in columns else "CAST(NULL AS VARCHAR2(2048))"
        to_sql_expr = "TO_SQL" if "TO_SQL" in columns else "TO_CLOB(NULL)"
        bind_sql_expr = "BIND_SQL" if "BIND_SQL" in columns else "TO_CLOB(NULL)"
        test_sql_expr = "TEST_SQL" if "TEST_SQL" in columns else "TO_CLOB(NULL)"
        updated_expr = "TO_CHAR(UPD_TS, 'YYYY-MM-DD HH24:MI:SS')" if "UPD_TS" in columns else "TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS')"
        where_sql = "FR_SQL IS NOT NULL"
        if "EDIT_FR_SQL" in columns:
            where_sql = f"{where_sql} OR EDIT_FR_SQL IS NOT NULL"
        sql = f"""
            SELECT ROWIDTOCHAR(ROWID), SPACE_NM, SQL_ID, FR_SQL, {edit_fr_expr}, {status_expr}, {user_edited_expr}, {tag_kind_expr},
                   {target_table_expr}, {to_sql_expr}, {bind_sql_expr}, {test_sql_expr}, {updated_expr}
              FROM {table}
             WHERE {where_sql}
             ORDER BY {updated_expr} DESC
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = []
            for row in cur.fetchall():
                row_id = self._lob_to_str(row[0]).strip()
                space_nm = self._lob_to_str(row[1]).strip()
                sql_id = self._lob_to_str(row[2]).strip()
                fr_sql = self._lob_to_str(row[3]).strip()
                edit_fr_sql = self._lob_to_str(row[4]).strip()
                source_sql = edit_fr_sql or fr_sql
                to_sql = self._lob_to_str(row[9]).strip()
                bind_sql = self._lob_to_str(row[10]).strip()
                test_sql = self._lob_to_str(row[11]).strip()
                status = self._lob_to_str(row[5]).strip().upper()
                user_edited = self._lob_to_str(row[6]).strip().upper()
                is_active = bool(source_sql) and user_edited == "Y" and status in {"PASS", "PASS-CONVERSION"} and bool(to_sql or bind_sql or test_sql)
                doc_key = f"{space_nm}:{sql_id}" if space_nm or sql_id else row_id
                rows.append(
                    self._entity(
                        doc_id=f"SQL:{self._hash_text(doc_key)[:24]}",
                        source=SQL_TABLE,
                        row_id=row_id,
                        space_nm=space_nm,
                        sql_id=sql_id,
                        status_conversion=status,
                        user_edited=user_edited,
                        tag_kind=self._lob_to_str(row[7]),
                        target_table=self._lob_to_str(row[8]),
                        source_sql=source_sql,
                        to_sql=to_sql,
                        bind_sql=bind_sql,
                        test_sql=test_sql,
                        # dense_vector is generated from EDIT_FR_SQL first, otherwise FR_SQL, for correct SQL hint retrieval.
                        content=self._sql_content(source_sql),
                        is_active=is_active,
                        updated_at=self._lob_to_str(row[12]),
                    )
                )
            return rows

    def _entity(self, **values: Any) -> dict[str, Any]:
        defaults = {
            "source": "",
            "rag_id": "",
            "row_id": "",
            "space_nm": "",
            "sql_id": "",
            "category": "",
            "rule_type": "",
            "use_yn": "",
            "user_edited": "",
            "status_conversion": "",
            "tag_kind": "",
            "source_tables": "",
            "target_table": "",
            "guidance_text": "",
            "source_sql": "",
            "target_sql": "",
            "to_sql": "",
            "bind_sql": "",
            "test_sql": "",
            "content": "",
            "is_active": False,
            "updated_at": "",
        }
        entity = {**defaults, **values}
        for key in ("source_sql", "target_sql", "to_sql", "bind_sql", "test_sql", "content"):
            entity[key] = self._truncate(entity.get(key), TEXT_MAX)
        entity["guidance_text"] = self._truncate(entity.get("guidance_text"), 8192)
        entity["source_tables"] = self._truncate(entity.get("source_tables"), 2048)
        entity["target_table"] = self._truncate(entity.get("target_table"), 2048)
        entity["content_hash"] = self._hash_text(json.dumps({key: entity.get(key) for key in sorted(entity) if key not in {"dense_vector", "content_hash"}}, ensure_ascii=False, sort_keys=True))
        return entity

    def _rag_content(self, category: str, rule_type: str, guidance: str, source_sql: str, target_sql: str) -> str:
        source = source_sql.strip()
        if source:
            return self._sql_content(source)
        if rule_type == RAG_GENERAL:
            return guidance.strip() or target_sql.strip() or category
        return ""

    def _sql_content(self, source_sql: str) -> str:
        source = source_sql.strip()
        return "\n".join([self._normalize_sql_shape(source), source]).strip()

    def _embed_texts(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        endpoint = self._embedding_endpoint(config["base_url"])
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"
        request = urllib.request.Request(endpoint, data=json.dumps({"model": config["model"], "input": texts}).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=config["timeout_seconds"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        vectors = self._extract_embedding_vectors(body)
        if len(vectors) != len(texts):
            raise ValueError(f"embedding response count mismatch: expected={len(texts)}, actual={len(vectors)}")
        return vectors

    def _extract_embedding_vectors(self, body: Any) -> list[list[float]]:
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                return [[float(value) for value in item["embedding"]] for item in data if isinstance(item, dict) and isinstance(item.get("embedding"), list)]
            if isinstance(body.get("embeddings"), list):
                return [[float(value) for value in item] for item in body["embeddings"] if isinstance(item, list)]
            if isinstance(body.get("embedding"), list):
                return [[float(value) for value in body["embedding"]]]
        return []

    def _embedding_endpoint(self, base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/embeddings"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/embeddings"
        return f"{normalized}/v1/embeddings"

    def _normalize_sql_shape(self, sql_text: str) -> str:
        text = re.sub(r"/\*.*?\*/", " ", sql_text or "", flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", " ", text)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip().upper()

    def _milvus_client(self, config: dict[str, Any]) -> Any:
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    def _db_config(self) -> dict[str, Any]:
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "SFAADM").strip(),
        }

    def _milvus_config(self) -> dict[str, Any]:
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "rag_collection": self._clean_collection_name(getattr(self, "rag_collection_name", "") or os.getenv("MILVUS_RAG_COLLECTION") or RAG_COLLECTION),
            "correct_sql_collection": self._clean_collection_name(getattr(self, "correct_sql_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_COLLECTION") or CORRECT_SQL_COLLECTION),
        }

    def _embed_config(self) -> dict[str, Any]:
        return {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing DB config: {', '.join(missing)}")

    def _require_milvus_config(self, config: dict[str, Any]) -> None:
        missing = [key for key in ("uri", "username", "password", "db_name") if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing Milvus config: {', '.join(missing)}")

    def _require_embed_config(self, config: dict[str, Any]) -> None:
        if not config["base_url"]:
            raise ValueError("rag_embed_base_url is required")
        if not config["model"]:
            raise ValueError("rag_embed_model is required")

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        import oracledb

        dsn = oracledb.makedsn(str(db_config.get("db_host") or "").strip(), int(db_config.get("db_port") or 1521), service_name=str(db_config.get("db_service_name") or "").strip())
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
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

    def _clean_collection_name(self, value: Any) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
            raise ValueError(f"Invalid Milvus collection name: {clean}")
        return clean



    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        owner, table_name = self._split_owner_table(table)
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            if owner:
                cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :owner AND TABLE_NAME = :table_name", {"owner": owner, "table_name": table_name})
            else:
                cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :table_name", {"table_name": table_name})
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

    def _hash_text(self, value: Any) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()

    def _truncate(self, value: Any, max_len: int) -> str:
        text = str(value or "")
        encoded = text.encode("utf-8", errors="ignore")
        if len(encoded) <= max_len:
            return text
        return encoded[:max_len].decode("utf-8", errors="ignore")

    def _chunks(self, values: list[Any], size: int):
        for index in range(0, len(values), size):
            yield values[index:index + size]
