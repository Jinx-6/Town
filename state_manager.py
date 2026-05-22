# @Time    :2026/4/9 09:47
# @Author  :进喜
# @File    :state_manager.py
# @Software:PyCharm


from typing import Dict, List, Optional
from datetime import datetime

class StateManager:
    """NPC状态管理器 (内存态)"""

    def __init__(self):
        self.npc_states: Dict[str, dict] = {}

    def initialize_npcs(self, initial_data: Optional[List[dict]] = None):
        """
        初始化NPC状态
        优化点：支持从外部（如配置文件或数据库）传入数据，不再把张三李四写死在代码里
        """
        # 如果外部没有传数据，则使用默认的兜底测试数据
        npcs = initial_data or [
            {
                "npc_id": "zhang_san",
                "name": "张三",
                "role": "Python工程师",
                "position": {"x": 300, "y": 200}
            },
            {
                "npc_id": "li_si",
                "name": "李四",
                "role": "产品经理",
                "position": {"x": 500, "y": 200}
            },
            {"npc_id": "wang_wu",
             "name": "王五",
             "role": "UI设计师",
             "position": {"x": 700, "y": 200}
             }
        ]

        for npc in npcs:
            self.npc_states[npc["npc_id"]] = {
                **npc,
                "is_busy": False,
                "current_action": "idle",
                "last_interaction": None,
                "background_dialogue": ""  # 👈 新增：用于存储后台生成的自言自语
            }

    def get_npc_state(self, npc_id: str) -> Optional[dict]:
        """获取NPC状态"""
        return self.npc_states.get(npc_id)

    def get_all_npc_states(self) -> List[dict]:
        """获取所有NPC状态"""
        return list(self.npc_states.values())

    def is_npc_busy(self, npc_id: str) -> bool:
        """检查NPC是否忙碌"""
        npc = self.npc_states.get(npc_id)
        return npc["is_busy"] if npc else False

    def set_npc_busy(self, npc_id: str, busy: bool):
        """设置NPC忙碌状态"""
        if npc_id in self.npc_states:
            self.npc_states[npc_id]["is_busy"] = busy
            if busy:
                self.npc_states[npc_id]["last_interaction"] = datetime.now().isoformat()

    def get_npc_count(self) -> int:
        """获取NPC数量"""
        return len(self.npc_states)

    # 👇 核心补充：对接我们的后台时间齿轮
    def update_npc_background_dialogue(self, npc_id: str, dialogue: str):
        """
        更新NPC的背景对话/动作（供上帝视角的 Simulator 调用）
        Godot 前端可以读取这个字段，在 NPC 头上冒出“气泡”
        """
        if npc_id in self.npc_states:
            self.npc_states[npc_id]["background_dialogue"] = dialogue
            self.npc_states[npc_id]["current_action"] = "talking_to_self"