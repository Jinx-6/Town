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
from typing import List, Optional
from datetime import datetime

# ✨ 注意：这里导入的是你定义的 RetrievedMemory 和 BackendTarget
from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from ..schema.retrieval import RetrievedMemory
from ..schema.routing import BackendTarget
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
        """
        向向量库添加记忆，强制写入 agent_id。
        """
        role_str = getattr(item.role, 'value', item.role)

        # 构造元数据，加入 agent_id 以实现隔离
        chroma_metadata = {
            "agent_id": item.agent_id,  # 👈 身份标签
            "role": role_str,
            "stage": MemoryStage.SEMANTIC.value,
            "timestamp": item.timestamp.isoformat(),
            # 兼容处理 metadata 字典或 Pydantic 对象
            "metadata_json": json.dumps(
                item.metadata if isinstance(item.metadata, dict) else item.metadata.model_dump())
        }

        self.collection.add(
            documents=[item.content],
            metadatas=[chroma_metadata],
            ids=[item.id]
        )

    def search(self, query: str, agent_id: str, limit: int = 5) -> List[RetrievedMemory]:
        """
        ✨ 改造完成：使用 RetrievedMemory 封装返回结果
        """
        # 使用 where 子句实现身份隔离
        results = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where={"agent_id": agent_id}  # 👈 核心：只看属于该 Agent 的档案
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

    def delete_by_agent(self, agent_id: str):
        """物理删除某个 Agent 的所有记忆（如：NPC 重置）"""
        self.collection.delete(where={"agent_id": agent_id})