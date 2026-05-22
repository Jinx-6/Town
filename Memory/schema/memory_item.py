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


# 🔥 升级点 1：将宽泛的 dict 升级为严格的 BaseModel
# 这样就可以利用 Pydantic 对元数据进行极值限制
class MemoryMetadata(BaseModel):
    importance: int = Field(default=0, ge=0, le=10, description="重要度评分 (0-10)")
    location: str = Field(default="unknown", description="发生地点")
    entities: List[str] = Field(default_factory=list, description="提取出的实体")
    tokens: int = Field(default=0, ge=0, description="Token消耗统计")
    # ✨ 新增：预留扩展位，用于存储不可预见的额外信息
    extra_info: Dict[str, Any] = Field(default_factory=dict, description="其他扩展元数据")


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