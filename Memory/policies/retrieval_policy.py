# @Time    :2026/5/12 13:58
# @Author  :进喜
# @File    :retrieval_policy.py
# @Software:PyCharm


from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievedMemory


class RetrievalConfig(BaseModel):
    """
    多路召回宏观配置文件
    调节这些参数可以彻底改变 AI 提取记忆的行为模式。
    """
    # 1. 黄金配额分配 (Quota Allocation)
    # 假设总共有 10 个记忆名额，这里定义各数据源的占比上限
    graph_quota_ratio: float = Field(default=0.4, description="图谱事实最大占比 (L3)")
    vector_quota_ratio: float = Field(default=0.5, description="向量记忆最大占比 (L2)")
    cache_quota_ratio: float = Field(default=0.1, description="近期缓存占比 (L0/L1)")

    # 2. 短路熔断机制 (Short-Circuit)
    enable_short_circuit: bool = Field(default=True, description="是否允许短路阻断")
    short_circuit_threshold: float = Field(default=0.95, description="触发短路的分数阈值(仅限图谱)")

    # 3. 多样性与去重 (Diversity & Deduplication)
    enable_dedup: bool = Field(default=True, description="是否开启语义去重")
    # 如果两个记忆的内容太相似（比如计算 Jaccard 或文本重合度），丢弃分低的那个
    similarity_penalty_threshold: float = Field(default=0.85, description="触发去重的相似度阈值")


class RetrievalPolicy:
    """
    检索宏观策略执行官
    在 RetrieveHub 融合阶段被调用，负责配额裁决、熔断检测和去重。
    """

    def __init__(self, config: Optional[RetrievalConfig] = None):
        self.config = config or RetrievalConfig()

    def calculate_quotas(self, total_limit: int) -> Dict[BackendTarget, int]:
        """
        计算各个存储后端的“录取名额”
        返回: {BackendTarget.GRAPH: 4, BackendTarget.VECTOR: 5, ...}
        """
        return {
            BackendTarget.GRAPH: max(1, int(total_limit * self.config.graph_quota_ratio)),
            BackendTarget.VECTOR: max(1, int(total_limit * self.config.vector_quota_ratio)),
            BackendTarget.SQLITE: max(1, int(total_limit * self.config.cache_quota_ratio))
        }

    def should_short_circuit(self, current_results: List[RetrievedMemory]) -> bool:
        """
        判断是否触发“短路熔断”：
        如果我们已经从图谱中找到了极其确凿的“一击必杀”事实，就没必要再搜向量库了。
        """
        if not self.config.enable_short_circuit:
            return False

        for res in current_results:
            if res.source == BackendTarget.GRAPH and res.score >= self.config.short_circuit_threshold:
                # 命中了高分图谱事实！直接阻断后续的耗时检索。
                return True

        return False

    def enforce_diversity(self, sorted_results: List[RetrievedMemory]) -> List[RetrievedMemory]:
        """
        执行多样性惩罚 / 去重机制
        输入：已经按分数排好序的结果
        输出：剔除高度重复后的结果
        """
        if not self.config.enable_dedup:
            return sorted_results

        diverse_results = []
        seen_texts = []

        for res in sorted_results:
            text = res.item.content

            # 简单的文本重叠度检测（生产环境中可以用轻量级特征或 Hash）
            is_duplicate = False
            for seen in seen_texts:
                # 如果当前文本和已选文本的字符重叠度极高，视为重复
                if self._calculate_overlap(text, seen) > self.config.similarity_penalty_threshold:
                    is_duplicate = True
                    break

            if not is_duplicate:
                diverse_results.append(res)
                seen_texts.append(text)

        return diverse_results

    def _calculate_overlap(self, text1: str, text2: str) -> float:
        """简单的 Jaccard 相似度/字符重合度计算"""
        set1 = set(text1)
        set2 = set(text2)
        if not set1 or not set2:
            return 0.0
        return len(set1.intersection(set2)) / len(set1.union(set2))