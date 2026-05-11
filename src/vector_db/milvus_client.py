from pymilvus import (
    connections,
    Collection,
    FieldSchema,
    CollectionSchema,
    DataType,
    IndexType,
    utility,
)
from src.config import settings

_connected = False
EMBEDDING_DIM = 1024  # text-embedding-v4

LEGAL_KNOWLEDGE_COLLECTION = "legal_knowledge"
SESSION_DOCUMENTS_COLLECTION = "session_documents"

_INDEX_PARAMS = {
    "metric_type": "IP",
    "index_type": "IVF_FLAT",
    "params": {"nlist": 128},
}


def connect():
    global _connected
    if not _connected:
        connections.connect(
            alias="default",
            host=settings.milvus_host,
            port=settings.milvus_port,
        )
        _connected = True


def _create_index(coll: Collection):
    if not coll.has_index():
        coll.create_index(field_name="vector", index_params=_INDEX_PARAMS)


def _create_legal_knowledge():
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="law_name", dtype=DataType.VARCHAR, max_length=256, default_value=""),
        FieldSchema(name="chapter", dtype=DataType.VARCHAR, max_length=512, default_value=""),
        FieldSchema(name="article_number", dtype=DataType.VARCHAR, max_length=128, default_value=""),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields, description="法律知识库")
    coll = Collection(name=LEGAL_KNOWLEDGE_COLLECTION, schema=schema)
    _create_index(coll)


def _create_session_documents():
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="session_id", dtype=DataType.VARCHAR, max_length=64, default_value=""),
        FieldSchema(name="document_name", dtype=DataType.VARCHAR, max_length=256, default_value=""),
        FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="chunk_index", dtype=DataType.INT64, default_value=0),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields, description="用户文档向量")
    coll = Collection(name=SESSION_DOCUMENTS_COLLECTION, schema=schema)
    _create_index(coll)


def init_collections():
    connect()
    if not utility.has_collection(LEGAL_KNOWLEDGE_COLLECTION):
        _create_legal_knowledge()
    if not utility.has_collection(SESSION_DOCUMENTS_COLLECTION):
        _create_session_documents()


def get_collection(name: str) -> Collection:
    connect()
    coll = Collection(name)
    coll.load()
    return coll


def get_legal_collection() -> Collection:
    return get_collection(LEGAL_KNOWLEDGE_COLLECTION)


def get_session_docs_collection() -> Collection:
    return get_collection(SESSION_DOCUMENTS_COLLECTION)
