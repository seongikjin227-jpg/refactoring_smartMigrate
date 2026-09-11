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
# 04_saveVectorDB Milvus Vector DB 동기화
# =============================================================================
# 이 컴포넌트는 런타임 검색기가 아니라 운영자가 수동으로 실행하는 일회성 동기화 도구다.
#
# 12C/15C 같은 런타임 컴포넌트는 Milvus 검색만 수행하고,
# Oracle 원천 테이블 전체를 반복 조회하거나 매번 임베딩하지 않는다. 04_saveVectorDB가 별도
# 유지보수 단계에서 Oracle row를 읽고 검색 대상 SQL을 임베딩한 뒤
# 결과 vector와 metadata를 Milvus에 upsert한다.
#
# 데이터 소유 기준:
#
# 벡터 소유 기준:
# - dense_vector는 SOURCE SQL만 기준으로 생성한다.
# - guidance_text / target_sql / to_sql / bind_sql / test_sql은 검색 후
#   프롬프트 구성에 쓰는 metadata이며 semantic vector key가 아니다.
#
class NewType04SaveVectorDB(Component):
    display_name = "04 Sync Milvus Vector DB"
    description = "One-shot sync from Oracle source tables to Milvus RAG collections."
    name = "NewType04SaveVectorDB"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
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

    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def run(self) -> Message:
        # ---------------------------------------------------------------------
        # 전체 동기화 오케스트레이션
        # ---------------------------------------------------------------------
        # 1. Langflow 입력과 환경변수 fallback 값을 읽는다.
        # 2. Oracle 원천 테이블에서 RAG/Correct SQL row를 조회한다.
        # 3. 첫 실제 텍스트로 embedding vector dimension을 확인한다.
        # 4. 필요한 Milvus collection이 존재하는지 확인하고 없으면 생성한다.
        # 5. 변경된 active row는 upsert하고, 더 이상 유효하지 않은 문서는 비활성화한다.
        started = time.perf_counter()
        payload = self._parse_payload(getattr(self, "payload_json", ""))
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
            "component": "04_syncMilvusVectorDB",
            "trigger": {
                "management_route": payload.get("management_route") or "",
                "user_request": payload.get("user_request") or "",
                "routing_reason": payload.get("management_routing_reason") or "",
            },
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
        return Message(text=self._answer(result))

    # VectorDB 동기화 결과를 Chat Output에 바로 연결할 수 있는 사용자 메시지로 만든다.
    def _answer(self, result: dict[str, Any]) -> str:
        if not result.get("ok"):
            return (
                "Correct SQL 및 Conversion / Tuning Guide를 Milvus Vector DB에 동기화하지 못했습니다.\n"
                f"- RAG 실패 batch: {result.get('rag', {}).get('failed_batch_count', 0)}\n"
                f"- Correct SQL Conversion 실패 batch: {result.get('correct_sql_conversion', {}).get('failed_batch_count', 0)}\n"
                f"- Correct SQL Migration 실패 batch: {result.get('correct_sql_migration', {}).get('failed_batch_count', 0)}"
            )
        rag = result.get("rag") or {}
        conversion = result.get("correct_sql_conversion") or {}
        migration = result.get("correct_sql_migration") or {}
        return (
            "Correct SQL 및 Conversion / Tuning Guide를 Milvus Vector DB에 동기화 완료했습니다.\n"
            f"- RAG Guide: active={rag.get('active_count', 0)}, upserted={rag.get('upserted_count', 0)}, skipped={rag.get('skipped_count', 0)}, deactivated={rag.get('deactivated_count', 0)}\n"
            f"- Correct SQL Conversion: active={conversion.get('active_count', 0)}, upserted={conversion.get('upserted_count', 0)}, skipped={conversion.get('skipped_count', 0)}, deactivated={conversion.get('deactivated_count', 0)}\n"
            f"- Correct SQL Migration: active={migration.get('active_count', 0)}, upserted={migration.get('upserted_count', 0)}, skipped={migration.get('skipped_count', 0)}, deactivated={migration.get('deactivated_count', 0)}\n"
            f"- Milvus DB: {result.get('milvus_db_name')}\n"
            f"- Embedding Model: {result.get('embedding_model')}\n"
            f"- Elapsed: {result.get('elapsed_seconds')}s"
        )

    # 04 Management Router에서 넘어온 payload를 dict로 읽고, 직접 실행이면 빈 dict로 둔다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
        parsed = json.loads(clean)
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed

    # Milvus collection 존재 여부를 확인하고 없으면 schema에 맞춰 생성한다.
    def _ensure_collection(self, client: Any, collection_name: str, vector_dim: int, schema_kind: str) -> bool:
        # ---------------------------------------------------------------------
        # Milvus collection 초기화
        # ---------------------------------------------------------------------
        # collection이 이미 있으면 재생성하지 않는다.
        # 운영 collection은 플랫폼에서 미리 만든 schema일 수 있으므로 기존 구조를 존중한다.
        
        #
        # collection이 없을 때만 표준 schema로 생성한다. BM25 sparse 검색을 먼저 시도하고,
        # Milvus 환경이 analyzer/functions를 허용하지 않으면 dense-only collection으로 생성한다.
        
        if client.has_collection(collection_name):
            client.load_collection(collection_name=collection_name)
            return False
        try:
            self._create_collection(client, collection_name, vector_dim, schema_kind, with_bm25=True)
        except Exception:
            self._create_collection(client, collection_name, vector_dim, schema_kind, with_bm25=False)
        client.load_collection(collection_name=collection_name)
        return True

    # RAG/Correct SQL 용도에 맞는 Milvus collection schema와 index를 생성한다.
    def _create_collection(self, client: Any, collection_name: str, vector_dim: int, schema_kind: str, with_bm25: bool) -> None:
        # ---------------------------------------------------------------------
        # Milvus schema 정의
        # ---------------------------------------------------------------------
        # doc_id는 Oracle row 식별자에서 만든 안정적인 primary key다.
        # content_hash는 _sync_collection()에서 변경 여부를 판단하는 값이다.
        # content는 embedding API에 실제로 전달되는 텍스트다.
        # dense_vector는 content에서 생성된 embedding 결과다.
        #
        # sparse_vector는 선택 필드이며, BM25 사용 시 Milvus가 content에서 생성한다.
        # 현재 12C/15C 검색은 dense_vector만 사용하지만,
        # sparse_vector를 남겨두면 나중에 Oracle 동기화 로직을 바꾸지 않고 hybrid search를 붙일 수 있다.
        
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

            # BM25 sparse vector는 content 필드에서 Milvus 내부가 계산한다.
            # sparse_vector를 만들기 위해 embedding API를 별도로 호출하지 않는다.
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(Function(name="content_bm25", input_field_names=["content"], output_field_names=["sparse_vector"], function_type=FunctionType.BM25))

        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        if with_bm25:
            index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25", params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params, consistency_level="Bounded")

    # Oracle snapshot과 Milvus 문서를 비교해 변경분 upsert와 stale 비활성화를 수행한다.
    def _sync_collection(self, client: Any, collection_name: str, rows: list[dict[str, Any]], embed_config: dict[str, Any]) -> dict[str, Any]:
        # ---------------------------------------------------------------------
        # 변경 row 동기화
        # ---------------------------------------------------------------------
        # 비용이 큰 작업은 embedding 생성이므로, 변경되지 않은 row는 다시 임베딩하지 않는다.
        
        #
        # 먼저 Milvus의 활성 doc_id와 content_hash를 조회한다.
        # 다음 조건에 해당하는 row만 embedding/upsert 대상이 된다:
        # - 현재 Oracle snapshot에서 active 상태이고,
        # - content_hash가 Milvus의 active 복사본과 다를 때만 upsert한다.
        #
        # 예전에는 Milvus에 있었지만 현재 Oracle 기준으로 비활성인 row는
        # 즉시 삭제하지 않고 가능하면 inactive로 표시한다.
        # 이렇게 하면 검색에서는 제외하면서도 추적 이력은 유지할 수 있다.
        active_doc_ids = {row["doc_id"] for row in rows if row.get("is_active")}
        existing = self._query_existing_docs(client, collection_name)
        to_upsert = [row for row in rows if row.get("is_active") and existing.get(row["doc_id"]) != row["content_hash"]]
        skipped = len([row for row in rows if row.get("is_active")]) - len(to_upsert)
        failures: list[dict[str, Any]] = []
        upserted = 0
        for batch in self._chunks(to_upsert, BATCH_SIZE):
            try:
                # 이 줄에서만 현재 batch의 embedding API를 호출한다.
                # 반환된 vector는 dense_vector로 붙이고,
                # 나머지 metadata 필드와 함께 Milvus에 기록한다.
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

    # 조회 조건을 조립해 DB에서 요청된 정보를 가져온다.
    def _query_existing_docs(self, client: Any, collection_name: str) -> dict[str, str]:
        # 현재 Oracle 원천 테이블에 대응하는 활성 문서만 읽는다.
        # 변경 여부 판단에는 doc_id와 content_hash만 있으면 충분하므로 결과를 작게 유지한다.
        
        result: dict[str, str] = {}
        try:
            rows = client.query(collection_name=collection_name, filter='doc_id != ""', output_fields=["doc_id", "content_hash", "is_active"], limit=16384)
        except TypeError:
            rows = client.query(collection_name=collection_name, filter='doc_id != ""', output_fields=["doc_id", "content_hash", "is_active"])
        for row in rows or []:
            if row.get("is_active"):
                result[str(row.get("doc_id"))] = str(row.get("content_hash") or "")
        return result

    # Oracle snapshot에서 사라진 문서를 Milvus에서 물리 삭제하지 않고 inactive로 바꾼다.
    def _deactivate_missing_docs(self, client: Any, collection_name: str, existing: dict[str, str], active_doc_ids: set[str]) -> int:
        # stale 문서는 Milvus에는 active로 남아 있지만 최신 Oracle snapshot에서는 active가 아닌 문서다.
        # 보통 USE_YN, status, active 조건이 바뀐 경우다.
        
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

    # 첫 active 문서 embedding으로 Milvus FLOAT_VECTOR dimension을 결정한다.
    def _detect_vector_dim(self, rows: list[dict[str, Any]], embed_config: dict[str, Any]) -> int:
        # Milvus FLOAT_VECTOR는 collection 생성 시 고정 dimension이 필요하다.
        # 실제 dimension은 embedding endpoint 응답을 기준으로 삼는다.
        # 첫 번째 비어 있지 않은 동기화 content를 임베딩해서 dimension을 확인한다.
        for row in rows:
            content = str(row.get("content") or "").strip()
            if content:
                vector = self._embed_texts([content], embed_config)[0]
                return len(vector)
        raise ValueError("No active source rows found for Milvus vector sync")

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_rag_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        # ---------------------------------------------------------------------
        # Oracle NEXT_MIG_RAG_INFO -> SM_RAG_RULES row 변환
        # ---------------------------------------------------------------------
        # SEARCH row는 SOURCE_SQL이 있을 때 vector 검색 가능한 예시가 된다.
        # GENERAL row도 저장하지만 12C/15C에서는 vector 유사도가 아니라
        # category/rule_type 조건 조회로 guide 문맥에 넣는다.
        #
        # vector 검색의 의미 기준은 SOURCE_SQL이다.
        # GUIDANCE_TEXT와 TARGET_SQL은 검색 결과가 프롬프트에서 설명력을 갖도록 metadata로 함께 복사한다.
        
        table = self._qualify(RAG_TABLE, db_config.get("system_schema"))
        sql = f"""
            SELECT RAG_ID,
                   CATEGORY,
                   RULE_TYPE,
                   USE_YN,
                   SOURCE_TABLES,
                   GUIDANCE_TEXT,
                   SOURCE_SQL,
                   TARGET_SQL,
                   TO_CHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI:SS')
              FROM {table}
             ORDER BY UPDATED_AT DESC NULLS LAST, RAG_ID DESC
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
                # dense_vector는 SOURCE_SQL만으로 만들고, guidance/target_sql은 프롬프트 metadata로만 둔다.
                content = self._rag_content(category, rule_type, guidance, source_sql, target_sql)
                # 런타임 검색에는 active row만 노출된다.
                # 비활성/미지원 row는 동기화 snapshot에는 남을 수 있지만,
                # Milvus에서는 검색 대상에서 제외되거나 inactive 처리된다.
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

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_correct_sql_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        # ---------------------------------------------------------------------
        # Oracle NEXT_SQL_INFO -> SM_CORRECT_SQL_CONVERSION row 변환
        # ---------------------------------------------------------------------
        # 이 collection은 이전에 사람이 보정한 SQL 쌍을 저장한다.
        # 12C는 TO_SQL/BIND_SQL/TEST_SQL 생성 시 이 값을 힌트로 사용한다.
        #
        # 검색 기준은 원본 FROM SQL이다:
        # - 사용자가 source SQL을 보정했으면 EDIT_FR_SQL을 우선한다.
        # - 보정본이 없을 때만 FR_SQL을 사용한다.
        #
        # 생성 SQL 컬럼은 vector 검색 후 함께 반환되는 metadata다.
        # dense_vector에는 포함하지 않는다.
        table = self._qualify(SQL_TABLE, db_config.get("system_schema"))
        sql = f"""
            SELECT SPACE_NM,
                   SQL_ID,
                   FR_SQL,
                   EDIT_FR_SQL,
                   STATUS_CONVERSION,
                   USER_EDITED,
                   TAG_KIND,
                   TARGET_TABLE,
                   TO_SQL,
                   BIND_SQL,
                   TEST_SQL,
                   TO_CHAR(UPD_TS, 'YYYY-MM-DD HH24:MI:SS')
              FROM {table}
             WHERE FR_SQL IS NOT NULL
                OR EDIT_FR_SQL IS NOT NULL
             ORDER BY UPD_TS DESC NULLS LAST
        """
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = []
            for row in cur.fetchall():
                space_nm = self._lob_to_str(row[0]).strip()
                sql_id = self._lob_to_str(row[1]).strip()
                fr_sql = self._lob_to_str(row[2]).strip()
                edit_fr_sql = self._lob_to_str(row[3]).strip()
                source_sql = edit_fr_sql or fr_sql
                to_sql = self._lob_to_str(row[8]).strip()
                bind_sql = self._lob_to_str(row[9]).strip()
                test_sql = self._lob_to_str(row[10]).strip()
                status = self._lob_to_str(row[4]).strip().upper()
                user_edited = self._lob_to_str(row[5]).strip().upper()
                # 사람이 보정했고 성공한 conversion row만 correct SQL 힌트로 사용한다.
                # 실패 row나 손대지 않은 row는 모델에 나쁜 예시를 주지 않도록 제외한다.
                
                is_active = bool(source_sql) and user_edited == "Y" and status in {"PASS", "PASS-CONVERSION"} and bool(to_sql or bind_sql or test_sql)
                if not space_nm or not sql_id:
                    continue
                doc_key = f"{space_nm}:{sql_id}"
                rows.append(
                    self._entity(
                        doc_id=f"SQL:{self._hash_text(doc_key)[:24]}",
                        space_nm=space_nm,
                        sql_id=sql_id,
                        status_conversion=status,
                        user_edited=user_edited,
                        tag_kind=self._lob_to_str(row[6]),
                        target_table=self._lob_to_str(row[7]),
                        source_sql=source_sql,
                        to_sql=to_sql,
                        bind_sql=bind_sql,
                        test_sql=test_sql,
                        # correct SQL 힌트 검색용 dense_vector는 EDIT_FR_SQL을 우선 사용하고, 없으면 FR_SQL을 사용한다.
                        content=self._sql_content(source_sql),
                        is_active=is_active,
                        updated_at=self._lob_to_str(row[11]),
                    )
                )
            return rows

    # DB 또는 payload에서 이 단계에 필요한 입력 데이터를 로드한다.
    def _load_correct_migration_rows(self, db_config: dict[str, Any]) -> list[dict[str, Any]]:
        table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
        sql = f"""
            SELECT MAP_ID,
                   FR_TABLE,
                   TO_TABLE,
                   CONDITION,
                   MIG_SQL,
                   VERIFY_SQL,
                   USER_EDITED,
                   STATUS,
                   TO_CHAR(UPD_TS, 'YYYY-MM-DD HH24:MI:SS')
              FROM {table}
             WHERE MIG_SQL IS NOT NULL
               AND VERIFY_SQL IS NOT NULL
             ORDER BY UPD_TS DESC NULLS LAST
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
                # Migration SQL 검색은 생성 당시와 같은 업무 문맥이 필요하다:
                # source/target table, filter condition, 확정된 MIG/VERIFY SQL을 함께 저장한다.
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
                        is_active=user_edited == "Y" and status == "PASS" and bool(mig_sql) and bool(verify_sql),
                        updated_at=self._lob_to_str(row[8]),
                    )
                )
            return rows

    # Milvus에 저장할 공통 entity 구조를 만들고 metadata/hash를 함께 채운다.
    def _entity(self, **values: Any) -> dict[str, Any]:
        # 각 collection에는 자기 schema에 정의된 필드만 전달한다.
        # Milvus dynamic field를 꺼두었기 때문에 RAG rule row가 SQL job 컬럼을 섞어 넣을 수 없다.
        
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
        # content_hash에는 content뿐 아니라 metadata도 포함한다.
        # SOURCE_SQL이 같아도 프롬프트 metadata가 바뀌면 upsert가 발생하게 하기 위해서다.
        # 이 경우 dense_vector 값은 그대로일 수 있지만,
        # Milvus에는 갱신된 guidance/output 필드가 반영된다.
        entity["content_hash"] = self._hash_text(json.dumps({key: entity.get(key) for key in sorted(entity) if key not in {"dense_vector", "content_hash"}}, ensure_ascii=False, sort_keys=True))
        return entity

    # RAG rule에서 embedding 대상이 될 content 문자열을 만든다.
    def _rag_content(self, category: str, rule_type: str, guidance: str, source_sql: str, target_sql: str) -> str:
        # content가 실제 embedding 대상이다.
        #
        # SEARCH RAG row는 SOURCE_SQL 유사도로 검색되어야 하므로 SOURCE_SQL을 우선 사용한다.
        
        #
        # GENERAL row는 12C/15C에서 vector 검색하지 않는다. 다만 active row가 공통 schema에 들어가도록
        # 최소 content를 만들어 둔다. 실제 GENERAL guide는
        # category/rule_type 조건 조회로 로드된다.
        source = source_sql.strip()
        if source:
            return self._sql_content(source)
        if rule_type == RAG_GENERAL:
            return guidance.strip() or target_sql.strip() or category
        return ""

    # Correct SQL 검색에서 source SQL 구조와 원문을 함께 담은 embedding 입력을 만든다.
    def _sql_content(self, source_sql: str) -> str:
        # 같은 SQL을 두 가지 관점으로 묶어 embedding한다:
        # 1. 정규화된 SQL 구조: 주석/literal/숫자를 줄여 구조 유사도에 집중한다.
        #    구조가 비슷한 SQL을 가깝게 찾기 위한 입력이다.
        # 2. 원본 SQL 텍스트: 함수, 테이블명, join, clause를 그대로 보존한다.
        #
        # 여기서도 embedding 대상은 source SQL뿐이며 guidance나 target SQL은 넣지 않는다.
        # 정규화 SQL을 앞에 붙여 literal 차이보다 구조가 더 잘 반영되게 한다.
        
        source = source_sql.strip()
        return "\n".join([self._normalize_sql_shape(source), source]).strip()

    # embedding API에 텍스트 묶음을 보내 dense vector 목록을 받아온다.
    def _embed_texts(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        # ---------------------------------------------------------------------
        # Embedding API 호출
        # ---------------------------------------------------------------------
        # OpenAI 호환 /v1/embeddings endpoint를 호출한다.
        # 응답에 지원 형식의 embedding vector만 있으면 특정 vendor를 가정하지 않는다.
        
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

    # 문자열이나 payload에서 후속 로직에 필요한 값을 추출한다.
    def _extract_embedding_vectors(self, body: Any) -> list[list[float]]:
        # 자주 쓰는 embedding 응답 형식을 모두 수용한다:
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                return [[float(value) for value in item["embedding"]] for item in data if isinstance(item, dict) and isinstance(item.get("embedding"), list)]
            if isinstance(body.get("embeddings"), list):
                return [[float(value) for value in item] for item in body["embeddings"] if isinstance(item, list)]
            if isinstance(body.get("embedding"), list):
                return [[float(value) for value in body["embedding"]]]
        return []

    # 입력된 embedding base URL을 /v1/embeddings endpoint로 정규화한다.
    def _embedding_endpoint(self, base_url: str) -> str:
        # Langflow에는 service root, /v1, /v1/embeddings URL 중 아무 형태나 입력할 수 있다.
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/embeddings"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/embeddings"
        return f"{normalized}/v1/embeddings"

    # 비교와 검색이 안정적으로 동작하도록 입력 값을 정규화한다.
    def _normalize_sql_shape(self, sql_text: str) -> str:
        # SQL 구조는 유지하고 literal 같은 잡음을 제거한다.
        # literal이나 숫자 상수가 달라도 비슷한 SQL이 가까운 vector가 되도록 한다.
        
        text = re.sub(r"/\*.*?\*/", " ", sql_text or "", flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", " ", text)
        text = re.sub(r"'(?:''|[^'])*'", " STR ", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", text)
        text = re.sub(r"\bSUBQUERY_\d+\b", "SUBQUERY", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip().upper()

    # RAG/Correct SQL 검색에 사용할 Milvus client를 생성한다.
    def _milvus_client(self, config: dict[str, Any]) -> Any:
        # ---------------------------------------------------------------------
        # Milvus 연결
        # ---------------------------------------------------------------------
        # Milvus URI는 입력값 그대로 전달한다. host/port를 쪼개거나 기본 port를 붙이지 않는다.
        # username/password도 token 형태로 바꾸지 않는다.
        # 현재 사용 환경에서 동작 확인된 연결 방식을 그대로 따른다.
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    # payload와 Langflow 입력에서 Oracle 접속 및 schema 설정을 모은다.
    def _db_config(self) -> dict[str, Any]:
        # DB 접속 정보는 Langflow 입력을 명시적으로 받는다.
        # port 같은 기본값 외에는 환경변수 fallback을 사용하지 않는다.
        return {
            "db_host": str(getattr(self, "db_host", "") or "").strip(),
            "db_port": int(getattr(self, "db_port", None) or 1521),
            "db_service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "db_username": str(getattr(self, "db_username", "") or "").strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    # Milvus 접속 및 collection 설정을 모은다.
    def _milvus_config(self) -> dict[str, Any]:
        # Milvus 값은 컴포넌트 입력 또는 환경변수로 받을 수 있다.
        # 같은 Langflow graph를 환경 간 이동할 때 설정 재사용을 쉽게 하기 위해서다.
        
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "rag_collection": self._clean_collection_name(getattr(self, "rag_collection_name", "") or os.getenv("MILVUS_RAG_COLLECTION") or RAG_COLLECTION),
            "correct_sql_conversion_collection": self._clean_collection_name(getattr(self, "correct_sql_conversion_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_CONVERSION_COLLECTION") or CORRECT_SQL_CONVERSION_COLLECTION),
            "correct_sql_migration_collection": self._clean_collection_name(getattr(self, "correct_sql_migration_collection_name", "") or os.getenv("MILVUS_CORRECT_SQL_MIGRATION_COLLECTION") or CORRECT_SQL_MIGRATION_COLLECTION),
        }

    # embedding endpoint/model/timeout 설정을 모아 검증에 넘긴다.
    def _embed_config(self) -> dict[str, Any]:
        # embedding 설정은 collection 생성 시 dimension 확인과
        # 변경 row batch upsert 단계에서 함께 사용된다.
        return {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }

    # 필수 DB 접속 값이 없으면 DB 작업 전에 명확히 실패시킨다.
    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        # Oracle 연결 전에 필수 접속 값 누락을 먼저 명확히 실패시킨다.
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing DB config: {', '.join(missing)}")

    # Milvus 접속 필수 값 누락을 collection 작업 전에 명확히 실패시킨다.
    def _require_milvus_config(self, config: dict[str, Any]) -> None:
        # 현재 Milvus 2.6.5 연결은 username/password 방식을 사용한다.
        missing = [key for key in ("uri", "username", "password", "db_name") if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"missing Milvus config: {', '.join(missing)}")

    # embedding 호출 필수 값 누락을 vector 생성 전에 명확히 실패시킨다.
    def _require_embed_config(self, config: dict[str, Any]) -> None:
        # 내부 gateway에서는 embedding API key가 비어 있을 수 있지만,
        # endpoint와 model은 항상 필요하다.
        if not config["base_url"]:
            raise ValueError("rag_embed_base_url is required")
        if not config["model"]:
            raise ValueError("rag_embed_model is required")

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
    def _connect(self, db_config: dict[str, Any]):
        # Oracle 11g 호환 접속 경로다. vector 연산은 Oracle에 맡기지 않는다.
        # Oracle은 rule/correct SQL row의 원천 저장소 역할만 한다.
        import oracledb

        dsn = oracledb.makedsn(str(db_config.get("db_host") or "").strip(), int(db_config.get("db_port") or 1521), service_name=str(db_config.get("db_service_name") or "").strip())
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str, schema: Any) -> str:
        value = str(table_name or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return f"{self._clean_identifier(owner)}.{self._clean_identifier(name)}"
        clean_table = self._clean_identifier(value)
        clean_schema = str(schema or "").strip().upper()
        if not clean_schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        return f"{self._clean_identifier(clean_schema)}.{clean_table}"

    # 동적 SQL identifier에 안전한 Oracle 문자만 허용한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # LLM 응답이나 사용자 입력에서 실행/저장에 불필요한 문자를 제거한다.
    def _clean_collection_name(self, value: Any) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
            raise ValueError(f"Invalid Milvus collection name: {clean}")
        return clean

    # Oracle LOB 값을 연결 종료 전에 문자열로 읽는다.
    def _lob_to_str(self, value: Any) -> str:
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")

    # 숫자 입력을 양의 정수로 변환하고 실패하면 기본값을 사용한다.
    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    # content와 metadata 변경을 감지할 SHA-256 hash를 만든다.
    def _hash_text(self, value: Any) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()

    # Milvus scalar field 길이 제한에 맞춰 긴 문자열을 자른다.
    def _truncate(self, value: Any, max_len: int) -> str:
        text = str(value or "")
        encoded = text.encode("utf-8", errors="ignore")
        if len(encoded) <= max_len:
            return text
        return encoded[:max_len].decode("utf-8", errors="ignore")

    # 대량 upsert 입력을 Milvus insert 단위로 나눈다.
    def _chunks(self, values: list[Any], size: int):
        for index in range(0, len(values), size):
            yield values[index:index + size]
