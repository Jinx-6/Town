# @Time    :2026/5/14 14:40
# @Author  :进喜
# @File    :api.py
# @Software:PyCharm


# api/main.py
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional

# 导入你之前写好的核心组件
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub
from Memory.schema.memory_item import MemoryRole
from Memory.schema.retrieval import RetrievalRequest

app = FastAPI(title="Town Memory Engine API")

# --- 1. 全局组件初始化 (建议在启动时完成) ---
# 这里引用你之前测试成功的那些初始化逻辑
# ingest_hub, retrieve_hub = init_memory_system()

# --- 2. 数据模型定义 ---
class IngestRequest(BaseModel):
    content: str
    agent_id: str
    role: MemoryRole = MemoryRole.USER
    confidence: float = 1.0

class QueryRequest(BaseModel):
    agent_id: str
    query: str
    limit: int = 5

# --- 3. 路由设计 ---

@app.post("/v1/ingest")
async def ingest_memory(req: IngestRequest):
    """
    存入记忆：
    Agent 说话或观察到的信息，通过这里进入引擎。
    """
    # 这里调用你之前的 ingest_hub.process_message
    # 因为 ingest 内部用了 AsyncDispatcher，所以这里会立即返回
    # 而复杂的图谱提取会在后台静默完成。
    result = IngestHub.process_message(
        content=req.content,
        role=req.role,
        agent_id=req.agent_id, # 记得在 Schema 里加上 agent_id 区分不同居民
        confidence=req.confidence
    )
    return {"status": "processing", "memory_id": result.id if result else "async"}

@app.post("/v1/retrieve")
async def retrieve_memory(req: QueryRequest):
    """
    检索记忆：
    Agent 决策前，先来这里问：“我记得关于这个人/事的什么？”
    """
    retrieval_req = RetrievalRequest(query=req.query, limit=req.limit)
    # 调用你写好的 RetrieveHub
    results =RetrieveHub.retrieve(retrieval_req, agent_id=req.agent_id)
    return {"results": results.results}