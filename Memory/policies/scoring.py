# @Time    :2026/5/12 13:55
# @Author  :进喜
# @File    :scoring.py
# @Software:PyCharm


'''
仅仅依赖“语义相似度”会出现问题，比如匹配到一个月之前的事件，因此要加上时间衰减和多维度加权
'''
import math
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional


class ScoringWeights(BaseModel):
    """
    打分权重配置（调音台）
    你可以随时在配置文件中修改这些值，而不需要改动核心代码。
    """
    semantic_weight: float = Field(default=0.6, description="语义相似度权重 (alpha)")
    recency_weight: float = Field(default=0.3, description="时间新鲜度权重 (beta)")
    importance_weight: float = Field(default=0.1, description="重要性/阶段权重 (gamma)")

    # lambda 值越大，遗忘越快。0.05 意味着大约 14 天后新鲜度得分降为 0.5
    decay_rate: float = Field(default=0.05, description="时间衰减系数 (lambda)")


class MemoryScorer:
    """
    记忆打分评判官
    负责在检索层 (RetrieveHub) 将多路召回的数据进行标准化、打分和重新排序。
    """

    def __init__(self, weights: Optional[ScoringWeights] = None):
        # 依赖注入：如果没有传入自定义权重，则使用默认的配置
        self.weights = weights or ScoringWeights()

    def calculate_recency_score(self, created_at: datetime, now: Optional[datetime] = None) -> float:
        """
        计算时间新鲜度得分 (基于指数衰减曲线)
        得分范围: (0, 1]，越近越接近 1
        """
        if not created_at.tzinfo:
            created_at = created_at.replace(tzinfo=timezone.utc)

        now = now or datetime.now(timezone.utc)

        # 如果是未来的时间（防异常），直接给满分
        if created_at > now:
            return 1.0

        delta_time = now - created_at
        delta_days = delta_time.total_seconds() / (24 * 3600)

        # 指数衰减算法: exp(-lambda * delta_t)
        return math.exp(-self.weights.decay_rate * delta_days)

    def compute_final_score(
            self,
            semantic_score: float,
            created_at: datetime,
            is_semantic_stage: bool = False
    ) -> float:
        """
        计算最终的综合召回得分

        :param semantic_score: 向量数据库返回的原始相似度 (0-1)
        :param created_at: 记忆创建时间
        :param is_semantic_stage: 是否为长期语义记忆（L2/L3），长期记忆天生具有更高的重要性
        """
        # 1. 计算时间分
        recency_score = self.calculate_recency_score(created_at)

        # 2. 计算重要性分 (如果是经过提纯的 SEMANTIC 阶段，基础分给高一点)
        importance_score = 0.8 if is_semantic_stage else 0.4

        # 3. 按照公式加权求和
        final_score = (
                self.weights.semantic_weight * semantic_score +
                self.weights.recency_weight * recency_score +
                self.weights.importance_weight * importance_score
        )

        return round(final_score, 4)