import json
from src.database.postgres import get_conn, put_conn


def save_session_memory(
    session_id: str,
    short_term_memory: list[dict],
    summary_list: list[str],
    window_token_count: int,
    state: str = "idle",
    has_document: bool = False,
    document_name: str | None = None,
):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO session_memory
                   (session_id, short_term_memory, summary_list, window_token_count,
                    state, has_document, document_name, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (session_id) DO UPDATE SET
                    short_term_memory = EXCLUDED.short_term_memory,
                    summary_list = EXCLUDED.summary_list,
                    window_token_count = EXCLUDED.window_token_count,
                    state = EXCLUDED.state,
                    has_document = EXCLUDED.has_document,
                    document_name = EXCLUDED.document_name,
                    updated_at = NOW()""",
                (
                    session_id,
                    json.dumps(short_term_memory, ensure_ascii=False),
                    json.dumps(summary_list, ensure_ascii=False),
                    window_token_count,
                    state,
                    has_document,
                    document_name,
                ),
            )
        conn.commit()
    finally:
        put_conn(conn)


def get_session_memory(session_id: str) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT short_term_memory, summary_list, window_token_count,
                          state, has_document, document_name
                   FROM session_memory
                   WHERE session_id = %s""",
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "short_term_memory": row[0] if isinstance(row[0], list) else json.loads(row[0]),
                "summary_list": row[1] if isinstance(row[1], list) else json.loads(row[1]),
                "window_token_count": row[2],
                "state": row[3],
                "has_document": row[4],
                "document_name": row[5],
            }
    finally:
        put_conn(conn)


def delete_session_memory(session_id: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM session_memory WHERE session_id = %s",
                (session_id,),
            )
        conn.commit()
    finally:
        put_conn(conn)
