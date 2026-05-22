# @Time    :2026/4/22 15:31
# @Author  :进喜
# @File    :assembler.py

from typing import List, Dict, Any
from ..schema.memory_item import MemoryStage
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievalResponse, RetrievedMemory


class PromptAssembler:
    """
    智能上下文组装引擎 (Context Assembler)
    负责将打分后的异构记忆进行二次过滤、分类，并按优先级拼装为 LLM 易读的 XML 结构。
    """

    def __init__(
            self,
            agent_name: str = "张三",
            max_context_length: int = 4000,
            min_score_threshold: float = 0.35  # ✨ 噪音门槛：分数太低的记忆不准上桌
    ):
        self.agent_name = agent_name
        self.max_context_length = max_context_length
        self.min_score_threshold = min_score_threshold

    def assemble(self, raw_query: str, retrieval_response: RetrievalResponse) -> List[Dict[str, str]]:
        """
        核心入口：将检索响应(RetrievalResponse)转化为标准的 Message 数组。
        """

        # 1. 记忆分类桶 (由原来的字符串匹配改为基于 BackendTarget 枚举)
        graph_facts = []  # L3：高可信事实
        recent_contexts = []  # L0/L1：近期对话
        historical_memories = []  # L2：长短期语义记忆

        # 2. 遍历并利用分数过滤
        for res in retrieval_response.results:
            # ✨ 质量防御：如果 Scoring 系统给出的综合分数太低，说明相关度极差，直接舍弃
            if res.score < self.min_score_threshold:
                continue

            content_with_score = f"{res.item.content} (相关度: {res.score:.2f})"

            if res.source == BackendTarget.GRAPH:
                graph_facts.append(res.item.content)  # 图谱事实通常很简洁，不加分数后缀
            elif res.source == BackendTarget.SQLITE:  # 假设 SQLITE 代表了近期流水账
                recent_contexts.append(content_with_score)
            elif res.source == BackendTarget.VECTOR:
                historical_memories.append(content_with_score)

        # 3. 按照“记忆优先级”构建 XML 结构
        # 优先级：客观事实 > 近期上下文 > 历史长记忆
        context_parts = []

        if graph_facts:
            part = "<objective_facts>\n" + "\n".join([f"- {f}" for f in graph_facts]) + "\n</objective_facts>"
            context_parts.append(part)

        if recent_contexts:
            part = "<recent_context>\n" + "\n".join([f"- {c}" for c in recent_contexts]) + "\n</recent_context>"
            context_parts.append(part)

        if historical_memories:
            part = "<historical_memory>\n" + "\n".join(
                [f"- {m}" for m in historical_memories]) + "\n</historical_memory>"
            context_parts.append(part)

        context_str = "\n\n".join(context_parts)

        # 4. 动态长度截断 (优先保证事实和近期上下文不被截断)
        if len(context_str) > self.max_context_length:
            context_str = context_str[:self.max_context_length] + "\n...[部分次要记忆已省略]..."

        # 5. 构建带引导性的 System Prompt
        system_prompt = self._generate_system_prompt(context_str)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_query}
        ]

    def _generate_system_prompt(self, context_str: str) -> str:
        """生成具备灵魂的指令模板"""
        return f"""
你叫{self.agent_name}。你拥有一套精密的外置记忆系统，能够调取与用户相关的历史事实、往期对话及深度画像。

【你的大脑记忆区】
{context_str if context_str else "（当前无相关历史记忆）"}

【回复准则】
1. **事实优先**：如果 <objective_facts> 中有冲突，请以此区为准。
2. **自然引用**：像老朋友聊天一样自然地运用这些记忆（例如：“我记得你上次提到过...”，“根据之前的情况...”），严禁复读原文或提及“我的数据库显示”。
3. **记忆补全**：如果记忆中存在关于用户的偏好、习惯或重要承诺，请在回复中予以体现以增强亲密度。
4. **诚实原则**：如果记忆区为空，请基于常识和当前对话逻辑回答，不要无中生有。
"""