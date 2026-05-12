import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["FLAGS_use_mkldnn"] = "0"

from paddleocr import PaddleOCR

print("Preloading PaddleOCR models...")
engine = PaddleOCR(
    use_doc_orientation_classify=True,
    use_doc_unwarping=False,
    use_textline_orientation=True,
    lang="ch",
)
# Trigger a dummy prediction to ensure all models are loaded
result = engine.ocr(__file__)
print("Models loaded successfully.")
