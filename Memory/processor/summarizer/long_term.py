# @Time    :2026/5/6 15:23
# @Author  :进喜
# @File    :long_term.py
# @Software:PyCharm


from typing import Optional
from LLMClient import LLMClient


class LongTermSummarizer:
    """
    长期记忆压缩器 (Long-Term Memory Summarizer)
    将天级别的陈旧对话（Episodic Memory），压缩提纯为高密度的语义记忆（Semantic Memory）。
    """

    def __init__(self):
        llm = LLMClient()
        self.model_name = llm.model
        self.client = llm.sync_client

    def compress_to_semantic(self, dialogue_text: str) -> str:
        """
        核心方法：将流水账转换为上帝视角的客观陈述。
        """
        system_prompt = """
        你是一个精通信息提纯的“记忆降维算法”。
        请将以下冗长、零碎的对话记录，压缩为一段高密度的、第三人称视角的客观总结。

        【压缩法则】
        1. 必须剔除：语气词（啊、哦、嗯）、日常寒暄、无意义的重复对话。
        2. 必须保留：用户的核心偏好、发生的重要事实、Agent曾做出的承诺或给出的核心建议。
        3. 视角：使用“用户”和“张三(Agent)”作为主语进行客观陈述。
        4. 长度限制：务必将结果控制在 100~150 字以内。
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"【原始对话记录】:\n{dialogue_text}"}
                ],
                temperature=0.3  # 低温度，保证信息不失真
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"⚠️ [LongTermSummarizer] 长期记忆压缩失败: {e}")
            return ""