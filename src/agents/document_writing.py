import os
import re
from collections.abc import AsyncGenerator
from typing import Union

from docx import Document as DocxDocument
from src.agents.base import BaseAgent, AgentResponse
from src.tools.template_manager import list_templates, load_template
from src.llm.client import chat_completion, chat_completion_stream
from src.config import settings
from loguru import logger

WRITING_PROMPT = """你是一个法律文书撰写专家。用户需要生成一份{template_name}。

## 文书模板（用 { } 标记的是需要填充的字段）
{template_content}

## 当前信息
{existing_info}

## 用户输入
{user_input}

请执行以下任务：
1. 从用户输入中提取模板需要的所有字段信息
2. 如果某些字段用户未提供，使用合理的默认值填充，并用【待补充：字段名】标记
3. 输出完整的填充后文书正文（不含模板标记 {}）
4. 在文书末尾列出需要用户确认或补充的字段清单
5. 文书格式和条款需基于真实法律文书规范，不得编造不存在的法律条款

## 完整文书
"""

EXPORT_PROMPT = """请对以下法律文书进行法言法语润色和格式规范化，保持原有内容不变，仅优化表述。

## 当前文书
{document}

## 润色后文书
"""


class DocumentWritingAgent(BaseAgent):
    def __init__(self):
        super().__init__("document_writing")

    async def execute(
        self,
        session_id: str,
        user_input: str,
        context: list[dict],
    ) -> AgentResponse:
        # 1. Match template
        template = self._match_template(user_input)
        if not template:
            return AgentResponse(
                content="请明确您需要的文书类型。支持的类型包括：借款合同、劳动合同、民事起诉状等。",
                metadata={"message_type": "文书", "step": "select_template"},
            )

        template_id = template["id"]
        template_name = template["name"]
        template_content = load_template(template_id)

        # 2. Extract existing info from conversation context
        existing_info = ""
        for msg in context:
            if msg.get("role") == "user":
                existing_info += msg.get("content", "") + "\n"

        # 3. Generate document (safe replacement to avoid {} format conflicts)
        prompt = (WRITING_PROMPT
            .replace("{template_name}", template_name)
            .replace("{template_content}", template_content)
            .replace("{existing_info}", existing_info)
            .replace("{user_input}", user_input)
        )
        messages = context + [{"role": "user", "content": prompt}]

        draft = await chat_completion(messages=messages, temperature=0.5, max_tokens=4096)

        # 4. Polish
        polish_prompt = EXPORT_PROMPT.format(document=draft)
        polished = await chat_completion(
            messages=[{"role": "user", "content": polish_prompt}],
            temperature=0.3,
            max_tokens=4096,
        )

        return AgentResponse(
            content=polished,
            metadata={
                "message_type": "文书",
                "template": template_id,
                "template_name": template_name,
            },
        )

    async def stream_execute(
        self, session_id: str, user_input: str, context: list[dict],
    ) -> AsyncGenerator[Union[str, AgentResponse], None]:
        template = self._match_template(user_input)
        if not template:
            yield AgentResponse(
                content="请明确您需要的文书类型。支持的类型包括：借款合同、劳动合同、民事起诉状等。",
                metadata={"message_type": "文书", "step": "select_template"},
            )
            return

        template_id = template["id"]
        template_name = template["name"]
        template_content = load_template(template_id)

        existing_info = ""
        for msg in context:
            if msg.get("role") == "user":
                existing_info += msg.get("content", "") + "\n"

        prompt = (WRITING_PROMPT
            .replace("{template_name}", template_name)
            .replace("{template_content}", template_content)
            .replace("{existing_info}", existing_info)
            .replace("{user_input}", user_input)
        )
        messages = context + [{"role": "user", "content": prompt}]

        # Stream draft generation
        full_draft = []
        async for chunk in chat_completion_stream(messages=messages, temperature=0.5, max_tokens=4096):
            full_draft.append(chunk)
            yield chunk
        draft = "".join(full_draft)

        # Polish (non-streaming, quick)
        polish_prompt = EXPORT_PROMPT.format(document=draft)
        polished = await chat_completion(
            messages=[{"role": "user", "content": polish_prompt}],
            temperature=0.3, max_tokens=4096,
        )

        yield AgentResponse(
            content=polished,
            metadata={
                "message_type": "文书",
                "template": template_id,
                "template_name": template_name,
            },
        )

    def _match_template(self, user_input: str) -> dict | None:
        templates = list_templates()
        if not templates:
            return None

        keyword_map = {
            "loan_contract.md": ["借款", "贷款", "借条", "借钱"],
            "labor_contract.md": ["劳动", "用工", "员工", "公司合同"],
            "complaint.md": ["起诉", "诉讼", "告", "打官司"],
        }

        scores = {}
        for tpl in templates:
            tid = tpl["id"]
            kw = keyword_map.get(tid, [])
            score = sum(1 for k in kw if k in user_input)
            scores[tid] = score

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            for t in templates:
                if t["id"] == best:
                    return t

        # No keywords matched: return first template as default
        return templates[0]


def export_to_markdown(content: str, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)


def export_to_docx(content: str, output_path: str):
    doc = DocxDocument()
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped:
            doc.add_paragraph(stripped)
    doc.save(output_path)
