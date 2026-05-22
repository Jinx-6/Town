# @Time    :2026/5/13 09:05
# @Author  :进喜
# @File    :online_feedback.py
# @Software:PyCharm


from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import os


class FeedbackType(str, Enum):
    """用户反馈类型"""
    THUMBS_UP = "thumbs_up"  # 👍 点赞（隐式强化：召回的记忆非常棒）
    THUMBS_DOWN = "thumbs_down"  # 👎 踩（隐式惩罚：大概率是出现幻觉或记忆过时了）
    CORRECTION = "correction"  # ✍️ 直接纠正（如用户说：“不对，我早就从腾讯离职了”）
    REGENERATE = "regenerate"  # 🔄 重新生成（用户觉得回答得不好，这也是一种弱负面信号）


class FeedbackEvent(BaseModel):
    """单条反馈事件的数据模型"""
    event_id: str = Field(..., description="反馈事件的唯一ID")
    message_id: str = Field(..., description="用户针对的哪一条AI回复的ID")
    user_id: str = Field(default="default_user")
    feedback_type: FeedbackType = Field(...)

    # ✨ 核心追踪：这条回复当时是基于哪些记忆碎片生成的？
    cited_memory_ids: List[str] = Field(default_factory=list)

    # 用户的详细吐槽（如果有的话）
    text_comment: Optional[str] = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.now)


class FeedbackCollector:
    """
    在线反馈收集与分析中心
    负责记录用户动作，并对“屡遭差评”的记忆进行降权或打标。
    """

    def __init__(self, log_dir: str = "logs/feedback"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, "online_feedback.jsonl")

    def record_feedback(self, feedback: FeedbackEvent):
        """
        1. 记录前端传来的反馈事件 (落盘为 JSONL 日志，方便后续跑大数据分析)
        """
        record = feedback.model_dump(mode='json')

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(
            f"📊 [FeedbackCollector] 收到在线反馈: {feedback.feedback_type.value} | 涉及记忆数: {len(feedback.cited_memory_ids)}")

        # 2. 如果是严重的负面反馈，立刻触发告警或自动降权逻辑
        if feedback.feedback_type in [FeedbackType.THUMBS_DOWN, FeedbackType.CORRECTION]:
            self._handle_negative_feedback(feedback)

    def _handle_negative_feedback(self, feedback: FeedbackEvent):
        """
        处理负面反馈的熔断机制 (概念性逻辑)
        """
        print(f"⚠️ 警报：AI 回复 {feedback.message_id} 遭到用户差评！")

        if not feedback.cited_memory_ids:
            print("   -> 无法追踪肇事记忆，可能只是模型单纯生成得不好。")
            return

        print(f"   -> 正在锁定肇事记忆: {feedback.cited_memory_ids}")

        # 【进阶玩法】：
        # 1. 自动降权：拿到这些 cited_memory_ids，去 VectorStore 或 GraphStore 里把它们的 confidence 扣掉 0.2。
        # 2. 隔离审查：把这些记忆 ID 发送给后台的“脏数据审查队列”，让大模型再深度反思一次。
        # 3. 纠正覆写：如果是 CORRECTION，可以直接抓取 text_comment 抛给 IngestHub，强制触发 UpdatePolicy 覆写错误记忆。

        if feedback.feedback_type == FeedbackType.CORRECTION and feedback.text_comment:
            print(f"   -> 💡 触发纠正机制：用户明确指出错误内容 [{feedback.text_comment}]")
            # 伪代码：重新走一遍摄入流程，置信度拉满到 1.0 (强制覆写)
            # ingest_hub.process_message(content=feedback.text_comment, role=MemoryRole.USER, confidence=1.0)

    def generate_daily_report(self) -> Dict[str, Any]:
        """
        统计当天的运行状况，供开发者监控系统健康度
        """
        stats = {
            "total": 0,
            "thumbs_up": 0,
            "thumbs_down": 0,
            "correction": 0
        }

        if not os.path.exists(self.log_file):
            return stats

        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    stats["total"] += 1
                    ftype = record.get("feedback_type")
                    if ftype in stats:
                        stats[ftype] += 1
                except Exception:
                    pass

        # 计算好评率 (满意度)
        positive = stats["thumbs_up"]
        negative = stats["thumbs_down"] + stats["correction"]
        if positive + negative > 0:
            stats["satisfaction_rate"] = positive / (positive + negative)
        else:
            stats["satisfaction_rate"] = 1.0

        return stats