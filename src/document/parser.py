"""PDF parser: uses PaddleOCR Docker service for structure + OCR → Markdown."""

import httpx
from src.config import settings
from loguru import logger


async def parse_pdf(pdf_path: str) -> str:
    """Parse PDF to Markdown via PaddleOCR Docker service. Raises on error."""

    url = f"{settings.ocr_service_url}/ocr"
    logger.info(f"Parsing PDF via OCR service: {url}")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with open(pdf_path, "rb") as f:
                response = await client.post(
                    url,
                    files={"file": (pdf_path, f, "application/pdf")},
                )
    except httpx.ConnectError:
        raise RuntimeError(
            "PaddleOCR 服务不可用，请确保 OCR Docker 容器已启动。\n"
            f"启动命令: docker-compose up -d ocr"
        )
    except Exception as e:
        raise RuntimeError(f"调用 OCR 服务失败: {e}")

    if response.status_code != 200:
        raise RuntimeError(
            f"OCR 服务返回错误 (HTTP {response.status_code}): {response.text}"
        )

    data = response.json()
    markdown = data.get("markdown", "")
    if not markdown.strip():
        raise RuntimeError("OCR 服务返回了空内容，文档可能为空或无法识别")

    return markdown
