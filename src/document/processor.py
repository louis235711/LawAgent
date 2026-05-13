import os
import json
from src.config import settings
from src.database.redis import update_session
from src.document.parser import parse_document
from src.utils.text_chunker import chunk_markdown
from src.utils.token_counter import count_tokens
from src.vector_db.milvus_client import SESSION_DOCUMENTS_COLLECTION
from src.rag.pipeline import insert_chunks
from src.rag.bm25 import build_session_bm25
from loguru import logger

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".md", ".txt"}


async def process_upload(
    session_id: str,
    file_content: bytes,
    original_filename: str,
    file_size: int = 0,
) -> dict:
    """Full pipeline: save → parse → chunk → (Milvus + BM25) or direct context."""

    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    # 1. Save original file
    upload_dir = os.path.join(settings.uploads_dir, session_id)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, original_filename)
    with open(file_path, "wb") as f:
        f.write(file_content)
    logger.info(f"[PROCESSOR] saved: {file_path} ({file_size} bytes)")

    # 2. Parse to text
    text = await parse_document(file_path, original_filename)
    txt_name = f"{os.path.splitext(original_filename)[0]}.txt"
    txt_path = os.path.join(upload_dir, txt_name)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"[PROCESSOR] parsed text: {len(text)} chars → {txt_path}")

    # 3. Chunk (512 tokens, 96 overlap)
    chunks = chunk_markdown(text, max_tokens=512, overlap_tokens=96)
    logger.info(f"[PROCESSOR] chunked: {len(chunks)} pieces")

    # 4. Save chunks to JSON
    json_path = os.path.join(upload_dir, f"{os.path.splitext(original_filename)[0]}_chunks.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    logger.info(f"[PROCESSOR] chunks saved: {json_path}")

    total_tokens = count_tokens(text)
    use_rag = len(chunks) >= 10

    if use_rag:
        # 5a. Build session BM25 index
        build_session_bm25(session_id, chunks)
        logger.info(f"[PROCESSOR] session BM25 built: {len(chunks)} docs")

        # 5b. Insert to Milvus
        metadata = [
            {"session_id": session_id, "document_name": original_filename, "chunk_index": i}
            for i in range(len(chunks))
        ]
        await insert_chunks(chunks, SESSION_DOCUMENTS_COLLECTION, metadata)
        logger.info(f"[PROCESSOR] {len(chunks)} chunks → Milvus")

    # 6. Update Redis session state
    await update_session(
        session_id,
        has_document=True,
        document_name=original_filename,
        file_size=file_size,
        chunk_count=len(chunks),
        use_rag=use_rag,
        state="has_document",
    )

    return {
        "session_id": session_id,
        "filename": original_filename,
        "chunks": len(chunks),
        "total_tokens": total_tokens,
        "file_size": file_size,
        "use_rag": use_rag,
    }
