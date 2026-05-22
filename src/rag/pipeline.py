from src.llm.embedding import embed_texts
from src.llm.rerank import rerank
from src.vector_db.milvus_client import (
    get_collection,
    LEGAL_KNOWLEDGE_COLLECTION,
    SESSION_DOCUMENTS_COLLECTION,
)
from src.rag.bm25 import get_legal_bm25, build_legal_bm25, get_session_bm25, ensure_session_bm25, get_memory_bm25, build_memory_bm25
from src.rag.query_rewriter import rewrite_query
from loguru import logger

RRF_K = 60  # RRF smoothing constant


def _truncate(text: str, max_len: int = 80) -> str:
    t = (text or "").replace("\n", " ")
    return t[:max_len] + ("..." if len(t) > max_len else "")


def _rrf_fusion(
    vector_results: list[tuple[int, float]],
    bm25_results: list[tuple[int, float]],
    top_k: int,
) -> list[int]:
    """RRF fusion of two ranked lists. Returns sorted doc indices."""
    scores: dict[int, float] = {}

    for rank, (doc_idx, _) in enumerate(vector_results):
        scores[doc_idx] = scores.get(doc_idx, 0.0) + 1.0 / (RRF_K + rank + 1)

    for rank, (doc_idx, _) in enumerate(bm25_results):
        scores[doc_idx] = scores.get(doc_idx, 0.0) + 1.0 / (RRF_K + rank + 1)

    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in sorted_items[:top_k]]


async def retrieve(
    query: str,
    collection_name: str,
    top_k: int = 10,
    output_fields: list[str] | None = None,
) -> list[dict]:
    """Pure vector retrieval with rerank (unchanged for session docs)."""
    if output_fields is None:
        output_fields = ["chunk_text"]

    query_vec = (await embed_texts([query]))[0]

    coll = get_collection(collection_name)
    search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
    results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=top_k * 3,  # over-retrieve for rerank
        output_fields=output_fields,
    )

    hits = results[0]
    if not hits:
        return []

    documents = [hit.entity.get("chunk_text") or "" for hit in hits]
    reranked = await rerank(query, documents, top_k=top_k)

    extra_fields = [f for f in output_fields if f != "chunk_text"]
    return [
        {
            "chunk_text": documents[r["index"]],
            "score": r["relevance_score"],
            **{f: hits[r["index"]].entity.get(f) or "" for f in extra_fields},
        }
        for r in reranked
    ]


async def insert_chunks(
    chunks: list[str],
    collection_name: str,
    metadata: list[dict] | None = None,
):
    """Embed chunks and insert into Milvus. Rebuilds BM25 if legal_knowledge."""
    if not chunks:
        return

    vectors = await embed_texts(chunks)
    coll = get_collection(collection_name)
    entities = []

    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        entity = {"chunk_text": chunk, "vector": vec}
        if metadata and i < len(metadata):
            entity.update(metadata[i])
        entities.append(entity)

    coll.insert(entities)
    coll.flush()


