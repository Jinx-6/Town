from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum


class MemoryRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MemoryStage(str, Enum):
    SENSORY = "sensory"
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryMetadata(BaseModel):
    # ── 原有字段 ──
    importance: int = Field(default=0, ge=0, le=10, description="重要度评分 (0-10)")
    location: str = Field(default="unknown", description="发生地点")
    entities: List[str] = Field(default_factory=list, description="提取出的实体")
    tokens: int = Field(default=0, ge=0, description="Token消耗统计")
    extra_info: Dict[str, Any] = Field(default_factory=dict, description="其他扩展元数据")

    # ── 新增：轻量级结构化元数据 ──
    memory_type: str = Field(default="unknown",
        description="记忆类型: event/query/dialogue/preference/fact/unknown")
    temporal_text: str = Field(default="",
        description="原始时间词，如 '昨天'、'前天'、'今天上午'")
    relative_time: str = Field(default="",
        description="标准化相对时间: 昨天/前天/今天/最近")
    keywords: List[str] = Field(default_factory=list,
        description="提取的重要词汇")
    event_type: str = Field(default="",
        description="事件类型: help/repair/buy/ask/general")
    is_factual_memory: bool = Field(default=True,
        description="是否为事实记忆；问句为 False")


class MemoryItem(BaseModel):
    # ✨ Pydantic v2 推荐的配置方式
    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        arbitrary_types_allowed=True
    )

    id: str = Field(default_factory=lambda: str(uuid4()), description="唯一UUID")
    content: str = Field(..., description="记忆文本")
    role: MemoryRole = Field(..., description="角色")

    # ✨ 核心：身份隔离标签（已保留）
    agent_id: str = Field(default="default_agent", description="所属居民ID")

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC时间戳"
    )

    metadata: MemoryMetadata = Field(default_factory=MemoryMetadata)
    stage: MemoryStage = Field(default=MemoryStage.SENSORY)

    # 注意：如果不需要在 API 中传输向量，可以设置 repr=False 减少日志输出压力
    vector: Optional[List[float]] = Field(None, description="Embedding向量", repr=False)

    @field_validator('content')
    @classmethod
    def validate_content_robustness(cls, v: str) -> str:
        stripped_content = v.strip()
        if not stripped_content:
            raise ValueError("内容不能为空")
        if len(stripped_content) > 5000:
            raise ValueError(f"内容超长 ({len(stripped_content)})")
        return stripped_content