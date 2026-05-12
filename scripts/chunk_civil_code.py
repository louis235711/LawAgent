"""民法典切片：按编/分编/章/节层级切分，max 1024 tokens，不切割单条。"""
import re
import json
import sys
sys.path.insert(0, ".")

from src.utils.token_counter import count_tokens

MAX_TOKENS = 1024


def parse_civil_code(filepath: str):
    """解析民法典md，返回 [{law_name, chapter, article_number, content}]"""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 跳过目录，从第一个 H1（第X编）开始
    start = 0
    for i, l in enumerate(lines):
        if re.match(r"^# 第[一二三四五六七八九十百千]+编\b", l):
            start = i
            break

    law_name = "中华人民共和国民法典"
    records = []

    # 当前层级上下文
    current_part = ""       # H1: 编
    current_subpart = ""    # H2: 分编
    current_chapter = ""    # H3: 章
    current_section = ""    # H4: 节
    current_articles = []   # [(article_num, text), ...]

    def build_chapter_path():
        parts = [
            p for p in [
                current_part,
                current_subpart,
                current_chapter,
                current_section,
            ] if p
        ]
        return " > ".join(parts)

    def flush_articles():
        nonlocal current_articles
        if not current_articles:
            return

        chapter_path = build_chapter_path()
        article_nums = [a[0] for a in current_articles]
        article_label = (
            article_nums[0]
            if len(article_nums) == 1
            else f"{article_nums[0]}-{article_nums[-1]}"
        )

        # 按条逐条填充，超过 MAX_TOKENS 就切分（单独一条超限则不切）
        chunk = []
        chunk_nums = []
        chunk_tokens = 0

        for anum, atext in current_articles:
            article_block = f"**{anum}** {atext}"
            at = count_tokens(article_block)

            # 如果这条本身就超限，先 flush 已有 chunk 再单独成块
            if at > MAX_TOKENS:
                if chunk:
                    records.append({
                        "law_name": law_name,
                        "chapter": chapter_path,
                        "article_number": chunk_nums[0] if len(chunk_nums) == 1 else f"{chunk_nums[0]}-{chunk_nums[-1]}",
                        "content": "\n\n".join(chunk),
                    })
                    chunk = []
                    chunk_nums = []
                    chunk_tokens = 0
                records.append({
                    "law_name": law_name,
                    "chapter": chapter_path,
                    "article_number": anum,
                    "content": article_block,
                })
                continue

            if chunk and chunk_tokens + at > MAX_TOKENS:
                records.append({
                    "law_name": law_name,
                    "chapter": chapter_path,
                    "article_number": chunk_nums[0] if len(chunk_nums) == 1 else f"{chunk_nums[0]}-{chunk_nums[-1]}",
                    "content": "\n\n".join(chunk),
                })
                chunk = []
                chunk_nums = []
                chunk_tokens = 0

            chunk.append(article_block)
            chunk_nums.append(anum)
            chunk_tokens += at

        if chunk:
            records.append({
                "law_name": law_name,
                "chapter": chapter_path,
                "article_number": chunk_nums[0] if len(chunk_nums) == 1 else f"{chunk_nums[0]}-{chunk_nums[-1]}",
                "content": "\n\n".join(chunk),
            })

        current_articles.clear()

    for i in range(start, len(lines)):
        line = lines[i].strip()

        # 跳过空行和纯数字页码
        if not line:
            continue

        # H1: 第X编
        if re.match(r"^# 第[一二三四五六七八九十百千]+编\b", line):
            flush_articles()
            current_part = line.lstrip("# ").strip()
            current_subpart = ""
            current_chapter = ""
            current_section = ""

        # H2: 第X分编
        elif re.match(r"^## 第[一二三四五六七八九十百千]+分编\b", line):
            flush_articles()
            current_subpart = line.lstrip("# ").strip()
            current_chapter = ""
            current_section = ""

        # H3: 第X章
        elif re.match(r"^### 第[一二三四五六七八九十百千]+章\b", line):
            flush_articles()
            current_chapter = line.lstrip("# ").strip()
            current_section = ""

        # H4: 第X节
        elif re.match(r"^#### 第[一二三四五六七八九十百千]+节\b", line):
            flush_articles()
            current_section = line.lstrip("# ").strip()

        # 附则
        elif re.match(r"^# 附则$", line):
            flush_articles()
            current_part = "附则"
            current_subpart = ""
            current_chapter = ""
            current_section = ""

        # 文章：**第X条**
        elif re.match(r"^\*\*第[一二三四五六七八九十百千]+条\*\*", line):
            m = re.match(r"^\*\*(第[一二三四五六七八九十百千]+条)\*\*(.*)", line)
            if m:
                article_num = m.group(1)
                article_text = m.group(2).strip()
                current_articles.append((article_num, article_text))

        # 文章续行（多段落的条：列表项、续文等）
        elif current_articles and not line.startswith("#"):
            prev_num, prev_text = current_articles[-1]
            # 如果前一行以换行结尾则保持段落分隔
            delimiter = "\n" if prev_text.endswith("\n") else " "
            current_articles[-1] = (prev_num, prev_text + delimiter + line)

    flush_articles()
    return records


def main():
    filepath = "data/laws/中华人民共和国民法典.md"
    print(f"Parsing {filepath}...")
    records = parse_civil_code(filepath)
    print(f"Total chunks: {len(records)}")

    tokens_list = [count_tokens(r["content"]) for r in records]
    total_tokens = sum(tokens_list)
    max_tok = max(tokens_list)
    over_limit = sum(1 for t in tokens_list if t > MAX_TOKENS)

    print(f"Total tokens: {total_tokens}")
    print(f"Max chunk tokens: {max_tok}")
    print(f"Chunks > {MAX_TOKENS} (single long article): {over_limit}")

    out_path = "data/laws/民法典_切片.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out_path}")

    # 章节分布
    parts = {}
    for r in records:
        p = r["chapter"].split(" > ")[0]
        parts[p] = parts.get(p, 0) + 1
    print("\n=== 各编切片数 ===")
    for p, n in parts.items():
        print(f"  {p}: {n}")

    # 样本
    print("\n=== 前3条 ===")
    for r in records[:3]:
        tok = count_tokens(r["content"])
        print(f"  chapter: {r['chapter']}")
        print(f"  article: {r['article_number']} | tokens: {tok}")
        print(f"  content: {r['content'][:150]}...")
        print()


if __name__ == "__main__":
    main()
