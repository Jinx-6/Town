# @Time    :2026/4/18 20:52
# @Author  :进喜
# @File    :vector_store.py
# @Software:PyCharm


"""
让NPC拥有联想能力的关键
    暂时使用ChromaDB
"""
import chromadb
import json
from typing import List, Optional, Dict, Any
from datetime import datetime

# ✨ 注意：这里导入的是你定义的 RetrievedMemory 和 BackendTarget
from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from ..schema.retrieval import RetrievedMemory
from ..schema.routing import BackendTarget
from ..processor.chroma_metadata_helper import sanitize_for_chroma, normalize_for_sqlite
import chromadb.utils.embedding_functions as embedding_functions

class VectorStore:
    """
    L2 级语义记忆 (Semantic Memory)
    已集成身份隔离与 Schema 标准化。
    """

    def __init__(self, db_path: str = "./chroma_db", collection_name: str = "cyber_town_memory"):
        self.client = chromadb.PersistentClient(path=db_path)

        # 配置中文向量模型 (BGE-Small)
        model_name_or_path = r"D:\DTcoding\models\BAAI\bge-small-zh-v1___5"
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name_or_path
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef
        )

    def add(self, item: MemoryItem):
        """向向量库添加记忆，扁平化 metadata 字段以支持 ChromaDB where 过滤。"""
        role_str = getattr(item.role, 'value', item.role)
        meta_dict = item.metadata if isinstance(item.metadata, dict) else item.metadata.model_dump()

        chroma_metadata = sanitize_for_chroma({
            "agent_id": item.agent_id,
            "role": role_str,
            "stage": MemoryStage.SEMANTIC.value,
            "timestamp": item.timestamp.isoformat(),
            "memory_type": meta_dict.get("memory_type", "unknown"),
            "event_type": meta_dict.get("event_type", ""),
            "is_factual_memory": meta_dict.get("is_factual_memory", True),
            "relative_time": meta_dict.get("relative_time", ""),
            "importance": meta_dict.get("importance", 0),
            "metadata_json": json.dumps(normalize_for_sqlite(meta_dict))
        })

        self.collection.add(
            documents=[item.content],
            metadatas=[chroma_metadata],
            ids=[item.id]
        )

    def search(self, query: str, agent_id: str, limit: int = 5,
               metadata_filters: Optional[Dict[str, Any]] = None) -> List[RetrievedMemory]:
        """
        语义检索，支持可选的 metadata 硬性过滤。
        """
        conditions = [{"agent_id": agent_id}]

        if metadata_filters:
            for key, value in metadata_filters.items():
                if key == "keywords":
                    continue
                if isinstance(value, (bool, str, int, float)):
                    conditions.append({key: value})

        # ChromaDB 单条件直接传 dict，多条件必须用 $and
        if len(conditions) == 1:
            where_clause = conditions[0]
        else:
            where_clause = {"$and": conditions}

        results = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where=where_clause
        )

        retrieval_results = []

        if not results['documents'] or not results['documents'][0]:
            return retrieval_results

        for i in range(len(results['ids'][0])):
            doc_id = results['ids'][0][i]
            content = results['documents'][0][i]
            meta = results['metadatas'][0][i]
            distance = results['distances'][0][i]

            # 还原 MemoryItem 对象
            item = MemoryItem(
                id=doc_id,
                content=content,
                agent_id=meta.get("agent_id", agent_id),  # 获取身份
                role=MemoryRole(meta["role"]),
                timestamp=datetime.fromisoformat(meta["timestamp"]),
                stage=MemoryStage(meta["stage"]),
                metadata=json.loads(meta["metadata_json"])
            )

            # ✨ 核心：使用你的 RetrievedMemory 类进行标准化封装
            retrieved_hit = RetrievedMemory(
                item=item,
                score=distance,  # Chroma 的 score 是欧氏距离
                source=BackendTarget.VECTOR  # 标记来源为向量库
            )
            retrieval_results.append(retrieved_hit)

        return retrieval_results

    def delete(self, memory_id: str):
        """Delete a single memory by its id from the vector store."""
        self.collection.delete(ids=[memory_id])

    def delete_by_agent(self, agent_id: str):
        """物理删除某个 Agent 的所有记忆（如：NPC 重置）"""
        self.collection.delete(where={"agent_id": agent_id})

    def clear(self):
        """清空向量库全部数据（用于索引重建）"""
        self.collection.delete(where={})