async def retrieve_legal(query: str, top_k: int = 5, history_text: str = "") -> list[dict]:
    """Hybrid retrieval: BM25 + Cosine vector search → RRF fusion → rerank → top_k."""
    # Rewrite query for better legal retrieval
    if history_text:
        rewritten = await rewrite_query(query, history_text)
    else:
        rewritten = query

    logger.info(f"[RAG] query: {query}" + (f" → rewritten: {rewritten}" if rewritten != query else ""))
    query_vec = (await embed_texts([rewritten]))[0]

    # 1. Vector search: over-retrieve 30
    coll = get_collection(LEGAL_KNOWLEDGE_COLLECTION)
    search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
    vec_results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=30,
        output_fields=["chunk_text", "law_name", "chapter", "article_number"],
    )
    vec_hits = vec_results[0]

    if vec_hits:
        _vec_items = [
            f"#{hit.id}({hit.distance:.4f}):{_truncate(hit.entity.get('chunk_text',''),60)}"
            for hit in vec_hits[:15]
        ]
        logger.debug(f"[RAG:VEC] top{len(vec_hits)}:\n  " + "\n  ".join(_vec_items))
    else:
        logger.debug(f"[RAG:VEC] no results")

    # 2. BM25 search: over-retrieve 30
    bm25 = get_legal_bm25()
    bm25_results = bm25.search(rewritten, top_k=30)

    if bm25_results:
        _bm25_items = [
            f"idx={idx}({score:.4f}):{_truncate(bm25.documents[idx] if idx < len(bm25.documents) else '',60)}"
            for idx, score in bm25_results[:15]
        ]
        logger.debug(f"[RAG:BM25] top{len(bm25_results)}:\n  " + "\n  ".join(_bm25_items))
    else:
        logger.debug(f"[RAG:BM25] no results (built={bm25._built})")

    # 3. RRF fusion → top 30 candidates
    vec_indices = [(hit.id, hit.distance) for hit in vec_hits]
    fused_indices = _rrf_fusion(vec_indices, bm25_results, top_k=30)

    _fused_items = [
        f"idx={i}(src={'vec' if i in {h.id for h in vec_hits} else 'bm25'})"
        for i in fused_indices[:15]
    ]
    logger.debug(f"[RAG:RRF] fused top{len(fused_indices)}: {', '.join(_fused_items)}")

    # 4. Build document list from fused indices (deduplicate by text)
    id_to_hit = {hit.id: hit for hit in vec_hits}
    bm25_docs: dict[int, str] = {}
    for doc_idx, _ in bm25_results:
        if doc_idx < len(bm25.documents):
            bm25_docs[doc_idx] = bm25.documents[doc_idx]

    documents = []
    doc_sources = []  # Milvus hit or BM25 metadata dict
    seen: set[str] = set()
    for idx in fused_indices:
        hit = id_to_hit.get(idx)
        if hit:
            text = hit.entity.get("chunk_text") or ""
            if text and text not in seen:
                seen.add(text)
                documents.append(text)
                doc_sources.append(hit)
        elif idx in bm25_docs:
            text = bm25_docs[idx]
            if text and text not in seen:
                seen.add(text)
                documents.append(text)
                doc_sources.append(bm25.get_meta(idx))

    logger.debug(f"[RAG:RRF] after dedup: {len(documents)} unique docs (was {len(fused_indices)})")

    if not documents:
        logger.warning(f"[RAG] no documents after fusion")
        return []

    # 5. Rerank unique documents → final top_k, then filter by relevance threshold
    reranked = await rerank(rewritten, documents, top_k=top_k)

    # Filter out low-relevance results
    reranked = [r for r in reranked if r["relevance_score"] >= 0.4]
    logger.debug(f"[RAG:RERANK] after score filter (>=0.4): {len(reranked)} results")

    _rerank_items = [
        f"  [{r['relevance_score']:.4f}] {documents[r['index']]}"
        for r in reranked
    ]
    logger.info(f"[RAG:RERANK] final {len(reranked)} results:\n" + "\n".join(_rerank_items))

    results = []
    for r in reranked:
        idx = r["index"]
        source = doc_sources[idx]
        # source can be a Milvus hit or a BM25 metadata dict
        if hasattr(source, "entity"):
            results.append({
                "chunk_text": documents[idx],
                "score": r["relevance_score"],
                "law_name": source.entity.get("law_name") or "",
                "chapter": source.entity.get("chapter") or "",
                "article_number": source.entity.get("article_number") or "",
            })
        else:
            meta = source or {}
            results.append({
                "chunk_text": documents[idx],
                "score": r["relevance_score"],
                "law_name": meta.get("law_name", ""),
                "chapter": meta.get("chapter", ""),
                "article_number": meta.get("article_number", ""),
            })

    logger.info(f"[RAG] done: {len(results)} results, top_score={results[0]['score']:.4f}" if results else "[RAG] done: 0 results")
    return results


async def build_bm25_from_collection():
    """Build/Rebuild BM25 index from all legal_knowledge chunks."""
    coll = get_collection(LEGAL_KNOWLEDGE_COLLECTION)
    # Query all chunks in batches (Milvus limit: 16384)
    all_chunks = []
    all_meta = []
    offset = 0
    batch = 2000
    while True:
        results = coll.query(
            expr="id >= 0",
            output_fields=["chunk_text", "law_name", "chapter", "article_number"],
            limit=batch,
            offset=offset,
        )
        if not results:
            break
        for h in results:
            all_chunks.append(h.get("chunk_text") or "")
            all_meta.append({
                "law_name": h.get("law_name") or "",
                "chapter": h.get("chapter") or "",
                "article_number": h.get("article_number") or "",
            })
        if len(results) < batch:
            break
        offset += batch

    build_legal_bm25(all_chunks, all_meta)
    return len(all_chunks)


