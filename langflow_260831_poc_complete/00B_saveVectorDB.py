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
CORRECT_SQL_CONVERSION_COLLECTION = "SM_CORRECT_SQL_CONVERSION"
CORRECT_SQL_MIGRATION_COLLECTION = "SM_CORRECT_SQL_MIGRATION"
RAG_GENERAL = "GENERAL"
RAG_SEARCH = "SEARCH"
BATCH_SIZE = 32
TEXT_MAX = 65535


# =============================================================================
# 00B Sync Milvus Vector DB
# =============================================================================
# This component is intentionally a one-shot migration/sync utility.
#
# Runtime components such as 12C and 15C should only search Milvus. They should
# not repeatedly load all Oracle rows and re-embed them. 00B is the separate
# maintenance step that reads Oracle source tables, embeds the searchable SQL
# text once, and upserts the resulting vectors into Milvus.
#
# Data ownership:
# - NEXT_MIG_RAG_INFO  -> SM_RAG_RULES
# - NEXT_SQL_INFO      -> SM_CORRECT_SQL
#
# Vector ownership:
# - dense_vector is generated from SOURCE SQL only.
# - guidance_text / target_sql / to_sql / bind_sql / test_sql are metadata used
#   after retrieval when building prompts; they are not the semantic vector key.
#
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
        StrInput(name="correct_sql_conversion_collection_name", display_name="Correct SQL Conversion Collection Name", value=CORRECT_SQL_CONVERSION_COLLECTION, required=False),
        StrInput(name="correct_sql_migration_collection_name", display_name="Correct SQL Migration Collection Name", value=CORRECT_SQL_MIGRATION_COLLECTION, required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=True),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run", types=["Data"])]

    def run(self) -> Data:
        # ---------------------------------------------------------------------
        # Main orchestration
        # ---------------------------------------------------------------------
        # 1. Read Langflow inputs / environment fallback.
        # 2. Load Oracle rows from the two source tables.
        # 3. Detect embedding vector dimension from the first real text.
        # 4. Ensure both Milvus collections exist.
        # 5. Upsert changed active rows and deactivate stale Milvus documents.
        started = time.perf_counter()
        db_config = self._db_config()
        milvus_config = self._milvus_config()
        embed_config = self._embed_config()
        self._require_db_config(db_config)
        self._require_milvus_config(milvus_config)
        self._require_embed_config(embed_config)

        rag_rows = self._load_rag_rows(db_config)
        conversion_rows = self._load_correct_sql_rows(db_config)
        migration_rows = self._load_correct_migration_rows(db_config)
        active_rows = rag_rows + conversion_rows + migration_rows
        vector_dim = self._detect_vector_dim(active_rows, embed_config)

        client = self._milvus_client(milvus_config)
        created = {
            "rag": self._ensure_collection(client, milvus_config["rag_collection"], vector_dim, "rag"),
            "correct_sql_conversion": self._ensure_collection(client, milvus_config["correct_sql_conversion_collection"], vector_dim, "conversion"),
            "correct_sql_migration": self._ensure_collection(client, milvus_config["correct_sql_migration_collection"], vector_dim, "migration"),
        }

        rag_result = self._sync_collection(client, milvus_config["rag_collection"], rag_rows, embed_config)
        conversion_result = self._sync_collection(client, milvus_config["correct_sql_conversion_collection"], conversion_rows, embed_config)
        migration_result = self._sync_collection(client, milvus_config["correct_sql_migration_collection"], migration_rows, embed_config)

        result = {
            "ok": not rag_result["failures"] and not conversion_result["failures"] and not migration_result["failures"],
            "component": "00B_syncMilvusVectorDB",
            "milvus_db_name": milvus_config["db_name"],
            "collections": {
                "rag_rules": milvus_config["rag_collection"],
                "correct_sql_conversion": milvus_config["correct_sql_conversion_collection"],
                "correct_sql_migration": milvus_config["correct_sql_migration_collection"],
            },
            "collection_created": created,
            "vector_dim": vector_dim,
            "embedding_model": embed_config["model"],
            "source_scope": {
                RAG_TABLE: "all rows synced; USE_YN='Y' and SOURCE_SQL present become active",
                SQL_TABLE: "USER_EDITED='Y' and STATUS_CONVERSION pass rows become active for correct SQL hints",
            },
            "rag": rag_result,
            "correct_sql_conversion": conversion_result,
            "correct_sql_migration": migration_result,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        self.status = result
        return Data(data=result)

    def _ensure_collection(self, client: Any, collection_name: str, vector_dim: int, schema_kind: str) -> bool:
        # ---------------------------------------------------------------------
        # Milvus collection bootstrap
        # ---------------------------------------------------------------------
        # If the collection already exists, this component never recreates it.
        # Existing schemas are respected because production collections may have
        # been created manually by the platform team.
        #
        # If the collection is missing, create it using the expected schema. BM25
        # sparse search is attempted first, then the code falls back to a dense-
        # only collection for Milvus setups that do not allow analyzer/functions.
        if client.has_collection(collection_name):
            client.load_collection(collection_name=collection_name)
            return False
        try:
            self._create_collection(client, collection_name, vector_dim, schema_kind, with_bm25=True)
        except Exception:
            self._create_collection(client, collection_name, vector_dim, schema_kind, with_bm25=False)
        client.load_collection(collection_name=collection_name)
        return True

    def _create_collection(self, client: Any, collection_name: str, vector_dim: int, schema_kind: str, with_bm25: bool) -> None:
        # ---------------------------------------------------------------------
        # Milvus schema definition
        # ---------------------------------------------------------------------
        # doc_id is the stable primary key, generated from the Oracle row identity.
        # content_hash is the change detector used by _sync_collection().
        # content is the exact text sent to the embedding API.
        # dense_vector is the embedding generated from content.
        #
        # sparse_vector is optional. It is generated by Milvus BM25 from content
        # when with_bm25=True. Current 12C/15C retrieval uses dense_vector only,
        # but keeping sparse_vector available lets us add hybrid search later
        # without changing the Oracle sync logic.
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("doc_id", DataType.VARCHAR, is_primary=True, auto_id=False, max_length=256)
        if schema_kind == "rag":
            schema.add_field("rag_id", DataType.VARCHAR, max_length=128)
            schema.add_field("category", DataType.VARCHAR, max_length=64)
            schema.add_field("rule_type", DataType.VARCHAR, max_length=32)
            schema.add_field("use_yn", DataType.VARCHAR, max_length=8)
            schema.add_field("source_tables", DataType.VARCHAR, max_length=2048)
            schema.add_field("guidance_text", DataType.VARCHAR, max_length=8192)
            schema.add_field("source_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("target_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        elif schema_kind == "conversion":
            schema.add_field("space_nm", DataType.VARCHAR, max_length=512)
            schema.add_field("sql_id", DataType.VARCHAR, max_length=512)
            schema.add_field("status_conversion", DataType.VARCHAR, max_length=100)
            schema.add_field("user_edited", DataType.VARCHAR, max_length=8)
            schema.add_field("tag_kind", DataType.VARCHAR, max_length=100)
            schema.add_field("target_table", DataType.VARCHAR, max_length=2048)
            schema.add_field("source_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("to_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("bind_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("test_sql", DataType.VARCHAR, max_length=TEXT_MAX)
        elif schema_kind == "migration":
            schema.add_field("map_id", DataType.VARCHAR, max_length=128)
            schema.add_field("fr_table", DataType.VARCHAR, max_length=2048)
            schema.add_field("to_table", DataType.VARCHAR, max_length=2048)
            schema.add_field("condition", DataType.VARCHAR, max_length=8192)
            schema.add_field("mig_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("verify_sql", DataType.VARCHAR, max_length=TEXT_MAX)
            schema.add_field("user_edited", DataType.VARCHAR, max_length=8)
            schema.add_field("status", DataType.VARCHAR, max_length=100)
        else:
            raise ValueError(f"Unsupported collection schema: {schema_kind}")
        schema.add_field("content", DataType.VARCHAR, max_length=TEXT_MAX, enable_analyzer=with_bm25)
        schema.add_field("content_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("is_active", DataType.BOOL)
        schema.add_field("updated_at", DataType.VARCHAR, max_length=64)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=vector_dim)
        if with_bm25:
            from pymilvus import Function, FunctionType

            # BM25 sparse vector is derived inside Milvus from the content field.
            # No embedding API call is made for sparse_vector.
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(Function(name="content_bm25", input_field_names=["content"], output_field_names=["sparse_vector"], function_type=FunctionType.BM25))

        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        if with_bm25:
            index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25", params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params, consistency_level="Bounded")

    def _sync_collection(self, client: Any, collection_name: str, rows: list[dict[str, Any]], embed_config: dict[str, Any]) -> dict[str, Any]:
        # ---------------------------------------------------------------------
        # Changed-row sync
        # ---------------------------------------------------------------------
        # The expensive operation is embedding generation, so this function avoids
        # embedding unchanged rows.
        #
        # Existing Milvus active doc_id -> content_hash is queried first.
        # A row is embedded/upserted only when:
        # - it is active in the current Oracle snapshot, and
        # - its content_hash differs from the active Milvus copy.
        #
        # Rows that used to exist in Milvus but are no longer active in Oracle are
        # not blindly deleted first. We mark them inactive when possible so old
        # records are kept out of search while preserving traceability.
        active_doc_ids = {row["doc_id"] for row in rows if row.get("is_active")}
        existing = self._query_existing_docs(client, collection_name)
        to_upsert = [row for row in rows if row.get("is_active") and existing.get(row["doc_id"]) != row["content_hash"]]
        skipped = len([row for row in rows if row.get("is_active")]) - len(to_upsert)
        failures: list[dict[str, Any]] = []
        upserted = 0
        for batch in self._chunks(to_upsert, BATCH_SIZE):
            try:
                # Only this line calls the embedding API for the batch.
                # The returned vectors are attached as dense_vector and then
                # written to Milvus with the rest of the metadata fields.
                vectors = self._embed_texts([row["content"] for row in batch], embed_config)
                entities = [{**row, "dense_vector": vector} for row, vector in zip(batch, vectors)]
                client.upsert(collection_name=collection_name, data=entities)
                upserted += len(entities)
            except Exception as exc:
                failures.append({"doc_ids": [row["doc_id"] for row in batch], "error": str(exc)})

        deactivated = self._deactivate_missing_docs(client, collection_name, existing, active_doc_ids)
        return {
            "loaded_count": len(rows),
            "active_count": len(active_doc_ids),
            "upserted_count": upserted,
            "skipped_count": max(skipped, 0),
            "deactivated_count": deactivated,
            "failed_batch_count": len(failures),
            "failures": failures[:10],
        }

    def _query_existing_docs(self, client: Any, collection_name: str) -> dict[str, str]:
        # Read only active documents for this Oracle source table.
        # The result is deliberately small: doc_id and content_hash are enough to
        # decide whether a row must be re-embedded.
        result: dict[str, str] = {}
        try:
            rows = client.query(collection_name=collection_name, filter='doc_id != ""', output_fields=["doc_id", "content_hash", "is_active"], limit=16384)
        except TypeError:
            rows = client.query(collection_name=collection_name, filter='doc_id != ""', output_fields=["doc_id", "content_hash", "is_active"])
        for row in rows or []:
            if row.get("is_active"):
                result[str(row.get("doc_id"))] = str(row.get("content_hash") or "")
        return result

    def _deactivate_missing_docs(self, client: Any, collection_name: str, existing: dict[str, str], active_doc_ids: set[str]) -> int:
        # A stale document is active in Milvus but not active in the latest Oracle
        # snapshot. This usually means USE_YN changed, status changed, or the row
        # no longer satisfies the active criteria.
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
        # Milvus FLOAT_VECTOR fields require a fixed dimension at collection
        # creation time. The embedding endpoint is the source of truth, so detect
        # the dimension by embedding the first non-empty sync content.
        for row in rows:
            content = str(row.get("content") or "").strip()
            if content:
                vector = self._embed_texts([content], embed_config)[0]
                return len(vector)
        raise ValueError("No active source rows found for Milvus vector sync")

    def _load_rag_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        # ---------------------------------------------------------------------
        # Oracle -> SM_RAG_RULES row mapping
        # ---------------------------------------------------------------------
        # SEARCH rows become vector-searchable examples when SOURCE_SQL exists.
        # GENERAL rows are stored too, but 12C/15C load them by scalar query as
        # guidance, not by vector similarity.
        #
        # For vector search, SOURCE_SQL is the only semantic key. The guidance and
        # target SQL columns are still copied into Milvus because the retrieved
        # rows need to explain what rule/example should be applied in the prompt.
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
                # Active rows are the only rows searched at runtime.
                # Inactive/unsupported rows can still be represented in the sync
                # snapshot, but they will be skipped or deactivated in Milvus.
                is_supported = category in {"SQL_CONVERSION", "SQL_TUNING"} and rule_type in {RAG_GENERAL, RAG_SEARCH}
                has_rule_body = bool(source_sql) if rule_type == RAG_SEARCH else bool(guidance or source_sql or target_sql)
                is_active = use_yn == "Y" and is_supported and has_rule_body
                rows.append(
                    self._entity(
                        doc_id=f"RAG:{rag_id}",
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
        # ---------------------------------------------------------------------
        # Oracle -> SM_CORRECT_SQL row mapping
        # ---------------------------------------------------------------------
        # This collection stores previously corrected SQL pairs. 12C uses it as
        # a hint source when generating TO_SQL/BIND_SQL/TEST_SQL.
        #
        # The searchable side is the original FROM SQL:
        # - EDIT_FR_SQL wins when a user corrected the source SQL.
        # - FR_SQL is used as the fallback.
        #
        # The generated SQL columns are metadata returned after vector retrieval;
        # they are not embedded into dense_vector.
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
                # Only trusted, user-edited successful conversion rows are used
                # as correct SQL hints. Failed or untouched rows are excluded so
                # the RAG hint does not teach the model bad output.
                is_active = bool(source_sql) and user_edited == "Y" and status in {"PASS", "PASS-CONVERSION"} and bool(to_sql or bind_sql or test_sql)
                doc_key = f"{space_nm}:{sql_id}" if space_nm or sql_id else row_id
                rows.append(
                    self._entity(
                        doc_id=f"SQL:{self._hash_text(doc_key)[:24]}",
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

    def _load_correct_migration_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        """Load user-confirmed migration SQL examples for SM_CORRECT_SQL_MIGRATION."""
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        required = {"MAP_ID", "MIG_SQL", "USER_EDITED", "STATUS"}
        if not required.issubset(columns):
            return []
        fr_table_expr = "FR_TABLE" if "FR_TABLE" in columns else "CAST(NULL AS VARCHAR2(4000))"
        to_table_expr = "TO_TABLE" if "TO_TABLE" in columns else "CAST(NULL AS VARCHAR2(4000))"
        condition_expr = "CONDITION" if "CONDITION" in columns else "TO_CLOB(NULL)"
        verify_expr = "VERIFY_SQL" if "VERIFY_SQL" in columns else "TO_CLOB(NULL)"
        updated_expr = "TO_CHAR(UPD_TS, 'YYYY-MM-DD HH24:MI:SS')" if "UPD_TS" in columns else "TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS')"
        sql = f"""
            SELECT MAP_ID, {fr_table_expr}, {to_table_expr}, {condition_expr}, MIG_SQL, {verify_expr}, USER_EDITED, STATUS, {updated_expr}
              FROM {table}
             WHERE MIG_SQL IS NOT NULL
             ORDER BY {updated_expr} DESC
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = []
            for row in cur.fetchall():
                map_id = self._lob_to_str(row[0]).strip()
                fr_table = self._lob_to_str(row[1]).strip()
                to_table = self._lob_to_str(row[2]).strip()
                condition = self._lob_to_str(row[3]).strip()
                mig_sql = self._lob_to_str(row[4]).strip()
                verify_sql = self._lob_to_str(row[5]).strip()
                user_edited = self._lob_to_str(row[6]).strip().upper()
                status = self._lob_to_str(row[7]).strip().upper()
                # Retrieval needs the same business context used to create a migration:
                # source/target table, filter condition, and the confirmed MIG_SQL.
                search_content = "\n".join(
                    part for part in (f"FR_TABLE: {fr_table}", f"TO_TABLE: {to_table}", f"CONDITION: {condition}", f"MIG_SQL: {mig_sql}") if part.strip()
                )
                rows.append(
                    self._entity(
                        doc_id=f"MIG:{self._hash_text(map_id)[:24]}",
                        map_id=map_id,
                        fr_table=fr_table,
                        to_table=to_table,
                        condition=condition,
                        mig_sql=mig_sql,
                        verify_sql=verify_sql,
                        user_edited=user_edited,
                        status=status,
                        content=search_content,
                        is_active=user_edited == "Y" and status == "PASS" and bool(mig_sql),
                        updated_at=self._lob_to_str(row[8]),
                    )
                )
            return rows

    def _entity(self, **values: Any) -> dict[str, Any]:
        # Each collection passes only its own schema fields. Dynamic fields are
        # disabled in Milvus, so a RAG rule can never add SQL-job columns and
        # vice versa.
        entity = dict(values)
        for key in ("source_sql", "target_sql", "to_sql", "bind_sql", "test_sql", "mig_sql", "verify_sql", "content"):
            if key in entity:
                entity[key] = self._truncate(entity.get(key), TEXT_MAX)
        for key in ("guidance_text", "condition"):
            if key in entity:
                entity[key] = self._truncate(entity.get(key), 8192)
        for key in ("source_tables", "target_table", "fr_table", "to_table"):
            if key in entity:
                entity[key] = self._truncate(entity.get(key), 2048)
        # content_hash includes metadata as well as content. This intentionally
        # causes an upsert when prompt metadata changes, even if SOURCE_SQL stays
        # the same. In that case the dense_vector may be numerically unchanged,
        # but Milvus still receives the updated guidance/output fields.
        entity["content_hash"] = self._hash_text(json.dumps({key: entity.get(key) for key in sorted(entity) if key not in {"dense_vector", "content_hash"}}, ensure_ascii=False, sort_keys=True))
        return entity

    def _rag_content(self, category: str, rule_type: str, guidance: str, source_sql: str, target_sql: str) -> str:
        # content is what gets embedded.
        #
        # SEARCH RAG rows must be searched by SOURCE_SQL similarity, so SOURCE_SQL
        # is the preferred and normal path.
        #
        # GENERAL rows are not vector-searched by 12C/15C. A small fallback content
        # value lets the row fit the common schema if it is active, but GENERAL
        # guidance is loaded by category/rule_type scalar filters.
        source = source_sql.strip()
        if source:
            return self._sql_content(source)
        if rule_type == RAG_GENERAL:
            return guidance.strip() or target_sql.strip() or category
        return ""

    def _sql_content(self, source_sql: str) -> str:
        # Embed two views of the same SQL:
        # 1. normalized SQL shape: comments/literals/numbers reduced, useful for
        #    matching structurally similar statements
        # 2. original SQL text: preserves functions, table names, joins, clauses
        #
        # This is still "source SQL only"; it does not include guidance or target
        # SQL. The normalization just gives the embedding model a stable pattern
        # before the raw SQL.
        source = source_sql.strip()
        return "\n".join([self._normalize_sql_shape(source), source]).strip()

    def _embed_texts(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        # ---------------------------------------------------------------------
        # Embedding API call
        # ---------------------------------------------------------------------
        # This uses an OpenAI-compatible /v1/embeddings endpoint. The component
        # does not assume a specific vendor as long as the response contains
        # embedding vectors in one of the supported shapes below.
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
        # Support common embedding response formats:
        # - {"data": [{"embedding": [...]}]}
        # - {"embeddings": [[...]]}
        # - {"embedding": [...]}
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
        # Accept either a service root, /v1, or /v1/embeddings URL from Langflow.
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/embeddings"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/embeddings"
        return f"{normalized}/v1/embeddings"

    def _normalize_sql_shape(self, sql_text: str) -> str:
        # Keep SQL structure, remove noisy values.
        # This helps similar SQLs stay close even when literals or numeric
        # constants differ between jobs.
        text = re.sub(r"/\*.*?\*/", " ", sql_text or "", flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", " ", text)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip().upper()

    def _milvus_client(self, config: dict[str, Any]) -> Any:
        # ---------------------------------------------------------------------
        # Milvus connection
        # ---------------------------------------------------------------------
        # Pass uri exactly as entered. Do not split host/port, do not append a
        # default port, and do not convert username/password into token form.
        # This matches the connection style that worked in the user's environment.
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    def _db_config(self) -> dict[str, Any]:
        # Langflow DB inputs are explicit. Unlike Milvus/embedding config, these
        # do not currently use environment fallback except for defaults.
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "SFAADM").strip(),
        }

    def _milvus_config(self) -> dict[str, Any]:
        # Milvus values can be supplied directly in the component or through env
        # vars, which is useful when the same Langflow graph is moved between
        # environments.
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "rag_collection": self._clean_collection_name(getattr(self, "rag_collection_name", "") or os.getenv("MILVUS_RAG_COLLECTION") or RAG_COLLECTION),
            "correct_sql_conversion_collection": self._clean_collection_name(getattr(self, "correct_sql_conversion_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_CONVERSION_COLLECTION") or CORRECT_SQL_CONVERSION_COLLECTION),
            "correct_sql_migration_collection": self._clean_collection_name(getattr(self, "correct_sql_migration_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_MIGRATION_COLLECTION") or CORRECT_SQL_MIGRATION_COLLECTION),
        }

    def _embed_config(self) -> dict[str, Any]:
        # Embedding config is shared by collection creation dimension detection
        # and the changed-row batch upsert step.
        return {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        # Fail early before opening Oracle if required connection fields are empty.
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing DB config: {', '.join(missing)}")

    def _require_milvus_config(self, config: dict[str, Any]) -> None:
        # Milvus 2.6.5 connection in this environment uses username/password.
        missing = [key for key in ("uri", "username", "password", "db_name") if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing Milvus config: {', '.join(missing)}")

    def _require_embed_config(self, config: dict[str, Any]) -> None:
        # The embedding API key may be blank for internal gateways, but endpoint
        # and model are always required.
        if not config["base_url"]:
            raise ValueError("rag_embed_base_url is required")
        if not config["model"]:
            raise ValueError("rag_embed_model is required")

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        # Oracle 11g-compatible access path. Vector math is intentionally not
        # delegated to Oracle; Oracle is only the source of rule/correct-SQL rows.
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
