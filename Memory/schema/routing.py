# @Time    :2026/4/18 21:10
# @Author  :进喜
# @File    :routing.py
# @Software:PyCharm


from enum import Enum
from pydantic import BaseModel
from typing import List, Optional

class BackendTarget(str, Enum):
    """定义支持的后端存储目标"""
    CACHE = "cache"
    SQLITE = "sqlite"
    VECTOR = "vector"
    GRAPH = "graph"

class IntentType(str, Enum):
    FACT_STATEMENT = "fact_statement"
    TASK_INSTRUCTION = "task_instruction"
    QA_QUERY = "qa_query"
    CHITCHAT = "chitchat"

class RoutingDecision(BaseModel):
    """Router 决策结果"""
    targets: List[BackendTarget]
    reason: str = "默认路由策略"
    intent_type: Optional[IntentType] = None