"""Import legal texts into Milvus knowledge base.

Usage:
    python scripts/import_laws.py data/laws/civil_code_contract.md
    python scripts/import_laws.py data/laws/   # import all .md files in directory

Legal source files are PDFs placed in data/laws/, which should first be
converted to Markdown via MinerU before running this script.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.text_chunker import chunk_by_chapter
from src.utils.token_counter import count_tokens
from src.vector_db.milvus_client import init_collections, get_legal_collection
from src.llm.embedding import embed_texts
from loguru import logger


async def import_markdown(filepath: str):
    """Chunk a Markdown legal file and insert into Milvus."""
    filename = os.path.basename(filepath)
    law_name = os.path.splitext(filename)[0]

    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    chapters = chunk_by_chapter(text)
    logger.info(f"{law_name}: {len(chapters)} chapters from {filepath}")

    coll = get_legal_collection()
    total_chunks = 0
    entities = []

    for chapter in chapters:
        vectors = await embed_texts([chapter["content"]])
        entities.append({
            "chunk_text": chapter["content"],
            "law_name": law_name,
            "chapter": chapter["title"],
            "article_number": "",
            "vector": vectors[0],
        })
        total_chunks += 1

    if entities:
        coll.insert(entities)
        coll.flush()

    return {
        "law_name": law_name,
        "file": filepath,
        "chapters": len(chapters),
        "chunks": total_chunks,
        "total_tokens": count_tokens(text),
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


async def main():
    parser = argparse.ArgumentParser(description="Import legal texts into Milvus")
    parser.add_argument("path", help="Markdown file or directory of .md files")
    args = parser.parse_args()

    init_collections()

    if os.path.isfile(args.path):
        files = [args.path]
    elif os.path.isdir(args.path):
        files = sorted(
            os.path.join(args.path, f)
            for f in os.listdir(args.path)
            if f.endswith(".md")
        )
    else:
        logger.error(f"Path not found: {args.path}")
        return

    if not files:
        logger.warning("No .md files found")
        return

    results = []
    for fp in files:
        result = await import_markdown(fp)
        results.append(result)
        logger.info(f"Imported {result['law_name']}: {result['chunks']} chunks, "
                    f"{result['total_tokens']} tokens")

    total_chunks = sum(r["chunks"] for r in results)
    logger.info(f"Done. {len(results)} files, {total_chunks} total chunks imported.")


if __name__ == "__main__":
    asyncio.run(main())