async def retrieve_session_docs(
    query: str,
    session_id: str | None = None,
    top_k: int = 5,
    history_text: str = "",
) -> list[dict]:
    """Hybrid retrieval for session documents: BM25 + Vector → RRF → rerank."""
    if history_text:
        rewritten = await rewrite_query(query, history_text)
    else:
        rewritten = query

    logger.info(f"[RAG:DOC] query: {query}" + (f" → rewritten: {rewritten}" if rewritten != query else ""))
    query_vec = (await embed_texts([rewritten]))[0]

    # 1. Vector search
    coll = get_collection(SESSION_DOCUMENTS_COLLECTION)
    search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
    expr = f'session_id == "{session_id}"' if session_id else None

    vec_results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=30,
        expr=expr,
        output_fields=["chunk_text", "document_name", "chunk_index"],
    )
    vec_hits = vec_results[0]
    logger.debug(f"[RAG:DOC:VEC] hits: {len(vec_hits)}")

    # 2. BM25 search (rebuild from Milvus if lost after restart)
    bm25 = await ensure_session_bm25(session_id) if session_id else None
    bm25_results = bm25.search(rewritten, top_k=30) if bm25 else []
    logger.debug(f"[RAG:DOC:BM25] hits: {len(bm25_results)}")

    if not vec_hits and not bm25_results:
        return []

    # 3. RRF fusion
    vec_indices = [(hit.id, hit.distance) for hit in vec_hits]
    fused_indices = _rrf_fusion(vec_indices, bm25_results, top_k=30)

    # 4. Build document list (deduplicated)
    id_to_hit = {hit.id: hit for hit in vec_hits}
    bm25_docs: dict[int, str] = {}
    for doc_idx, _ in bm25_results:
        if doc_idx < len(bm25.documents):
            bm25_docs[doc_idx] = bm25.documents[doc_idx]

    documents = []
    seen: set[str] = set()
    for idx in fused_indices:
        hit = id_to_hit.get(idx)
        if hit:
            text = hit.entity.get("chunk_text") or ""
            if text and text not in seen:
                seen.add(text)
                documents.append(text)
        elif idx in bm25_docs:
            text = bm25_docs[idx]
            if text and text not in seen:
                seen.add(text)

    logger.debug(f"[RAG:DOC] after dedup: {len(documents)} unique docs")

    if not documents:
        return []

    # 5. Rerank
    reranked = await rerank(rewritten, documents, top_k=top_k)
    reranked = [r for r in reranked if r["relevance_score"] >= 0.3]

    _doc_items = [
        f"  [{r['relevance_score']:.4f}] {documents[r['index']]}"
        for r in reranked
    ]
    logger.info(f"[RAG:DOC:RERANK] final {len(reranked)} results:\n" + "\n".join(_doc_items))

    # Build results with metadata from vec_hits where available
    id_to_hit_full = {hit.id: hit for hit in vec_hits}
    return [
        {
            "chunk_text": documents[r["index"]],
            "score": r["relevance_score"],
        }
        for r in reranked
    ]


