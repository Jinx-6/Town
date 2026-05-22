# @Time    :2026/4/9 09:26
# @Author  :进喜
# @File    :agent.py
# @Software:PyCharm

from memory import MemoryManager
from LLMClient import LLMClient


class CyberAgent:
    def __init__(self, npc_id: str, name: str, role: str, personality: str):
        self.npc_id = npc_id
        self.name = name
        self.role = role
        self.personality = personality
        self.base_system_prompt = f"你是{name}，一位{role}。你的性格特点是：{personality}。你在 Datawhale 办公室工作。"
        self.memory = MemoryManager(npc_id=npc_id, max_history_messages=10)
        self.llm = LLMClient()


    async def observe_and_react(self, observer_name: str, event_description: str, affinity_level: str = "陌生") -> str:
        """
        核心动作：感知并反应（新增了对 observer 的好感度动态感知）
        """
        # 2. 定义好感度对应的态度字典
        affinity_prompts = {
            "陌生": "你刚认识这位玩家，保持礼貌但不要过于热情，回复简短专业。",
            "熟悉": "你已经认识这位玩家，可以进行正常的交流，回复自然友好。",
            "友好": "你把这位玩家当作朋友，愿意分享更多信息，回复详细热情。",
            "亲密": "你非常信任这位玩家，可以分享部分私人话题，回复充满关心。",
            "挚友": "你把这位玩家当作最好的朋友，无话不谈，甚至可以开玩笑。"
        }

        # 3. 动态构建这一次对话的“情境提示” (Situational Context)
        attitude_instruction = affinity_prompts.get(affinity_level, affinity_prompts["陌生"])  # # 语法：dictionary.get(你要找的键, 如果没找到就用这个默认值)
        situational_prompt = f"【系统提示】现在正在和你交互的人是：{observer_name}。你们当前的关系是：{affinity_level}。{attitude_instruction}"

        # 4. 记录外部输入到大脑
        current_event = f"[{observer_name}]: {event_description}"
        await self.memory.add_interaction(role="user", content=current_event)

        # 5. 提取上下文
        context = await self.memory.get_current_context()

        # 6. 【核心拼装】将基础人设、动态态度和对话上下文融合！
        messages = [
                       {"role": "system", "content": f"{self.base_system_prompt}\n\n{situational_prompt}"}
                   ] + context

        # 7. 思考并生成回复
        response = await self.llm.generate(messages=messages)

        # 8. 将自己的反应存入记忆
        await self.memory.add_interaction(role="assistant", content=response)

        return response

    async def chat(self, player_name: str, player_message: str, system_prompt: str) -> str:
        """
        专门给 main.py 的 /dialogue 接口提供的发声通道
        """
        from LLMClient import LLMClient
        llm = LLMClient()

        # 历史消息
        history_context = await self.memory.get_current_context(
            current_message=player_message,
            player_name=player_name
        )
        # 历史上下文 + 当前玩家消息
        messages = history_context + [
            {"role": "user", "content": player_message}
        ]

        # 2. 严格按照 LLMClient.generate 的要求，传入两个参数！
        response = await llm.generate(
            system_prompt=system_prompt,
            messages=messages
        )


        # 分别存入玩家和NPC的话
        await self.memory.add_interaction(role="user", content=player_message, player_name=player_name)
        await self.memory.add_interaction(role="assistant", content=response, player_name=player_name)

        return response