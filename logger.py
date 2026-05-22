# @Time    :2026/4/9 09:49
# @Author  :进喜
# @File    :logger.py
# @Software:PyCharm


import logging
import os
import json
from datetime import datetime


class DialogueLogger:
    """独立的对话与错误日志中心"""

    def __init__(self):
        # 1. 确保日志文件夹存在
        os.makedirs("logs", exist_ok=True)

        # 2. 配置业务对话日志 (专门记录谁说了什么)
        self.dialogue_logger = logging.getLogger("dialogue")
        self.dialogue_logger.setLevel(logging.INFO)
        # 写入 logs/dialogue.log
        dh = logging.FileHandler("logs/dialogue.log", encoding="utf-8")
        dh.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
        self.dialogue_logger.addHandler(dh)

        # 3. 配置系统错误日志 (专门记录报错)
        self.error_logger = logging.getLogger("error")
        self.error_logger.setLevel(logging.ERROR)
        # 写入 logs/error.log
        eh = logging.FileHandler("logs/error.log", encoding="utf-8")
        eh.setFormatter(logging.Formatter('%(asctime)s | ERROR | %(message)s'))
        self.error_logger.addHandler(eh)

    def log_dialogue(self, npc_id: str, player_name: str, player_message: str, npc_reply: str, affinity_info: dict):
        """记录标准对话，采用 JSON 格式，方便日后数据分析"""
        log_data = {
            "npc_id": npc_id,
            "player_name": player_name,
            "affinity_level": affinity_info["level"],
            "player_message": player_message,
            "npc_reply": npc_reply
        }
        # 将字典转为不转义中文的 JSON 字符串
        self.dialogue_logger.info(json.dumps(log_data, ensure_ascii=False))

    def log_error(self, error_msg: str):
        """记录报错信息"""
        self.error_logger.error(error_msg)


# ==========================================
# 暴露出一个全局单例供其他模块直接引用
# ==========================================
logger_instance = DialogueLogger()