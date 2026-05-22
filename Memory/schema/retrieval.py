# @Time    :2026/5/10 10:42
# @Author  :进喜
# @File    :retrieval.py
# @Software:PyCharm

'''
1.图谱搜出来的关系、向量搜出来的句子、SQLite 搜出来的流水账，全部被强行打包成了统一的 RetrievedMemory 格式。
    这对上层的 RetrieveHub 来说非常舒服，它只需要处理这个统一的列表，无需关心底层用的什么语法。
2.如果只有一个特定的查询只关心人物关系，就可以只传 targets=[BackendTarget.GRAPH]
3.直接将结果变为了高可读的文本

本文件引入了软件架构中极其重要的“数据传输对象（DTO - Data Transfer Object）”和“适配器模式（Adapter）”的思想。
将Neo4j（图谱）、 Chroma/Faiss（向量）、 SQLite统一为了RetrievalMemory，也就是说实现了标准化也就是说给到LLM的记忆格式是由本文件决定的并且是统一的可读的，Prompt形式
'''
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from .memory_item import MemoryItem
from .routing import BackendTarget


# ==========================================
# 1. 检索请求 (Request) - “点菜单”
# ==========================================
class RetrievalRequest(BaseModel):
    """
    统一记忆检索请求。
    """
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(..., description="检索文本或核心实体")
    limit: int = Field(default=5, description="单库返回上限")

    # ✨ 核心：在请求层预留 agent_id，确保 API 传入时有据可查
    agent_id: str = Field(default="default_agent", description="发起检索的 Agent 身份 ID")

    targets: List[BackendTarget] = Field(
        default=[BackendTarget.VECTOR, BackendTarget.GRAPH, BackendTarget.SQLITE],
        description="检索目标库"
    )
    score_threshold: float = Field(default=0.7, description="最低相似度门槛")
    metadata_filters: Optional[Dict[str, Any]] = Field(default=None)


# ==========================================
# 2. 单条结果 (Hit) - “合并移植后的结果封装”
# ==========================================
class RetrievedMemory(BaseModel):
    """
    ✨ 移植与进化：原 RetrievalResult 现已统一为 RetrievedMemory。
    """
    model_config = ConfigDict(use_enum_values=True)

    item: MemoryItem = Field(..., description="底层返回的记忆实体")

    # 这里的 score 已经根据不同后端进行了归一化处理
    score: float = Field(default=1.0, description="相关性得分")

    # 相比原 RetrievalResult 的 source_db: str，
    # 这里使用强类型的 BackendTarget，安全性更高
    source: BackendTarget = Field(..., description="数据来源后端")


# ==========================================
# 3. 总体返回包 (Response) - “一桌成品菜”
# ==========================================
class RetrievalResponse(BaseModel):
    """
    统一记忆检索结果包。
    """
    original_query: str = Field(..., description="原始查询词")
    agent_id: str = Field(default="default_agent", description="归属 Agent")
    results: List[RetrievedMemory] = Field(default_factory=list, description="排序后的结果")

    def get_formatted_context(self) -> str:
        """
        [工程利器]：直接转化为 LLM 可读的上下文。
        """
        if not self.results:
            return "（无相关历史记忆，这可能是你们第一次交流）"

        context_lines = []
        # 增加一个身份标识头
        context_lines.append(f"### 与居民 [{self.agent_id}] 相关的历史背景：")

        for rank, hit in enumerate(self.results, start=1):
            time_str = hit.item.timestamp.strftime('%Y-%m-%d %H:%M')
            # 根据来源显示不同的标签
            source_label = f"[{hit.source}]"

            context_lines.append(
                f"{rank}. {source_label} ({time_str}): {hit.item.content}"
            )

        return "\n".join(context_lines)