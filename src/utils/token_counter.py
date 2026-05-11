import tiktoken

# DeepSeek models are compatible with cl100k_base encoding
_ENCODING_NAME = "cl100k_base"
_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding(_ENCODING_NAME)
    return _encoder


def count_tokens(text: str) -> int:
    encoder = get_encoder()
    return len(encoder.encode(text))


def count_message_tokens(messages: list[dict]) -> int:
    """Count tokens for a list of chat messages.
    Follows OpenAI's token counting formula for chat models:
    each message: 4 tokens base + content tokens
    """
    total = 0
    encoder = get_encoder()
    for msg in messages:
        total += 4  # message framing tokens
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(encoder.encode(content))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += len(encoder.encode(part["text"]))
    total += 2  # assistant priming
    return total
