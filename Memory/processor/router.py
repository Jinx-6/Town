# @Time    :2026/4/18 21:10
# @Author  :进喜
# @File    :router.py
# @Software:PyCharm

from ..schema.memory_item import MemoryItem
from ..schema.routing import RoutingDecision, BackendTarget

class WriteRouter:
    """
    智能路由层 (极速质检员)：决定数据该发往哪些底层存储。
    纯 CPU 计算，零外部 I/O 阻塞，保证在几毫秒内返回路由决策。
    """

    def __init__(self):
        # 启发式图谱触发词库（生产环境中可替换为本地轻量级 NLP 模型或正则）
        # 只要包含这些词，说明句子中极大概率包含“实体关系”，值得大模型去抽一下
        self.graph_triggers = [
            "喜欢", "讨厌", "爱", "恨",
            "是", "叫", "属于",
            "在", "去", "搬", "住",
            "爸爸", "妈妈", "老婆", "老公", "朋友", "同事", "狗", "猫",
            "买", "卖", "想"
        ]

    def route(self, item: MemoryItem) -> RoutingDecision:
        # 基础配置：无论如何，必须进 L0 缓存（保证当前对话连贯）和 L1 数据库（流水账兜底防丢）
        targets = [BackendTarget.CACHE, BackendTarget.SQLITE]
        content = item.content.strip()

        # 策略 1：废话/低信息量过滤（防爆库策略）
        # 如果用户只发了“嗯”、“好的”、“哈哈”，没有语义和图谱价值
        if len(content) < 4:
            return RoutingDecision(
                targets=targets,
                reason="低信息量回复，仅保留工作缓存与情景日志，阻断语义与图谱写入"
            )

        # 策略 2：常规长文本，允许进入 L2 向量库进行模糊语义索引
        targets.append(BackendTarget.VECTOR)
        reason = "常规有效文本，进入缓存、日志及向量库"

        # 策略 3：高密度关系嗅探 (触发 L3 异步图谱抽取)
        # 既然我们已经有了强大的异步事件总线，这里就可以果断放行，不怕阻塞！
        if any(trigger in content for trigger in self.graph_triggers):
            targets.append(BackendTarget.GRAPH)
            reason = "探测到潜在实体关系词汇，追加分配图谱异步抽取任务"

        return RoutingDecision(targets=targets, reason=reason)