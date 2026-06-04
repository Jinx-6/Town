# @Time    :2026/5/12 13:57
# @Author  :进喜
# @File    :retention.py
# @Software:PyCharm


from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional

from ..schema.memory_item import MemoryItem, MemoryStage, MemoryRole


class RetentionConfig(BaseModel):
    """
    遗忘法则配置（系统新陈代谢的生命时钟）
    你可以随时调整这些参数来改变 Agent 的“记性”。
    """
    # 1. 触发压缩的时间线 (L1 原生流水账 -> L2/L3 提炼后的高维记忆)
    # 默认：距离现在超过 1 天的流水账，就会被清道夫盯上，拉去后台进行大模型总结。
    episodic_compression_threshold_days: float = Field(default=1.0, description="流水账压缩阈值(天)")

    # 2. 彻底销毁的时间线 (TTL - Time To Live)
    # 默认：流水账原话保留 30 天后彻底物理删除（反正已经提炼过了）
    episodic_ttl_days: int = Field(default=30, description="L1 情景记忆保留天数")

    # 默认：总结后的语义记忆保留 1 年
    semantic_ttl_days: int = Field(default=365, description="L2 语义记忆保留天数")

    # 💡 注：L3 知识图谱（Graph）通常被视为“客观真理”或“人物核心设定”，我们默认它是永久记忆，不设置 TTL。


class RetentionPolicy:
    """
    遗忘裁判官
    负责向 ConsolidateHub（清道夫）下发“死刑判决”或“劳动改造”指令。
    """

    def __init__(self, config: Optional[RetentionConfig] = None):
        self.config = config or RetentionConfig()

    def should_compress(self, item: MemoryItem, now: Optional[datetime] = None) -> bool:
        """
        判断一条记忆是否已经“成熟”，需要被大模型拉去总结提炼？
        """
        # 只有原汁原味的流水账（EPISODIC）才需要被压缩
        if item.stage != MemoryStage.EPISODIC:
            return False

        now = now or datetime.now(timezone.utc)
        item_time = item.timestamp if item.timestamp.tzinfo else item.timestamp.replace(tzinfo=timezone.utc)

        delta_days = (now - item_time).total_seconds() / (24 * 3600)

        # 如果时间已经超过了设定的阈值（比如 1 天），就该压缩了
        return delta_days >= self.config.episodic_compression_threshold_days

    def should_delete(self, item: MemoryItem, now: Optional[datetime] = None) -> bool:
        """
        判断一条记忆是否已经彻底过期，需要被物理销毁（Garbage Collection）？
        """
        # 铁律：系统强加的核心设定（如 Agent 的人格提示词）绝对不可遗忘
        if item.role == MemoryRole.SYSTEM:
            return False

        now = now or datetime.now(timezone.utc)
        item_time = item.timestamp if item.timestamp.tzinfo else item.timestamp.replace(tzinfo=timezone.utc)

        delta_days = (now - item_time).total_seconds() / (24 * 3600)

        # 根据不同阶层的记忆，应用不同的生存期
        if item.stage == MemoryStage.EPISODIC:
            return delta_days > self.config.episodic_ttl_days

        elif item.stage == MemoryStage.SEMANTIC:
            return delta_days > self.config.semantic_ttl_days

        # 默认放行（比如未定义的类型，或者图谱实体，坚决不删）
        return False