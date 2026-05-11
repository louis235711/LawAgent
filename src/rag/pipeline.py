from src.llm.embedding import embed_texts
from src.llm.rerank import rerank
from src.vector_db.milvus_client import (
    get_collection,
    LEGAL_KNOWLEDGE_COLLECTION,
    SESSION_DOCUMENTS_COLLECTION,
)


async def retrieve(
    query: str,
    collection_name: str,
    top_k: int = 10,
) -> list[dict]:
    """Retrieve relevant chunks from Milvus: embed → search → rerank."""
    query_vec = (await embed_texts([query]))[0]

    coll = get_collection(collection_name)
    search_params = {"metric_type": "IP", "params": {"nprobe": 16}}
    results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=top_k * 3,  # over-retrieve for rerank
        output_fields=["chunk_text"],
    )

    hits = results[0]
    if not hits:
        return []

    documents = [hit.entity.get("chunk_text") or "" for hit in hits]

    # Rerank
    reranked = await rerank(query, documents, top_k=top_k)

    return [
        {
            "chunk_text": documents[r["index"]],
            "score": r["relevance_score"],
        }
        for r in reranked
    ]


async def insert_chunks(
    chunks: list[str],
    collection_name: str,
    metadata: list[dict] | None = None,
):
    """Embed chunks and insert into Milvus."""
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


async def retrieve_legal(query: str, top_k: int = 10) -> list[dict]:
    return await retrieve(query, LEGAL_KNOWLEDGE_COLLECTION, top_k=top_k)


async def retrieve_session_docs(
    query: str,
    session_id: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    coll = get_collection(SESSION_DOCUMENTS_COLLECTION)

    query_vec = (await embed_texts([query]))[0]

    search_params = {"metric_type": "IP", "params": {"nprobe": 16}}
    expr = f'session_id == "{session_id}"' if session_id else None

    results = coll.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=top_k * 3,
        expr=expr,
        output_fields=["chunk_text", "document_name", "chunk_index"],
    )

    hits = results[0]
    if not hits:
        return []

    documents = [hit.entity.get("chunk_text") or "" for hit in hits]
    reranked = await rerank(query, documents, top_k=top_k)

    return [
        {
            "chunk_text": documents[r["index"]],
            "score": r["relevance_score"],
            "document_name": hits[r["index"]].entity.get("document_name"),
            "chunk_index": hits[r["index"]].entity.get("chunk_index"),
        }
        for r in reranked
    ]
