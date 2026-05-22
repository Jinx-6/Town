# @Time    :2026/5/10 11:09
# @Author  :进喜
# @File    :events.py
# @Software:PyCharm


import uuid
from enum import Enum
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from .memory_item import MemoryItem
from .routing import BackendTarget

# ==========================================
# 1. 事件类型枚举 (系统里会发生哪些动作)
# ==========================================
class EventType(str, Enum):
    MEMORY_WRITE = "memory_write"             # 写入新记忆事件
    GRAPH_EXTRACTION = "graph_extraction"     # 图谱异步抽取事件
    CONSOLIDATION_TRIGGER = "consolidation"   # 记忆压缩/长时固化触发事件
    CONFLICT_DETECTED = "conflict_detected"   # 发现记忆冲突事件

# ==========================================
# 2. 基础事件模型 (所有事件的基类)
# ==========================================
class BaseMemoryEvent(BaseModel):
    """所有记忆引擎事件的通用信封"""
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: EventType = Field(...)
    trace_id: Optional[str] = Field(
        default=None,
        description="链路追踪ID，如果一个请求触发了多个事件，用这个串起来方便查日志"
    )

# ==========================================
# 3. 具体的事件载荷 (Payload) - 各种不同的电报
# ==========================================

class MemoryWriteEvent(BaseMemoryEvent):
    """
    异步写入事件
    Agent 聊完天后立刻扔出这个事件，然后就可以去干别的了。
    """
    event_type: EventType = EventType.MEMORY_WRITE
    item: MemoryItem = Field(..., description="需要写入的记忆对象")
    targets: List[BackendTarget] = Field(
        default=[BackendTarget.SQLITE, BackendTarget.VECTOR],
        description="需要将这条记忆同步写入哪些底层数据库"
    )

class GraphExtractionEvent(BaseMemoryEvent):
    """
    异步图谱抽取事件
    图谱抽取极慢（需要大模型思考三元组），绝对不能阻塞主线程。
    """
    event_type: EventType = EventType.GRAPH_EXTRACTION
    source_item_id: str = Field(..., description="触发抽取的原始流水账记忆ID")
    raw_text: str = Field(..., description="需要进行实体关系抽取的原始文本")
    agent_id: str = Field(..., description="所属居民 ID")  # 必须带上身份 ID，否则 Graph Store 不知道这三元组该写给谁

class ConsolidationTriggerEvent(BaseMemoryEvent):
    """
    压缩合并触发事件
    由大管家或定时任务发出，通知 ConsolidateHub 开始“干活”。
    """
    event_type: EventType = EventType.CONSOLIDATION_TRIGGER
    trigger_reason: str = Field(
        default="token_limit_reached",
        description="触发原因：例如 'scheduled_task' (定时), 'token_limit' (容量满了)"
    )
    days_before: int = Field(default=7, description="压缩多少天以前的记忆")
    batch_size: int = Field(default=10, description="本次最多处理几条")