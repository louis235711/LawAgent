"""PDF parser: tries MinerU first, falls back to pdfminer, then raw text."""

import os
import subprocess
from loguru import logger


async def parse_pdf(pdf_path: str) -> str:
    """Parse PDF to Markdown. Tries MinerU → pdfminer.six → raw text."""

    # Strategy 1: MinerU (magic-pdf)
    try:
        result = await _mineru_parse(pdf_path)
        if result and result.strip():
            return result
    except Exception as e:
        logger.info(f"MinerU not available: {e}")

    # Strategy 2: pdfminer.six
    try:
        return _pdfminer_parse(pdf_path)
    except Exception as e:
        logger.info(f"pdfminer not available: {e}")

    # Strategy 3: raw read
    try:
        return _raw_read(pdf_path)
    except Exception:
        raise RuntimeError(f"Unable to parse PDF: {pdf_path}")


async def _mineru_parse(pdf_path: str) -> str:
    output_dir = os.path.dirname(pdf_path)
    cmd = ["magic-pdf", "-p", pdf_path, "-o", output_dir]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"MinerU failed")
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    md_path = os.path.join(output_dir, base, f"{base}.md")
    if os.path.exists(md_path):
        with open(md_path, encoding="utf-8") as f:
            return f.read()
    raise FileNotFoundError(f"MinerU output not found: {md_path}")


def _pdfminer_parse(pdf_path: str) -> str:
    from pdfminer.high_level import extract_text
    text = extract_text(pdf_path)
    if not text.strip():
        raise RuntimeError("pdfminer extracted empty text")
    lines = text.split("\n")
    return "\n".join(line.strip() for line in lines if line.strip())


def _raw_read(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="ignore")
    if not text.strip():
        raise RuntimeError("Raw read produced empty text")
    lines = text.split("\n")
    return "\n".join(line for line in lines if line.strip())


async def _run_subprocess(cmd: list[str]):
    return await __import__("asyncio").create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
