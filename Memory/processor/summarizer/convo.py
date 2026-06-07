# @Time    :2026/5/6 15:26
# @Author  :进喜
# @File    :convo.py
# @Software:PyCharm


from typing import List, Optional
from LLMClient import LLMClient
from Memory.schema.memory_item import MemoryItem, MemoryRole


class ConvoSummarizer:
    “””
    短期对话摘要器 (Conversation Sliding Window Summarizer)
    用于解决单次会话过长导致的 Token 爆炸问题，生成当前对话的”前情提要”。
    “””

    def __init__(self, llm: LLMClient = None):
        llm = llm or LLMClient()
        self.model_name = llm.model
        self.client = llm.sync_client

    def generate_sliding_summary(self, recent_items: List[MemoryItem], previous_summary: str = "") -> str:
        """
        核心方法：增量式摘要。
        将“旧的摘要” + “刚被踢出缓存的几句对话”，融合成一个“新的摘要”。
        """
        dialogue_text = ""
        for item in recent_items:
            speaker = "用户" if item.role == MemoryRole.USER else "张三(你)"
            dialogue_text += f"{speaker}: {item.content}\n"

        system_prompt = """
        你是一个专门负责“前情提要”的助手。
        你需要根据【之前的摘要】和【近期被移出的对话】，生成一个最新的、连贯的【当前对话背景摘要】。

        要求：
        1. 保持行文流畅，让大模型读了就能立刻接上话茬。
        2. 重点保留当前正在讨论的话题上下文。
        3. 长度控制在 80 字以内。
        """

        prompt_content = f"""
        【之前的摘要】: {previous_summary if previous_summary else "无"}
        【近期被移出的对话】:
        {dialogue_text}
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_content}
                ],
                temperature=0.5
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"⚠️ [ConvoSummarizer] 短期上下文摘要失败: {e}")
            return previous_summary  # 如果失败，降级返回旧摘要