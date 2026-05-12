import psycopg2
import psycopg2.pool
from src.config import settings

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=settings.postgres_dsn,
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def put_conn(conn):
    get_pool().putconn(conn)


def init_db():
    """Run migrations on startup."""
    conn = get_conn()
    try:
        for migration in ("migrations/001_init.sql", "migrations/002_session_memory.sql", "migrations/003_add_references.sql"):
            with conn.cursor() as cur:
                cur.execute(open(migration, encoding="utf-8").read())
        conn.commit()
    finally:
        put_conn(conn)