async def retrieve_session_memory(
    query: str,
    user_id: int,
    top_k: int = 5,
) -> list[dict]:
    """Hybrid retrieval for session memory (long-term structured summaries).

    Searches across all of a user's past session summaries stored in Milvus.
    Filters out expired entries (older than 7 days). Renews on hit.
    """
    import time
    from src.vector_db.milvus_client import get_collection, SESSION_MEMORY_COLLECTION

    now = int(time.time())
    ttl_seconds = 7 * 24 * 3600
    min_created_at = now - ttl_seconds

    # Ensure BM25 index is built
    await ensure_memory_bm25(user_id)

    query_vec = (await embed_texts([query]))[0]

    # 1. Vector search
    coll = get_collection(SESSION_MEMORY_COLLECTION)
    search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}

    vec_results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=top_k * 3,
        expr=f'user_id == {user_id} and created_at > {min_created_at}',
        output_fields=["summary_text", "session_id", "topic", "created_at"],
    )
    vec_hits = vec_results[0]
    logger.debug(f"[RAG:MEM:VEC] hits: {len(vec_hits)}")

    # 2. BM25 search
    bm25 = get_memory_bm25(user_id)
    bm25_results = bm25.search(query, top_k=top_k * 3) if bm25 else []
    logger.debug(f"[RAG:MEM:BM25] hits: {len(bm25_results)}")

    if not vec_hits and not bm25_results:
        return []

    # 3. RRF fusion
    vec_indices = [(hit.id, hit.distance) for hit in vec_hits]
    fused_indices = _rrf_fusion(vec_indices, bm25_results, top_k=top_k * 3)

    # 4. Build document list (deduplicated)
    id_to_hit = {hit.id: hit for hit in vec_hits}
    bm25_docs: dict[int, str] = {}
    for doc_idx, _ in bm25_results:
        if bm25 and doc_idx < len(bm25.documents):
            bm25_docs[doc_idx] = bm25.documents[doc_idx]

    documents = []
    doc_sources = []
    seen: set[str] = set()
    for idx in fused_indices:
        hit = id_to_hit.get(idx)
        if hit:
            text = hit.entity.get("summary_text") or ""
            if text and text not in seen:
                seen.add(text)
                documents.append(text)
                doc_sources.append(hit)
        elif idx in bm25_docs:
            text = bm25_docs[idx]
            if text and text not in seen:
                seen.add(text)
                documents.append(text)
                doc_sources.append(bm25.get_meta(idx) if bm25 else {})

    if not documents:
        return []

    # 5. Rerank
    reranked = await rerank(query, documents, top_k=top_k)
    reranked = [r for r in reranked if r["relevance_score"] >= 0.3]

    # 6. Build results + renew TTL on hit
    results = []
    renew_ids = []
    for r in reranked:
        idx = r["index"]
        source = doc_sources[idx]
        if hasattr(source, "entity"):
            text = source.entity.get("summary_text") or documents[idx]
            session_id = source.entity.get("session_id", "")
            topic = source.entity.get("topic", "")
            renew_ids.append(source.id)
        else:
            text = documents[idx]
            session_id = ""
            topic = ""
        results.append({
            "chunk_text": text,
            "score": r["relevance_score"],
            "session_id": session_id,
            "topic": topic,
        })

    # Renew TTL on hit entries (fire-and-forget)
    if renew_ids:
        _renew_memory_ttl(coll, renew_ids, now)

    logger.info(f"[RAG:MEM] done: {len(results)} results for user={user_id}")
    return results


def _renew_memory_ttl(coll, doc_ids: list[int], now: int):
    """Update created_at to renew TTL on accessed memory entries."""
    try:
        for doc_id in doc_ids:
            coll.update(
                expr=f"id == {doc_id}",
                data=[{"created_at": now}],
            )
    except Exception as e:
        logger.warning(f"[RAG:MEM] TTL renewal failed: {e}")


async def ensure_memory_bm25(user_id: int):
    """Build or recover BM25 index for a user's session memory."""
    from src.vector_db.milvus_client import get_collection, SESSION_MEMORY_COLLECTION
    import time

    existing = get_memory_bm25(user_id)
    if existing is not None:
        return

    ttl_seconds = 7 * 24 * 3600
    min_created_at = int(time.time()) - ttl_seconds

    try:
        coll = get_collection(SESSION_MEMORY_COLLECTION)
        offset = 0
        batch = 500
        chunks = []
        meta = []
        while True:
            results = coll.query(
                expr=f'user_id == {user_id} and created_at > {min_created_at}',
                output_fields=["summary_text", "topic", "session_id"],
                limit=batch,
                offset=offset,
            )
            if not results:
                break
            for r in results:
                text = r.get("summary_text") or ""
                if text:
                    chunks.append(text)
                    meta.append({
                        "topic": r.get("topic") or "",
                        "session_id": r.get("session_id") or "",
                    })
            if len(results) < batch:
                break
            offset += batch

        if chunks:
            build_memory_bm25(user_id, chunks, meta)
            logger.info(f"[RAG:MEM] BM25 built for user={user_id}: {len(chunks)} summaries")
    except Exception as e:
        logger.warning(f"[RAG:MEM] BM25 build failed for user={user_id}: {e}")
