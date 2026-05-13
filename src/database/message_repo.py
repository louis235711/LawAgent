import json
from src.database.postgres import get_conn, put_conn


def save_message(
    session_id: str,
    role: str,
    content: str,
    token_count: int,
    message_type: str = "咨询",
    references: list[dict] | None = None,
    metadata: dict | None = None,
):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_messages
                   (session_id, message_role, message_content, token_count, message_type, "references", metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    session_id,
                    role,
                    content,
                    token_count,
                    message_type,
                    json.dumps(references or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        put_conn(conn)


def get_messages(session_id: str, limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT message_role, message_content, token_count, create_time, message_type, "references", metadata
                   FROM conversation_messages
                   WHERE session_id = %s
                   ORDER BY create_time DESC
                   LIMIT %s""",
                (session_id, limit),
            )
            rows = cur.fetchall()
            messages = []
            for row in reversed(rows):
                refs = row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]")
                meta = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
                messages.append({
                    "role": row[0],
                    "content": row[1],
                    "token_count": row[2],
                    "create_time": row[3].isoformat(),
                    "message_type": row[4],
                    "references": refs,
                    "metadata": meta,
                })
            return messages
    finally:
        put_conn(conn)


def get_recent_messages(session_id: str, limit: int = 20) -> list[dict]:
    """Get recent messages for Redis recovery."""
    return get_messages(session_id, limit)
