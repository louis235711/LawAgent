import re
from src.utils.token_counter import count_tokens


def chunk_markdown(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 96,
) -> list[str]:
    """Recursively chunk text: headings → paragraphs → sentences → force-split."""
    if not text.strip():
        return []

    if count_tokens(text) <= max_tokens:
        return [text.strip()]

    # Level 1: split by Markdown headings
    sections = _split_by_headings(text)
    if len(sections) > 1:
        return _chunk_sections(sections, max_tokens, overlap_tokens)

    # Level 2: split by paragraph breaks (double newlines)
    paragraphs = _split_by_paragraphs(text)
    if len(paragraphs) > 1:
        return _chunk_items(paragraphs, max_tokens, overlap_tokens)

    # Level 3: split by sentence-ending punctuation
    return _chunk_by_sentences(text, max_tokens, overlap_tokens)


def chunk_by_chapter(law_text: str, max_tokens: int = 1024) -> list[dict]:
    """Chunk legal text by chapter, returning [{title, content}]."""
    chunks = []
    sections = _split_by_headings(law_text)

    for heading, body in sections:
        section_text = f"{heading}\n{body}" if heading else body
        tokens = count_tokens(section_text)

        if tokens <= max_tokens:
            chunks.append({"title": heading or "", "content": section_text.strip()})
        else:
            sub_chunks = chunk_markdown(section_text, max_tokens)
            for sub in sub_chunks:
                chunks.append({"title": heading or "", "content": sub})

    return chunks


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    sections = []
    current_heading = ""
    current_lines = []

    for line in lines:
        if re.match(r"^#{1,6}\s", line):
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, "\n".join(current_lines).strip()))

    if not sections:
        sections.append(("", text))

    return sections


def _split_by_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _split_by_sentences(text: str) -> list[str]:
    """Split by Chinese/English sentence-ending punctuation."""
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    return [s.strip() for s in parts if s.strip()]


def _chunk_sections(
    sections: list[tuple[str, str]],
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    chunks = []
    current = ""

    for heading, body in sections:
        section_text = f"{heading}\n{body}" if heading else body

        # If combining would overflow, start a new chunk
        if current and count_tokens(current + "\n" + section_text) > max_tokens:
            chunks.append(current.strip())
            current = section_text
        else:
            current = current + "\n" + section_text if current else section_text

        # If the section itself is oversized, recurse
        if count_tokens(current) > max_tokens:
            sub_chunks = chunk_markdown(current, max_tokens, overlap_tokens)
            chunks.extend(sub_chunks)
            current = ""

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _chunk_items(
    items: list[str],
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    chunks = []
    current = ""

    for item in items:
        if current and count_tokens(current + "\n\n" + item) > max_tokens:
            chunks.append(current.strip())
            current = item
        else:
            current = current + "\n\n" + item if current else item

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _chunk_by_sentences(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split by sentences. Falls back to force-split if no sentence boundaries found."""
    sentences = _split_by_sentences(text)

    # No sentence boundaries: force-split by token count
    if len(sentences) <= 1:
        return _force_split(text.strip(), max_tokens, overlap_tokens)

    chunks = []
    current = ""
    current_tokens = 0

    for sent in sentences:
        sent_tokens = count_tokens(sent)

        # Single sentence larger than max_tokens: force-split it
        if sent_tokens > max_tokens:
            if current.strip():
                chunks.append(current.strip())
                current = ""
                current_tokens = 0
            sub_chunks = _force_split(sent, max_tokens, overlap_tokens)
            chunks.extend(sub_chunks)
            continue

        if current_tokens + sent_tokens > max_tokens and current:
            chunks.append(current.strip())
            overlap_text = _extract_tail_tokens(current, overlap_tokens)
            current = overlap_text
            current_tokens = count_tokens(current)

        current = current + sent if current else sent
        current_tokens = count_tokens(current)

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _force_split(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Last resort: split by approximate token boundaries using fixed-size windows.

    Attempts to find natural break points (punctuation, spaces, newlines)
    near the token boundary. If none exist, falls back to hard cut.
    """
    if count_tokens(text) <= max_tokens:
        return [text]

    total_tokens = count_tokens(text)
    chunks = []
    chars = list(text)
    pos = 0
    effective_max = max(1, max_tokens - overlap_tokens)

    while pos < len(chars):
        # Estimate safe end position (chars ≈ tokens for most CJK text)
        end = min(pos + effective_max * 2, len(chars))

        if end >= len(chars):
            chunks.append(text[pos:].strip())
            break

        # Search backward from end for a natural break point
        window = text[pos:end]
        break_pos = None

        for pattern in [
            r"[。！？.!?\n](?!\w)",  # sentence end + not abbreviation
            r"[，,;；\n]",
            r"\s{2,}",
            r"\s",
        ]:
            matches = list(re.finditer(pattern, window))
            if matches:
                # Pick the match closest to effective_max tokens
                target = effective_max * 2
                best = min(matches, key=lambda m: abs(m.end() - target))
                break_pos = best.end()
                break

        if break_pos is None or break_pos == 0:
            break_pos = effective_max * 2

        chunk = text[pos:pos + break_pos].strip()
        if chunk:
            chunks.append(chunk)
        pos = pos + break_pos

    return chunks


def _extract_tail_tokens(text: str, max_tokens: int) -> str:
    sentences = _split_by_sentences(text)
    result = ""
    for s in reversed(sentences):
        candidate = s + result
        if count_tokens(candidate) > max_tokens:
            break
        result = candidate
    return result
