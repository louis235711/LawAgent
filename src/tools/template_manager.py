import os
from src.config import settings


def list_templates() -> list[dict]:
    """List available templates with display names."""
    template_map = {
        "loan_contract.md": "借款合同",
        "labor_contract.md": "劳动合同",
        "complaint.md": "民事起诉状",
    }
    templates = []
    for fname in os.listdir(settings.templates_dir):
        if fname.endswith(".md"):
            templates.append({
                "id": fname,
                "name": template_map.get(fname, fname.replace(".md", "")),
            })
    return templates


def load_template(template_id: str) -> str:
    """Load template content by filename."""
    path = os.path.join(settings.templates_dir, template_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Template not found: {template_id}")
    with open(path, encoding="utf-8") as f:
        return f.read()
