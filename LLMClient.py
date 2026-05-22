# @Time    :2026/4/14 13:14
# @Author  :进喜
# @File    :LLMClient.py
# @Software:PyCharm


# @Time    :2026/4/14
# @Author  :进喜
# @File    :llm_client.py
# @Software:PyCharm

from openai import AsyncOpenAI
from config import settings


class LLMClient:
    """大模型统一转接头 (目前对接本地 Ollama)"""

    def __init__(self):
        # 使用 OpenAI 兼容模式连接本地 11434 端口
        self.client = AsyncOpenAI(
            api_key=settings.ollama_api_key,
            base_url=settings.ollama_base_url
        )
        self.model = settings.ollama_model_id

    async def generate(self, system_prompt: str, messages: list) -> str:
        """
        这就是为你 relationship.py 和 agent.py 量身定制的万能接口！
        不论底层是大模型怎么换，这里的入参和出参永远不变。
        """
        # 组装完整的对话上下文，将 System Prompt 放在列表最前面
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=0.7  # 控制回答的发散度
            )
            # 剥洋葱一样把核心文本提取出来返回
            return response.choices[0].message.content
        except Exception as e:
            print(f"❌ 大模型调用失败: {e}")
            return "（NPC 暂时陷入了沉思...）"