# @Time    :2026/4/8 15:35
# @Author  :进喜
# @File    :relationship.py
# @Software:PyCharm


import asyncio
from typing import Dict, Any


# 引入咱们自己封装的底层大模型接口
# from infra.llm_client import LLMClient

class RelationshipManager:
    """好感度管理器（情感中枢）"""

    def __init__(self, llm_client: 'LLMClient'):
        self.affinity_data: Dict[str, Dict[str, Any]] = {}  # 内存态存储
        self.llm = llm_client

    async def analyze_sentiment(self, player_message: str, npc_reply: str) -> int:
        """分析对话情感，异步返回好感度变化值"""

        # 优化点 1：消除大模型输出歧义，直接强制约定输出分数值
        prompt = f"""分析以下对话中玩家的态度:
玩家: {player_message}
NPC: {npc_reply}

请判断玩家的态度，并严格按照以下规则返回一个数字：
- 如果友好(礼貌、热情、感谢): 返回 5
- 如果中立(普通的询问或陈述): 返回 2
- 如果不友好(粗鲁、冷漠、批评): 返回 -3

【警告】只允许输出数字，不要包含任何标点符号或解释性文字！"""

        try:
            # 优化点 2：改为异步调用 (假设我们的 LLMClient 增加了 async_generate 方法)
            # 在实际开发中，任何涉及网络 I/O 的操作都必须 await
            response = await self.llm.generate(
                system_prompt="你是一个情感分析器。",
                messages=[{"role": "user", "content": prompt}]
            )

            # 清理可能的空格和换行
            score_change = int(response.strip())
            # 限制极值，防止大模型抽风输出个 100 出来
            return max(-3, min(5, score_change))
        except Exception as e:
            print(f"[Warning] 情感分析解析失败，默认给中立分: {e}")
            return 2

    async def update_affinity(self, npc_id: str, player_name: str,
                              player_message: str, npc_reply: str) -> dict:
        """更新好感度 (必须是 async，因为它内部调用了 async 的 analyze_sentiment)"""
        key = f"{npc_id}_{player_name}"

        # 1. 初始化陌生人关系
        if key not in self.affinity_data:
            self.affinity_data[key] = {
                "score": 0,
                "level": "陌生",
                "interaction_count": 0
            }

        # 2. 异步分析情绪分数
        score_change = await self.analyze_sentiment(player_message, npc_reply)

        # 3. 计算并截断分数 (0 ~ 100)
        current_score = self.affinity_data[key]["score"]
        new_score = max(0, min(100, current_score + score_change))

        # 4. 刷新等级
        level = self._get_affinity_level(new_score)

        # 5. 更新状态字典
        self.affinity_data[key].update({
            "score": new_score,
            "level": level,
            "interaction_count": self.affinity_data[key]["interaction_count"] + 1
        })

        return self.affinity_data[key]

    def _get_affinity_level(self, score: int) -> str:
        """纯内部逻辑：根据分数获取好感度等级"""
        if score <= 20:
            return "陌生"
        elif score <= 40:
            return "熟悉"
        elif score <= 60:
            return "友好"
        elif score <= 80:
            return "亲密"
        else:
            return "挚友"

    def get_affinity_info(self, npc_id: str, player_name: str) -> dict:
        """
        【对外接口】获取指定 NPC 与指定玩家的当前好感度信息
        """
        # 1. 组合一个唯一的双人关系键，比如 "zhang_san_jinxi"
        # 这样柜子里就不会把不同玩家的好感度搞混了
        relation_key = f"{npc_id}_{player_name}"

        # 2. 如果这是他们第一次聊天（柜子里找不到档案），就新建一个初始档案
        if relation_key not in self.affinity_data:
            self.affinity_data[relation_key] = {
                "score": 10, # 初始好感度给个 10 分（你可以随便改）
                "interaction_count": 0
            }

        # 3. 从柜子里把当前的分数拿出来
        current_score = self.affinity_data[relation_key]["score"]

        # 4. 关键一步：调用你发现的那个标尺（内部方法），把数字翻译成汉字等级！
        current_level = self._get_affinity_level(current_score)

        # 5. 组装成 main.py 期待的字典格式返回
        return {
            "level": current_level,
            "score": current_score
        }

