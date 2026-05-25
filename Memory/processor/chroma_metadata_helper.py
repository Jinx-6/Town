"""
ChromaDB / SQLite 元数据序列化工具。
处理 ChromaDB 不支持的类型（list/dict/None）和 SQLite json_extract 的 bool 比较问题。
"""

_ALLOWED_CHROMA_TYPES = (str, int, float, bool)


def sanitize_for_chroma(metadata: dict) -> dict:
    """过滤掉 ChromaDB where 子句不支持的 list/dict/None 类型。"""
    return {k: v for k, v in metadata.items() if isinstance(v, _ALLOWED_CHROMA_TYPES)}


def normalize_for_sqlite(metadata: dict) -> dict:
    """bool → 0/1，确保 json_extract(..., '$.is_factual_memory') = 1 比较一致。"""
    result = {}
    for k, v in metadata.items():
        if isinstance(v, bool):
            result[k] = 1 if v else 0
        else:
            result[k] = v
    return result
