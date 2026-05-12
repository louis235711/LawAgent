from fastapi import FastAPI, UploadFile, File, HTTPException
import uvicorn
import tempfile
import os
import fitz
import traceback

app = FastAPI(title="OCR Service")

from rapidocr_onnxruntime import RapidOCR

engine = RapidOCR()


def _result_to_markdown(result: list) -> str:
    lines = []
    if not result:
        return ""
    for item in result:
        text = item[1] if len(item) > 1 else ""
        if text and text.strip():
            lines.append(text.strip())
    return "\n".join(lines)



@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    suffix = os.path.splitext(file.filename)[1].lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            doc = fitz.open(tmp_path)
            all_md = []
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                pix = page.get_pixmap(dpi=200)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as img_tmp:
                    img_tmp.write(pix.tobytes("png"))
                    img_tmp.close()
                    result, _ = engine(img_tmp.name)
                    all_md.append(_result_to_markdown(result))
                    os.unlink(img_tmp.name)
            doc.close()
            return {"markdown": "\n\n---\n\n".join(all_md), "pages": len(all_md)}
        else:
            result, _ = engine(tmp_path)
            return {"markdown": _result_to_markdown(result)}
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="OCR processing failed")
    finally:
        os.unlink(tmp_path)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
