# @Time    :2026/4/9 15:12
# @Author  :进喜
# @File    :memory.py
# @Software:PyCharm

from typing import List, Dict, Optional


class MemoryManager:
    """NPC 的记忆管理器（海马体）- 支持多层级记忆架构"""

    # 必须传入 npc_id，因为未来数据库里有镇上所有人，你需要一个 Key 来区分这是谁的记忆
    def __init__(self, npc_id: str, max_history_messages: int = 10):
        self.npc_id = npc_id

        # 1. 短期记忆缓存（Working Memory）
        self.history: List[Dict[str, str]] = []
        self.max_history_messages = max_history_messages

        # [预留接口] 未来在这里初始化你的数据库连接
        # self.vdb_client = QdrantClient(...)  # 用于长期感性记忆
        # self.redis_client = Redis(...)       # 用于结构化理性记忆

    async def add_interaction(self, role: str, content: str, player_name: Optional[str] = None):
        """记录新的对话，并分发到不同的记忆层级"""

        # 1. 存入短期缓存（不变）
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_history_messages:
            self.history = self.history[-self.max_history_messages:]

        # 2. [预留] 异步存入长期感性记忆库 (Qdrant)
        # 把刚才说的话变成向量，存起来
        await self._save_to_vector_db(role, content)

        # 3. [预留] 提取事实并存入结构化记忆库 (Redis)
        # 只有当玩家说话时，才去分析他暴露了什么信息（比如“我叫进喜，我喜欢猫”）
        if role == "user" and player_name:
            await self._extract_and_save_facts(player_name, content)

    async def get_current_context(self, current_message: Optional[str] = None, player_name: Optional[str] = None) -> \
    List[Dict[str, str]]:
        """
        【核心升级】提取上下文供大模型使用。
        未来的上下文 = 结构化情报(Redis) + 相关长期记忆(Qdrant) + 最近的短期聊天(List)
        """
        final_context = []

        # 1. [预留] 注入 Redis 里的结构化档案
        if player_name:
            player_facts = await self._retrieve_facts(player_name)
            if player_facts:
                # 作为一个系统提示塞入上下文
                final_context.append({
                    "role": "system",
                    "content": f"【来自你的结构化记忆】关于{player_name}的情报：{player_facts}"
                })

        # 2. [预留] 注入 Qdrant 里的长期记忆
        if current_message:
            relevant_memories = await self._retrieve_from_vector_db(current_message)
            if relevant_memories:
                final_context.append({
                    "role": "system",
                    "content": f"【脑海中闪回的过往记忆】：{relevant_memories}"
                })

        # 3. 注入短期记忆（最近聊天的上下文）
        final_context.extend(self.history)

        return final_context

    async def clear_memory(self):
        """强制洗脑（短期）"""
        self.history = []
        # [预留] await self.vdb_client.delete(npc_id)

    # ==========================================
    # 以下为未来接入数据库预留的内部私有方法 (Hooks)
    # ==========================================

    async def _save_to_vector_db(self, role: str, content: str):
        """未来：调用 Embedding 模型，存入 Qdrant"""
        pass

    async def _extract_and_save_facts(self, player_name: str, content: str):
        """未来：用小模型判断是否包含重要信息，若是，更新 Redis 里的 JSON"""
        pass

    async def _retrieve_from_vector_db(self, query: str) -> str:
        """未来：根据玩家当前说的话，去 Qdrant 搜最相关的 Top 3 记忆文本"""
        return ""

    async def _retrieve_facts(self, player_name: str) -> str:
        """未来：从 Redis 读取针对这个玩家的人物画像/事实列表"""
        return ""