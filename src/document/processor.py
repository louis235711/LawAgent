import os
import uuid
import shutil
from src.config import settings
from src.database.redis import update_session
from src.document.parser import parse_pdf
from src.utils.text_chunker import chunk_markdown
from src.utils.token_counter import count_tokens
from src.vector_db.milvus_client import SESSION_DOCUMENTS_COLLECTION
from src.rag.pipeline import insert_chunks
from loguru import logger


async def process_upload(
    session_id: str,
    pdf_content: bytes,
    original_filename: str,
) -> dict:
    """Full pipeline: save PDF → parse → chunk → vectorize → update session."""

    # 1. Save original PDF
    upload_dir = os.path.join(settings.uploads_dir, session_id)
    os.makedirs(upload_dir, exist_ok=True)
    pdf_path = os.path.join(upload_dir, original_filename)
    with open(pdf_path, "wb") as f:
        f.write(pdf_content)
    logger.info(f"Saved PDF: {pdf_path}")

    # 2. Parse to Markdown
    markdown_text = await parse_pdf(pdf_path)
    md_path = os.path.join(upload_dir, f"{os.path.splitext(original_filename)[0]}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)
    logger.info(f"Parsed to Markdown: {md_path} ({len(markdown_text)} chars)")

    # 3. Chunk
    chunks = chunk_markdown(markdown_text)
    logger.info(f"Chunked into {len(chunks)} pieces")

    # 4. Vectorize and store in Milvus
    metadata = [
        {
            "session_id": session_id,
            "document_name": original_filename,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]
    await insert_chunks(chunks, SESSION_DOCUMENTS_COLLECTION, metadata)
    logger.info(f"Inserted {len(chunks)} chunks into Milvus")

    # 5. Update Redis session state
    await update_session(
        session_id,
        has_document=True,
        document_name=original_filename,
        state="has_document",
    )

    return {
        "session_id": session_id,
        "filename": original_filename,
        "chunks": len(chunks),
        "total_tokens": count_tokens(markdown_text),
    }
