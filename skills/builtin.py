"""
内置 Skill 实现。
"""
import time
from typing import List, Optional

from skills.base import Skill, SkillResult
from Memory.schema.memory_item import MemoryRole


class ObservePublicEventSkill(Skill):
    """观察公共频道事件，写入私有观察记忆。

    不生成回复，仅记录「我观察到谁说了什么」。
    """

    def __init__(self, max_content_len: int = 200):
        super().__init__()
        self.name = "observe_public_event"
        self.description = "观察 public topic 里的 agent 消息，写入私有观察记忆"
        self._max_content_len = max_content_len

    def applies_to(self, event) -> bool:
        if event.source_agent_id == "system":
            return False
        if "public" not in event.topic:
            return False
        return True

    async def run(self, event, agent) -> SkillResult:
        source = getattr(event, "metadata", {}) or {}
        agent_name = source.get("agent_name", event.source_agent_id)
        snippet = event.content[:self._max_content_len]

        observation = (
            f"[观察] {agent_name} 在 {event.topic} 频道说: {snippet}"
        )

        try:
            agent.ingest.process_message(
                content=observation,
                role=MemoryRole.SYSTEM,
                agent_id=agent.agent_id,
                metadata={"memory_type": "observation", "is_factual_memory": False},
            )
            # Allow async dispatcher a tick
            time.sleep(0.05)
            return SkillResult(success=True, data={"observation": observation})
        except Exception as e:
            return SkillResult(success=False, error=str(e))


class SummarizeRecentConversationSkill(Skill):
    """将最近 N 条对话事件汇总为一段摘要，写入长期记忆。"""

    def __init__(self, llm_client, max_events: int = 10):
        super().__init__()
        self.name = "summarize_recent_conversation"
        self.description = "将最近的公共事件汇总为摘要，写入 Agent 长期记忆"
        self._llm = llm_client
        self._max_events = max_events

    async def summarize(self, events: list, agent) -> SkillResult:
        if not events:
            return SkillResult(success=False, error="没有事件可供总结")

        recent = events[-self._max_events:]
        lines = []
        for e in recent:
            src = e.source_agent_id
            lines.append(f"[{src}] {e.content[:120]}")

        prompt = (
            "请用 2-3 句中文总结以下对话的主要内容，只输出摘要本身:\n"
            + "\n".join(lines)
        )

        try:
            summary = await self._llm.generate(
                system_prompt="你是一个对话总结助手。",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            return SkillResult(success=False, error=f"LLM 调用失败: {e}")

        try:
            agent.ingest.process_message(
                content=f"[对话摘要] {summary.strip()}",
                role=MemoryRole.SYSTEM,
                agent_id=agent.agent_id,
                metadata={
                    "memory_type": "summary",
                    "is_factual_memory": True,
                    "event_count": len(recent),
                },
            )
            time.sleep(0.05)
        except Exception as e:
            return SkillResult(success=False, error=f"写入记忆失败: {e}")

        return SkillResult(success=True, data={
            "summary": summary.strip(),
            "event_count": len(recent),
        })
