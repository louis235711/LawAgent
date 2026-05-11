from src.database.postgres import get_conn, put_conn


def save_message(
    session_id: str,
    role: str,
    content: str,
    token_count: int,
    message_type: str = "咨询",
):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_messages
                   (session_id, message_role, message_content, token_count, message_type)
                   VALUES (%s, %s, %s, %s, %s)""",
                (session_id, role, content, token_count, message_type),
            )
        conn.commit()
    finally:
        put_conn(conn)


def get_messages(session_id: str, limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT message_role, message_content, token_count, create_time, message_type
                   FROM conversation_messages
                   WHERE session_id = %s
                   ORDER BY create_time DESC
                   LIMIT %s""",
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "role": row[0],
                    "content": row[1],
                    "token_count": row[2],
                    "create_time": row[3].isoformat(),
                    "message_type": row[4],
                }
                for row in reversed(rows)
            ]
    finally:
        put_conn(conn)


def get_recent_messages(session_id: str, limit: int = 20) -> list[dict]:
    """Get recent messages for Redis recovery."""
    return get_messages(session_id, limit)
